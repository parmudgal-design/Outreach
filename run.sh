#!/usr/bin/env bash
# Start Dr.Tracker Outreach: sets up .venv on first run, then serves the UI + API + background worker.
#   ./run.sh                      -> http://127.0.0.1:8000
#   HOST=0.0.0.0 PORT=8080 OUTREACH_SECRET_KEY=… ./run.sh   (exposed on a network: needs a user account
#                                                               and a secret key — see docs/DEPLOYMENT.md)
# Settings can also live in .env (cp deploy/env.example .env); the app reads it itself, and
# variables set in the shell win over it.
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

# The few values this script needs itself, read from .env without running it as shell code.
env_value() { [ -f .env ] || return 0; sed -n "s/^[[:space:]]*\(export[[:space:]]\+\)\?$1[[:space:]]*=[[:space:]]*//p" .env | tail -n 1 | sed "s/^[\"']//; s/[\"']\$//"; }

export HOST="${HOST:-$(env_value HOST)}"
export HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-$(env_value PORT)}"
case "${HOST}" in
  127.0.0.1|localhost|::1) ;;
  *)
    # The app enforces this too (and also needs at least one user); failing here is just quicker.
    if [ -z "${OUTREACH_SECRET_KEY:-$(env_value OUTREACH_SECRET_KEY)}" ]; then
      echo "Refusing to listen on ${HOST} without OUTREACH_SECRET_KEY (it encrypts the API key and mail passwords)." >&2
      echo "Generate one with: .venv/bin/python -m backend.app.cli gen-key" >&2
      exit 1
    fi ;;
esac

exec .venv/bin/python -m uvicorn backend.app.main:app --host "${HOST}" --port "${PORT:-8000}"
