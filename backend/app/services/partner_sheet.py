"""The founder's partner workbook, imported as-is.

Layout: a `Legend` tab (reference only), then one tab per city. Each city tab
has a title in row 1, a note in row 2, a blank row 3, headers in row 4 and
partners from row 5:

    ID | Name | City | Type | Category | Tier | Phone | Email | Status | Notes / Source

Any tab whose header row matches is treated as a city tab, so new cities need
no code change. `read` turns the workbook into rows; `plan` works out what an
import would do (add, update, skip with a reason) without writing anything, so
the preview and the commit always agree; `apply` writes the plan.

Re-import rules (the sheet is the source of truth for partner fields):
  * match by email, else by partner ID with the tier segment ignored (the ID
    changes when a TBD tier is filled in);
  * only non-empty sheet values overwrite; an empty cell never erases data;
  * status, messages and history are never changed by a re-import, except
    that a sheet row now marked Bounced/Unsubscribed adds the person to the
    opt-out list (that can only make the app more careful);
  * an email change is applied only if nothing was sent to the old address;
  * suppressed/unsubscribed contacts are never touched.
"""
import io
import re
import time

from . import importer, marketplace

HEADERS = ["id", "name", "city", "type", "category", "tier", "phone", "email", "status", "notes / source"]
HEADER_SCAN_ROWS = 10

# Sheet Status -> contact status. Interested/Not interested are the app's adopted/declined.
STATUS_IN = {
    "": "new", "not contacted": "new", "new": "new", "to contact": "new",
    "contacted": "sent", "sent": "sent", "emailed": "sent",
    "replied": "replied", "responded": "replied",
    "interested": "adopted", "onboarded": "adopted", "listed": "adopted",
    "not interested": "declined", "declined": "declined",
    "bounced": "bounced", "bounce": "bounced",
    "unsubscribed": "unsubscribed", "opted out": "unsubscribed", "opt-out": "unsubscribed", "do not contact": "unsubscribed",
}
STATUS_OUT = {
    "new": "Not contacted", "drafted": "Not contacted", "sent": "Contacted", "replied": "Replied",
    "adopted": "Interested", "declined": "Not interested", "bounced": "Bounced", "unsubscribed": "Unsubscribed",
}
OPT_OUT = {"bounced", "unsubscribed"}
GENERIC_LOCALS = {"info", "care", "support", "hello", "contact", "admin", "enquiry", "enquiries", "sales", "help",
                  "customercare", "office", "team", "mail", "connect", "hi"}

# What a re-import may overwrite (non-empty sheet values only).
UPDATE_FIELDS = ["name", "type", "category", "tier", "partner_id", "phone", "city",
                 "website", "description", "source_url", "needs_check"]


class SheetError(Exception):
    pass


def _norm(v) -> str:
    return re.sub(r"\s+", " ", str(v if v is not None else "").strip().lower())


def _header_index(rows: list[list]) -> int | None:
    for i, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        if [_norm(c) for c in row[:len(HEADERS)]] == HEADERS:
            return i
    return None


def is_partner_workbook(filename: str, data: bytes) -> bool:
    if not (filename or "").lower().endswith((".xlsx", ".xlsm")):
        return False
    try:
        return bool(read(data)["tabs"])
    except SheetError:
        return False


def read(data: bytes) -> dict:
    """{'tabs': [names], 'skipped_tabs': [names], 'rows': [{tab, row, cells...}]}"""
    import openpyxl

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as e:
        raise SheetError(f"Couldn't read that workbook: {e}") from e
    tabs, skipped, rows = [], [], []
    for ws in wb.worksheets:
        table = [list(r) for r in ws.iter_rows(values_only=True)]
        h = _header_index(table)
        if h is None:
            skipped.append(ws.title)
            continue
        tabs.append(ws.title)
        for n, r in enumerate(table[h + 1:], start=h + 2):
            cells = [("" if v is None else str(v).strip()) for v in (list(r) + [None] * len(HEADERS))[:len(HEADERS)]]
            if not any(cells):
                continue  # trailing empty rows
            rows.append({"tab": ws.title, "row": n, **dict(zip(["id", "name", "city", "type", "category", "tier",
                                                                  "phone", "email", "status", "notes"], cells))})
    wb.close()
    return {"tabs": tabs, "skipped_tabs": skipped, "rows": rows}


def partner_key(pid: str) -> str:
    """'B-SUP-T2-MUM-01' and 'B-SUP-TBD-MUM-01' -> 'B-SUP-MUM-01' (tier-insensitive)."""
    parts = [p for p in (pid or "").strip().upper().split("-") if p]
    if len(parts) == 5:
        del parts[2]
    return "-".join(parts)


def blocked_reason(name: str, notes: str, patterns: list[str]) -> str:
    text = _norm(f"{name} {notes}")
    for p in patterns or []:
        p = _norm(p)
        if p and re.search(r"(?<![a-z0-9])" + re.escape(p) + r"(?![a-z0-9])", text):
            return p
    return ""


def is_generic(email: str) -> bool:
    return email.split("@")[0].lower() in GENERIC_LOCALS


def _first_email(cell: str) -> str:
    for part in re.split(r"[\s,;/]+", cell or ""):
        part = part.strip().strip("<>()").lower()
        if part:
            return part
    return ""


def to_contact(r: dict) -> dict:
    """One sheet row -> contact fields (empty string = the sheet had nothing)."""
    from_id = marketplace.parse_partner_id(r["id"])
    t = _norm(r["type"])
    ctype = {"b": "brand", "brand": "brand", "p": "professional", "professional": "professional"}.get(t) or from_id.get("type", "")
    category = marketplace.normalise_category(r["category"], ctype) or from_id.get("category", "")
    tier = r["tier"].upper()
    tier = "" if tier in ("TBD", "-") else tier
    parsed = marketplace.parse_notes_source(r["notes"])
    city = r["city"] or ""
    return {
        "name": re.sub(r"\s+", " ", r["name"]).strip(),
        "email": _first_email(r["email"]),
        "type": ctype,
        "category": category,
        "tier": tier or from_id.get("tier", ""),
        "partner_id": r["id"].strip().upper(),
        "phone": r["phone"],
        "city": importer.CITY_CODES.get(city.upper(), city) or r["tab"],
        **parsed,
        "_notes_present": bool(r["notes"]),
    }


def plan(conn, rows: list[dict], settings: dict) -> dict:
    """What an import would do. Read-only."""
    patterns = settings.get("blocked_patterns") or []
    existing = [dict(x) for x in conn.execute("SELECT * FROM contacts")]
    by_email = {c["email"].lower(): c for c in existing}
    by_key = {}
    for c in existing:
        if c.get("partner_id"):
            by_key.setdefault(partner_key(c["partner_id"]), c)
    suppressed = {r[0].lower() for r in conn.execute("SELECT email FROM suppression")}
    sent_to = {r[0] for r in conn.execute("SELECT DISTINCT contact_id FROM messages WHERE status = 'sent'")}

    adds, updates, unchanged, skipped, flags = [], [], [], [], []
    seen_email: dict[str, str] = {}
    seen_contact: set[int] = set()

    def skip(r, reason, detail=""):
        skipped.append({"tab": r["tab"], "row": r["row"], "name": r["name"], "reason": reason, "detail": detail})

    for r in rows:
        where = f"{r['tab']} row {r['row']}"
        c = to_contact(r)
        if hit := blocked_reason(r["name"], r["notes"], patterns):
            skip(r, "helpline", f"matches “{hit}” — helplines are never imported")
            continue
        status_text = _norm(r["status"])
        if status_text not in STATUS_IN:
            skip(r, "unknown status", f"Status “{r['status']}” isn't one of: {', '.join(sorted(set(STATUS_OUT.values())))}")
            continue
        sheet_status = STATUS_IN[status_text]
        if not c["email"]:
            skip(r, "no email")
            continue
        if not importer.EMAIL_RE.match(c["email"]):
            skip(r, "invalid email", c["email"])
            continue
        if c["email"] in seen_email:
            skip(r, "duplicate email", f"{c['email']} is already on {seen_email[c['email']]}")
            continue
        seen_email[c["email"]] = where
        if not c["type"]:
            skip(r, "unknown type", f"Type “{r['type']}” should be B or P")
            continue
        if not c["name"]:
            skip(r, "no name")
            continue

        match = by_email.get(c["email"])
        key = partner_key(c["partner_id"]) if c["partner_id"] else ""
        if match is None and key:
            match = by_key.get(key)
        if match is not None and match["id"] in seen_contact:
            skip(r, "duplicate partner", f"{where} matches a contact already updated from an earlier row")
            continue

        if match is None:
            if c["email"] in suppressed:
                skip(r, "opted out", "on the opt-out list")
                continue
            adds.append({"tab": r["tab"], "row": r["row"], "contact": c, "status": sheet_status,
                         "generic": is_generic(c["email"])})
            continue

        seen_contact.add(match["id"])
        if match["status"] == "unsubscribed" or match["email"].lower() in suppressed:
            skip(r, "opted out", f"{match['email']} is on the opt-out list — contact left unchanged")
            continue

        changes = {}
        for f in UPDATE_FIELDS:
            new = c.get(f, "")
            if f == "needs_check":
                # The Notes cell is the unit: if it's filled in and no longer has a CHECK note, the check is done.
                if c["_notes_present"] and new != (match.get(f) or ""):
                    changes[f] = new
                continue
            if new and new != (match.get(f) or ""):
                changes[f] = new
        if c["email"] != match["email"].lower():
            if match["id"] in sent_to:
                flags.append({"tab": r["tab"], "row": r["row"], "name": c["name"],
                              "message": f"Email changed in the sheet to {c['email']}, but {match['email']} was already "
                                         "emailed — kept the old address. Update it by hand if it's really changed."})
            elif c["email"] in suppressed or c["email"] in by_email:
                flags.append({"tab": r["tab"], "row": r["row"], "name": c["name"],
                              "message": f"Email changed in the sheet to {c['email']}, which is already in use or opted out — kept {match['email']}."})
            else:
                changes["email"] = c["email"]
        opt_out = sheet_status in OPT_OUT and match["status"] not in OPT_OUT
        if opt_out:
            flags.append({"tab": r["tab"], "row": r["row"], "name": c["name"],
                          "message": f"Sheet says {r['status']} — {match['email']} added to the opt-out list."})
        if changes or opt_out:
            updates.append({"tab": r["tab"], "row": r["row"], "contact_id": match["id"], "name": c["name"] or match["name"],
                            "changes": changes, "opt_out": sheet_status if opt_out else None})
        else:
            unchanged.append(match["id"])

    reasons: dict[str, int] = {}
    for s in skipped:
        reasons[s["reason"]] = reasons.get(s["reason"], 0) + 1
    return {
        "adds": adds, "updates": updates, "unchanged": len(unchanged), "skipped": skipped, "flags": flags,
        "counts": {"add": len(adds), "update": len(updates), "unchanged": len(unchanged),
                   "skipped": len(skipped), "skipped_by_reason": reasons,
                   "generic_inboxes": sum(1 for a in adds if a["generic"])},
    }


def apply(conn, p: dict, source: str) -> dict:
    """Write a plan from `plan` (call `plan` again inside the same transaction first)."""
    from . import outreach  # local import: outreach imports services that import this module's siblings

    now = time.time()
    added = redrafted = 0
    for a in p["adds"]:
        c = {k: v for k, v in a["contact"].items() if not k.startswith("_")}
        cid, reason = outreach.add_contact(conn, c, source=source)
        if not cid:
            continue
        added += 1
        if a["status"] != "new":
            # Already handled outside the app: never auto-drafted, and opt-outs are suppressed.
            outreach.set_status(conn, cid, a["status"], "from the founder's sheet")
    for u in p["updates"]:
        ch = u["changes"]
        if ch:
            ch = {**ch, "updated_at": now}
            if "type" in ch and ch["type"] in marketplace.TYPES:
                ch.update({"specialty_key": None, "specialty_source": ""})
            conn.execute(f"UPDATE contacts SET {', '.join(f'{k} = ?' for k in ch)} WHERE id = ?", (*ch.values(), u["contact_id"]))
            redrafted += _refresh_stale_draft(conn, u["contact_id"], now)
        if u["opt_out"]:
            outreach.set_status(conn, u["contact_id"], u["opt_out"], "marked in the founder's sheet")
    return {"added": added, "updated": len(p["updates"]), "redrafted": redrafted}


def _refresh_stale_draft(conn, contact_id: int, now: float) -> int:
    """A machine-written draft that predates the new details is discarded so the worker writes a fresh one.
    Drafts a person edited or wrote are kept (they're flagged in Review by the usual checks)."""
    stale = conn.execute(
        "SELECT id FROM messages WHERE contact_id = ? AND kind = 'initial' AND status IN ('draft', 'approved') "
        "AND drafted_by IN ('template', 'claude')", (contact_id,),
    ).fetchall()
    if not stale:
        return 0
    conn.execute(f"UPDATE messages SET status = 'discarded', updated_at = ? WHERE id IN ({', '.join('?' * len(stale))})",
                 (now, *[r[0] for r in stale]))
    conn.execute("UPDATE contacts SET status = 'new', draft_attempts = 0, last_error = NULL WHERE id = ? AND status = 'drafted'",
                 (contact_id,))
    return len(stale)
