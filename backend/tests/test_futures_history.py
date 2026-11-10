"""#254: KOSPI200 futures investor flows kept through the session and read back by day."""

from datetime import UTC, date, datetime, timedelta

from desk.db import make_engine, migrate
from desk.futures_history import FuturesHistory, row_of

# Tuesday 2026-11-10, KST = UTC + 9
OPEN = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)          # 10:00 KST
CLOSED = datetime(2026, 11, 10, 7, 0, tzinfo=UTC)        # 16:00 KST


def _view(price=702.35, foreign=-3200.0):
    return {"front": {"code": "A01612"}, "price": {"code": "A01612", "price": price, "open_interest": 245000,
                                                     "market_basis": 1.25},
            "investors": {"foreign": {"net_contracts": foreign, "net_value_mn": foreign * 175},
                          "individual": {"net_contracts": 1500.0, "net_value_mn": 260000.0},
                          "institution": {"net_contracts": 1700.0, "net_value_mn": 300000.0}}}


def _history(db_url):
    migrate(db_url)
    return FuturesHistory(make_engine(db_url))


def test_a_view_without_a_price_or_investors_is_not_history():
    assert row_of({"price": None, "investors": {"foreign": {}}}, OPEN) is None
    assert row_of({**_view(), "investors": None}, OPEN) is None
    row = row_of(_view(), OPEN)
    assert row["trade_date"] == "2026-11-10" and row["foreign_contracts"] == -3200.0 and row["code"] == "A01612"


def test_snapshots_only_in_the_session_and_read_back_by_day(db_url):
    h = _history(db_url)
    assert h.record(_view(), CLOSED) is False                 # after 15:45 KST
    assert h.record(_view(), OPEN) and h.record(_view(703.0, -2000.0), OPEN + timedelta(minutes=15))
    day = h.intraday(date(2026, 11, 10))
    assert [p["investors"]["foreign"]["net_contracts"] for p in day] == [-3200.0, -2000.0]
    assert day[0]["at"] == "2026-11-10T01:00:00+00:00" and day[1]["price"] == 703.0
    assert h.intraday(date(2026, 11, 9)) == []


def test_daily_is_each_days_last_snapshot_newest_first(db_url):
    h = _history(db_url)
    for d, foreign in ((0, -1.0), (0, -5.0), (1, 7.0), (1, 9.0), (6, 3.0)):     # Tue, Wed, next Mon
        h.record(_view(foreign=foreign), OPEN + timedelta(days=d, minutes=15 * abs(foreign)))
    days = h.daily(20)
    assert [(p["date"], p["investors"]["foreign"]["net_contracts"]) for p in days] == [
        ("2026-11-16", 3.0), ("2026-11-11", 9.0), ("2026-11-10", -5.0)]
    assert [p["date"] for p in h.daily(2)] == ["2026-11-16", "2026-11-11"]


def test_old_days_are_thinned_to_their_last_snapshot(db_url):
    h = _history(db_url)
    for minutes in (0, 15, 30):
        h.record(_view(foreign=-minutes), OPEN + timedelta(minutes=minutes))
    h.record(_view(), OPEN + timedelta(days=1))
    assert h.prune(OPEN + timedelta(days=70)) == 2
    assert [p["investors"]["foreign"]["net_contracts"] for p in h.intraday(date(2026, 11, 10))] == [-30.0]
    assert len(h.intraday(date(2026, 11, 11))) == 1
    assert h.prune(OPEN + timedelta(days=70)) == 0


def test_api_answers_daily_and_intraday(db_url):
    from tests.test_api import _client

    h = _history(db_url)
    h.record(_view(), OPEN)
    from tests.test_api import OWNER, PASSWORD

    with _client(db_url, login=False) as client:
        assert client.get("/api/market/futures/daily").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        daily = client.get("/api/market/futures/daily?days=5").json()
        assert daily["supported"] is False and daily["days"][0]["date"] == "2026-11-10"   # fake runs: no KIS
        day = client.get("/api/market/futures/intraday?date=2026-11-10").json()
        assert day["date"] == "2026-11-10" and len(day["points"]) == 1 and day["interval_min"] == 15
        assert client.get("/api/market/futures/intraday?date=11-10").status_code == 422
