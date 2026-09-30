"""Spreadsheet import: CSV / TSV / XLSX -> contact rows.

Two steps so the user can check the column mapping before anything is saved:
`parse` returns the headers, a guessed mapping and a preview; the router then
calls `to_contacts` with the (possibly corrected) mapping.
"""
import csv
import io
import re

FIELDS = ["name", "email", "type", "specialty", "clinic", "product", "city", "notes"]

SYNONYMS = {
    "name": ["name", "full name", "doctor", "doctor name", "contact", "contact name", "person"],
    "email": ["email", "e-mail", "email address", "mail", "email id"],
    "type": ["type", "contact type", "category", "segment"],
    "specialty": ["specialty", "speciality", "specialization", "specialisation", "department", "role", "designation", "title", "position"],
    "clinic": ["clinic", "hospital", "clinic / hospital", "hospital / clinic", "clinic/hospital", "hospital/clinic", "clinic name", "hospital name", "company name", "organisation", "organization", "company", "practice", "institution"],
    "product": ["product", "product name", "device", "brand", "offering"],
    "city": ["city", "location", "town", "district"],
    "notes": ["notes", "note", "comments", "remarks", "context", "personalisation", "personalization"],
}
FIRST_LAST = (["first name", "firstname", "first"], ["last name", "lastname", "surname", "last"])

TYPE_VALUES = {
    "doctor": ["doctor", "dr", "specialist", "physician", "clinic", "clinician", "surgeon"],
    "hospital": ["hospital", "institution", "institutional", "hospital pilot"],
    "company": ["company", "network", "insurer", "diagnostics", "chain", "corporate"],
    "partner": ["partner", "product", "product partner", "device", "vendor"],
}
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class ImportError_(Exception):
    pass


def _norm(h: str) -> str:
    return re.sub(r"[\s_]+", " ", (h or "").strip().lower())


def read_table(filename: str, data: bytes) -> tuple[list[str], list[list[str]]]:
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
        except ImportError as e:  # pragma: no cover
            raise ImportError_("XLSX support needs openpyxl installed.") from e
        try:
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception as e:
            raise ImportError_(f"Couldn't read that workbook: {e}") from e
        ws = wb.worksheets[0]
        table = [["" if v is None else str(v).strip() for v in row] for row in ws.iter_rows(values_only=True)]
    elif name.endswith(".xls"):
        raise ImportError_("Old .xls files aren't supported — save it as .xlsx or .csv first.")
    else:
        text = data.decode("utf-8-sig", errors="replace")
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel_tab if "\t" in text.splitlines()[0] else csv.excel
        table = [[c.strip() for c in row] for row in csv.reader(io.StringIO(text), dialect)]
    table = [r for r in table if any(c for c in r)]
    if not table:
        raise ImportError_("That file is empty.")
    headers, body = table[0], table[1:]
    width = len(headers)
    body = [(r + [""] * width)[:width] for r in body]
    return headers, body


def guess_mapping(headers: list[str], rows: list[list[str]]) -> dict:
    """field -> column index (or None). First/last-name columns map to name_first/name_last."""
    normed = [_norm(h) for h in headers]
    mapping: dict = {f: None for f in FIELDS}

    def looks_like_email(i):
        sample = [r[i] for r in rows[:50] if r[i]]
        return sample and sum(1 for v in sample if EMAIL_RE.match(v)) / len(sample) >= 0.6

    # Email first: by header, else the column whose values are mostly addresses.
    mapping["email"] = next((i for i, h in enumerate(normed) if h in SYNONYMS["email"]), None)
    if mapping["email"] is None:
        mapping["email"] = next((i for i in range(len(headers)) if looks_like_email(i)), None)
    used = {mapping["email"]} - {None}
    for field in FIELDS:
        if field == "email":
            continue
        for i, h in enumerate(normed):
            if i not in used and h in SYNONYMS[field] and not looks_like_email(i):
                mapping[field] = i
                used.add(i)
                break
    if mapping["name"] is None:
        first = next((i for i, h in enumerate(normed) if h in FIRST_LAST[0]), None)
        last = next((i for i, h in enumerate(normed) if h in FIRST_LAST[1]), None)
        if first is not None:
            mapping["name_first"], mapping["name_last"] = first, last
    return mapping


def normalise_type(value: str, default: str) -> str:
    v = _norm(value)
    if not v:
        return default
    for t, words in TYPE_VALUES.items():
        if v == t or v in words:
            return t
    for t, words in TYPE_VALUES.items():
        if any(w in v for w in words):
            return t
    return default


def to_contacts(headers: list[str], rows: list[list[str]], mapping: dict, default_type: str = "doctor") -> tuple[list[dict], list[dict]]:
    """Returns (contacts, problems). Problems are rows that couldn't become a contact."""
    def cell(row, field):
        i = mapping.get(field)
        return row[i].strip() if i is not None and i != "" and int(i) < len(row) else ""

    contacts, problems = [], []
    for n, row in enumerate(rows, start=2):  # row 1 is the header
        name = cell(row, "name")
        if not name and mapping.get("name_first") is not None:
            name = " ".join(x for x in (cell(row, "name_first"), cell(row, "name_last")) if x)
        email = cell(row, "email").strip().strip("<>").lower()
        if not email:
            problems.append({"row": n, "reason": "no email"})
            continue
        if not EMAIL_RE.match(email):
            problems.append({"row": n, "reason": f"invalid email {email!r}"})
            continue
        contacts.append({
            "name": name or email.split("@")[0],
            "email": email,
            "type": normalise_type(cell(row, "type"), default_type),
            "specialty": cell(row, "specialty"),
            "clinic": cell(row, "clinic"),
            "product": cell(row, "product"),
            "city": cell(row, "city"),
            "notes": cell(row, "notes"),
        })
    return contacts, problems
