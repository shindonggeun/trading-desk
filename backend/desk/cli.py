"""Account and database chores. There is no sign-up route; the owner is made here.

    python -m desk.cli migrate
    python -m desk.cli create-user <username>        # asks for the password twice
    python -m desk.cli set-password <username>       # also signs every browser out
    python -m desk.cli revoke-sessions <username>
    python -m desk.cli disable-otp <username>        # phone lost: turns OTP off, signs everyone out
    python -m desk.cli otp-key                       # prints a new DESK_OTP_KEY value

In Docker: ``docker compose exec backend python -m desk.cli create-user <name>``.
For scripts, DESK_NEW_PASSWORD supplies the password instead of the prompt.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import timedelta

from sqlalchemy.exc import IntegrityError

from . import otp
from .auth import MIN_PASSWORD, Auth
from .db import make_engine, migrate, now
from .settings import load


def _password() -> str:
    given = os.getenv("DESK_NEW_PASSWORD")
    if given:
        return given
    first = getpass.getpass(f"password (min {MIN_PASSWORD} chars): ")
    if first != getpass.getpass("again: "):
        sys.exit("passwords differ")
    return first


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m desk.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="bring the database schema to head")
    sub.add_parser("otp-key", help="print a new DESK_OTP_KEY (Fernet key) for the server env")
    for name in ("create-user", "set-password", "revoke-sessions", "disable-otp"):
        sub.add_parser(name).add_argument("username")
    rescore = sub.add_parser("rescore", help="forget plan outcomes so the next pass scores them again")
    rescore.add_argument("--days", type=int, default=None, help="only runs created in the last N days")
    args = parser.parse_args(argv)
    if args.command == "otp-key":
        print(otp.new_key())
        return 0

    settings = load()
    migrate(settings.database_url)
    if args.command == "migrate":
        print("schema at head")
        return 0

    engine = make_engine(settings.database_url)
    if args.command == "rescore":
        from .store import Store

        since = now() - timedelta(days=args.days) if args.days else None
        print(f"cleared {Store(engine).clear_outcomes(since)} outcome(s); the server rescores them next pass")
        return 0
    auth = Auth(engine, timedelta(hours=settings.session_max_hours), timedelta(hours=settings.session_idle_hours))
    try:
        if args.command == "create-user":
            auth.create_user(args.username, _password())
            print(f"created {args.username}")
        elif args.command == "set-password":
            if not auth.set_password(args.username, _password()):
                sys.exit(f"no such user: {args.username}")
            print(f"password changed; every session of {args.username} signed out")
        elif args.command == "revoke-sessions":
            print(f"signed out {auth.revoke_all(args.username)} session(s)")
        elif args.command == "disable-otp":
            if not auth.otp_disable_by_name(args.username):
                sys.exit(f"no such user: {args.username}")
            print(f"OTP off for {args.username}; recovery codes deleted, every session signed out")
    except ValueError as exc:
        sys.exit(str(exc))
    except IntegrityError:
        sys.exit(f"user exists: {args.username} (use set-password)")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
