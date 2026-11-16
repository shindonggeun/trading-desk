"""#409 GET /api/regime: the desk's own regime where semibot does not run, semibot's added when it does."""

from __future__ import annotations

import sqlite3
from datetime import timedelta

from fastapi.testclient import TestClient

from desk import regime, risk_regime, tips
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine
from desk.runner import make_fake_runner
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD, _client


def _live_client(db_url, **overrides) -> TestClient:
    """Like test_api._client but fake_runs=False, so the app builds the selling-score cache (risk)."""
    settings = Settings(database_url=db_url, fake_runs=False, fake_delay=0, **overrides)
    client = TestClient(create_app(settings, runner=make_fake_runner(0)), base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
    return client


RISK = {"US": {"market": "US", "as_of": "2026-11-13", "score": 34, "peak": 41, "phase": "calm", "label": "평온"},
        "KR": {"market": "KR", "as_of": "2026-11-16", "score": 72, "peak": 80, "phase": "selling", "label": "투매"}}
RATES = {"rates": "hiking", "yield10": "rising", "yield_spike": False, "dollar": "strong", "sideways": False,
         "values": {}, "as_of": "2026-11-13"}


def _fake_risk(monkeypatch, fail_kr=False):
    def current(self, ticker, trade_date=None):
        korean = risk_regime.is_korean(ticker)
        if korean and fail_kr:
            raise ConnectionError("no KR series")
        return RISK["KR" if korean else "US"]
    monkeypatch.setattr(risk_regime.Cache, "current", current)


def _semibot_db(path):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE regime_log (ts TEXT, market TEXT, label TEXT, score REAL, flags_json TEXT)")
    conn.execute("CREATE TABLE trigger_log (ts TEXT, symbol TEXT, kind TEXT, direction TEXT, level REAL)")
    conn.execute("INSERT INTO regime_log VALUES ('2026-11-16T10:00:00', 'US', 'RISK_ON', 61, '[]')")
    conn.commit()
    conn.close()


def test_without_semibot_the_desk_regime_is_given(db_url, monkeypatch):
    _fake_risk(monkeypatch)
    monkeypatch.setattr(tips.Cache, "regime", lambda self: RATES)
    with _live_client(db_url) as client:
        out = client.get("/api/regime").json()
    assert out["enabled"] is False and out["source"] == "desk"     # the old fields stay
    desk = out["desk"]
    assert [(m["market"], m["score"], m["label"]) for m in desk["markets"]] == [("us", 34, "평온"), ("kr", 72, "투매")]
    assert desk["rates"] == {"rates": "hiking", "yield10": "rising", "yield_spike": False, "dollar": "strong",
                             "sideways": False, "as_of": "2026-11-13"}
    assert desk["error"] is None


def test_with_semibot_both_are_given(db_url, monkeypatch, tmp_path):
    _fake_risk(monkeypatch)
    monkeypatch.setattr(tips.Cache, "regime", lambda self: RATES)
    db = tmp_path / "semibot.db"
    _semibot_db(db)
    with _live_client(db_url, semibot_db_path=str(db)) as client:
        out = client.get("/api/regime").json()
    assert out["source"] == "semibot" and out["regimes"][0]["label"] == "RISK_ON" and out["triggers"] == []
    assert len(out["desk"]["markets"]) == 2


def test_a_failing_part_leaves_the_rest(db_url, monkeypatch):
    _fake_risk(monkeypatch, fail_kr=True)
    monkeypatch.setattr(tips.Cache, "regime", lambda self: None)
    with _live_client(db_url) as client:
        desk = client.get("/api/regime").json()["desk"]
    assert [m["market"] for m in desk["markets"]] == ["us"]
    assert desk["rates"] is None
    assert "국장 위험 점수" in desk["error"] and "금리 · 달러 국면" in desk["error"]


def test_no_risk_score_in_fake_runs():
    # fake_runs builds no selling-score cache (risk is None): only the rate / dollar part can show
    out = regime.desk_view(None, lambda: RATES)
    assert out["markets"] == [] and out["rates"]["dollar"] == "strong" and "미장 위험 점수" in out["error"]


def test_the_regime_api_is_closed_without_a_session(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/regime").status_code == 401
