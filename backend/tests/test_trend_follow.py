"""#977 the daily trend-following read on fixed bars: each stage, the evidence and its guards."""

from datetime import UTC, date, datetime, time, timedelta

import pytest

from desk import market_calendar, trend_follow

NOW = datetime(2026, 11, 24, 23, tzinfo=UTC)          # well after every bar below closed
BASE = [100, 101, 102, 101, 100, 99, 98, 99]          # one triangle wave: swing highs 102.5, swing lows 97.5


def _days(n: int, last: date, market: str = "us") -> list[date]:
    out, day = [], last
    while len(out) < n:
        if day.weekday() < 5:
            out.append(day)
        day -= timedelta(days=1)
    return out[::-1]


def _at(day: date, market: str) -> str:
    zone = market_calendar.KST if market == "kr" else market_calendar.ET
    return datetime.combine(day, time(), zone).astimezone(UTC).isoformat()


def bars_of(closes: list[float], market: str = "us", last: date = date(2026, 11, 24), shapes=None) -> list[dict]:
    """Daily bars closing at ``closes``: open at the previous close, a half point of wick each side;
    ``shapes`` {index: (open, high, low, close)} sets a bar outright."""
    days = _days(len(closes), last, market)
    out, prev = [], closes[0]
    for i, (day, c) in enumerate(zip(days, closes, strict=True)):
        o, h, lo = prev, max(prev, c) + .5, min(prev, c) - .5
        if shapes and i in shapes:
            o, h, lo, c = shapes[i]
        out.append({"at": _at(day, market), "open": o, "high": h, "low": lo, "close": c, "volume": 1000.0})
        prev = c
    return out


def base(waves: int = 8) -> list[float]:
    return BASE * waves


def read(closes, ticker="SOXX", shapes=None, **kw):
    return trend_follow.build(ticker, {"bars": bars_of(closes, shapes=shapes), "source": "yahoo"}, NOW, **kw)


def test_first_strong_up_bar_after_a_decline_is_a_first_rebound():
    closes = base() + [97 - i for i in range(15)]                    # a fall to 83
    closes.append(85)
    got = read(closes, shapes={len(closes) - 1: (83, 85.2, 82.8, 85)})
    assert got["status"] == "confirmed"
    assert got["stage"]["state"] == "first_rebound" and got["stage"]["signal"] == "first_up_bar"
    assert got["stage"]["confirmed_at"] == got["last_bar"]["at"]
    assert got["trend"]["state"] == "down"
    assert got["key_prices"]["break"]["price"] == 102.5                # the swing highs over it, untouched
    assert got["key_prices"]["break"]["ref"]["kind"] == "swing"


def test_close_under_support_then_back_over_it_is_a_reclaim():
    closes = base() + [99, 96.5, 98.6]
    got = read(closes)
    assert got["stage"]["state"] == "first_rebound" and got["stage"]["signal"] == "support_reclaim"
    hold = got["key_prices"]["hold"]
    assert hold["price"] == 97.5 and hold["label"].startswith("회복한")
    assert hold["distance_pct"] == round((97.5 / 98.6 - 1) * 100, 2)


def test_a_wick_into_resistance_closing_under_it_waits():
    closes = base() + [100, 101, 101.5]
    got = read(closes, shapes={len(closes) - 1: (101, 102.4, 100.8, 101.5)})
    assert got["stage"]["state"] == "awaiting_confirmation" and got["stage"]["signal"] == "wick_touch"
    confirm = next(c for c in got["conditions"] if c["id"] == "close_above_resistance")
    assert got["key_prices"]["break"]["price"] == 102.5
    assert 102.5 < confirm["price"] < 104 and confirm["met"] is False     # the line plus 0.3 ATR


def test_a_finished_close_over_resistance_is_a_breakout():
    closes = base() + [100, 101, 103.5, 104]
    got = read(closes)
    stage = got["stage"]
    assert stage["state"] == "breakout_confirmed" and stage["signal"] == "close_above"
    assert stage["confirmed_at"] == bars_of(closes)[-2]["at"]           # the breaking bar, not the last one
    assert got["key_prices"]["hold"]["price"] == 102.5 and got["key_prices"]["hold"]["label"].startswith("돌파한")
    assert next(c for c in got["conditions"] if c["id"] == "hold_after_break")["met"] is True


def test_a_close_back_under_the_broken_line_fails_the_breakout():
    closes = base() + [100, 101, 103.5, 101.5]
    got = read(closes)
    assert got["stage"]["state"] == "failed_breakout" and got["stage"]["signal"] == "close_back_below"
    assert got["stage"]["confirmed_at"] == got["last_bar"]["at"]
    assert got["key_prices"]["break"]["price"] == 102.5
    assert next(c for c in got["conditions"] if c["id"] == "close_back_below")["met"] is True


def test_a_close_only_just_over_the_line_is_no_breakout():
    closes = base() + [100, 101, 102.9]                               # under 102.5 + 0.3 ATR
    got = read(closes)
    assert got["stage"]["state"] == "awaiting_confirmation" and got["stage"]["signal"] is None
    assert got["key_prices"]["break"]["price"] == 102.5                # still the line to break
    confirm = next(c for c in got["conditions"] if c["id"] == "close_above_resistance")
    assert confirm["price"] > 102.9 and confirm["met"] is False      # the line plus 0.3 ATR


def test_a_next_close_under_the_broken_line_does_not_hold_it():
    closes = base() + [100, 101, 103.5, 102.3]                        # under 102.5, above the fail level
    got = read(closes)
    assert got["stage"]["state"] == "breakout_confirmed"
    assert next(c for c in got["conditions"] if c["id"] == "hold_after_break")["met"] is False
    just = read(base() + [100, 101, 103.5])                           # no bar after the break yet
    assert next(c for c in just["conditions"] if c["id"] == "hold_after_break")["met"] is None


def test_a_strong_bar_in_a_rise_is_no_first_rebound():
    closes = [100 + .3 * i for i in range(80)]
    closes.append(closes[-1] + 2.5)
    got = read(closes, shapes={80: (closes[-2], closes[-1] + .1, closes[-2] - .1, closes[-1])})
    assert got["stage"]["signal"] != "first_up_bar"


def test_the_frame_channel_stays_while_the_last_bar_trades():
    closes = base() + [100, 101]
    bars = bars_of(closes)
    i, j, live = len(bars) - 30, len(bars) - 14, len(bars) - 1
    channel = {"id": f"1d-channel-{bars[i]['at']}-{bars[j]['at']}", "direction": "flat", "base": "upper",
               "width_pct": 5.06,                                     # drawn to the bar still trading
               "upper": {"start": {"at": bars[i]["at"], "price": 102.5}, "end": {"at": bars[live]["at"], "price": 102.5}},
               "lower": {"start": {"at": bars[i]["at"], "price": 97.5}, "end": {"at": bars[live]["at"], "price": 97.5}}}
    day = datetime.fromisoformat(bars[-1]["at"]).astimezone(market_calendar.ET).date()
    during = datetime.combine(day, time(12), market_calendar.ET).astimezone(UTC)
    got = trend_follow.build("SOXX", {"bars": bars, "source": "yahoo"}, during, frame={"channels": [channel]})
    assert got["status"] == "in_progress"
    assert next(e for e in got["evidence"] if e["kind"] == "channel_quarter")["status"] == "confirmed"


@pytest.mark.parametrize(("ticker", "stamp", "at", "done"), [
    ("005930.KS", "2026-11-23T15:00:00+00:00", "2026-11-24T06:49:00+00:00", False),   # KRX 15:49 KST
    ("005930.KS", "2026-11-23T15:00:00+00:00", "2026-11-24T06:51:00+00:00", True),
    ("NQ=F", "2026-11-24T05:00:00+00:00", "2026-11-24T22:05:00+00:00", False),       # 17:05 ET
    ("NQ=F", "2026-11-24T05:00:00+00:00", "2026-11-24T22:11:00+00:00", True),
    ("NQ=F", "2026-11-24T05:00:00+00:00", "2026-11-25T00:00:00+00:00", False),       # Tue 19:00 ET: a new session's row
    ("BTC-USDT", "2026-11-24T00:00:00+00:00", "2026-11-24T22:00:00+00:00", False),   # its day ends at UTC midnight
    ("BTC-USDT", "2026-11-24T00:00:00+00:00", "2026-11-25T00:00:00+00:00", True),
])
def test_a_daily_bar_is_final_once_its_session_closes(ticker, stamp, at, done):
    assert trend_follow.final(ticker, {"at": stamp}, datetime.fromisoformat(at)) is done


def test_a_market_without_a_known_session_is_not_read():
    got = read(base() + [100, 101], ticker="7203.T")
    assert got["status"] == "unavailable" and got["reason"] == "unsupported_market" and got["instrument"] is None
    assert trend_follow.instrument("KRW=X", "yahoo")["unit"] == "KRW"
    assert trend_follow.instrument("ETH-BTC", "yahoo")["unit"] == "BTC"


def test_the_bar_still_trading_is_left_out():
    closes = base() + [100, 101, 103.5]
    bars = bars_of(closes)
    open_day = datetime.fromisoformat(bars[-1]["at"]).astimezone(market_calendar.ET).date()
    during = datetime.combine(open_day, time(12), market_calendar.ET).astimezone(UTC)
    got = trend_follow.build("SOXX", {"bars": bars, "source": "yahoo"}, during)
    assert got["status"] == "in_progress"
    assert got["as_of"] == bars[-2]["at"]
    assert got["stage"]["state"] != "breakout_confirmed"              # the breaking close is not final yet


def test_a_swing_low_is_not_used_before_it_is_confirmed():
    closes = base() + [99, 97, 95, 96]                                # 95's bar could become a swing low
    got = read(closes)
    div = next(e for e in got["evidence"] if e["kind"] == "divergence")
    bars = bars_of(closes)
    assert all(a["at"] != bars[-2]["at"] for a in div["anchors"])
    assert all(a["at"] <= bars[-1 - trend_follow.SWING_K]["at"] for a in div["anchors"])


def test_lower_price_lows_with_higher_rsi_lows_count_in_a_row():
    drop, bounce = [98, 96, 94, 92, 90], [91, 92.5, 94, 95, 95.5]
    drift = [95 - .55 * (i + 1) for i in range(10)]                    # a slower fall to a lower low
    closes = base(6) + drop + bounce + drift + [drift[-1] + 1 + i for i in range(4)]
    div = next(e for e in read(closes)["evidence"] if e["kind"] == "divergence")
    assert div["status"] == "confirmed" and div["meta"]["indicator"] == "rsi14" and div["meta"]["pivot_k"] == 3
    assert div["meta"]["comparisons"] == 1 and len(div["anchors"]) == 2
    first, second = div["anchors"]
    assert second["price"] < first["price"] and second["rsi"] > first["rsi"]
    flat = next(e for e in read(base(8) + drop + [91, 92, 93, 94, 95, 96])["evidence"] if e["kind"] == "divergence")
    assert flat["status"] == "pending" and flat["meta"]["comparisons"] == 0 and flat["confirmed_at"] is None


def test_extensions_differ_on_a_linear_and_a_log_scale():
    assert trend_follow.extension(100, 200, 1.618, "linear") == pytest.approx(261.8)
    assert trend_follow.extension(100, 200, 1.618, "log") == pytest.approx(100 * 2 ** 1.618)
    got = read(base() + [100, 101])
    fib = next(e for e in got["evidence"] if e["kind"] == "fib_extension")
    assert fib["meta"]["scale"] == "linear" and fib["meta"]["ratios"] == "1.414,1.618,2.618"
    low, high = fib["meta"]["wave_low"], fib["meta"]["wave_high"]
    assert fib["prices"] == [round(trend_follow.extension(low, high, r, "linear"), 2) for r in (1.414, 1.618, 2.618)]
    assert fib["meta"]["log_prices"] != ",".join(str(p) for p in fib["prices"])


def test_the_quarter_lines_of_the_swing_range_and_the_channel():
    got = read(base() + [100, 101])
    q = next(e for e in got["evidence"] if e["kind"] == "quarter_level")
    assert q["status"] == "confirmed" and q["meta"]["scale"] == "linear"
    assert q["prices"] == [102.5 - .25 * 5, 97.5 + .25 * 5]
    ch = next(e for e in got["evidence"] if e["kind"] == "channel_quarter")
    assert ch["status"] == "insufficient" and ch["prices"] == []          # no frame given: nothing drawn


def test_channel_quarter_lines_are_log_lines_of_the_frame_channel():
    closes = base() + [100, 101]
    bars = bars_of(closes)
    i, j, last = len(bars) - 30, len(bars) - 14, len(bars) - 1
    channel = {"id": f"1d-channel-{bars[i]['at']}-{bars[j]['at']}", "direction": "flat", "base": "upper",
               "width_pct": 5.06,
               "upper": {"start": {"at": bars[i]["at"], "price": 102.5}, "end": {"at": bars[last]["at"], "price": 102.5}},
               "lower": {"start": {"at": bars[i]["at"], "price": 97.5}, "end": {"at": bars[last]["at"], "price": 97.5}}}
    got = trend_follow.build("SOXX", {"bars": bars, "source": "yahoo"}, NOW, frame={"channels": [channel]})
    ch = next(e for e in got["evidence"] if e["kind"] == "channel_quarter")
    assert ch["status"] == "confirmed" and ch["meta"]["scale"] == "log"
    import math
    q = math.exp(math.log(97.5) + .25 * (math.log(102.5) - math.log(97.5)))
    assert ch["prices"][2] == round(q, 2) and ch["prices"][2] != 97.5 + .25 * 5
    assert [a["at"] for a in ch["anchors"]] == [bars[i]["at"], bars[j]["at"]]


def test_stochastic_settings_are_stated_and_change_the_read():
    closes = base() + [97 - i for i in range(10)] + [88 + 2 * i for i in range(8)]
    bars = bars_of(closes)
    chart = trend_follow.stochastic(bars)
    fast = trend_follow.stochastic(bars, k=5, smooth=1, d=3)
    assert (chart["meta"]["k"], chart["meta"]["smooth"], chart["meta"]["d"]) == (14, 1, 3)
    assert fast["meta"]["k"] == 5
    assert chart["status"] == fast["status"] == "confirmed"
    assert chart["meta"]["bars_to_80"] != fast["meta"]["bars_to_80"]
    assert chart["meta"]["bars_to_80"] <= trend_follow.STOCH_MAX_BARS
    still = trend_follow.stochastic(bars[:-6])                         # the climb not reached 80 yet
    assert still["status"] == "pending" and still["confirmed_at"] is None


def test_products_are_never_one_another():
    closes = base() + [100, 101]
    perp = trend_follow.build("SOXLUSDT", {"bars": bars_of(closes), "source": "yahoo"}, NOW)
    assert perp["status"] == "unavailable" and perp["reason"] == "product_mismatch" and perp["instrument"] is None
    soxl = read(closes, ticker="SOXL")["instrument"]
    assert (soxl["kind"], soxl["unit"], soxl["session"], soxl["rollover"]) == ("etf", "USD", "us_regular", None)
    nq = read(closes, ticker="NQ=F")["instrument"]
    assert (nq["kind"], nq["unit"], nq["rollover"]) == ("continuous_future", "index_points", "yahoo_front_month")
    assert "NQ1!" in nq["note"]
    assert read(closes, ticker="SOXX", peers={"^KS11": bars_of(closes)})["lead"] is None


def test_missing_or_short_bars_say_so():
    assert trend_follow.build("SOXX", None, NOW)["reason"] == "missing_daily_source"
    short = trend_follow.build("SOXX", {"bars": bars_of(base(2)), "source": "yahoo"}, NOW)
    assert short["reason"] == "insufficient_bars" and short["evidence"] == []


def _kr(closes, last=date(2026, 11, 24)):
    return bars_of(closes, "kr", last)


def test_lead_compares_the_same_breakout_rule_on_the_same_session():
    flat = base()
    kospi = _kr(flat + [100, 101, 101.5, 103.5, 104])                  # breaks on the 4th last-but-one bar
    hynix = _kr(flat + [100, 103.5, 104, 104.5, 105])                   # two bars earlier
    samsung = _kr(flat + [100, 101, 101, 101, 101])                     # not yet
    got = trend_follow.build("^KS11", {"bars": kospi, "source": "yahoo"}, NOW,
                             peers={"000660.KS": hynix, "005930.KS": samsung})
    states = {i["ticker"]: i["state"] for i in got["lead"]["items"]}
    assert states == {"000660.KS": "led", "005930.KS": "waiting"}
    assert got["lead"]["benchmark"] == "^KS11" and got["lead"]["benchmark_at"] == kospi[-2]["at"]
    item = next(e for e in got["evidence"] if e["kind"] == "lead")
    assert "돌파 시점 비교" in item["detail"]


def test_lead_lines_up_a_feed_missing_its_last_bar():
    flat = base()
    hynix = _kr(flat + [100, 101, 101.5, 103.5, 104, 104.5])                     # breaks on the 11-20 bar
    kospi = _kr(flat + [100, 101, 101.5, 103.5, 104], last=date(2026, 11, 23))   # its 11-24 bar missing
    samsung = _kr(flat + [100, 103.5, 104, 104.5, 105, 105])
    got = trend_follow.build("000660.KS", {"bars": hynix, "source": "toss"}, NOW,
                             peers={"^KS11": kospi, "005930.KS": samsung})
    states = {i["ticker"]: (i["state"], i["at"]) for i in got["lead"]["items"]}
    assert states["000660.KS"] == ("same", hynix[-3]["at"])                     # KOSPI's break day too
    assert states["005930.KS"] == ("led", samsung[-5]["at"])


def test_lead_without_a_matching_session_is_not_comparable():
    flat = base()
    kospi = _kr(flat + [100, 101, 101.5, 103.5, 104])
    stale = _kr(flat + [100, 103.5, 104, 104.5], last=date(2026, 11, 18))       # four sessions behind
    got = trend_follow.build("000660.KS", {"bars": stale, "source": "toss"}, NOW,
                             peers={"^KS11": kospi, "005930.KS": None})
    assert got["lead"]["items"] == [{"ticker": "000660.KS", "state": "not_comparable", "at": None},
                                    {"ticker": "005930.KS", "state": "not_comparable", "at": None}]
    assert next(e for e in got["evidence"] if e["kind"] == "lead")["status"] == "insufficient"


def test_replay_reads_each_day_on_its_own_past_and_counts_failures():
    from desk import trend_follow_replay as rp

    held = bars_of(base() + [100, 101, 103.5, 104, 104.5] + [105] * 9)
    failed = bars_of(base() + [100, 101, 103.5, 104, 101] + [100] * 9)
    for bars, fail in ((held, None), (failed, held[-10]["at"][:10])):
        rows = [r for r in rp.replay("SOXX", bars) if r["state"] == "breakout_confirmed"]
        assert [r["day"] for r in rows] == [held[-12]["at"][:10]]       # the breaking day, read on its own bars
        assert rows[0]["line"] == 102.5
        assert (rows[0]["failed_on"] is None) == (fail is None)
    s = rp.summary(rp.replay("SOXX", failed))
    assert s["breakout"]["n"] == 1 and s["breakout"]["failed"] == 1
