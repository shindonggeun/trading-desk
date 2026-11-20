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
        assert day["date"] == "2026-11-10" and len(day["points"]) == 1 and day["interval_min"] == 1
        assert client.get("/api/market/futures/intraday?date=11-10").status_code == 422


def _kst(h, m, s=0, day=10):
    return datetime(2026, 11, day, h, m, s, tzinfo=UTC) - timedelta(hours=9)    # KST wall clock -> UTC


def test_snapshots_sit_on_the_clock_grid_from_the_open_and_close_once_after_the_bell():
    from desk.futures_history import seconds_to_next

    assert seconds_to_next(_kst(7, 30), 15) == 30 * 60                # #677 wait for the 08:00 pre-market
    assert seconds_to_next(_kst(8, 0), 15) == 15 * 60                 # 08:00 taken -> 08:15 (the pre-market's grid)
    assert seconds_to_next(_kst(8, 30), 15) == 15 * 60                # -> 08:45, the open
    assert seconds_to_next(_kst(8, 30), 30) == 15 * 60                # not 09:00: the pre-market's grid stops at 08:45
    assert seconds_to_next(_kst(8, 45), 30) == 30 * 60                # and the session's starts there (-> 09:15)
    assert seconds_to_next(_kst(8, 44), 2) == 60                      # 08:46 on the 08:00 grid -> 08:45
    assert seconds_to_next(_kst(8, 46), 2) == 60                      # -> 08:47 on the 08:45 grid (not 08:48)
    assert seconds_to_next(_kst(8, 45), 15) == 15 * 60                # 08:45 taken -> 09:00
    assert seconds_to_next(_kst(10, 7), 15) == 8 * 60                 # a restart at 10:07 -> 10:15
    assert seconds_to_next(_kst(15, 37), 15) == 8 * 60                # 15:45 is on the grid
    assert seconds_to_next(_kst(15, 45), 15) == 60                    # -> the closing snapshot at 15:46
    assert seconds_to_next(_kst(15, 46), 15) == 3600                  # then the next open, an hour at most
    assert seconds_to_next(_kst(10, 7), 5) == 3 * 60
    fri_evening = _kst(20, 0, day=13)
    assert seconds_to_next(fri_evening, 15) == 3600


def test_the_closing_snapshot_is_the_days_daily_row(db_url):
    from desk.futures_history import closing_window, interval_min

    assert closing_window(_kst(15, 46)) and not closing_window(_kst(16, 0)) and not closing_window(_kst(15, 46, day=14))
    assert interval_min(0) == 0 and interval_min(0.1) == 1.0 and interval_min(15) == 15.0
    h = _history(db_url)
    assert h.record(_view(foreign=-100.0), _kst(15, 45))
    assert h.record(_view(foreign=-180.0), _kst(15, 46))               # the final totals
    assert not h.record(_view(), _kst(16, 5))
    assert h.daily(1)[0]["investors"]["foreign"]["net_contracts"] == -180.0


def test_a_fresh_get_skips_the_cache():
    import asyncio

    from desk.futures_flow import FuturesFlow

    calls = []

    class Flow(FuturesFlow):
        async def _fetch(self):
            calls.append(1)
            return {"n": len(calls)}, True

    flow = Flow(None)
    assert asyncio.run(flow.get())["n"] == 1 and asyncio.run(flow.get())["n"] == 1
    assert asyncio.run(flow.get(fresh=True))["n"] == 2



# #677 the pre-market -------------------------------------------------------------------------------


def _spot_view(day: str, foreign: float):
    record = {"date": day, "investors": {"foreigner": {"net": foreign * 1e8}, "institution": {"net": -100e8},
                                         "individual": {"net": -foreign * 1e8 + 100e8}}}
    return {"kospi": {"records": [record]}, "kosdaq": {"records": []}}


def test_the_pre_market_keeps_spot_only_rows_that_the_session_readers_never_see(db_url):
    from desk.db import make_engine, migrate
    from desk.futures_history import FuturesHistory, premarket_window

    migrate(db_url)
    engine = make_engine(db_url)
    history = FuturesHistory(engine)
    day = "2026-11-10"
    assert not premarket_window(_kst(7, 59)) and premarket_window(_kst(8, 0)) and not premarket_window(_kst(8, 45))
    assert history.record_spot(_spot_view(day, -300), _kst(8, 10)) is True
    assert history.record_spot(_spot_view(day, -500), _kst(8, 44)) is True
    assert history.record_spot(_spot_view(day, -500), _kst(8, 45)) is False          # the session's own snapshot
    assert history.record_spot(_spot_view("2026-11-09", -500), _kst(8, 20)) is False  # yesterday's record: nothing
    assert history.record_spot(_spot_view(day, -500), _kst(8, 20, day=14)) is False  # Saturday
    assert history.intraday(date(2026, 11, 10)) == []                                  # bars, turns, alerts: as before
    assert history.daily(5) == []                                       # the futures' days: not before the open
    pre = history.premarket(date(2026, 11, 10))
    assert [p["spot"]["kospi"]["foreign"] for p in pre] == [-300.0, -500.0]
    assert pre[0]["price"] is None and pre[0]["investors"]["foreign"]["net_contracts"] is None
    assert history.record(_view(price=701.0), _kst(8, 45), _spot_view(day, -550))
    assert history.record(_view(price=703.0), _kst(9, 0), _spot_view(day, -600))
    assert [p["price"] for p in history.intraday(date(2026, 11, 10))] == [701.0, 703.0]
    assert [p["spot"]["kospi"]["foreign"] for p in history.premarket(date(2026, 11, 10))] == [-300.0, -500.0, -550.0]
    assert [d["price"] for d in history.daily(5)] == [703.0]
    history.prune(now=_kst(10, 0, day=12), keep_days=0)                 # thinned: the last futures row stays
    assert [p["price"] for p in history.intraday(date(2026, 11, 10))] == [703.0]
    assert history.premarket(date(2026, 11, 10)) == []
    engine.dispose()


def test_the_pre_market_block_reads_first_last_and_the_move():
    from desk import flow_intraday

    def p(hm, foreign, day="2026-11-10"):
        return {"at": f"{day}T{hm}:00+09:00", "price": None,
                "investors": {c: {"net_contracts": None} for c in ("foreign", "institution", "individual")},
                "spot": {"kospi": {"foreign": foreign, "institution": -100.0, "individual": -foreign + 100},
                         "kosdaq": {}}}
    pts = [p("08:01", -50.0), p("08:30", -300.0), p("08:59", -1168.8), p("09:00", -2000.0)]
    got = flow_intraday.premarket(pts, "kospi")
    assert got["from"].startswith("2026-11-10T08:01") and got["to"].startswith("2026-11-10T08:59") and got["n"] == 3
    assert got["first"]["foreign"] == -50.0 and got["last"]["foreign"] == -1168.8 and got["change"]["foreign"] == -1118.8
    assert flow_intraday.premarket(pts, "kosdaq") is None and flow_intraday.premarket([], "kospi") is None
    assert flow_intraday.premarket(pts, "futures") is None
