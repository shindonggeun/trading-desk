"""#620 today's strong / weak sectors: the series, no look-ahead, the model, the record, the view, the brief, the API."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta

import numpy as np
import pytest

from desk import day_sectors as ds
from desk import market_calendar
from desk import morning_brief as mb
from desk.broker.base import Bar
from tests.test_api import _client
from tests.test_morning_brief import NOW, _sources

FIRST, LAST = date(2023, 6, 1), date(2026, 11, 17)


def _days(market: str, first: date = FIRST, last: date = LAST) -> list[date]:
    out, d = [], first
    while d <= last:
        if market_calendar.is_open(market, d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _at(m: ds.Market, d: date) -> str:
    return datetime.combine(d, time(0), m.tz).astimezone(UTC).isoformat()


def _bars(m: ds.Market, days: list[date], excess: dict[str, dict[date, float]], seed: int) -> dict[str, list]:
    """The benchmark a random walk; each sector the benchmark's return plus its excess (%) that day."""
    rng = np.random.default_rng(seed)
    bench, closes = 100.0, {s.ticker: 100.0 for s in m.sectors}
    out = {m.benchmark: [], **{s.ticker: [] for s in m.sectors}}
    for i, d in enumerate(days):
        r = 0.0 if i == 0 else float(rng.normal(0, 0.01))
        bench *= 1 + r
        out[m.benchmark].append(Bar(_at(m, d), bench, bench, bench, bench, 1))
        for s in m.sectors:
            if i:
                closes[s.ticker] *= 1 + r + excess[s.id].get(d, 0.0) / 100
            c = closes[s.ticker]
            out[s.ticker].append(Bar(_at(m, d), c, c, c, c, 1))
    return out


def _world(signal: float = 0.8, seed: int = 7) -> dict[str, list]:
    """US sectors at random; each Korean sector follows its linked US sector's last session before it."""
    rng = np.random.default_rng(seed)
    us_days, kr_days = _days("us"), _days("kr")
    us_ex = {s.id: {d: float(rng.normal(0, 1)) for d in us_days} for s in ds.US.sectors}
    kr_ex = {}
    for s in ds.KR.sectors:
        kr_ex[s.id] = {}
        for d in kr_days:
            prev = [u for u in us_days if u < d][-1:] if s.link else []
            lead = us_ex[s.link][prev[0]] if prev else 0.0
            kr_ex[s.id][d] = signal * lead + float(rng.normal(0, 1))
    return {**_bars(ds.US, us_days, us_ex, seed + 1), **_bars(ds.KR, kr_days, kr_ex, seed + 2)}


@pytest.fixture(scope="module")
def world():
    return _world()


def _kst(d: date, hh: int, mm: int = 0) -> datetime:
    return datetime.combine(d, time(hh, mm), ds.KST)


def test_the_tables_link_both_ways_and_every_ticker_is_read():
    for m in (ds.KR, ds.US):
        ids = {s.id for s in ds.other(m).sectors}
        assert all(s.link in ids for s in m.sectors if s.link)
        assert len({s.id for s in m.sectors}) == len(m.sectors)
        assert set(ds.symbols(m)) >= {m.benchmark, ds.other(m).benchmark}


def test_the_excess_series_and_the_features_read_only_finished_sessions(world):
    data = ds.build(ds.KR, world)
    day = date(2026, 11, 17)
    semis = next(s for s in ds.KR.sectors if s.id == "semis")
    f = ds.features(data, semis, day)
    vals, last = data.own["semis"].before(day, 20)
    assert last == date(2026, 11, 16) and f["own_1d"] == pytest.approx(vals[-1])
    assert f["own_5d"] == pytest.approx(sum(vals[-5:])) and f["own_20d"] == pytest.approx(sum(vals))
    # Korea's 11/17 reads the US session of 11/16 (closed at 06:00 KST on the 17th), not the 17th's
    assert f["link_day"] == date(2026, 11, 16)
    assert f["link_1d"] == pytest.approx(data.link["semis"].at(date(2026, 11, 16)))
    # the US 11/17 reads Korea's 11/17 (closed before the US open)
    us = ds.build(ds.US, world)
    assert ds.features(us, next(s for s in ds.US.sectors if s.id == "semis"), day)["link_day"] == day


def test_the_picks_never_change_with_what_happened_after_their_cutoff(world):
    day = date(2026, 11, 10)
    before = ds.picks(ds.build(ds.KR, world), ds.train(ds.build(ds.KR, world), day), day)
    later = {t: [b if datetime.fromisoformat(b.at).astimezone(ds.KST).date() < day
                 else Bar(b.at, b.open * 3, b.high * 3, b.low * 3, b.close * 3, 1) for b in bars]
             for t, bars in world.items() if t.endswith(".KS")}
    us_later = {t: [b if datetime.fromisoformat(b.at).astimezone(ds.ET).date() < day
                    else Bar(b.at, b.open / 2, b.high / 2, b.low / 2, b.close / 2, 1) for b in bars]
                for t, bars in world.items() if not t.endswith(".KS")}
    moved = {**world, **later, **us_later}
    after = ds.picks(ds.build(ds.KR, moved), ds.train(ds.build(ds.KR, moved), day), day)
    assert [(r["id"], r["prob"]) for r in before] == [(r["id"], r["prob"]) for r in after]


def test_the_model_finds_the_lead_and_beats_following_yesterday(world):
    data = ds.build(ds.KR, world)
    model = ds.train(data, date(2026, 11, 18))
    w = dict(zip(ds.FEATURES, model.w[1:], strict=True))
    assert w["link_1d"] > 0.5 and abs(w["own_20d"]) < 0.2
    rec = ds.record(data, date(2026, 11, 18))
    assert rec["days"] == ds.HOLDOUT_DAYS and rec["to"] == "2026-11-17"
    assert rec["trained_until"] < rec["from"]                       # fit only on the sessions before the record
    assert rec["top3_hit"] > rec["baseline"]["top3_hit"] and rec["rank_ic"] > rec["baseline"]["rank_ic"] + 0.2
    assert len(rec["daily"]) == rec["days"] and len(rec["daily"][0]["top"]) == ds.PICKS


@pytest.mark.parametrize("market", [ds.KR, ds.US], ids=["kr", "us"])
def test_the_view_reuses_training_rows_without_changing_either_fit(world, market, monkeypatch):
    now = _kst(LAST, 18)
    ticker = market.sectors[0].ticker
    gaps = {**world, ticker: world[ticker][::2]}
    data = ds.build(market, gaps)
    model = ds.train(data, LAST)
    expected_picks = [(row["id"], row["prob"]) for row in ds.picks(data, model, LAST)]
    expected_record = ds.record(data, LAST)

    model_days = []
    make_rows = ds.rows

    def counted(series, days):
        model_days.extend(days)
        return make_rows(series, days)

    monkeypatch.setattr(ds, "rows", counted)
    actual = ds.view(market, gaps, now)
    assert actual["ready"] and actual["date"] == LAST.isoformat()
    assert [(row["id"], row["prob"]) for row in actual["sectors"]] == expected_picks
    assert actual["model"]["rows"] == model.rows
    assert actual["record"] == expected_record
    assert actual["record"]["trained_until"] < actual["record"]["from"]
    assert model_days == [day for day in data.bench_days if day < LAST]


def test_each_benchmark_is_normalized_once_per_view(world, monkeypatch):
    benchmarks = [world[ds.KR.benchmark], world[ds.US.benchmark]]
    reads = []
    closes = ds._closes

    def counted(bars, tz, upto):
        reads.extend(i for i, bench in enumerate(benchmarks) if bars is bench)
        return closes(bars, tz, upto)

    monkeypatch.setattr(ds, "_closes", counted)
    ds.build(ds.KR, world)
    assert reads == [0, 1]


def test_the_view_before_the_open_has_reasons_and_no_outcome_yet(world):
    v = ds.view(ds.KR, world, _kst(date(2026, 11, 17), 7, 30))
    assert v["ready"] and v["date"] == "2026-11-17" and v["result"] is None
    assert len(v["top"]) == len(v["bottom"]) == ds.PICKS
    assert v["top"][0]["prob"] >= v["top"][-1]["prob"] >= v["bottom"][-1]["prob"] >= v["bottom"][0]["prob"]
    assert all(r["excess_pct"] is None for r in v["sectors"])
    assert all(r["reasons"] for r in v["top"] + v["bottom"])
    assert not any(k.startswith("_") for r in v["sectors"] for k in r)
    linked = [x for r in v["top"] for x in r["reasons"] if x.startswith("미장 ")]
    assert linked and "11/16 시장 대비" in linked[0]
    assert v["model"]["weights"]["link_1d"] > 0 and v["record"]["days"] == ds.HOLDOUT_DAYS


def test_after_the_close_the_day_is_scored(world):
    v = ds.view(ds.KR, world, _kst(date(2026, 11, 17), 16, 5))
    assert v["result"] is not None and set(v["result"]) >= {"top_hits", "bottom_hits", "rank_ic"}
    assert all(r["excess_pct"] is not None for r in v["sectors"])
    early = ds.view(ds.KR, world, _kst(date(2026, 11, 17), 15, 50))      # 20 minutes after the close: not yet
    assert early["result"] is None


def test_without_the_sessions_it_needs_it_waits():
    world = _world()
    cut = {t: [b for b in bars if datetime.fromisoformat(b.at).astimezone(ds.ET).date() < date(2026, 11, 16)]
           for t, bars in world.items() if not t.endswith(".KS")}
    v = ds.view(ds.KR, {**world, **cut}, _kst(date(2026, 11, 17), 7, 30))  # the US 11/16 not in yet
    # the 17th waits; meanwhile the 16th's picks and their score
    assert v["ready"] and v["target"] == "2026-11-17" and v["date"] == "2026-11-16" and v["result"] is not None
    assert "11/17 판정은" in v["note"] and "직전 세션(11/16)" in v["note"]
    empty = ds.view(ds.KR, {}, _kst(date(2026, 11, 17), 7, 30))
    assert empty["ready"] is False and "아직" in empty["note"] and empty["top"] == []


def test_a_linked_session_still_in_progress_is_not_read(world):
    # 00:30 KST on the 17th: Korea's target is the 17th, but the US 11/16 session runs until 06:00 KST
    assert ds.view(ds.KR, world, _kst(date(2026, 11, 17), 0, 30))["date"] == "2026-11-16"     # the last one
    assert ds.view(ds.KR, world, _kst(date(2026, 11, 17), 6, 40))["date"] == "2026-11-17"
    # 00:30 ET on the 17th (14:30 KST): the US target is the 17th, Korea's 17th closes at 15:30 KST
    et = datetime.combine(date(2026, 11, 17), time(0, 30), ds.ET)
    assert ds.view(ds.US, world, et)["date"] == "2026-11-16"
    assert ds.view(ds.US, world, et + timedelta(hours=2))["date"] == "2026-11-17"
    # a weekend shows Friday's score, not two days of "wait"
    sat = ds.view(ds.US, world, datetime.combine(date(2026, 11, 14), time(10), ds.ET))
    assert sat["target"] == "2026-11-16" and sat["date"] == "2026-11-13" and sat["result"] is not None


def test_the_csat_day_closes_an_hour_late():
    assert ds.session_close(ds.KR, date(2026, 11, 19)).time() == time(16, 30)
    assert ds.session_close(ds.KR, date(2026, 11, 18)).time() == time(15, 30)
    assert ds.session_close(ds.US, date(2026, 11, 19)).time() == time(16, 0)


def test_a_reversal_is_said_as_one():
    row = {"id": "bank", "_f": {"own_1d": -2.3, "own_5d": 1.0, "own_20d": 4.0, "link_1d": 0.0, "link_5d": 0.0,
                                 "link_day": None},
           "_push": {"own_1d": 0.4, "own_5d": -0.1, "own_20d": 0.2, "link_1d": 0.0, "link_5d": 0.0}}
    assert ds.reasons(ds.KR, row, up=True) == ["직전 거래일 시장 대비 -2.3% — 되돌림 쪽", "최근 20거래일 시장 대비 +4.0%"]


def test_the_cache_keeps_a_view_per_session_and_asks_a_late_one_again_soon():
    asked = []

    def candles(t, i, n):
        asked.append((t, n))
        return []
    clock = [0.0]
    cache = ds.Cache(clock=lambda: clock[0])
    now = _kst(date(2026, 11, 17), 7, 30)                            # its US session is over: no bars = late
    cache.read("kr", candles, now)
    first = len(asked)
    assert first == len(set(ds.symbols(ds.KR))) and {n for _, n in asked} == {ds.HISTORY_BARS}
    cache.read("kr", candles, now)
    assert len(asked) == first                                       # cached
    clock[0] = ds.NOT_READY_TTL_SEC - 5
    cache.read("kr", candles, now, ahead=10)                         # prewarm: asked again before it expires
    assert len(asked) == first                                       # ... but its bars are this young: kept
    clock[0] = ds.NOT_READY_TTL_SEC + ds.BARS_FRESH_SEC
    cache.read("kr", candles, _kst(date(2026, 11, 18), 7, 30))       # another session: read again
    assert len(asked) == 2 * first


def _view(target: str, *, ready: bool, date_: str | None = None, result=None) -> dict:
    return {"market": "us", "target": target, "date": date_ or target, "ready": ready, "result": result}


def test_a_view_stands_until_it_can_change():
    """#864 대기 뷰는 필요한 세션 마감 + 30분까지, 당일 판정은 그 세션 채점까지, 채점된 것은 대상이 바뀔 때까지."""
    mon = date(2026, 11, 23)
    before = _kst(mon, 11, 22)
    # the US's Monday needs Korea's Monday (16:00 KST with SCORE_AFTER): Friday's scored view until then
    assert ds.ready_at(ds.US, mon) == _kst(mon, 16, 0)
    assert ds.stands_until(ds.US, _view("2026-11-23", ready=True, date_="2026-11-20"), before) == _kst(mon, 16, 0)
    assert ds.stands_until(ds.US, _view("2026-11-23", ready=False), _kst(mon, 16, 1)) is None   # bars late
    kr = {**_view("2026-11-23", ready=True), "market": "kr"}
    assert ds.stands_until(ds.KR, kr, before) == _kst(mon, 16, 0)     # 15:30 close + 30 min: then scored
    assert ds.stands_until(ds.KR, kr, _kst(mon, 16, 5)) is None       # past it and still no score: late
    scored = {**kr, "result": {"spearman": 0.1}}
    assert ds.stands_until(ds.KR, scored, _kst(mon, 16, 5)) == _kst(mon, 16, 5) + timedelta(days=1)


def test_a_waiting_view_is_not_rebuilt_by_each_prewarm_pass(monkeypatch):
    """#864 운영: US 뷰가 한국장 마감 전 2분 TTL 이라 prewarm(ahead 300초)이 4분마다 10~14초씩 다시 만들었다."""
    built, asked = [], []
    mon = date(2026, 11, 23)

    def fake_view(m, bars, now):
        built.append(now)
        day = ds.target(m, now).isoformat()
        return _view(day, ready=True, date_="2026-11-20") if now < ds.ready_at(m, ds.target(m, now))             else _view(day, ready=True, result={"spearman": 0.2})

    monkeypatch.setattr(ds, "view", fake_view)
    clock = [0.0]
    cache = ds.Cache(clock=lambda: clock[0])
    now = _kst(mon, 11, 22)
    def candles(t, i, n):
        asked.append(n)
        return _flat(1, date(2026, 11, 20))

    for _ in range(10):                                              # 40 minutes of prewarm passes
        cache.read("us", candles, now, ahead=300)
        clock[0] += 240
        now += timedelta(seconds=240)
    assert len(built) == 1
    clock[0] += 5 * 3600
    cache.read("us", candles, _kst(mon, 16, 1), ahead=300)
    assert len(built) == 2                                           # its Korean session is in: rebuilt


def _flat(n: int, last: date, close: float = 100.0) -> list:
    days = [last - timedelta(days=k) for k in range(n)][::-1]
    return [Bar(datetime.combine(d, time(0), UTC).isoformat(timespec="seconds"), close, close, close, close, 0.0)
            for d in days]


def test_both_markets_share_the_bars_and_a_later_rebuild_reads_only_the_last_few(monkeypatch):
    monkeypatch.setattr(ds, "view", lambda m, bars, now: {**_view(ds.target(m, now).isoformat(), ready=False),
                                                          "bars": bars})
    asked = []

    def candles(t, i, n):
        asked.append((t, n))
        bars = _flat(n, date(2026, 11, 20))
        if n == ds.RECENT_BARS:                                      # today's bar moved; the older ones did not
            bars[-1] = Bar(bars[-1].at, 101.0, 101.0, 101.0, 101.0, 0.0)
        return bars

    clock = [0.0]
    cache = ds.Cache(clock=lambda: clock[0])
    now = _kst(date(2026, 11, 23), 16, 1)                           # past both ready times: a late view
    cache.read("kr", candles, now)
    cache.read("us", candles, now)
    assert len(asked) == len(set(ds.symbols(ds.KR))) == len(set(ds.symbols(ds.US)))   # one read per ETF
    asked.clear()
    clock[0] = ds.NOT_READY_TTL_SEC + ds.BARS_FRESH_SEC
    v = cache.read("kr", candles, now + timedelta(minutes=4))
    assert asked and {n for _, n in asked} == {ds.RECENT_BARS}       # the day's full read: only the last few
    bars = v["bars"][ds.KR.benchmark]
    assert len(bars) == ds.HISTORY_BARS and bars[-1].close == 101.0 and bars[0].close == 100.0


SECTOR_VIEW = {"ready": True, "date": "2026-11-18", "name": "국장",
               "top": [{"name": "반도체", "prob": 0.612}, {"name": "방산", "prob": 0.571}, {"name": "건설", "prob": 0.553}],
               "bottom": [{"name": "증권", "prob": 0.384}, {"name": "은행", "prob": 0.41}, {"name": "인터넷", "prob": 0.437}]}


def _brief(view):
    return mb.bundle(asyncio.run(mb.collect(_sources(sectors=lambda: view), NOW)), NOW)


def test_the_morning_brief_says_the_korean_picks_and_checks_their_numbers():
    b = _brief(SECTOR_VIEW)
    draft = mb.rules(b)
    assert "오늘 국장에서 시장보다 강할 확률이 높은 섹터: 반도체 61% · 방산 57% · 건설 55%" in draft["good"]
    assert "오늘 국장에서 시장보다 약할 확률이 높은 섹터(강할 확률): 증권 38% · 은행 41% · 인터넷 44%" in draft["bad"]
    check = mb.allowed_numbers(b)
    assert mb._ok("반도체가 61%로 시장보다 강할 확률이 가장 높아요.", check, 200)
    assert mb._ok("반도체가 57%로 시장보다 강할 확률이 높아요.", check, 200) is None    # 방산's number on 반도체
    assert mb._ok("은행은 75% 확률로 강해요.", check, 200) is None
    assert _brief({**SECTOR_VIEW, "date": "2026-11-17"})["sectors"] is None          # not today's picks
    assert _brief({"ready": False})["sectors"] is None and "섹터" not in " ".join(mb.rules(_brief({"ready": False}))["good"])


def test_the_sectors_api(db_url, monkeypatch):
    with _client(db_url) as client:
        assert client.get("/api/day-call/sectors?market=xx").status_code == 422
        assert client.get("/api/day-call/sectors?market=kr").status_code == 503     # scripted runs fetch nothing
        monkeypatch.setattr(ds.Cache, "cached", lambda self, market, now, ahead=0: {**SECTOR_VIEW, "market": market})
        got = client.get("/api/day-call/sectors?market=us").json()
        assert got["market"] == "us" and got["top"][0]["name"] == "반도체"
        # #705 the dashboard card's one call carries the same sector answer
        assert client.get("/api/day-call/board?sectors=us").json()["sectors"] == got


def test_the_sectors_api_needs_a_session(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/day-call/sectors?market=kr").status_code == 401


def _row_loop(frame, count):
    """The bars as yahoo_adjusted built them before #704 (frame.iterrows), the reference."""
    from zoneinfo import ZoneInfo

    from desk.broker.base import Bar

    frame = frame.dropna(subset=["Open", "High", "Low", "Close"])
    bars = [Bar(at=i.to_pydatetime().astimezone(ZoneInfo("UTC")).isoformat(timespec="seconds"), open=float(r["Open"]),
                high=float(r["High"]), low=float(r["Low"]), close=float(r["Close"]), volume=0.0)
            for i, r in frame.iterrows()]
    return bars[-count:]


def _frame(n, tz):
    import numpy as np
    import pandas as pd

    rng = np.random.default_rng(3)
    idx = pd.date_range("2016-01-04", periods=n, freq="B", tz=tz)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    frame = pd.DataFrame({"Open": close * 1.001, "High": close * 1.01, "Low": close * 0.99, "Close": close,
                          "Volume": rng.integers(1, 10**6, n), "Dividends": 0.0}, index=idx)
    frame.iloc[5, 0] = np.nan                     # a row with a missing price is dropped, as before
    return frame


def test_column_wise_bars_are_the_row_loops():
    # #704 the same bars from the same frame, US and Korean (Yahoo's index is in the exchange's zone)
    from desk import day_sectors
    from desk.broker.base import Bar

    for tz in ("America/New_York", "Asia/Seoul"):
        frame = _frame(700, tz)
        for count in (1, 250, 700, 2000):
            assert day_sectors.frame_bars(frame, count, Bar) == _row_loop(frame, count)
    assert day_sectors.frame_bars(_frame(10, "UTC").iloc[:0], 5, Bar) == []


def test_a_rebuild_reads_all_again_when_yahoo_readjusted_the_history():
    """#864 리뷰: 낮에 분배금 · 분할로 조정 계수가 바뀌면 최근 봉을 옛 이력 위에 얹지 않는다(가짜 수익률)."""
    old = _flat(30, date(2026, 11, 20))
    adjusted = [Bar(b.at, b.close * 0.97, b.close * 0.97, b.close * 0.97, b.close * 0.97, 0.0) for b in old[-10:]]
    assert ds._merge(old, adjusted) is None
    assert ds._merge(old, _flat(10, date(2026, 12, 20))) is None      # nothing in common
    assert ds._merge(old, []) == old
    same = ds._merge(old, old[-10:])
    assert same == old


@pytest.mark.parametrize("failure", ["timeout", "empty"])
def test_both_markets_share_a_failed_etf_read_and_retry_next_pass(monkeypatch, caplog, failure):
    built, asked = [], []
    caplog.set_level("INFO", logger=ds.log.name)

    def fake_view(m, bars, now):
        built.append((m.id, set(bars)))
        day = ds.target(m, now).isoformat()
        return _view(day, ready=True, date_="2026-11-20")              # waiting for its target: held if complete

    monkeypatch.setattr(ds, "view", fake_view)
    down = True

    def candles(t, i, n):
        asked.append((t, n))
        if t == "SPY" and down:
            if failure == "timeout":
                raise TimeoutError()
            return []
        return _flat(n, date(2026, 11, 20))

    clock = [0.0]
    cache = ds.Cache(clock=lambda: clock[0])
    now = _kst(date(2026, 11, 23), 11, 22)
    cache.read("us", candles, now, ahead=300)
    cache.read("kr", candles, now, ahead=300)
    assert asked.count(("SPY", ds.HISTORY_BARS)) == 1
    assert len(asked) == len(ds.symbols(ds.US))
    assert all("SPY" not in bars for _, bars in built)
    assert cache.cached("us", now, ahead=300) is None
    first, second = [m for m in caplog.messages if m.startswith("day sectors: rebuild")]
    assert f"full_calls={len(asked)} " in first
    expected_failures = "origin_errors=1 origin_empty=0" if failure == "timeout" else "origin_errors=0 origin_empty=1"
    assert expected_failures in first
    assert "full_calls=0 " in second and "reused_failures=1" in second
    assert all(field in first for field in ("lock_ms=", "bars_ms=", "view_ms=", "origin_max_ms="))

    clock[0] += 240
    down = False
    cache.read("us", candles, now + timedelta(minutes=4), ahead=300)   # the next pass reads it again
    assert len(built) == 3 and "SPY" in built[-1][1]
    assert asked.count(("SPY", ds.HISTORY_BARS)) == 2
    clock[0] += 240
    cache.read("us", candles, now + timedelta(minutes=8), ahead=300)   # complete now: held until 16:00
    assert len(built) == 3


@pytest.mark.parametrize("adjusted", [False, True])
def test_a_failed_refresh_keeps_last_good_bars_without_marking_them_fresh(adjusted):
    asked = []
    clock = [0.0]
    cache = ds.Cache(clock=lambda: clock[0])
    ticker = ds.US.benchmark
    day = date(2026, 11, 23)
    now = _kst(day, 15, 58)
    old = _flat(30, date(2026, 11, 20))
    new = _flat(30, date(2026, 11, 20), close=97)
    cache._read_bars([ticker], lambda t, i, n: old, now)

    def candles(t, i, n):
        asked.append(n)
        return new[-n:] if adjusted and n == ds.RECENT_BARS else []

    clock[0] = ds.BARS_FRESH_SEC
    later = now + timedelta(minutes=4)
    bars, complete, _ = cache._read_bars([ticker], candles, later)
    assert bars[ticker] == old and not complete
    assert asked == ([ds.RECENT_BARS, ds.HISTORY_BARS] if adjusted else [ds.RECENT_BARS])

    reused, complete, _ = cache._read_bars([ticker], candles, later)
    assert reused[ticker] == old and not complete
    assert len(asked) == (2 if adjusted else 1)

    clock[0] += ds.BARS_FRESH_SEC
    recovered, complete, _ = cache._read_bars([ticker], lambda t, i, n: new[-n:], later + timedelta(minutes=4))
    assert recovered[ticker] == new and complete
