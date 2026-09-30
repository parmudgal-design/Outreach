#!/usr/bin/env bash
# Start Dr.Tracker Outreach: sets up .venv on first run, then serves the UI + API + background worker.
#   ./run.sh                      -> http://127.0.0.1:8000
#   HOST=0.0.0.0 PORT=8080 OUTREACH_PASSWORD=secret ./run.sh   (expose on a network — set a password!)
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

if [ -n "${HOST:-}" ] && [ "${HOST}" != "127.0.0.1" ] && [ -z "${OUTREACH_PASSWORD:-}" ]; then
  echo "Refusing to listen on ${HOST} without OUTREACH_PASSWORD set (the UI holds your API keys)." >&2
  exit 1
fi

exec .venv/bin/python -m uvicorn backend.app.main:app --host "${HOST:-127.0.0.1}" --port "${PORT:-8000}"
