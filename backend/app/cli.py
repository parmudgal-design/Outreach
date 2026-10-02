"""Command line tasks that don't need the server running.

    python -m backend.app.cli create-admin --email you@example.com --name "Your Name"
    python -m backend.app.cli add-user --email colleague@example.com --name "Name" [--admin]
    python -m backend.app.cli set-password --email you@example.com
    python -m backend.app.cli list-users
    python -m backend.app.cli gen-key

Passwords are prompted for (twice), or read from stdin with --password-stdin.
"""
import argparse
import getpass
import sys

from . import auth, db, secretbox


def _password(args) -> str:
    if args.password_stdin:
        return sys.stdin.readline().rstrip("\n")
    first = getpass.getpass("Password: ")
    if first != getpass.getpass("Again: "):
        raise auth.AuthError("The two passwords don't match.")
    return first


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m backend.app.cli", description="Dr.Tracker Outreach admin tasks")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("create-admin", "create an admin user (use this for the first account)"),
                        ("add-user", "create a user"), ("set-password", "reset a user's password")):
        p = sub.add_parser(name, help=help_)
        p.add_argument("--email", required=True)
        if name != "set-password":
            p.add_argument("--name", default="")
        if name == "add-user":
            p.add_argument("--admin", action="store_true")
        p.add_argument("--password-stdin", action="store_true", help="read the password from stdin instead of prompting")
    sub.add_parser("list-users", help="list users")
    sub.add_parser("gen-key", help="print a new OUTREACH_SECRET_KEY")
    args = parser.parse_args(argv)

    if args.cmd == "gen-key":
        print(secretbox.generate_key())
        print("Keep this in a password manager. Losing it means re-entering the API key and mail passwords.", file=sys.stderr)
        return 0

    db.init()
    try:
        with db.connect() as conn:
            db.set_actor("cli")
            if args.cmd in ("create-admin", "add-user"):
                role = "admin" if args.cmd == "create-admin" or args.admin else "member"
                u = auth.create_user(conn, args.email, args.name, _password(args), role)
                print(f"Created {u['role']} {u['email']}.")
            elif args.cmd == "set-password":
                row = conn.execute("SELECT id FROM users WHERE email = ?", (args.email.strip().lower(),)).fetchone()
                if not row:
                    raise auth.AuthError(f"No user {args.email}.")
                auth.set_password(conn, row[0], _password(args))
                db.log_event("user", f"Password reset for {args.email} from the command line", conn=conn)
                print(f"Password changed for {args.email}; their sessions were signed out.")
            elif args.cmd == "list-users":
                for r in conn.execute("SELECT email, name, role FROM users ORDER BY created_at"):
                    print(f"{r['email']}\t{r['role']}\t{r['name']}")
    except auth.AuthError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
