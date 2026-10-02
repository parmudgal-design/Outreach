#!/usr/bin/env bash
# Start Dr.Tracker Outreach: sets up .venv on first run, then serves the UI + API + background worker.
#   ./run.sh                      -> http://127.0.0.1:8000
#   HOST=0.0.0.0 PORT=8080 OUTREACH_SECRET_KEY=… ./run.sh   (exposed on a network: needs a user account
#                                                               and a secret key — see docs/DEPLOYMENT.md)
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/python ]; then
  echo "Creating virtualenv…"
  if ! python3 -m venv .venv 2>/dev/null; then
    # Debian/Ubuntu without python3-venv: create without pip, then bootstrap it.
    rm -rf .venv
    python3 -m venv --without-pip .venv
    curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python - -q
  fi
fi
.venv/bin/python -m pip install -q -r requirements.txt

export HOST="${HOST:-127.0.0.1}"
case "${HOST}" in
  127.0.0.1|localhost|::1) ;;
  *)
    # The app enforces this too (and also needs at least one user); failing here is just quicker.
    if [ -z "${OUTREACH_SECRET_KEY:-}" ]; then
      echo "Refusing to listen on ${HOST} without OUTREACH_SECRET_KEY (it encrypts the API key and mail passwords)." >&2
      echo "Generate one with: .venv/bin/python -m backend.app.cli gen-key" >&2
      exit 1
    fi ;;
esac

exec .venv/bin/python -m uvicorn backend.app.main:app --host "${HOST}" --port "${PORT:-8000}"
