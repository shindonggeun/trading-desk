"""Account and database chores. There is no sign-up route; the owner is made here.

    python -m desk.cli migrate
    python -m desk.cli create-user <username>        # asks for the password twice
    python -m desk.cli set-password <username>       # also signs every browser out
    python -m desk.cli revoke-sessions <username>

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

from .auth import MIN_PASSWORD, Auth
from .db import make_engine, migrate
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
    for name in ("create-user", "set-password", "revoke-sessions"):
        sub.add_parser(name).add_argument("username")
    args = parser.parse_args(argv)

    settings = load()
    migrate(settings.database_url)
    if args.command == "migrate":
        print("schema at head")
        return 0

    engine = make_engine(settings.database_url)
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
    except ValueError as exc:
        sys.exit(str(exc))
    except IntegrityError:
        sys.exit(f"user exists: {args.username} (use set-password)")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
