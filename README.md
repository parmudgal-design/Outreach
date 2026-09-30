# Dr.Tracker Outreach

A single-page tool for emailing specialists, clinic doctors, hospitals and health companies across India: build a contact list, draft a personalised email for each contact with Claude, and track replies. See `dr-tracker-outreach-overview.pdf` for the full system overview.

- `index.html` is the whole app (HTML, CSS and JS). It is published as a private Claude Artifact: https://claude.ai/artifact/Ja2qZazSZTvXUfJUkpebpv
- Data lives in the viewer's browser (`localStorage`, key `drtracker_outreach_v1`).
- Capabilities: `sample` (AI drafting) and `downloads` (backup export).

## Features

| Area | What it does |
|---|---|
| Setup | Sender identity, tone and language, a separate pitch for each contact type, the compliance address, and backup export/import |
| Contacts | Doctor, Hospital, Company and Product partner types. Add contacts one at a time or paste them from a spreadsheet. Duplicates and opted-out emails are blocked. |
| Compose | One-click AI draft in the story-led style from the PDF: hook subject, opening scene, the gap, the answer, a low-effort ask, and a P.S. A **pre-send check** flags placeholders, figures not in Setup, pressure language, a missing name and over-long copy. |
| Compliance | A DPDP-oriented footer (sender identity, address, opt-out) is added at send time. Marking a contact **Unsubscribed** puts their email on an opt-out list that survives deletion and blocks re-import. |
| Tracker | Funnel view (not started → drafted → sent → replied → adopted, plus opted out) and a status table. |
| Auto-send | IST 10am–6pm Mon–Sat window, batch cap and delay. **Draft queued contacts** drafts the whole queue for review. |

## Not done yet

- **Live Gmail send.** "Run batch" stays disabled. Wiring it needs the Gmail connector authorised in claude.ai, plus one real, observed send so the exact tool call shape is known and not guessed.
- Reply and bounce detection, a proper sending domain (SPF/DKIM/DMARC), background sending and multi-user use. See section 5 of the PDF.

## Testing locally

Open `index.html` in a browser. AI drafting and the Gmail connection only work inside claude.ai. Everything else (contacts, the pre-send check, the opt-out list, the tracker and backup) works from a local file.
