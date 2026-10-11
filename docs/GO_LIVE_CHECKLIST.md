# Go-live checklist

Work through this before the first real send. Owners: **Founder**, **Deployer** (whoever runs the server), **Legal** (a qualified reviewer). Tick each box when done.

## 1. Server and access

- [ ] **Deployer:** app deployed with HTTPS, following [DEPLOYMENT.md](DEPLOYMENT.md). `docker compose ps` shows the app healthy.
- [ ] **Deployer:** `OUTREACH_SECRET_KEY` saved in the team password manager, separate from the backups.
- [ ] **Deployer:** `OUTREACH_PUBLIC_URL` set in `.env` (e.g. `https://outreach.dr-tracker.com`) so reply alerts link to the app.
- [ ] **Deployer:** a user account created for everyone who reviews drafts. No shared logins.
- [ ] **Deployer:** daily backups scheduled (cron), with copies going off the server.
- [ ] **Deployer:** one restore tested end to end (stop, copy backup over, remove `-wal`/`-shm`, start, log in, data looks right).
- [ ] **Deployer:** the server (or laptop) stays on and the app running from 10:00 to 18:00 IST, Monday to Saturday. The worker lives in the app.

## 2. Setup page

- [ ] **Founder:** Setup → Sender: name, role, phone and registered business address filled in. The pre-send check blocks sending without the address.
- [ ] **Founder:** Setup → Marketplace pitch: read the built-in wording and confirm it's how you want Dr.Tracker described (edit it there if not; no code change needed):
  - [ ] value proposition for brands
  - [ ] value proposition for professionals
  - [ ] trial text (only used if `mention_trial` is turned on; the built-in text promises a free first month, so check it's a real offer first; see section 5)
  - [ ] signature name and role (blank uses the sender's)
- [ ] **Founder:** the doctor and hospital pitches read correctly for each audience.

## 3. Contacts

- [ ] **Founder:** prepare the CSV with these columns: `Full Name, Email, Speciality, Hospital / Clinic, Product, City, Category, Remarks`, plus `Website` where known. `Category` is `brand` or `professional`, `Speciality` the marketplace category (e.g. "Supplements"), `Product` one line on what they sell or do. `backend/tests/fixtures/marketplace_contacts.csv` is a worked example; Contacts → Template gives the same layout.
- [ ] **Founder:** Contacts → Upload spreadsheet. Check the column mapping in the preview, then import. Read the summary: added, duplicates, opted-out and invalid rows.
- [ ] **Founder:** open 10 to 20 contacts and check type, category and description. Fix any by hand.
- [ ] **Founder:** check how many contacts are generic inboxes (`info@`, `care@`, `contact@`). Expect fewer replies from these.
- [ ] **Founder:** with auto-draft on, drafts appear in **Review** within a minute or two. Until the API key is added they come from templates; once it's added, use **Rewrite** (or discard them) so Claude writes them fresh with the website research.
- [ ] *(Only if the founder's multi-tab workbook is used instead of a CSV)* upload it unchanged; Contacts → Update workbook writes Status back to it.

## 4. Connections: test end to end with a test mailbox

- [ ] **Deployer:** a real Anthropic API key added. **Connections → Test** passes.
- [ ] **Deployer:** the Claude call works with the current model name and parameters. The model setting defaults to `claude-opus-5-5`. Drafting uses the beta messages endpoint with server-side fallback, `output_config` effort and a JSON schema. If Test fails, pick another model under Connections and test again.
- [ ] **Deployer:** SMTP: **Test login** and **Send test** pass. "Show original" in Gmail shows SPF, DKIM and DMARC PASS.
- [ ] **Deployer:** IMAP **Test** passes.
- [ ] **Founder:** Connections → **Email me when someone replies** set to a person's own inbox (not the sending mailbox).
- [ ] **Founder:** open 5 brand contacts with websites and press **Read website now**. The excerpt should describe what they sell. Sites that block it are fine; those emails use the CSV alone.
- [ ] **Deployer + Founder:** full loop with one test contact (your own second mailbox):
  - [ ] draft created and reads well
  - [ ] approved in Review
  - [ ] sent (check Activity → Outbox)
  - [ ] reply from the test mailbox detected, status changes, follow-up cancelled
  - [ ] a suggested response appears in Review next to the reply, with the right intent (try one "interested" reply and one asking about price)
  - [ ] the alert email arrives, and the approved response lands in the same thread in the test mailbox
  - [ ] bounce detected (send to a made-up address on a real domain)
- [ ] **Founder:** read the first 10 real Claude drafts closely before approving any, and the first few suggested replies.

## 5. Wording and legal

- [ ] **Founder:** supplies the real marketplace wording (section 2). No placeholder text goes out.
- [ ] **Legal:** qualified review of the pitch text:
  - [ ] brand emails against the ASCI Code (truthful, substantiated claims; no "best", "No. 1", "India's first" without proof)
  - [ ] emails to doctors against the NMC Professional Conduct Regulations 2023 (no gifts, fees or incentives; no asking doctors to endorse or prescribe)
- [ ] **Legal:** sign-off before turning on `mention_trial` or adding any commission or fee language. Never use commission language with GPs or specialists.
- [ ] **Legal:** DPDP Act 2023 check on stored contact data:
  - [ ] what's stored: names, emails, phones, notes and sources from the founder's sheet
  - [ ] who can access it (user accounts, server admins, backup locations)
  - [ ] how long it's kept, and when it's deleted
  - [ ] how a deletion request is handled
  - [ ] the opt-out list is kept permanently and never deleted

## 6. Deliverability

- [ ] **Founder + Deployer:** follow [DELIVERABILITY.md](DELIVERABILITY.md). Up to about 20 a day from a dedicated Workspace mailbox; a separate sending domain (SES) before going higher.
- [ ] **Founder:** Reply-To is a monitored Workspace inbox.
- [ ] **Founder:** mail-tester.com score 9/10 or better.
- [ ] **Deployer:** Google Postmaster Tools set up for the sending domain.

## 7. First sends

- [ ] **Founder:** run in **dry-run** mode first. Check Activity → Outbox: right people, right drafts, right pacing.
- [ ] **Founder:** switch to SMTP mode.
- [ ] **Founder:** Campaign → daily cap set to **10** for the first week.
- [ ] **Founder:** watch bounces (under 2%), complaints (under 0.1%), replies and opt-outs daily for the first two weeks. Raise the cap only per the warm-up table in DELIVERABILITY.md.
