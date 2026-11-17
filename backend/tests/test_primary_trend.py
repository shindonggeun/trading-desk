"""#460: the indices' primary trend (bull / bear / range / turning), its evidence, record and guide."""

import math
from datetime import date, timedelta

from desk import primary_trend as pt
from tests.test_api import _client


def _rows(closes: list[float]) -> list[dict]:
    start = date(2015, 1, 1)
    return [{"day": (start + timedelta(days=i)).isoformat(), "open": c, "high": c * 1.005, "low": c * 0.995,
             "close": c} for i, c in enumerate(closes)]


def _wave(i: int, amp: float = 0.02, period: int = 20) -> float:
    return 1 + amp * math.sin(2 * math.pi * i / period)


def rising(n: int = 420) -> list[float]:
    return [100 * 1.0012 ** i * _wave(i) for i in range(n)]


def falling(n: int = 420) -> list[float]:
    top = [100 * 1.0010 ** i * _wave(i) for i in range(260)]
    drop = [top[-1] * 0.9965 ** (i + 1) * _wave(260 + i) for i in range(n - 260)]   # about -40%
    return top + drop


def sideways(n: int = 420) -> list[float]:
    return [100 * _wave(i, 0.04, 30) for i in range(n)]


def bottom_turn() -> list[float]:
    fall = [100 * 0.9985 ** i * _wave(i) for i in range(330)]
    return fall + [fall[-1] * 1.01 ** (i + 1) * _wave(330 + i) for i in range(20)]   # over a still-falling line


def test_a_steady_rise_is_a_bull_market_with_its_evidence():
    v = pt.market_view("^NDX", "nasdaq100", "나스닥100", _rows(rising()))
    assert v["state"] == "bull" and v["state_ko"] == "대세 상승장" and v["score"] >= 3
    ev = {e["id"]: e for e in v["evidence"]}
    assert ev["above_200"]["value"] > 0 and "200일선(장기 평균) 위" in ev["above_200"]["text"]
    assert ev["slope_200"]["vote"] == 1 and ev["cross_50_200"]["vote"] == 1
    assert ev["structure"]["vote"] == 1 and "HH" in ev["structure"]["text"]
    assert ev["drawdown"]["value"] > pt.CORRECTION and v["since"] <= v["as_of"]


def test_a_fall_past_twenty_percent_shows_as_a_bear_market_within_days():
    days = pt.daily_states(_rows(falling()))
    deep = [d for d in days if d["raw"] == "bear" and d["drawdown"] <= pt.BEAR_DD]
    assert deep and all(d["needs"] == pt.HOLD_BEAR_DD for d in deep)
    assert all(d["state"] == "bear" for d in deep if d["run"] >= pt.HOLD_BEAR_DD)
    shallow = [d for d in days if d["raw"] == "bear" and d["drawdown"] > pt.BEAR_DD]
    assert all(d["needs"] == pt.HOLD for d in shallow)          # a bear by the votes alone: the usual hold


def test_a_fall_past_twenty_percent_is_a_bear_market():
    v = pt.market_view("^KS11", "kospi", "코스피", _rows(falling()))
    assert v["state"] == "bear"
    ev = {e["id"]: e for e in v["evidence"]}
    assert ev["drawdown"]["value"] <= pt.BEAR_DD and "약세장" in ev["drawdown"]["text"]
    assert ev["cross_50_200"]["vote"] == -1 and "데드크로스" in ev["cross_50_200"]["text"]


def test_a_flat_market_is_a_range():
    v = pt.market_view("^GSPC", "sp500", "S&P500", _rows(sideways()))
    assert v["state"] == "range"
    assert {e["id"]: e for e in v["evidence"]}["slope_200"]["vote"] == 0


def test_a_rebound_over_a_falling_200_day_line_is_turning():
    days = pt.daily_states(_rows(bottom_turn()))
    last = days[-1]
    assert last["votes"]["above"] == 1 and last["votes"]["slope"] == -1
    assert last["raw"] == "turning"
    v = pt.market_view("^KS11", "kospi", "코스피", _rows(bottom_turn()))
    # eight sessions into the turn: still the bear shown, the turn said as pending
    assert v["state"] == "bear" and v["pending"] == {"state": "turning", "state_ko": "전환 중", "sessions": 8,
                                                     "needs": pt.HOLD}
    assert v["since_known"] is False                       # bear since the first judged day: began before it


def test_one_days_swing_does_not_flip_the_state():
    closes = rising()
    closes[-1] = closes[-2] * 0.80                     # one bad day under the 200-day line
    days = pt.daily_states(_rows(closes))
    assert days[-1]["raw"] != "bull" and days[-1]["state"] == "bull"
    v = pt.market_view("^NDX", "nasdaq100", "나스닥100", _rows(closes))
    assert v["state"] == "bull" and v["pending"]["sessions"] == 1 and v["pending"]["needs"] == pt.HOLD


def test_a_new_state_shows_once_it_held_its_sessions_and_dates_from_their_first():
    seq = ["bull"] * 5 + ["turning"] * (pt.HOLD - 1) + ["bull"] + ["turning"] * pt.HOLD
    days = [f"d{i:03d}" for i in range(len(seq))]
    out = pt.hysteresis(seq, days)
    assert out[5 + pt.HOLD - 2] == ("bull", "d000", pt.HOLD - 1)        # HOLD-1 sessions of turning: not yet
    assert out[5 + pt.HOLD - 1] == ("bull", "d000", 1)                  # broken by one bull day: counts again
    assert out[-1] == ("turning", days[-pt.HOLD], pt.HOLD)              # HOLD after it: from their first
    assert out[-2][0] == "bull"
    closes = rising() + [rising()[-1] * 0.70] * (pt.HOLD + 5)   # a held break on real votes
    days = pt.daily_states(_rows(closes))
    assert days[-1]["state"] != "bull" and days[-1]["state"] == days[-1]["raw"]
    assert days[-1]["since"] > days[-pt.HOLD - 6]["day"]


def test_mixed_holds_switch_on_the_days_own_hold():
    days = [f"d{i}" for i in range(6)]
    raws = ["bull"] * 3 + ["bear"] * 3
    assert pt.hysteresis(raws, days, [15] * 6)[-1] == ("bull", "d0", 3)
    assert pt.hysteresis(raws, days, [15, 15, 15, 3, 3, 3])[-1] == ("bear", "d3", 3)
    assert pt.hysteresis(raws, days, [15, 15, 15, 3, 3, 3])[-2][0] == "bull"


def test_a_day_is_judged_on_the_bars_up_to_it_only():
    import random

    rng = random.Random(7)
    closes, c = [], 100.0
    for _ in range(900):
        c *= 1 + rng.gauss(0.0003, 0.015)
        closes.append(c)
    rows = _rows(closes)
    full = pt.daily_states(rows)
    part = pt.daily_states(rows[:600])
    keys = ("day", "votes", "drawdown", "raw", "state", "since", "run")
    assert [{k: d[k] for k in keys} for d in part] == [{k: d[k] for k in keys} for d in full[:len(part)]]


def test_the_record_is_the_exact_forward_return_per_state():
    days = [{"i": k, "day": f"d{k:03d}", "close": 100 * 1.01 ** k, "state": "bull" if k < 40 else "range"}
            for k in range(70)]
    rec = pt.record(days)
    bull = rec["states"]["bull"]["20"]
    assert bull["n"] == 40 and bull["median"] == round((1.01 ** 20 - 1) * 100, 2) and bull["win"] == 100.0
    assert bull["p10"] == bull["median"] and bull["all_median"] == bull["median"]
    assert rec["states"]["range"]["20"] is None              # 10 range days with a 20-session future: too few
    assert rec["states"]["bull"]["60"] is None and rec["since"] == "d000"


def test_the_cache_answers_at_once_while_another_caller_fetches():
    c = pt.Cache(lambda symbols: {"^NDX": _rows(rising())})
    c._lock.acquire()                                        # another caller is fetching
    try:
        assert c.view() is None                              # nothing yet: at once, not after the fetch
    finally:
        c._lock.release()
    rows = c.view()
    c._lock.acquire()
    try:
        assert c.view() == rows                               # the last rows while it fetches again
    finally:
        c._lock.release()


def test_too_little_history_has_no_row_and_the_view_skips_it():
    assert pt.market_view("^SOX", "sox", "필라델피아 반도체", _rows(rising(150))) is None
    out = pt.view({"^NDX": _rows(rising()), "^SOX": _rows(rising(150))})
    assert [r["market"] for r in out] == ["nasdaq100"]


def test_the_record_compares_each_state_with_all_days():
    rec = pt.market_view("^NDX", "nasdaq100", "나스닥100", _rows(rising(900)))["record"]
    bull = rec["states"]["bull"]["20"]
    assert bull["n"] >= 20 and 0 <= bull["win"] <= 100 and bull["all_median"] is not None
    assert rec["states"]["bear"]["20"] is None              # too few bear days: not shown as a record
    assert set(rec["states"]) == set(pt.STATES)
    assert bull["p10"] <= bull["median"] and bull["all_p10"] is not None
    note = pt.record_note("나스닥100", "bull", rec)
    assert "대세 상승장" in note and "60거래일" in note and "예측하지 않는다" in note
    assert pt.record_note("나스닥100", "bear", rec) is None  # no record for it: no sentence


def test_the_guide_has_every_state_in_plain_korean():
    g = pt.guide()
    assert set(g) == set(pt.STATES)
    for state, row in g.items():
        assert row["state_ko"] == pt.STATE_KO[state] and row["do"] and row["dont"] and row["size"] and row["stops"]
        assert len(row["prefer_ko"]) == len(row["prefer"]) and len(row["avoid_ko"]) == len(row["avoid"])
        assert row["prefer"] == [] and row["basis"] == pt.GUIDE_BASIS   # no setup recommended without a record
        for line in row["do"] + row["dont"]:                           # a backtest is named with its source
            assert "백테스트" not in line or "종목 단위" in line


def test_the_cache_keeps_a_full_answer_and_retries_a_short_one_soon():
    now = [0.0]
    calls = []

    def fetch(symbols):
        calls.append(symbols)
        return {"^NDX": _rows(rising())} if len(calls) == 1 else {s: _rows(rising()) for s in symbols}

    c = pt.Cache(fetch, ttl_sec=3600, fail_ttl_sec=60, clock=lambda: now[0])
    assert [r["market"] for r in c.view()] == ["nasdaq100"]
    now[0] = 30
    assert len(c.view()) == 1 and len(calls) == 1          # a short answer is kept for the failure ttl
    now[0] = 61
    assert len(c.view()) == len(pt.MARKETS) and len(calls) == 2
    now[0] = 3000
    c.view()
    assert len(calls) == 2                                  # a full answer for the ttl


def test_a_failed_fetch_is_an_empty_list():
    c = pt.Cache(lambda symbols: (_ for _ in ()).throw(RuntimeError("yahoo down")))
    assert c.view() == []


def test_the_tips_api_adds_the_trend_and_the_guide(db_url, monkeypatch):
    from desk import tips
    from tests.test_tips import _series

    monkeypatch.setattr(tips.Cache, "regime", lambda self: tips.regime(_series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0)))
    monkeypatch.setattr(pt.Cache, "view", lambda self: pt.view({"^NDX": _rows(rising()), "^KS11": _rows(falling())}))
    with _client(db_url) as client:
        out = client.get("/api/tips").json()
    assert out["tips"] and out["regime"]                     # the #330 fields are kept
    assert {r["market"]: r["state"] for r in out["trend"]} == {"kospi": "bear", "nasdaq100": "bull"}
    row = out["trend"][0]
    assert {"market", "name", "symbol", "state", "state_ko", "since", "as_of", "evidence", "record"} <= set(row)
    assert set(out["guide"]) == set(pt.STATES) and out["trend_error"] is None


def test_the_per_ticker_tips_do_not_wait_for_the_indices(db_url, monkeypatch):
    from desk import tips
    from tests.test_tips import _series

    monkeypatch.setattr(tips.Cache, "regime", lambda self: tips.regime(_series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0)))

    def never(self):
        raise AssertionError("the badges' call must not fetch the indices")

    monkeypatch.setattr(pt.Cache, "view", never)
    with _client(db_url) as client:
        out = client.get("/api/tips?ticker=gdx").json()
    assert out["tips"] and "trend" not in out and "guide" not in out


def test_a_slow_index_fetch_answers_the_tips_without_the_trend(db_url, monkeypatch):
    import time

    from desk import api, tips
    from tests.test_tips import _series

    monkeypatch.setattr(tips.Cache, "regime", lambda self: tips.regime(_series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0)))
    monkeypatch.setattr(api, "TREND_WAIT_SEC", 0.2)
    monkeypatch.setattr(pt.Cache, "view", lambda self: time.sleep(1.0) or [])
    with _client(db_url) as client:
        began = time.monotonic()
        out = client.get("/api/tips").json()
        took = time.monotonic() - began
    assert took < 0.9 and out["tips"] and out["trend"] == [] and "불러오는 중" in out["trend_error"]


def test_the_tips_api_without_the_index_series_still_answers_the_tips(db_url, monkeypatch):
    from desk import tips
    from tests.test_tips import _series

    monkeypatch.setattr(tips.Cache, "regime", lambda self: tips.regime(_series(3.0, 3.6, 3.5, 4.1, 4.0, 2.0)))
    monkeypatch.setattr(pt.Cache, "view", lambda self: [])
    with _client(db_url) as client:
        out = client.get("/api/tips").json()
    assert out["tips"] and out["trend"] == [] and out["trend_error"] and out["guide"]
