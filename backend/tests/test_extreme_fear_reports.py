import json
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from desk import extreme_fear_reports, market_calendar
from desk.api import create_app
from desk.auth import Auth
from desk.db import make_engine
from desk.fear import Fear
from desk.settings import Settings
from tests.test_api import OWNER, PASSWORD


def _synthetic():
    sessions, prices, cnn, vix = [], [], [], []
    day = date(2024, 10, 15)
    while len(sessions) < 40:
        if market_calendar.is_open("us", day):
            opened, closed = market_calendar.session_bounds("us", day)
            dt = day.isoformat()
            sessions.append({"date": dt, "opens_at": opened.isoformat(), "closes_at": closed.isoformat()})
            index = len(sessions)
            close = 100 - index if index <= 18 else 82 + (index - 18) * 2
            prices.append({"date": dt, "open": close, "high": close + 2, "low": close - 1, "close": close,
                           "available_at": closed.isoformat()})
            cnn.append({"date": dt, "value": 10 if index == 17 else 20,
                        "available_at": (closed + timedelta(minutes=17)).isoformat()})
            vix.append({"date": dt, "value": 30, "available_at": (closed + timedelta(minutes=16)).isoformat()})
        day += timedelta(days=1)
    return {
        "schema_version": 1, "kind": "synthetic",
        "price_adjustment": extreme_fear_reports.PRICE_ADJUSTMENT,
        "as_of": (datetime.fromisoformat(sessions[-1]["closes_at"]) + timedelta(hours=1)).isoformat(),
        "sources": {key: {"name": key, "version": "fixture", "evidence": "synthetic", "usage": "test"}
                    for key in extreme_fear_reports.INPUT_SOURCES},
        "sessions": sessions, "prices": {"SOXL": prices, "KORU": prices}, "cnn": cnn, "vix": vix,
    }


def test_offline_cli_is_reproducible_and_runtime_hides_synthetic(monkeypatch, tmp_path):
    data = _synthetic()
    source = tmp_path / "source.json"
    source.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "extreme_fear_soxl.json"
    monkeypatch.setattr("sys.argv", ["extreme_fear_reports", str(source), str(output), "--symbol", "SOXL"])
    extreme_fear_reports.main()
    first = output.read_bytes()
    extreme_fear_reports.main()
    assert output.read_bytes() == first
    report = json.loads(first)
    assert report["dataset"]["kind"] == "synthetic"
    assert report["strategies"]["triple"]["horizons"]["5"]["n"] == 1
    assert report["strategies"]["triple"]["horizons"]["252"]["immature"] == 1
    actual = extreme_fear_reports.get("SOXL", root=tmp_path, as_of=datetime.fromisoformat(data["as_of"]))
    assert actual["status"] == "unverified" and actual["strategies"] == {} and not actual["signal_history"]


def test_latest_closed_session_and_observations_do_not_become_signals(tmp_path):
    # Black Friday closes at 13:00 ET. Before then, Wednesday is the last closed session.
    before = datetime(2025, 11, 28, 17, 59, tzinfo=UTC)
    get = extreme_fear_reports.get
    assert get("KORU", root=tmp_path, as_of=before)["current"]["date"] == "2025-11-26"
    after = before + timedelta(minutes=1)
    report = get("KORU", root=tmp_path, as_of=after, observations=(
        {"key": "cnn", "source": "CNN Fear & Greed", "as_of": "2025-11-28T18:00:00Z", "value": 8},
        {"key": "vix", "source": "Yahoo ^VIX", "as_of": "2025-11-28", "value": 31},
    ))
    assert report["current"]["date"] == "2025-11-28"
    assert report["current"]["matched_count"] == 0 and report["current"]["conditions"]["cnn"]["value"] is None
    assert report["observations"]["cnn"]["status"] == "unverified"


def test_runtime_separates_historical_results_from_current_session(tmp_path):
    data = _synthetic()
    # Exercise the private, trusted-file loader without distributing any fixture as real data.
    data["kind"] = "approved"
    report = extreme_fear_reports.build(json.dumps(data).encode(), "SOXL")
    (tmp_path / "extreme_fear_soxl.json").write_text(json.dumps(report), encoding="utf-8")
    as_of = datetime.fromisoformat(data["as_of"])
    assert extreme_fear_reports.get("SOXL", root=tmp_path, as_of=as_of - timedelta(seconds=1))["strategies"] == {}
    current = extreme_fear_reports.get("SOXL", root=tmp_path, as_of=as_of)
    assert current["current"]["date"] == report["current"]["date"]
    later = extreme_fear_reports.get("SOXL", root=tmp_path, as_of=as_of + timedelta(days=7))
    assert later["strategies"] == report["strategies"]
    assert later["current"]["conditions"]["rsi"]["reason"] == "report_has_no_current_session"


def test_offline_input_requires_coherent_adjustment_and_complete_calendar():
    data = _synthetic()
    data["price_adjustment"] = "back_adjusted"
    with pytest.raises(ValueError, match="consistent causal forward"):
        extreme_fear_reports.build(json.dumps(data).encode(), "KORU")
    data["price_adjustment"] = extreme_fear_reports.PRICE_ADJUSTMENT
    data["sessions"].pop(12)
    with pytest.raises(ValueError, match="missing.*NYSE session"):
        extreme_fear_reports.build(json.dumps(data).encode(), "KORU")


def test_fresh_us_observations_only_read_cache():
    now = [10]
    gauges = Fear(clock=lambda: now[0])
    assert gauges.us_observations() == ()
    gauges._cache = {"cnn": (20, {"key": "cnn", "value": 10}), "vix": (20, {"key": "vix", "value": None}),
                     "vkospi": (20, {"key": "vkospi", "value": 40})}
    assert gauges.us_observations() == ({"key": "cnn", "value": 10},)
    now[0] = 20
    assert gauges.us_observations() == ()


def test_research_route_requires_existing_session_and_fixed_symbols(db_url, tmp_path, monkeypatch):
    settings = Settings(database_url=db_url, fake_runs=True, fake_delay=0)
    app = create_app(settings)
    engine = make_engine(db_url)
    try:
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    finally:
        engine.dispose()
    get = extreme_fear_reports.get
    monkeypatch.setattr(extreme_fear_reports, "get", lambda symbol, **kwargs: get(symbol, root=tmp_path, **kwargs))
    with TestClient(app, base_url="https://testserver") as client:
        route = "/api/research/extreme-fear"
        assert client.get(route + "?symbol=SOXL").status_code == 401
        assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
        for suffix in ("", "?symbol=SPY", "?symbol=../SOXL"):
            assert client.get(route + suffix).status_code == 422
        for symbol in ("SOXL", "KORU"):
            response = client.get(route + "?symbol=" + symbol)
            assert response.status_code == 200
            assert response.json()["symbol"] == symbol and response.json()["status"] == "unverified"
            assert response.json()["observations"] == {}
