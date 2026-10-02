# Go-live checklist

Work through this before the first real send. Owners: **Founder**, **Deployer** (whoever runs the server), **Legal** (a qualified reviewer). Tick each box when done.

## 1. Server and access

- [ ] **Deployer:** app deployed with HTTPS, following [DEPLOYMENT.md](DEPLOYMENT.md). `docker compose ps` shows the app healthy.
- [ ] **Deployer:** `OUTREACH_SECRET_KEY` saved in the team password manager, separate from the backups.
- [ ] **Deployer:** a user account created for everyone who reviews drafts. No shared logins.
- [ ] **Deployer:** daily backups scheduled (cron), with copies going off the server.
- [ ] **Deployer:** one restore tested end to end (stop, copy backup over, remove `-wal`/`-shm`, start, log in, data looks right).
- [ ] **Deployer:** the server (or laptop) stays on and the app running from 10:00 to 18:00 IST, Monday to Saturday. The worker lives in the app.

## 2. Setup page

- [ ] **Founder:** Setup → Sender: name, role, phone and registered business address filled in. The pre-send check blocks sending without the address.
- [ ] **Founder:** Setup → Marketplace pitch: replace the placeholder wording with the real wording (Review shows a warning on every marketplace draft until you do):
  - [ ] value proposition for brands
  - [ ] value proposition for professionals
  - [ ] trial text (only used if `mention_trial` is turned on; see section 5)
  - [ ] signature name and role
- [ ] **Founder:** the doctor and hospital pitches read correctly for each audience.

## 3. Contacts

- [ ] **Founder:** upload the founder's workbook **unchanged** (Contacts → Upload spreadsheet).
- [ ] **Founder:** read the import summary before confirming: how many to add, to update, and skipped (by reason), plus any flags.
- [ ] **Founder:** the 315 contacts already in the database from the earlier CSV import are corrected by this re-import (contact types updated, stale template drafts replaced). Open a sample of 10 to 20 in **Review** and check type, specialty and draft.
- [ ] **Founder:** check how many contacts are generic inboxes (`info@`, `care@`, `contact@`). Expect fewer replies from these.
- [ ] **Founder:** download the workbook back with Status updated (Contacts → Update workbook) and open it, to confirm the round trip works.

## 4. Connections: test end to end with a test mailbox

- [ ] **Deployer:** a real Anthropic API key added. **Connections → Test** passes.
- [ ] **Deployer:** the Claude call works with the current model name and parameters. The model setting defaults to `claude-opus-5-5`. Drafting uses the beta messages endpoint with server-side fallback, `output_config` effort and a JSON schema. If Test fails, pick another model under Connections and test again.
- [ ] **Deployer:** SMTP: **Test login** and **Send test** pass. "Show original" in Gmail shows SPF, DKIM and DMARC PASS.
- [ ] **Deployer:** IMAP **Test** passes.
- [ ] **Deployer + Founder:** full loop with one test contact (your own second mailbox):
  - [ ] draft created and reads well
  - [ ] approved in Review
  - [ ] sent (check Activity → Outbox)
  - [ ] reply from the test mailbox detected, status changes, follow-up cancelled
  - [ ] bounce detected (send to a made-up address on a real domain)
- [ ] **Founder:** read the first 10 real Claude drafts closely before approving any.

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
