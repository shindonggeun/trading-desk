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


def test_the_cache_keeps_a_view_per_session_and_refetches_when_old():
    asked = []

    def candles(t, i, n):
        asked.append(t)
        return []
    clock = [0.0]
    cache = ds.Cache(ttl=100, clock=lambda: clock[0])
    now = _kst(date(2026, 11, 17), 7, 30)
    cache.read("kr", candles, now)
    first = len(asked)
    assert first == len(ds.symbols(ds.KR))
    cache.read("kr", candles, now)
    assert len(asked) == first                                       # cached
    clock[0] = 95
    cache.read("kr", candles, now, ahead=10)                         # prewarm: refetch before it expires
    assert len(asked) == 2 * first
    cache.read("kr", candles, _kst(date(2026, 11, 18), 7, 30))       # another session
    assert len(asked) == 3 * first
    clock[0] = 95 + ds.NOT_READY_TTL_SEC + 1                         # no bars: a waiting view is asked again soon
    cache.read("kr", candles, _kst(date(2026, 11, 18), 7, 30))
    assert len(asked) == 4 * first


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


def test_the_sectors_api_needs_a_session(db_url):
    with _client(db_url, login=False) as client:
        assert client.get("/api/day-call/sectors?market=kr").status_code == 401
