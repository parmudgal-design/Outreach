# Dr.Tracker Outreach

An email outreach system for bringing specialists, clinic doctors, hospitals, health companies and product partners across India on board with Dr.Tracker. You add your API keys, upload a spreadsheet, review the first drafts, and it runs on its own. It drafts each email for the recipient and their specialty, sends at a safe pace inside Indian working hours, follows up once, and notices replies, bounces and opt-outs.

`dr-tracker-outreach-overview.pdf` describes the original browser-only version, which is kept in [`artifact/`](artifact/). This version builds out the roadmap from section 5 of that PDF: live sending, reply and bounce detection, background automation, and a path to a real sending domain.

## Run it

```bash
./run.sh                 # creates .venv on first run, then serves http://127.0.0.1:8000
PORT=8765 ./run.sh       # pick another port if 8000 is taken
```

Then open the page and follow the **Getting started** checklist on the Dashboard:

1. **Setup:** sender name, role, phone and business address (required for the compliance footer), plus a pitch for each audience. The pitches are optional; sensible defaults are used.
2. **Contacts → Upload spreadsheet:** CSV or `.xlsx`. Columns are matched automatically (you can fix the mapping), duplicates and opted-out emails are skipped, and each doctor's specialty is detected. Try `samples/sample-contacts.csv`, or download the template from the Contacts page.
3. **Connections:** paste an Anthropic API key for AI-written drafts, and an email account (for Gmail, an App Password) to send for real. Without keys everything still works: drafts come from built-in templates, and "sent" emails are only recorded in the outbox (dry run).
4. **Review:** read and edit the drafts, then approve. **Campaign → Start.**

Going live: work through [docs/GO_LIVE_CHECKLIST.md](docs/GO_LIVE_CHECKLIST.md). Hosting (Docker + Caddy for HTTPS, backups, restore) is in [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md), and sending-domain setup (SPF, DKIM, DMARC, warm-up) in [docs/DELIVERABILITY.md](docs/DELIVERABILITY.md).

## Security

- **Logins:** each person gets their own account (scrypt-hashed password, server-side session cookie). Create the first one under **Setup → Users** or with `python -m backend.app.cli create-admin --email you@… --name "…"`. Once any account exists, signing in is required. With no accounts the app is open only to requests from this computer. The Activity log records who approved and who sent each email.
- **Secrets at rest:** set `OUTREACH_SECRET_KEY` (generate with `python -m backend.app.cli gen-key`) and the API key and mail passwords are encrypted in the database. Existing plaintext values are encrypted at startup. Without the key they stay plaintext and the sidebar warns you. The app refuses to start if encrypted values exist and the key is missing or wrong.
- **Exposed hosts:** on any `HOST` other than localhost the app refuses to start without at least one user and without `OUTREACH_SECRET_KEY`.
- **Backups:** `python scripts/backup.py` uses SQLite's online backup API, keeps 14 daily copies, and is safe while the app runs. Schedule and restore steps: [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

## How it works

```
spreadsheet ─► contacts ─► specialty detection ─► draft (Claude or template)
                                                      │
                                         pre-send check (quality + Indian rules)
                                                      │
                                   Review: you approve (or auto-approve when clean)
                                                      │
          background worker ─► one email per step, paced, capped, 10:00–18:00 IST Mon–Sat
                                                      │
                                 SMTP (your mailbox or a transactional provider)
                                                      │
          IMAP poll ◄─ replies / bounces / "unsubscribe" ─► status updates, follow-ups stop,
                                                            opt-out list updated
          no reply after N days ─► one short follow-up in the same thread
```

| Piece | Where |
|---|---|
| API and UI server (FastAPI) | `backend/app/main.py`, `backend/app/api.py` |
| Background worker: drafting, sending, inbox, follow-ups | `backend/app/worker.py` |
| Pipeline rules: statuses, suppression, sending, inbound matching | `backend/app/services/outreach.py` |
| Specialty detection and per-specialty scenes and subjects | `backend/app/services/specialties.py` |
| Drafting prompt, including the Indian compliance rules | `backend/app/services/prompts.py` |
| Claude drafter and keyless template drafter | `backend/app/services/drafting.py` |
| Pre-send checks | `backend/app/services/qa.py` |
| SMTP sending and IMAP reply/bounce detection | `backend/app/services/mailer.py`, `inbox.py` |
| Spreadsheet import | `backend/app/services/importer.py` |
| Storage (SQLite, `data/outreach.db`, owner-only permissions) | `backend/app/db.py` |
| The founder's partner workbook: import, re-import, Status write-back | `backend/app/services/partner_sheet.py` |
| Logins, sessions, startup safety checks; admin CLI | `backend/app/auth.py`, `backend/app/cli.py` |
| Secret encryption (Fernet) | `backend/app/secretbox.py` |
| UI (plain HTML/CSS/JS, no build step) | `frontend/` |

## How the emails are written

The emails follow the demo emails in the PDF, and are invitations to come on board rather than sales pitches:

- **Subject:** a short story or a sharp question from the reader's own world, e.g. "Day ten, and her knee felt warm". Never the product name.
- **Opening:** an ordinary, hypothetical between-visit moment from their specialty. The system detects the specialty from the specialty column, degrees, clinic name, notes or email handle, and covers 18 specialties with their own scenes.
- **Middle:** names the gap in the doctor's own words. There is no product talk here.
- **Last paragraph:** the only place Dr.Tracker appears. It says what Dr.Tracker is, then asks for one low-effort next step.
- **P.S.:** an even lighter alternative, such as "reply 'video'".

### Marketplace partners: brands and professionals

Contact types `brand` and `professional` (`b`/`p` in the type column) get a different pitch: list on Dr.Tracker's marketplace and be found by people actively managing their health. There's no patient-monitoring talk.

- **The founder's workbook:** upload it exactly as it is. Every tab whose header row is `ID | Name | City | Type | Category | Tier | Phone | Email | Status | Notes / Source` is read (Legend and other tabs are skipped). The preview shows how many contacts will be added, updated, left as they are, and skipped by reason, before anything is saved. Skipped: no or invalid email, duplicate email (the first row wins), an unrecognised Status, and crisis or government helplines (the word list is under **Setup → Never import**). The sheet's Status is imported: Contacted, Replied, Interested and Not interested rows are never drafted, and Bounced and Unsubscribed rows go onto the opt-out list.
- **Re-importing an updated workbook** updates contacts in place, matched by email, then by partner ID with the tier ignored (the ID changes when a TBD tier is filled in). Only filled-in cells overwrite. Status and history aren't changed, except that a row newly marked Bounced or Unsubscribed adds an opt-out. An email change is held back (and flagged) if the old address was already emailed. Machine-written drafts for updated contacts are replaced; hand-edited ones are kept.
- **Status back to the sheet:** **Contacts → Update workbook** takes the current workbook and returns the same file with only the Status column changed (Not contacted, Contacted, Replied, Interested, Not interested, Bounced, Unsubscribed). **Status CSV** gives the same keyed by partner ID. In the app, "Interested" and "Not interested" are the adopted and declined statuses. The workbook is rewritten with openpyxl, which keeps values, formatting, merged cells and frozen panes but drops charts, images and some advanced features, so keep the original.
- **Other layouts:** the simple layout works (`Full Name, Email, Speciality, Hospital / Clinic, Product, City, Category, Remarks`). `Category` holds the type, `Speciality` the marketplace category (e.g. "Supplements", "Nutritionist / dietitian"), `Product` a one-line description, and `Remarks` may end with `Partner ID B-SUP-T2-MUM-01`. A founder-style sheet also works: title rows above the header, and a single `Notes / Source` cell holding the site, description, source and a `CHECK:` note.
- **Categories** (`services/marketplace.py`): brands FD wellness food, SUP supplements, PRO protein, DV devices; professionals NT nutritionist, GP general physician, SPC specialist/clinic, MH mental health, PT physio/trainer. Each category has its own opening, tone and ask.
- **Greetings** (`services/greeting.py`): a named person is greeted by name ("Dear Dr. Hooda", "Dear Sridevi"). Otherwise the team is greeted ("Hello NOTO team", "Hello Kenia Eye Hospital team").
- **Wording:** edit it under **Setup → Marketplace pitch**. That covers the value propositions, the trial switch and trial text, and the signature.
- **Checks:** these block sending: placeholders, broken greetings, any price, fee or trial wording while the trial switch is off, and commission, referral or per-booking language to a GP or specialist/clinic. These are warnings: the old monitoring pitch, more than 200 words, and a `CHECK:` note from the sheet.

Hospitals, companies and product partners each get their own approach. A partner pitch is built around the partner's specific product (the **Product** column).

## Indian rules built in

The drafting prompt states these rules, and the pre-send check enforces them again. Errors block sending; warnings are shown for review.

- **ASCI Code:** truthful, substantiated claims. No unsupported "best / No. 1 / India's first", and no fear-based scenes.
- **Consumer Protection Act 2019, CCPA misleading-ads (2022) and dark-pattern (2023) guidelines:** no false urgency or scarcity, and honest subjects (a fake "Re:" on a first email is blocked).
- **Drugs & Magic Remedies (Objectionable Advertisements) Act 1954:** no cure, prevention or guaranteed-outcome claims.
- **NMC Professional Conduct Regulations 2023 and UCPMP 2024:** no gifts, fees or incentives to doctors, and no asking doctors to endorse or prescribe anything, including partners' products.
- **DPDP Act 2023:** hypothetical scenes only; clear identity, address and a one-reply opt-out in every footer. Opt-outs are honoured automatically, survive deletion, and block re-import.
- **IT Act 2000:** honest sender identity and headers. A `List-Unsubscribe` header is included.

These are safeguards, not legal advice. Have someone qualified review your pitch text before large sends.

## Tests

```bash
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pytest backend/tests -q
```

The tests run the whole pipeline (import → draft → approve → send over real SMTP to a local test server → reply, bounce and unsubscribe handling → threaded follow-up), plus the daily cap, the IST window, specialty detection and the guardrail checks.

## Not verified yet

- **Real Claude drafts:** the Claude API call is written against the current SDK, but it hasn't run with a real key. Use **Connections → Test** once you add one, then read the first few drafts closely.
- **Real Gmail sending and IMAP:** tested only against a local SMTP server and synthetic inbound messages. Use **Send test** and **Test** on the Connections page with your account first.
- **Deliverability at scale:** a Workspace mailbox on dr-tracker.com (SPF, DKIM and DMARC are already published) is fine for 20–30 emails a day. For more, see [docs/DELIVERABILITY.md](docs/DELIVERABILITY.md).
- **Docker image:** the Dockerfile, compose file and Caddyfile are checked (`docker compose config`, `caddy adapt`) but the image hasn't been built and run here. Try it on the server first.
