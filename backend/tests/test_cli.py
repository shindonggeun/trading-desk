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


def test_unfinished_lists_queued_and_running_runs_for_deploy(db_url, monkeypatch, capsys):
    """#431 scripts/deploy.sh reads the count on the first line before it restarts the backend."""
    from desk.db import migrate
    from desk.store import Store

    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    migrate(db_url)
    engine = make_engine(db_url)
    store = Store(engine)
    assert cli.main(["unfinished"]) == 0
    assert capsys.readouterr().out.splitlines() == ["0"]

    running = store.create_run("KORU", "2026-11-16", {})
    store.mark_running(running)
    store.create_run("NVDA", "2026-11-16", {})
    store.mark_done(store.create_run("AAPL", "2026-11-16", {}), {}, None)
    assert [r["ticker"] for r in store.unfinished()] == ["KORU", "NVDA"]
    assert cli.main(["unfinished"]) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == "2" and out[1].startswith("KORU\trunning\t") and out[1].endswith(running)
    assert out[2].startswith("NVDA\tqueued\t")
    engine.dispose()
