# Deploying Dr.Tracker Outreach

This guide is for whoever puts the app on a server. It assumes you can use SSH, a terminal and a DNS control panel. You don't need to know the code.

The app is one process. It serves the web UI and the API, and it runs the background worker that drafts, sends and checks the inbox. All data is in one SQLite file. Run exactly one copy of the app against that file. Never run two replicas or a second container on the same database.

## 1. Choose a host

- A small Linux VPS is plenty: 1 vCPU, 1 to 2 GB RAM, 20 GB disk.
- Pick a region in India, for example Mumbai (AWS ap-south-1 or Lightsail Mumbai) or Bangalore (DigitalOcean BLR1).
- Ubuntu 24.04 LTS or Debian 12 is a good default.
- Install Docker Engine and the Compose plugin (`docker compose version` should work).
- Open inbound ports 80 and 443 (TCP, plus UDP 443 for HTTP/3) in the firewall or security group. Keep SSH (22) restricted to your IP if you can. Do not open port 8000.

The server must be on during the send window. Sending happens only from 10:00 to 18:00 IST, Monday to Saturday, and only while the app is running.

## 2. Point a domain at the server

1. Choose a hostname, for example `outreach.dr-tracker.com`.
2. In the DNS panel for `dr-tracker.com`, add an **A record**: name `outreach`, value = the server's public IPv4 address. Add an AAAA record too if the server has IPv6.
3. Wait until `dig +short outreach.dr-tracker.com` returns the server's IP.

Caddy needs this before it can get a certificate.

## 3. Get the code and create `.env`

```bash
git clone <repo-url> dr-tracker-outreach
cd dr-tracker-outreach
cp deploy/env.example .env
chmod 600 .env
```

Never commit `.env`. It holds the key that decrypts the stored API key and mail passwords.

## 4. Generate the secret key

```bash
docker compose build
docker compose run --rm --no-deps app python -m backend.app.cli gen-key
```

Copy the printed key into `.env` as `OUTREACH_SECRET_KEY=...`. Also set `DOMAIN` and `ACME_EMAIL`.

Save the key in the team password manager now. If it is lost:

- The Anthropic API key and the SMTP/IMAP passwords can't be decrypted. You re-enter them under Connections.
- Everything else (contacts, drafts, history, users) is kept.
- Any backup restored later needs the same key.

The app refuses to start if encrypted values exist and the key is missing or wrong.

## 5. Create the first admin, then start

Create the admin **before** the first `up`. On a public host the app refuses to start when there are no users.

```bash
docker compose run --rm --no-deps app python -m backend.app.cli create-admin --email you@dr-tracker.com --name "Your Name"
# prompts for the password twice
docker compose up -d
docker compose ps        # app should show "healthy", caddy "running"
```

Open `https://<DOMAIN>` and log in.

## 6. HTTPS

Caddy (see `deploy/Caddyfile`) gets and renews a Let's Encrypt certificate on its own. It needs:

- the DNS record from step 2,
- ports 80 and 443 open from the internet,
- `DOMAIN` set in `.env`.

If `ACME_EMAIL` is empty, delete the `email` line in `deploy/Caddyfile`, or Caddy won't start.

The app runs with `--proxy-headers --forwarded-allow-ips="*"` so it sees the real client and scheme behind Caddy. Compose sets `OUTREACH_COOKIE_SECURE=1`, so the session cookie is only sent over HTTPS. The app port is not published; only Caddy reaches it.

If the certificate fails, check `docker compose logs caddy`. The usual causes are DNS not pointing at the server yet, or port 80 blocked.

## 7. Add more users

Give everyone who reviews drafts their own login. Do not share one account. The Activity log records who approved and who sent each email.

The easiest way is in the app: an admin opens **Setup → Users** and adds the person (Member, or Admin if they should manage users too). Everyone can change their own password there.

From the command line:

```bash
docker compose exec app python -m backend.app.cli add-user --email colleague@dr-tracker.com --name "Colleague Name"          # member
docker compose exec app python -m backend.app.cli add-user --email colleague@dr-tracker.com --name "Colleague Name" --admin  # admin
docker compose exec app python -m backend.app.cli list-users
```

Reset a forgotten password (this also signs them out everywhere):

```bash
docker compose exec app python -m backend.app.cli set-password --email colleague@dr-tracker.com
```

Five wrong passwords in a row lock that email out of signing in for 15 minutes.

## 8. Update to a new version

```bash
cd dr-tracker-outreach
docker compose exec -T app python scripts/backup.py     # backup first
git pull
docker compose build
docker compose up -d
docker compose ps
```

## 9. Environment variables

| Variable | Default | What it does |
|---|---|---|
| `OUTREACH_SECRET_KEY` | none | Fernet key that encrypts the Anthropic API key and SMTP/IMAP passwords in the database. Required on a non-localhost host. Generate with `python -m backend.app.cli gen-key`. |
| `DOMAIN` | none | Hostname Caddy serves and gets a certificate for. Used by Caddy only. |
| `ACME_EMAIL` | none | Email for Let's Encrypt notices. Used by Caddy only. |
| `OUTREACH_DATA_DIR` | `./data` (`/data` in Docker) | Folder for the database and backups. |
| `OUTREACH_DB` | `$OUTREACH_DATA_DIR/outreach.db` | Path to the SQLite database. |
| `HOST` | `127.0.0.1` (`0.0.0.0` in Docker) | Address the app listens on. |
| `PORT` | `8000` | Port the app listens on. |
| `OUTREACH_COOKIE_SECURE` | off (`1` in compose) | Set to `1` behind HTTPS so the session cookie is marked Secure. |
| `OUTREACH_PUBLIC_URL` | none | The address people open the app at, e.g. `https://outreach.dr-tracker.com`. Used for the link in reply alert emails. |
| `OUTREACH_TICK_SECONDS` | `15` | Seconds between worker ticks. |
| `OUTREACH_WORKER` | `1` | Set to `0` to run without the background worker (nothing gets sent). |

`OUTREACH_PASSWORD` no longer exists. Per-user logins replaced it.

API keys, mail accounts, sender details and pacing (daily cap, delay between sends) are not environment variables. They are set in the UI and stored in the database.

## 10. Logs and health

```bash
docker compose logs -f app       # app and worker
docker compose logs -f caddy     # HTTPS and access
docker compose ps                # health status
```

`GET /healthz` returns `{"ok": true}` when the app can reach its database. It needs no login. Docker uses it for the health check.

## 11. Backups

`scripts/backup.py` uses SQLite's online backup API, so it is safe while the app runs. It writes `/data/backups/outreach-YYYY-MM-DD.db` (one per day), checks the copy, and keeps the newest 14.

Run it daily from the host's crontab (`crontab -e`):

```cron
# 02:30 every day (server time)
30 2 * * * cd /home/deploy/dr-tracker-outreach && docker compose exec -T app python scripts/backup.py >> /var/log/outreach-backup.log 2>&1
```

The cron user must be able to run `docker` (root, or a member of the `docker` group). Options: `--keep 30` keeps more days, `--dest /path` writes elsewhere.

The backups live in the same Docker volume, on the same disk, as the database. A disk failure or a deleted server loses both. Copy them off the server every day too, for example:

```bash
# copy the volume's backups to the host, then to object storage
docker compose cp app:/data/backups ./backups-export
aws s3 sync ./backups-export s3://<bucket>/outreach-backups/   # or rclone, restic, etc.
```

Keep the off-server copies private. They contain contact data and encrypted secrets. Store `OUTREACH_SECRET_KEY` separately from the backups (in the password manager), not in the same bucket.

## 12. Restore from a backup

1. Stop the app: `docker compose stop app`.
2. Copy the backup over the live database and remove the WAL side files:

   ```bash
   docker compose run --rm --no-deps -v "$PWD/outreach-2026-10-01.db:/restore.db:ro" app \
     sh -c 'cp /restore.db /data/outreach.db && rm -f /data/outreach.db-wal /data/outreach.db-shm && chmod 600 /data/outreach.db'
   ```

   (If the backup is already in `/data/backups`, use `cp /data/backups/outreach-2026-10-01.db /data/outreach.db` inside the same `sh -c`.)
3. Make sure `.env` has the same `OUTREACH_SECRET_KEY` as when the backup was taken.
4. Start: `docker compose start app`, then check `docker compose ps` and log in.

Test a restore once before you rely on backups. You can do it on a spare machine.

## 13. Running without Docker

For use on one laptop:

```bash
./run.sh            # http://127.0.0.1:8000
```

- With zero users, access is open on localhost only. That's fine for one person on their own machine.
- Once any user exists, login is required.
- To listen on another address (`HOST=0.0.0.0`), you need at least one user and `OUTREACH_SECRET_KEY` set. The app refuses to start otherwise. Prefer the Docker setup with HTTPS for anything beyond localhost.
- The CLI works the same way: `.venv/bin/python -m backend.app.cli create-admin --email ... --name "..."`.
- Back up with `.venv/bin/python scripts/backup.py` (writes to `data/backups`).

The worker lives inside the app. If the laptop is asleep or the app is closed between 10:00 and 18:00 IST, nothing is sent and replies aren't checked. Sending resumes when the app runs again.
