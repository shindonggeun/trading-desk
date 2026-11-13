"""#359: today's spot and futures investor flows as minute bars, and the foreign buy/sell turns."""

from datetime import UTC, date, datetime, timedelta

import pytest

from desk import flow_intraday as fi
from desk.db import make_engine, migrate
from desk.futures_history import FuturesHistory, row_of, spot_cols


def _kst(h, m):
    return datetime(2026, 11, 10, h, m, tzinfo=UTC) - timedelta(hours=9)


def _spot(foreign_won, day="2026-11-10"):
    rec = {"date": day, "investors": {"foreign": {"net": foreign_won}, "institution": {"net": -foreign_won / 2},
                                      "individual": {"net": None}}}
    return {"kospi": {"records": [rec]}, "kosdaq": {"error": "down", "records": []}}


def _view(foreign):
    return {"price": {"code": "A01612", "price": 700.0, "open_interest": 1.0, "market_basis": 1.0},
            "investors": {"foreign": {"net_contracts": foreign}, "individual": {"net_contracts": -foreign},
                          "institution": {"net_contracts": 0.0}}}


def _point(h, m, fut, kospi):
    """A snapshot as desk.futures_history.intraday reads it back."""
    return {"at": _kst(h, m).isoformat(), "price": 700.0 + m / 10,
            "investors": {"foreign": {"net_contracts": fut}, "institution": {"net_contracts": 0.0},
                          "individual": {"net_contracts": None if fut is None else -fut}},
            "spot": {"kospi": {"foreign": kospi, "institution": None, "individual": None},
                     "kosdaq": {"foreign": None, "institution": None, "individual": None}}}


def test_spot_columns_are_todays_net_in_100_million_won():
    cols = spot_cols(_spot(-1_240e8), "2026-11-10")
    assert cols["kospi_foreign_100m"] == -1240.0 and cols["kospi_institution_100m"] == 620.0
    assert cols["kospi_individual_100m"] is None and cols["kosdaq_foreign_100m"] is None
    assert spot_cols(_spot(5e8, day="2026-11-09"), "2026-11-10")["kospi_foreign_100m"] is None   # yesterday's record
    assert row_of(_view(100.0), _kst(10, 0), _spot(3e8))["kospi_foreign_100m"] == 3.0


def test_snapshots_keep_spot_and_read_it_back(db_url):
    migrate(db_url)
    h = FuturesHistory(make_engine(db_url))
    assert h.record(_view(-200.0), _kst(10, 0), _spot(-50e8))
    assert h.record(_view(-300.0), _kst(10, 1))                    # no spot: the futures still count
    day = h.intraday(date(2026, 11, 10))
    assert day[0]["spot"]["kospi"]["foreign"] == -50.0 and day[1]["spot"]["kospi"]["foreign"] is None


def test_bars_close_at_the_last_snapshot_and_carry_what_the_bar_added():
    pts = [_point(9, 1, 100.0, 10.0), _point(9, 3, 300.0, 40.0), _point(9, 5, 250.0, 70.0), _point(9, 7, 600.0, 90.0)]
    five = fi.bars(pts, "futures", 5)
    assert [b["t"][11:16] for b in five] == ["09:05", "09:10"]
    assert [b["foreign_contracts"] for b in five] == [250.0, 600.0]
    assert [b["foreign_delta"] for b in five] == [250.0, 350.0]           # the first bar's delta is its total
    one = fi.bars(pts, "kospi", 1)
    assert [b["foreign"] for b in one] == [10.0, 40.0, 70.0, 90.0] and one[1]["foreign_delta"] == 30.0
    assert "price" in five[0] and "price" not in one[0]


def test_a_turn_needs_the_dead_band_and_two_snapshots():
    # buying all morning, wobbling near zero, then selling for good: one sell turn
    futs = [800, 900, 400, -100, 200, -600, -700, -800]
    pts = [_point(10, i, float(v), None) for i, v in enumerate(futs)]
    out = fi.turns(pts, "futures")
    assert out == [{"t": _kst(10, 6).astimezone(fi.KST).isoformat(timespec="seconds"), "market": "futures",
                    "who": "foreign", "kind": "sell_turn", "value": -700.0}]
    # one snapshot past the band is not a turn; the first side of the day is not a turn either
    assert fi.turns([_point(10, i, float(v), None) for i, v in enumerate([800, 900, -600, 900])], "futures") == []
    back = fi.turns([_point(10, i, float(v), None) for i, v in enumerate([-800, -900, 600, 700, -400, -500])], "kospi")
    assert back == []                                                           # no spot values at all
    spot = [_point(10, i, None, float(v)) for i, v in enumerate([-800, -900, 600, 700, -400, -500])]
    assert [t["kind"] for t in fi.turns(spot, "kospi")] == ["buy_turn", "sell_turn"]


def test_view_checks_the_bucket():
    with pytest.raises(ValueError):
        fi.view([], 7)
    assert fi.view([], 30) == {"bucket_min": 30, "spot": {"kospi": [], "kosdaq": []}, "futures": [], "turns": []}


def test_api_answers_bars_and_turns(db_url):
    from tests.test_api import OWNER, PASSWORD, _client

    migrate(db_url)
    h = FuturesHistory(make_engine(db_url))
    for i, v in enumerate([800.0, 900.0, -600.0, -700.0]):
        h.record(_view(v), _kst(10, i), _spot(v * 1e8))
    with _client(db_url, login=False) as client:
        assert client.get("/api/market/flow/intraday").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/market/flow/intraday?date=2026-11-10&bucket=1").json()
        assert body["date"] == "2026-11-10" and body["bucket_min"] == 1 and body["supported"] is False
        assert len(body["futures"]) == 4 and body["spot"]["kospi"][-1]["foreign"] == -700.0
        assert {(t["market"], t["kind"]) for t in body["turns"]} == {("futures", "sell_turn"), ("kospi", "sell_turn")}
        assert client.get("/api/market/flow/intraday?bucket=7").status_code == 422
        assert client.get("/api/market/flow/intraday?date=11-10").status_code == 422
