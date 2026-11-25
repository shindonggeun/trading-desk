"""#577 session gaps: which count, when they fill, the odds of gaps like them - from fixed bars."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pytest

from desk import gap_fill
from desk.events_calendar import ET, Event


def _days(n: int, start: date = date(2026, 1, 5)) -> list[date]:
    out, d = [], start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _bar(day: date, o: float, h: float, low: float, c: float) -> dict:
    return {"at": f"{day.isoformat()}T05:00:00+00:00", "open": o, "high": h, "low": low, "close": c, "volume": 1.0}


def _flat(day: date, level: float = 100.0) -> dict:
    return _bar(day, level, level + 0.5, level - 0.5, level)


def series() -> list[dict]:
    """80 quiet days at 100 (ATR 1), an up gap of 1 ATR that fills on its third session, quiet at 100,
    a down gap of 0.7 ATR that never fills, then a gap too small to count."""
    days = _days(120)
    bars = [_flat(d) for d in days[:80]]
    bars += [_bar(days[80], 101.0, 101.5, 100.6, 101.2), _bar(days[81], 101.2, 101.6, 100.4, 101.0),
             _bar(days[82], 101.0, 101.1, 99.9, 100.0)]
    bars += [_flat(d) for d in days[83:90]]
    bars += [_bar(days[90], 99.3, 99.8, 98.8, 99.0)]
    bars += [_flat(d, 99.0) for d in days[91:95]]
    bars += [_bar(days[95], 99.05, 99.4, 98.7, 99.0)]   # 0.05 - under MIN_GAP_ATR
    bars += [_flat(d, 99.0) for d in days[96:100]]
    return bars


def test_gaps_are_measured_with_what_was_known_at_the_open():
    bars = series()
    gaps = gap_fill.measure(bars, "QQQ")
    assert [(g["direction"], g["day"]) for g in gaps] == [("up", "2026-04-27"), ("down", "2026-05-11")]
    up, down = gaps
    assert up["fill_price"] == 100.0 and up["gap_pct"] == 1.0 and up["filled_in"] == 3 and up["size"] in ("mid", "large")
    assert up["trend"] == "down" and up["retraced"] == 1.0   # flat closes are not above their averages
    assert down["filled_in"] is None and down["seen"] == 10 and 0 < down["retraced"] < 1
    # no look-ahead: what comes after a gap's open never changes how it was sized or placed
    later = gap_fill.measure(bars[:83] + [_flat(d, 140.0) for d in _days(120)[83:100]], "QQQ")[0]
    assert {k: later[k] for k in ("atr_multiple", "size", "trend", "cause")} == \
        {k: up[k] for k in ("atr_multiple", "size", "trend", "cause")}


def test_odds_count_a_young_gap_only_where_it_was_seen():
    g = lambda filled, seen: {"filled_in": filled, "seen": seen}
    rows = [g(1, 50), g(3, 50), g(None, 50), g(None, 2), g(2, 2)]
    o = gap_fill.odds(rows)
    assert o["n"] == 5 and o["p1"] == round(1 / 5, 3)
    assert o["p3"] == round(3 / 4, 3)          # the gap seen 2 sessions and still open cannot say about 3
    assert o["p20"] == round(3 / 4, 3) and o["median_sessions"] == 2 and o["few"]
    # still open after 2 sessions: of those, the share that filled by the 2 + k-th
    after = gap_fill.odds(rows, after=2)
    assert after["n"] == 2 and after["p1"] == 0.5 and after["p5"] == 0.5


def test_open_gaps_carry_the_odds_of_gaps_like_them_still_open_as_long():
    bars = series()
    gaps = gap_fill.measure(bars, "QQQ")
    opened = gap_fill.open_gaps(gaps, bars)
    assert [o["direction"] for o in opened] == ["down"]
    o = opened[0]
    assert o["fill_price"] == 100.0 and o["open_sessions"] == 10 and o["to_fill_pct"] == round((100 / 99 - 1) * 100, 2)
    assert o["odds"]["n"] == 0           # no other down gap of its size


EVENTS = [Event("fomc", "FOMC", datetime(2026, 4, 24, 14, 0, tzinfo=ET), 2, "fed"),
          Event("cpi", "CPI", datetime(2026, 5, 11, 8, 30, tzinfo=ET), 2, "bls"),
          Event("jolts", "JOLTS", datetime(2026, 6, 1, 10, 0, tzinfo=ET), 1, "bls")]


def test_causes_come_from_the_calendar_the_season_or_the_break():
    days = gap_fill.calendar_days(EVENTS)
    assert days == {date(2026, 4, 24): "fomc", date(2026, 5, 11): "macro"}
    assert gap_fill._cause(date(2026, 4, 27), date(2026, 4, 24), days) == "fomc"     # the session after
    assert gap_fill._cause(date(2026, 5, 11), date(2026, 5, 8), days) == "macro"
    assert gap_fill._cause(date(2026, 7, 20), date(2026, 7, 17), {}) == "earnings"
    assert gap_fill._cause(date(2026, 6, 22), date(2026, 6, 19), {}) == "weekend"
    assert gap_fill._cause(date(2026, 6, 24), date(2026, 6, 23), {}) == "other"


@pytest.mark.parametrize(("ticker", "symbol", "leverage"), [
    ("KOSPI200F", "069500.KS", None), ("SOXL", "SOXX", 3.0), ("KORU", "EWY", 3.0), ("NQ=F", "NQ=F", None),
    ("nvda", "NVDA", None)])
def test_what_a_ticker_is_read_on(ticker, symbol, leverage):
    b = gap_fill.basis_of(ticker)
    assert (b["symbol"], b["leverage"]) == (symbol, leverage)


def test_a_product_gets_the_fill_in_its_own_price_and_a_side_aware_warning():
    product = [_flat(d, 10.0) for d in _days(100)]

    def fetch(t):
        return {"SOXX": series(), "SOXL": product}[t]

    gap_fill._CACHE.clear()
    rep = gap_fill.read(fetch, "SOXL", lambda: EVENTS)
    assert rep["basis"]["symbol"] == "SOXX" and rep["gaps_counted"] == 2 and rep["since"] == "2026-01-05"
    g = rep["open"][0]
    assert g["product_move_pct"] == round(3 * g["to_fill_pct"], 2)
    assert g["product_fill_price"] == round(10.0 * (1 + 3 * (100 / 99 - 1)), 4)
    assert gap_fill.entry_warning(rep, "long") is None               # a down gap is no risk to a long
    w = gap_fill.entry_warning(rep, "short")
    assert w["direction"] == "down" and w["move_pct"] == g["product_move_pct"] and "갭 아래 매도" in w["text"]
    assert "SOXL" in gap_fill.line_ko(rep, g) and "표본 0 · 표본 적음" in gap_fill.line_ko(rep, g)
    note = gap_fill.prompt_note(rep)
    assert note.startswith("Session gaps on SOXX") and "the product at about" in note
    assert gap_fill.cached("soxl") is rep and gap_fill.read(fetch, "SOXL") is rep   # one fetch per TTL


def test_too_few_bars_is_an_error_and_nothing_open_is_no_note():
    with pytest.raises(ValueError):
        gap_fill.report(lambda t: series()[:50], "QQQ")
    assert gap_fill.prompt_note(None) is None
    quiet = gap_fill.report(lambda t: [_flat(d) for d in _days(100)], "QQQ")
    assert quiet["open"] == [] and quiet["gaps_counted"] == 0 and gap_fill.prompt_note(quiet) is None
    assert quiet["table"]["all"] == {"n": 0, "p1": None, "p3": None, "p5": None, "p20": None,
                                     "median_sessions": None, "filled_share": None, "few": True}


def test_the_api_answers_the_odds(db_url, monkeypatch):
    from tests.test_api import _client

    monkeypatch.setattr(gap_fill, "yahoo_fetch", lambda _candles: lambda t: series())
    gap_fill._CACHE.clear()
    with _client(db_url) as client:
        got = client.get("/api/gaps", params={"ticker": "qqq"})
        assert got.status_code == 200
        body = got.json()
        assert body["ticker"] == "QQQ" and body["open"][0]["direction"] == "down"
        assert set(body["table"]) == {"all", "direction", "size", "cause", "trend", "direction_size"}
        assert client.get("/api/gaps", params={"ticker": "bad ticker!"}).status_code == 422
        # the entry summary carries the field (a scripted desk does not fetch the odds)
        entry = client.get("/api/entry-now", params={"ticker": "NVDA"})
        if entry.status_code == 200:
            assert entry.json()["gap_warning"] is None
    gap_fill._CACHE.clear()


def test_a_futures_session_dated_the_day_before_is_the_next_weekdays():
    """#568: after 18:00 New York the new session's bar carries the previous date until midnight."""
    assert gap_fill._session_day(date(2026, 11, 17), date(2026, 11, 17)) == date(2026, 11, 18)
    assert gap_fill._session_day(date(2026, 11, 13), date(2026, 11, 13)) == date(2026, 11, 16)   # Friday -> Monday
    assert gap_fill._session_day(date(2026, 11, 18), date(2026, 11, 17)) == date(2026, 11, 18)
    bars = series()
    bars[-1] = {**bars[-1], "at": bars[-2]["at"], "open": 101.0, "high": 101.2, "low": 100.8}   # dated like the one before
    last = gap_fill.measure(bars, "NQ=F")[-1]
    assert last["day"] == "2026-05-22" and last["cause"] == "other"   # Thursday's date: Friday's session


def _evening_row() -> list[dict]:
    """Thursday 2026-05-21's bar replaced by the next session's live row (Yahoo dates it Thursday from 18:00
    New York, yfinance drops the Thursday session that ended at 17:00): it opens 1.5 ATR under the close
    two sessions back - a whole day's fall, not a gap."""
    bars = series()[:-1]
    assert bars[-1]["at"].startswith("2026-05-21") and bars[-2]["close"] == 99.0
    bars[-1] = _bar(date(2026, 5, 21), 97.5, 97.8, 97.0, 97.2)
    return bars


@pytest.mark.parametrize(("ticker", "now", "counted"), [
    ("NQ=F", datetime(2026, 5, 21, 20, 0, tzinfo=ET), False),    # Thursday evening: the live row opens no gap
    ("NQ=F", datetime(2026, 5, 21, 18, 0, tzinfo=ET), False),    # from the reopen
    ("NQ=F", datetime(2026, 5, 21, 17, 30, tzinfo=ET), True),    # the hour the market is shut: Thursday's own bar
    ("NQ=F", datetime(2026, 5, 22, 0, 30, tzinfo=ET), True),     # after midnight the live row is dated Friday
    ("QQQ", datetime(2026, 5, 21, 20, 0, tzinfo=ET), True),      # an ETF's daily bar is its own session
])
def test_the_evening_live_row_of_a_futures_session_opens_no_gap(ticker, now, counted):
    """#741: 2026-11-19 23:54 ET showed NQ=F / ES=F down gaps of -2.16% / -2.01% that were the day's moves."""
    rep = gap_fill.report(lambda t: _evening_row(), ticker, now=now)
    days = [(g["day"], g["direction"]) for g in rep["open"]]
    assert (("2026-05-21", "down") in days) is counted
    assert ("2026-05-11", "down") in days    # the older open gap stays, and the live row still trades
    assert rep["last_close"] == 97.2


def test_a_sunday_evening_row_still_opens_the_weekend_gap():
    """No session is dated Sunday, so nothing was dropped: Friday's close to Sunday's open is a real gap."""
    bars = series() + [_bar(date(2026, 5, 24), 97.5, 97.8, 97.0, 97.2)]
    now = datetime(2026, 5, 24, 20, 0, tzinfo=ET)
    assert not gap_fill.evening_session(bars, "ES=F", now)
    gaps = gap_fill.report(lambda t: bars, "ES=F", now=now)["open"]
    assert ("2026-05-24", "down") in [(g["day"], g["direction"]) for g in gaps]


def test_futures_say_their_daily_gaps_are_mostly_the_weekends():
    assert "주말 · 휴장 뒤" in gap_fill.basis_of("NQ=F")["note"] and gap_fill.basis_of("SPY")["note"] is None


def test_cold_asks_for_one_ticker_at_once_read_its_bars_once():
    """#993 기동 직후 prewarm · 팁 화면 · run 노트가 같은 종목을 동시에 물으면 8년 일봉을 각자 읽었다."""
    gap_fill._CACHE.clear()
    reads, release = [], threading.Event()

    def fetch(symbol):
        reads.append(symbol)
        assert release.wait(5)                      # the first read is slow; the others ask meanwhile
        return series()

    with ThreadPoolExecutor(max_workers=4) as pool:
        asks = [pool.submit(gap_fill.read, fetch, "spy", ahead=a) for a in (300.0, 0.0, 0.0, 300.0)]
        deadline = time.monotonic() + 5
        while not reads and time.monotonic() < deadline:
            time.sleep(0.01)
        time.sleep(0.2)                             # the other three arrive (at the ticker's lock) meanwhile
        release.set()
        reports = [a.result(timeout=5) for a in asks]
    assert reads == ["SPY"] and all(r is reports[0] for r in reports)
    assert len(gap_fill._READS) == 0                # nobody left holding or waiting


def test_a_failed_read_is_held_a_moment_then_read_again():
    gap_fill._CACHE.clear()
    gap_fill._failed.clear()
    reads, clock = [], [1000.0]

    def fetch(symbol):
        reads.append(symbol)
        if len(reads) == 1:
            raise TimeoutError()
        return series()

    try:
        with pytest.raises(TimeoutError):
            gap_fill.read(fetch, "DIA", clock=lambda: clock[0])
        clock[0] += gap_fill.FAIL_HOLD_SEC - 1
        with pytest.raises(gap_fill.RecentFailure, match="TimeoutError"):
            gap_fill.read(fetch, "DIA", clock=lambda: clock[0])      # a moment later: not read again
        assert reads == ["DIA"]
        clock[0] += 1
        assert gap_fill.read(fetch, "DIA", clock=lambda: clock[0])["gaps_counted"] == 2 and reads == ["DIA", "DIA"]
        assert gap_fill._failed == {}                                  # a good read forgets the failure
    finally:
        gap_fill._failed.clear()


def test_askers_queued_behind_a_failed_read_fail_with_it():
    """#993 리뷰: 실패한 첫 읽기 뒤에 줄 선 요청이 8년 일봉을 하나씩 다시 읽지 않는다."""
    gap_fill._CACHE.clear()
    gap_fill._failed.clear()
    reads, release = [], threading.Event()

    def fetch(symbol):
        reads.append(symbol)
        assert release.wait(5)
        raise TimeoutError()

    try:
        with ThreadPoolExecutor(max_workers=3) as pool:
            asks = [pool.submit(gap_fill.read, fetch, "IWM") for _ in range(3)]
            deadline = time.monotonic() + 5
            while not reads and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.2)                         # the other two wait at the ticker's lock meanwhile
            release.set()
            failures = sorted(type(a.exception(timeout=5)).__name__ for a in asks)
        assert reads == ["IWM"] and failures == ["RecentFailure", "RecentFailure", "TimeoutError"]
    finally:
        gap_fill._failed.clear()
