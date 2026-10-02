# Email deliverability

For the founder and whoever runs outreach day to day. This covers where to send from, when you need a separate sending domain, how to set one up, and how to warm it up.

## 1. Where dr-tracker.com stands today

Checked on 2 October 2026:

| Record | Value |
|---|---|
| MX | `smtp.google.com` (Google Workspace) |
| SPF | `v=spf1 include:_spf.google.com ~all` |
| DKIM | published at selector `google` |
| DMARC | `v=DMARC1; p=quarantine; adkim=r; aspf=r; rua=mailto:dmarc_rua@onsecureserver.net` |

This is a sound setup. Mail sent through Google Workspace from an `@dr-tracker.com` address passes SPF, DKIM and DMARC.

## 2. Sending up to about 20 a day: use a Workspace mailbox

At the app's default of 20 emails a day, send straight from Workspace.

1. Create a dedicated mailbox, for example `partners@dr-tracker.com`. Don't use the founder's personal mailbox; keep its reputation and inbox separate.
2. Turn on 2-Step Verification for that mailbox, then create an **App Password** (Google Account → Security → App passwords).
3. In the app, open **Connections**:
   - SMTP host `smtp.gmail.com`, port `465`, security SSL. Username = the full address, password = the App Password.
   - IMAP host `imap.gmail.com`, port `993`, same username and App Password. Turn IMAP on so replies, bounces and opt-outs are detected.
4. Use **Test login**, **Send test** (to your own address) and IMAP **Test**.
5. Open the test email in Gmail → "Show original". SPF, DKIM and DMARC should all say PASS.

## 3. Why more than 20 to 30 a day needs a separate sending domain

Above roughly 20 to 30 cold emails a day, move cold outreach off the main domain:

- **Protect the main domain.** Every staff mailbox, invoice and password reset uses `dr-tracker.com`. Cold mail draws more spam reports and bounces. If Gmail or Outlook start to distrust the domain, everyday staff mail goes to spam too. A separate subdomain or domain carries its own reputation.
- **Workspace is not built for cold mail.** Google's sending limits are per user per day, and bulk unsolicited sending from Workspace can get the account flagged or suspended. A transactional provider is made for volume.
- **Bounces and complaints.** A provider like Amazon SES tracks bounces and complaints per domain, suppresses bad addresses, and tells you when rates get too high. Workspace gives you none of this.

Options for the sending domain:

- A subdomain such as `outreach.dr-tracker.com`. Easiest. It shares some reputation with the parent, but is isolated in practice.
- A separate lookalike domain such as `drtracker-partners.com`. Fully isolated. Needs its own website or redirect so it looks legitimate.

## 4. Set up Amazon SES (recommended)

Use region **ap-south-1 (Mumbai)**. Example subdomain: `outreach.dr-tracker.com`.

1. **Verify the domain.** SES console → Configuration → Identities → Create identity → Domain → `outreach.dr-tracker.com`.
2. **Easy DKIM.** Choose Easy DKIM, RSA 2048. SES shows **3 CNAME records**. Add all three in DNS. Wait until the identity shows "Verified" (minutes to a few hours).
3. **Custom MAIL FROM.** On the identity, set a MAIL FROM domain such as `bounce.outreach.dr-tracker.com`. Add the two records SES shows:
   - MX: `bounce.outreach` → `10 feedback-smtp.ap-south-1.amazonses.com`
   - TXT (SPF): `bounce.outreach` → `v=spf1 include:amazonses.com ~all`

   This makes SPF align with your domain instead of amazonses.com.
4. **DMARC for the subdomain.** Add TXT `_dmarc.outreach`:
   - Start with `v=DMARC1; p=none; rua=mailto:dmarc_rua@onsecureserver.net` (or another address you read).
   - After 2 to 4 weeks of clean reports, change to `p=quarantine`.
5. **Leave the sandbox.** New SES accounts can only send to verified addresses. Account dashboard → Request production access. Describe the use honestly: B2B partner invitations to businesses and professionals, low volume (start 10 a day), one follow-up, opt-out in every email, bounce and complaint handling. Approval takes about a day.
6. **SMTP credentials.** SES → SMTP settings → Create SMTP credentials. Save the username and password. These are not your AWS login.
7. **Bounce and complaint notifications.** Optionally send them to an SNS topic or an email address you watch. SES also keeps an account-level suppression list.
8. **App settings.** In **Connections**:
   - SMTP host `email-smtp.ap-south-1.amazonaws.com`
   - Port `465` with SSL, or `587` with STARTTLS
   - Username and password = the SES SMTP credentials
   - From address = something on the verified domain, for example `partners@outreach.dr-tracker.com`
   - Reply-To = a monitored Workspace inbox (see section 6)
9. Run **Test login** and **Send test**, then check "Show original" in Gmail for SPF, DKIM and DMARC PASS.

## 5. Postmark (alternative)

Postmark setup is similar:

1. Add a sender signature or domain.
2. Add the DKIM **TXT** record it shows.
3. Add the Return-Path **CNAME** (for example `pm-bounces.outreach` → `pm.mtasv.net`).
4. Use its SMTP host and server token in Connections.

Postmark is strict about cold outreach and may refuse or close accounts that send unsolicited mail. Read their terms before you sign up. SES is the more likely fit.

## 6. Replies

Keep **Reply-To** pointing at a real Workspace inbox, for example `partners@dr-tracker.com`. Then:

- people's replies land in a mailbox someone reads,
- the app's IMAP check (on that same mailbox) still sees replies, bounces and "unsubscribe" messages, and stops follow-ups.

Bounces from SES go to the MAIL FROM domain, not the inbox. Also watch the SES bounce notifications (step 7 above) so dead addresses get removed.

## 7. Warm-up

A new sending domain has no reputation. Increase volume slowly. Change the pace under **Campaign** (`daily_cap` = emails per day, `min_delay_seconds` = minimum gap between sends).

| Week | Emails per day |
|---|---|
| 1 | 10 |
| 2 | 20 |
| 3 | 30 to 40 |
| 4 onward | +10 per week, only while the numbers below stay healthy |

Targets:

- **Bounce rate under 2%.** If higher, stop and clean the list.
- **Spam complaints under 0.1%** (1 in 1,000). If higher, pause and look at the pitch and the list.
- Watch replies and opt-outs. A sudden rise in opt-outs means the targeting or wording is off.

Tools:

- **Google Postmaster Tools** (postmaster.google.com): add and verify the sending domain. Shows domain reputation and spam rate for Gmail recipients once volume is high enough.
- **mail-tester.com**: send one test email to the address it gives you. Aim for 9/10 or better. Fix anything it flags.
- SES dashboard: bounce and complaint rates.

Check the list. Generic inboxes (`info@`, `care@`, `contact@`, `support@`) reply less and bounce or get filtered more. If they are a large share of the list, expect lower reply rates, and send to named people first.

## 8. Opt-outs and DPDP

The app already adds an unsubscribe line and a `List-Unsubscribe` header to every email, and honours opt-outs automatically. Keep it that way:

- Don't remove the footer or the business address.
- Never delete the opt-out list; it stops opted-out addresses being re-imported.
- Send only to business contacts with a reasonable reason to hear from Dr.Tracker, and say where you got their details if asked.

This is not legal advice. See `GO_LIVE_CHECKLIST.md` for the legal review items.
