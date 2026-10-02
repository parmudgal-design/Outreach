#!/usr/bin/env python3
"""Back up the Dr.Tracker Outreach SQLite database. Standard library only.

Uses SQLite's online backup API, so it is safe to run while the app is running.
Writes <dest>/outreach-YYYY-MM-DD.db (one file per day; a second run on the same
day replaces that day's file atomically), checks the copy with
PRAGMA integrity_check, makes it owner-only (chmod 600), then deletes the oldest
outreach-*.db files beyond --keep.

    python scripts/backup.py                      # defaults from OUTREACH_DB / OUTREACH_DATA_DIR
    python scripts/backup.py --dest /srv/backups --keep 30

Note: the copy still holds encrypted secrets. Restoring it needs the same
OUTREACH_SECRET_KEY the app used when the backup was taken.
"""
import argparse
import datetime as dt
import os
import re
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
NAME_RE = re.compile(r"^outreach-(\d{4}-\d{2}-\d{2})\.db$")


def default_data_dir() -> Path:
    env = os.environ.get("OUTREACH_DATA_DIR")
    return Path(env) if env else REPO_ROOT / "data"


def default_db() -> Path:
    env = os.environ.get("OUTREACH_DB")
    return Path(env) if env else default_data_dir() / "outreach.db"


def backup(db: Path, dest: Path, keep: int, today: dt.date) -> Path:
    if not db.is_file():
        raise RuntimeError(f"database not found: {db}")
    dest.mkdir(parents=True, exist_ok=True)
    os.chmod(dest, 0o700)

    final = dest / f"outreach-{today.isoformat()}.db"
    tmp = dest / f".{final.name}.tmp-{os.getpid()}"
    try:
        tmp.unlink(missing_ok=True)
        # Open read-only so a typo in --db never creates an empty database.
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
        try:
            dst = sqlite3.connect(tmp)
            try:
                src.backup(dst)
                # Make the copy one self-contained file (no -wal/-shm side files).
                dst.execute("PRAGMA journal_mode=DELETE")
                result = dst.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise RuntimeError(f"integrity_check on the copy failed: {result}")
                dst.commit()
            finally:
                dst.close()
        finally:
            src.close()
        os.chmod(tmp, 0o600)
        os.replace(tmp, final)
    finally:
        tmp.unlink(missing_ok=True)

    print(f"backed up {db} -> {final} ({final.stat().st_size} bytes, integrity ok)")
    rotate(dest, keep)
    return final


def rotate(dest: Path, keep: int) -> None:
    dated = sorted(p for p in dest.iterdir() if p.is_file() and NAME_RE.match(p.name))
    old = dated[:-keep] if keep > 0 else []
    for p in old:
        p.unlink()
        print(f"removed old backup {p}")
    print(f"{len(dated) - len(old)} backup(s) kept in {dest} (keep={keep})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", type=Path, default=None,
                    help="database to back up (default: $OUTREACH_DB, else $OUTREACH_DATA_DIR/outreach.db, else ./data/outreach.db)")
    ap.add_argument("--dest", type=Path, default=None,
                    help="backup directory (default: <data dir>/backups)")
    ap.add_argument("--keep", type=int, default=14, help="number of daily backups to keep (default 14)")
    args = ap.parse_args(argv)

    if args.keep < 1:
        ap.error("--keep must be at least 1")
    db = args.db or default_db()
    if args.dest:
        dest = args.dest
    elif args.db:
        dest = db.parent / "backups"
    else:
        dest = default_data_dir() / "backups"
    try:
        backup(db, dest, args.keep, dt.date.today())
    except Exception as exc:  # noqa: BLE001 - report any failure as a non-zero exit
        print(f"backup FAILED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
