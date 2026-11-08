import json

import pytest

from desk import backtest_reports
from desk.broker.mock import MockBroker
from desk.prices import PriceSource
from tests.test_api import OWNER, PASSWORD
from tests.test_prices import _client


def _report(horizon: str, plans: int = 120) -> dict:
    return {"version": 1, "horizon": horizon, "tickers": ["SOXL", "005930.KS"], "start": "2019-06-01", "end": None,
            "generated_at": "2026-11-08T06:32:24+00:00", "costs": {"bps_per_side": 10.0},
            "baseline": {"plans": plans, "filled": 80, "expectancy_r": -0.14, "fill_rate": 0.66},
            "by_setup": {"long:dip_catch": {"plans": 40, "expectancy_r": -0.08}}, "notes": []}


@pytest.fixture
def snapshots(tmp_path, monkeypatch):
    (tmp_path / "swing.json").write_text(json.dumps(_report("swing")), encoding="utf-8")
    (tmp_path / "intraday.json").write_text(json.dumps(_report("intraday", 30)), encoding="utf-8")
    monkeypatch.setattr(backtest_reports, "DIR", tmp_path)
    return tmp_path


def test_the_list_follows_the_horizons_and_leaves_out_bad_files(snapshots):
    (snapshots / "scalp.json").write_text("{not json", encoding="utf-8")
    (snapshots / "short_swing.json").write_text(json.dumps(_report("swing")), encoding="utf-8")   # another horizon's
    rows = backtest_reports.summaries()
    assert [r["horizon"] for r in rows] == ["intraday", "swing"]
    swing = rows[1]
    assert swing["plans"] == 120 and swing["filled"] == 80 and swing["expectancy_r"] == -0.14
    assert swing["tickers"] == ["SOXL", "005930.KS"] and swing["generated_at"].startswith("2026-11-08") and swing["label"]
    assert backtest_reports.get("scalp") is None and backtest_reports.get("short_swing") is None
    assert backtest_reports.get("swing")["by_setup"]["long:dip_catch"]["plans"] == 40
    with pytest.raises(ValueError):
        backtest_reports.get("../secrets")


def test_the_api_needs_a_session_and_answers_404_and_422(db_url, snapshots):
    (snapshots / "intraday.json").unlink()
    with _client(db_url, PriceSource(MockBroker()), fake_runs=True) as client:
        assert client.get("/api/backtest/reports").status_code == 401
        assert client.get("/api/backtest/reports/swing").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert [r["horizon"] for r in client.get("/api/backtest/reports").json()] == ["swing"]
        assert client.get("/api/backtest/reports/swing").json()["baseline"]["plans"] == 120
        assert client.get("/api/backtest/reports/intraday").status_code == 404
        assert client.get("/api/backtest/reports/weekly").status_code == 422


def test_the_committed_snapshots_are_reports_of_their_horizon():
    rows = backtest_reports.summaries()
    assert {r["horizon"] for r in rows} >= {"swing", "intraday"}
    assert all(r["plans"] and r["generated_at"] for r in rows)
