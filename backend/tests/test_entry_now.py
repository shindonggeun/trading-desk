"""#312: the entry now - the side worth more per plan, why, and when to stand aside."""

import asyncio
import copy
from datetime import date, timedelta

import pytest

from desk import backtest, calibration, entry_now, horizons
from tests.test_backtest import _bars, _fetch

SWING = horizons.get("swing")


def _reach(p_limit=0.6, p_far=0.3, n=100) -> entry_now.Reach:
    r = entry_now.Reach()
    for side in ("long", "short"):
        for kind in ("limit", "stop"):
            for dist, p in ((0.0, 0.9), (0.5, p_limit), (1.5, p_far), (2.5, p_far / 2), (5.0, 0.05)):
                for i in range(n):
                    r.add("swing", side, kind, dist, i < round(p * n))
    return r


def _cal(exp_r, n=120, thin=False):
    return {"p_t1": 0.55, "exp_r": exp_r, "n": n, "thin": thin, "negative": False}


def _long(**over) -> dict:
    side = {"setup": "pullback", "score": 50, "avg_entry": 99.0, "stop": 95.0, "direction": "neutral",
            "zone": {"low": 98.0, "high": 100.0, "members": [{"price": 100.0, "basis": "일봉 지지", "weight": 1.0},
                                                             {"price": 98.5, "basis": "가치영역 하단", "weight": 1.5}]},
            "entries": [{"step": 1, "price": 100.0, "share": 0.5, "kind": "limit"},
                        {"step": 2, "price": 98.0, "share": 0.5, "kind": "limit"}],
            "targets": [{"role": "t1", "price": 106.0, "rr": 1.75}, {"role": "t2", "price": 110.0, "rr": 2.75}],
            "calibration": _cal(0.2)}
    return {**side, **over}


def _short(**over) -> dict:
    side = {"setup": "fade", "score": 80, "avg_entry": 106.0, "stop": 109.0, "direction": "neutral",
            "zone": {"low": 105.0, "high": 107.0, "members": [{"price": 106.0, "basis": "저항", "weight": 1.0}]},
            "entries": [{"step": 1, "price": 105.0, "share": 1.0, "kind": "limit"}],
            "targets": [{"role": "t1", "price": 100.0, "rr": 2.0}], "calibration": _cal(0.1)}
    return {**side, **over}


def _pb(ref=101.0, long=None, short=None) -> dict:
    return {"frame": {"reference_price": ref, "atr": 2.0}, "regime": {"id": "range"},
            "long": long if long is not None else _long(), "short": short if short is not None else _short()}


def test_the_side_worth_more_per_plan_wins_over_the_higher_score():
    # long: 0.5 ATR away (fills 60%) x +0.2R = +0.12; short: 2 ATR away (fills 30%) x +0.1R = +0.03 - score 80 > 50
    got = entry_now.choose(_pb(), "swing", _reach())
    best, other = got["best"], got["other"]
    assert best["side"] == "long" and other["side"] == "short" and got["stand_aside"] is None
    assert best["distance_atr"] == 0.5 and best["reach_prob"] == 0.6 and best["ev_r"] == pytest.approx(0.12)
    assert other["ev_r"] == pytest.approx(0.03) and other["score"] > best["score"]
    assert best["kind"] == "limit" and best["zone"] == [98.0, 100.0] and best["stop"] == 95.0
    assert best["from_now_pct"] == round((100 / 101 - 1) * 100, 2)
    assert "일봉 지지" in best["why"] and any("기대 +0.20R" in w for w in best["why"])


def test_in_the_zone_is_now_and_a_stop_entry_is_a_breakout():
    assert entry_now.choose(_pb(ref=99.0), "swing", _reach())["best"]["kind"] == "now"
    stop_long = _long(entries=[{"step": 1, "price": 103.0, "share": 1.0, "kind": "stop"}],
                      zone={"low": 102.0, "high": 103.0, "members": []}, stop=100.0)
    got = entry_now.choose(_pb(long=stop_long, short={"setup": "none"}), "swing", _reach())
    assert got["best"]["kind"] == "breakout" and got["best"]["distance_atr"] == 1.0


def test_negative_or_thin_records_stand_aside_but_keep_the_reference():
    neg = entry_now.choose(_pb(long=_long(calibration=_cal(-0.1)), short=_short(calibration=_cal(-0.4))),
                           "swing", _reach())
    assert neg["stand_aside"] and "음수" in neg["stand_aside"]["reason"] and neg["best"]["side"] == "long"
    thin = entry_now.choose(_pb(long=_long(calibration=_cal(None, n=12, thin=True)), short={"setup": "none"}),
                            "swing", _reach())
    assert thin["best"]["ev_r"] is None and "얇다" in thin["stand_aside"]["reason"]
    assert entry_now.pick(_pb(long=_long(calibration=_cal(-0.1)), short={"setup": "none"}), "swing", _reach()) is None


def test_no_setup_on_either_side_says_why_and_an_invalid_side_is_not_an_entry():
    got = entry_now.choose(_pb(long={"setup": "none", "reason": "롱 근거 없음"}, short={"setup": "none"}),
                           "swing", _reach())
    assert got["best"] is None and got["stand_aside"]["reason"] == "롱 근거 없음"
    got = entry_now.choose(_pb(long=_long(state="invalid")), "swing", _reach())
    assert got["best"]["side"] == "short" and got["other"] is None


def test_a_near_tie_goes_to_the_side_with_the_trend():
    long = _long(calibration=_cal(0.1))                       # 0.6 x 0.1 = 0.06
    short = _short(entries=[{"step": 1, "price": 102.0, "share": 1.0, "kind": "limit"}], zone={"low": 102.0, "high": 104.0},
                   calibration=_cal(0.1), direction="with")   # 0.5 ATR: 0.06 too
    assert entry_now.choose(_pb(long=long, short=short), "swing", _reach())["best"]["side"] == "short"


def test_the_trend_never_lifts_a_negative_side_over_a_positive_one():
    # long +0.012 against the trend, short -0.006 with it: within TIE_R, but across zero
    long = _long(calibration=_cal(0.02), direction="against")                          # 0.6 x 0.02
    short = _short(entries=[{"step": 1, "price": 102.0, "share": 1.0, "kind": "limit"}], zone={"low": 102.0, "high": 104.0},
                   calibration=_cal(-0.01), direction="with")                           # 0.6 x -0.01
    got = entry_now.choose(_pb(long=long, short=short), "swing", _reach())
    assert got["best"]["side"] == "long" and got["stand_aside"] is None
    # a gap wider than TIE_R stays with the better side whatever the trend
    short2 = {**short, "calibration": _cal(0.06)}                                       # 0.036 vs 0.012
    assert entry_now.choose(_pb(long=_long(calibration=_cal(0.1)), short=short2), "swing", _reach())["best"]["side"] == "long"
    short3 = {**short, "calibration": _cal(0.15)}                                       # 0.09 vs 0.06: > TIE_R
    long3 = _long(calibration=_cal(0.1), direction="with")
    assert entry_now.choose(_pb(long=long3, short=short3), "swing", _reach())["best"]["side"] == "short"


def test_no_price_or_atr_says_so_rather_than_a_thin_record():
    pb = _pb()
    pb["frame"]["atr"] = 0.0
    got = entry_now.choose(pb, "swing", _reach())
    assert got["best"]["distance_atr"] is None and "ATR" in got["stand_aside"]["reason"]


def test_the_reach_table_falls_back_and_round_trips(tmp_path):
    r = entry_now.Reach()
    for i in range(40):
        r.add("swing", "long", "limit", 0.5, i % 2 == 0)
    assert r.look("swing", "long", "limit", 0.6) == {"p": 0.5, "n": 40, "level": "kind"}
    assert r.look("swing", "long", "stop", 0.6)["level"] == "bucket"
    assert r.look("swing", "long", "limit", 9.0)["level"] == "side"
    assert r.look("swing", "short", "limit", 0.5)["p"] is None
    path = tmp_path / "reach.json"
    r.write({"source": "test"}, path)
    assert entry_now.Reach.load(path).cells == r.cells and entry_now.Reach.load(tmp_path / "none.json").cells == {}


def test_the_bottom_zone_steps_in_the_long_zone_are_named():
    got = entry_now.choose(_pb(), "swing", _reach())
    z = {"supported": True, "stage": "approach", "stage_ko": "접근", "score": 55,
         "ladder": [{"step": 1, "price": 99.2}, {"step": 2, "price": 90.0}]}
    note = entry_now._bottom_note(got["best"], got["other"], z, 2.0)
    assert note == {"stage": "approach", "stage_ko": "접근", "score": 55}
    assert any("인생 타점 1차 99.2" in w for w in got["best"]["why"])
    assert not any("2차" in w for w in got["best"]["why"])
    assert entry_now._bottom_note(got["best"], None, {"stage": "none"}, 2.0) is None


def test_live_entry_reads_the_live_playbooks(monkeypatch):
    pb = {**_pb(), "live": {"price": 101.0, "source": "toss", "fetched_at": "2026-11-13T00:00:00Z"},
          "as_of": "2026-11-13T00:00:01+00:00"}

    async def fake_current(prices, ticker, horizon, risk=None):
        assert ticker == "KORU" and horizon == "swing"
        return copy.deepcopy(pb)

    class Bottoms:
        def one(self, ticker, price, source):
            assert price == 101.0
            return {"stage": "watch", "stage_ko": "관찰", "score": 17, "ladder": [{"step": 1, "price": 99.0}]}

    monkeypatch.setattr(entry_now.playbook_live, "current", fake_current)
    got = asyncio.run(entry_now.entry_now(None, "KORU", "swing", bottoms=Bottoms(), reach=_reach()))
    assert got["price_now"] == 101.0 and got["price_source"] == "toss" and got["stale"] is False
    assert got["best"]["side"] == "long" and got["bottom"]["stage_ko"] == "관찰"
    assert any("인생 타점 1차" in w for w in got["best"]["why"]) and "과거 성적" in got["basis"]


def test_the_replay_has_no_look_ahead_and_compares_the_arms_on_a_training_table(monkeypatch):
    seen = []
    real = calibration.Table

    class Spy(real):
        def __init__(self, prior=None, meta=None):
            seen.append(prior)
            super().__init__(prior, meta)

    monkeypatch.setattr(entry_now.calibration, "Table", Spy)
    daily = _bars(330)
    snaps, _notes = entry_now.gather(["SOXL"], SWING, None, fetch=_fetch(daily), vix=None, risk=None, step=3)
    rows = entry_now.replay(snaps, SWING)
    assert rows and all(set(r["sides"]) <= {"long", "short"} for r in rows)
    # the plan of a day does not change when the bars after it do
    snap = next(s for s, _p in snaps if s.t >= 270)
    other = daily[:snap.t + 1] + [{**b, "close": b["close"] * 2, "high": b["high"] * 3} for b in daily[snap.t + 1:]]
    snap2 = backtest.snapshot("SOXL", SWING, other, _fetch(other)("SOXL", SWING, None)[1], snap.t)
    assert backtest.playbooks_at(snap2, SWING) == next(r["pb"] for r in rows if r["day"] == snap.day)
    out = entry_now.compare(rows, SWING)
    assert set(out["arms"]) == {"score", "ev", "entry_now"} and out["cut"]
    for arm in out["arms"].values():
        assert set(arm) == {"train", "valid"}
        assert arm["valid"]["plans"] == out["plans"]["valid"] and arm["valid"]["taken"] <= arm["valid"]["plans"]
        assert arm["valid"]["max_dd_r"] <= 0
    # standing aside never takes more plans than the pick that always takes one
    assert out["arms"]["entry_now"]["valid"]["taken"] <= out["arms"]["ev"]["valid"]["taken"]
    # the calibration it picked with is exactly the training trades' (before the cut minus the embargo)
    edge = (date.fromisoformat(out["cut"]) - timedelta(days=backtest.EMBARGO_DAYS["swing"])).isoformat()
    train_trades = [t for r in rows if r["day"] < edge for t in r["trades"].values()]
    assert seen[0] == calibration.from_trades(train_trades, "swing")
    # and the rows were not changed by it (a second compare gives the same answer)
    assert entry_now.compare(rows, SWING)["arms"] == out["arms"]


def test_the_api_needs_a_session_and_validates(db_url, monkeypatch):
    from tests.test_api import OWNER, PASSWORD
    from tests.test_playbook_live import _source
    from tests.test_prices import _client

    async def fake_current(prices, ticker, horizon, risk=None):
        if ticker == "DOWN":
            raise ConnectionError("no bars")
        return copy.deepcopy({**_pb(), "live": None, "as_of": "2026-11-13T00:00:00+00:00"})

    monkeypatch.setattr(entry_now.playbook_live, "current", fake_current)
    with _client(db_url, _source(), fake_runs=False) as client:
        assert client.get("/api/entry-now", params={"ticker": "NVDA"}).status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        assert client.get("/api/entry-now").status_code == 422
        assert client.get("/api/entry-now", params={"ticker": "NVDA", "horizon": "weekly"}).status_code == 422
        assert client.get("/api/entry-now", params={"ticker": "DOWN"}).status_code == 503
        got = client.get("/api/entry-now", params={"ticker": "NVDA"}).json()
        assert got["ticker"] == "NVDA" and got["horizon"] == "swing" and got["stale"] is True
        assert got["best"]["side"] in ("long", "short") and "basis" in got


def test_the_max_drawdown_is_by_plan_day():
    assert entry_now._max_dd([("d1", 1.0), ("d2", -2.0), ("d2", -0.5), ("d3", 1.0)]) == -2.5
    assert entry_now._max_dd([]) == 0.0
