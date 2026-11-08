from datetime import timedelta

import pytest

from desk import cli
from desk.auth import Auth
from desk.db import make_engine


def test_create_user_then_set_password(db_url, monkeypatch):
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    monkeypatch.setenv("DESK_NEW_PASSWORD", "first long passphrase")
    assert cli.main(["create-user", "owner"]) == 0
    with pytest.raises(SystemExit, match="user exists"):
        cli.main(["create-user", "owner"])

    monkeypatch.setenv("DESK_NEW_PASSWORD", "second long passphrase")
    assert cli.main(["set-password", "owner"]) == 0
    with pytest.raises(SystemExit, match="no such user"):
        cli.main(["set-password", "ghost"])

    monkeypatch.setenv("DESK_NEW_PASSWORD", "short")
    with pytest.raises(SystemExit, match="at least"):
        cli.main(["set-password", "owner"])

    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    assert auth.authenticate("owner", "second long passphrase") is not None
    assert auth.authenticate("owner", "first long passphrase") is None
    engine.dispose()


def test_reports_print_on_a_cp949_console(monkeypatch):
    """A Windows console / redirect is cp949: '—' and Korean must not stop a report (#166)."""
    import io
    import sys

    raw = io.BytesIO()
    out = io.TextIOWrapper(raw, encoding="cp949")
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp949"))
    cli._utf8_out()
    print("note: 시장 투매 지표 — 일부 없음")
    sys.stdout.flush()
    assert raw.getvalue().decode("utf-8").rstrip() == "note: 시장 투매 지표 — 일부 없음"
