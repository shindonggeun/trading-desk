"""#465: each plan line's basis and scenario, the unfilled gaps and the plain sentences."""

from desk import line_basis as lb
from desk import sr


def _bar(day: int, o: float, h: float, lo: float, c: float) -> dict:
    return {"at": f"2026-01-{day:02d}T00:00:00+00:00", "open": o, "high": h, "low": lo, "close": c}


def test_an_up_gap_stays_open_until_the_price_comes_back_and_shrinks_on_the_way():
    bars = [_bar(1, 100, 101, 99, 100), _bar(2, 100, 101, 99, 100.5),
            _bar(3, 106, 108, 105, 107),                      # gap up over 101 → 105
            _bar(4, 107, 109, 103, 108),                      # back to 103: the open part is 101-103
            _bar(5, 108, 112, 107, 111)]
    got = lb.gaps(bars, None, 111.0)
    assert len(got) == 1
    g = got[0]
    assert g["direction"] == "up" and g["fill_price"] == 101 and g["bottom"] == 101 and g["top"] == 103
    assert g["side"] == "below" and g["distance_pct"] == round((101 / 111 - 1) * 100, 2)
    assert g["timeframe"] == "1d" and g["formed_at"] == "2026-01-03" and "아래로" in g["text"]
    filled = bars + [_bar(6, 108, 108, 100.5, 101)]           # back under 101: filled, gone
    assert lb.gaps(filled, None, 101.0) == []


def test_a_down_gap_above_the_price_is_a_fill_target_up_and_a_tiny_one_is_noise():
    bars = [_bar(1, 100, 101, 99, 100), _bar(2, 95, 96, 94, 95), _bar(3, 95, 96.5, 94, 96)]
    g = lb.gaps(bars, None, 96.0)[0]
    assert g["direction"] == "down" and g["fill_price"] == 99 and g["top"] == 99 and g["bottom"] == 96.5
    assert g["side"] == "above" and g["distance_pct"] > 0 and "위로" in g["text"]
    tiny = [_bar(1, 100, 101, 99, 100), _bar(2, 101.1, 102, 101.1, 101.5)]   # 0.1 over: under GAP_MIN_ATR
    assert lb.gaps(tiny, None, 101.5) == []


def test_weekly_gaps_join_and_the_nearest_comes_first():
    days = [_bar(1, 100, 101, 99, 100), _bar(2, 110, 111, 109, 110), _bar(3, 110, 112, 109.5, 111)]
    weeks = [_bar(1, 80, 85, 79, 84), _bar(8, 90, 95, 89, 94), _bar(15, 100, 112, 99, 111)]
    got = lb.gaps(days, weeks, 111.0)
    assert [g["timeframe"] for g in got] == ["1d", "1w", "1w"]
    assert [g["fill_price"] for g in got] == [101, 95, 85]


SR = {"reference_price": 200.0, "atr": 4.0,
      "levels": [{"price": 230.0, "kind": "resistance", "timeframe": "1d", "zone": [228.0, 232.0], "basis": "스윙 고점 2회"},
                 {"price": 205.0, "kind": "resistance", "timeframe": "1w", "zone": [203.0, 207.0], "basis": "주봉 고점"},
                 {"price": 166.0, "kind": "support", "timeframe": "1d", "zone": [164.0, 168.0], "basis": "스윙 저점"}],
      "fibonacci": {"direction": "down", "levels": [{"ratio": 0.618, "price": 229.5}]},
      "trendlines": [{"kind": "support", "timeframe": "1d", "price_now": 190.0}]}


def test_a_hold_plans_levels_are_named_as_the_short_scenario_and_their_anchors():
    plan = {"bias": "flat", "rating": "Hold", "entry_price": 206.0, "stop_loss": 229.5, "price_target": 166.5,
            "long_trigger": 232.0, "short_trigger": 190.0}
    rows = {r["line"]: r for r in lb.basis(plan, SR, [])}
    for f in ("entry_price", "stop_loss", "price_target"):
        assert rows[f]["scenario"] == "short" and rows[f]["scenario_ko"] == "숏" and rows[f]["hold_plan"]
        assert "관망 계획이라" in rows[f]["text"] and "숏 시나리오" in rows[f]["text"]
    assert rows["long_trigger"]["scenario"] == "long" and not rows["long_trigger"]["hold_plan"]
    assert rows["short_trigger"]["scenario"] == "short"
    assert rows["entry_price"]["anchors"][0]["kind"] == "sr_zone" and "주봉 저항 구역" in rows["entry_price"]["anchors"][0]["text"]
    stop_kinds = {a["kind"] for a in rows["stop_loss"]["anchors"]}
    assert {"sr_zone", "fib"} <= stop_kinds                             # 229.5: in the zone and on the 0.618
    assert rows["price_target"]["anchors"][0]["text"].startswith("일봉 지지 구역")
    assert rows["short_trigger"]["anchors"][0]["kind"] == "trendline"
    assert rows["entry_price"]["from_now_pct"] == 3.0


def test_a_long_plan_line_off_every_level_says_it_is_the_models_number():
    plan = {"bias": "long", "entry_price": 185.0, "stop_loss": 178.0, "price_target": 215.0}
    rows = {r["line"]: r for r in lb.basis(plan, SR, [])}
    assert all(r["scenario"] == "long" and not r["hold_plan"] for r in rows.values())
    assert rows["entry_price"]["anchors"] == [] and "모델이 정한 숫자" in rows["entry_price"]["text"]
    assert rows["price_target"]["text"].startswith("목표 215.00 — 롱 시나리오의 익절 자리(여기서 일부 또는 전부 익절).")
    assert lb.basis(None, SR, []) == [] and lb.basis({"bias": "long"}, SR, []) == []


def test_a_gap_fill_price_is_an_anchor():
    gap = [{"timeframe": "1d", "fill_price": 178.2, "direction": "up"}]
    assert lb.anchors(178.0, SR, gap)[0]["kind"] == "gap"


def test_the_sentences_go_from_the_zone_above_to_the_one_below_with_their_actions():
    lines = lb.basis({"bias": "flat", "entry_price": 206.0, "stop_loss": 229.5, "price_target": 166.5}, SR, [])
    gap = lb.gaps([_bar(1, 100, 180, 99, 178), _bar(2, 190, 201, 189, 200)], None, 200.0)
    out = lb.explain(SR, gap, lines)
    # 200: the nearest zone over it is the weekly 203-207 (1.5% up), none at it, the daily 164-168 under it
    assert out[0].startswith("위로는 주봉 저항 구역(203.00~207.00)까지 +1.5%") and "일부 익절" in out[0]
    assert "종가로 207.00 위에 서면 돌파" in out[0]
    assert out[1].startswith("지금가 200.00 는 지지 · 저항 구역 사이")
    assert out[2].startswith("아래로는 일봉 지지 구역(164.00~168.00)") and "다음 받침" in out[2]
    assert "메움 가격 180.00" in out[3] and "눌림 때 내려올 수 있는 자리" in out[3]
    assert out[4:] == [r["text"] for r in lines]
    at_res = lb.explain({**SR, "reference_price": 204.0}, [], [])           # inside the weekly zone
    assert at_res[0].startswith("위로는 일봉 저항 구역(228.00~232.00)")
    assert at_res[1].startswith("지금가 204.00 는 주봉 저항 구역(203.00~207.00)에 붙어 있다") and "추격 매수" in at_res[1]
    at_sup = lb.explain({**SR, "reference_price": 167.0}, [], [])            # inside the support zone
    assert "일봉 지지 구역(164.00~168.00)에 붙어 있다" in at_sup[1] and "손절은 164.00 아래" in at_sup[1]
    assert len(at_sup) == 2                                                  # nothing under it to name
    # a resistance edge closer than the support it sits over: at the resistance, the support still named below
    close = {"reference_price": 162.85, "atr": 10.0,
             "levels": [{"price": 168.0, "kind": "resistance", "timeframe": "1w", "zone": [164.07, 172.7], "basis": "a"},
                        {"price": 158.0, "kind": "support", "timeframe": "1d", "zone": [157.39, 159.39], "basis": "b"}]}
    said = lb.explain(close, [], [])
    assert "주봉 저항 구역(164.07~172.70)에 붙어 있다" in said[0] and said[1].startswith("아래로는 일봉 지지 구역(157.39~159.39)")


def test_a_far_gap_is_listed_but_not_talked_about():
    far = [{"timeframe": "1w", "fill_price": 128.5, "distance_pct": -33.1, "text": "주봉 상승 갭 메움 가격 128.50"}]
    assert not any("갭" in s for s in lb.explain(SR, far, []))


def test_a_zone_names_two_of_its_reasons():
    zone = {"reference_price": 100.0, "atr": 2.0,
            "levels": [{"price": 101.0, "kind": "resistance", "timeframe": "1d", "zone": [100.5, 101.5],
                        "basis": "20일선 · 스윙 고점 · 지난주 고가 · 5일 고가"}]}
    assert lb.anchors(101.0, zone, [])[0]["text"] == "일봉 저항 구역(100.50~101.50) 안(20일선 · 스윙 고점 등)"


def test_no_reference_no_sentences():
    assert lb.explain({"levels": []}, [], []) == []


def test_the_levels_endpoint_adds_gaps_explain_and_with_a_run_the_line_basis(db_url):
    from tests.test_api import OWNER, PASSWORD, _wait_done
    from tests.test_prices import _client
    from tests.test_sr import _source

    sr._CACHE.clear()
    source, _ = _source()
    with _client(db_url, source, fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        out = client.get("/api/levels?ticker=NVDA").json()
        assert out["levels"] and isinstance(out["gaps"], list) and out["basis"] == [] and out["explain"]
        assert out["gaps_error"] is None
        run_id = client.post("/api/runs", json={"ticker": "NVDA"}).json()["id"]
        run = _wait_done(client, run_id)
        with_run = client.get(f"/api/levels?ticker=NVDA&run={run_id}").json()
        priced = [f for f, _ in lb.LINES if (run.get("plan") or {}).get(f) is not None]
        assert [r["line"] for r in with_run["basis"]] == priced
        assert all(r["text"] in with_run["explain"] for r in with_run["basis"])
        assert client.get("/api/levels?ticker=NVDA&run=nope").status_code == 404
        assert client.get(f"/api/levels?ticker=AAPL&run={run_id}").status_code == 422
        assert client.get("/api/levels?ticker=NVDA").json()["levels"] == out["levels"]   # the cached levels unchanged


def test_a_korean_gap_is_dated_on_the_korean_day():
    kst = [{"at": "2026-11-12T15:00:00+00:00", "open": 100, "high": 101, "low": 99, "close": 100},   # KST 11-13
           {"at": "2026-11-15T15:00:00+00:00", "open": 106, "high": 108, "low": 105, "close": 107}]  # KST Mon 11-16
    assert lb.gaps(kst, None, 107.0, "005930.KS")[0]["formed_at"] == "2026-11-16"
    assert lb.gaps(kst, None, 107.0, "NVDA")[0]["formed_at"] == "2026-11-15"   # a US bar keeps its UTC date


def test_a_hold_plan_without_a_stop_takes_its_side_from_the_target_or_says_it_cannot_tell():
    rows = {r["line"]: r for r in lb.basis({"bias": "flat", "entry_price": 206.0, "price_target": 166.5}, SR, [])}
    assert rows["entry_price"]["scenario"] == "short" and rows["price_target"]["scenario"] == "short"
    alone = lb.basis({"bias": "flat", "entry_price": 206.0}, SR, [])[0]
    assert alone["scenario"] is None and "방향을 알 수 없는 계획(관망 계획이라" in alone["text"] and "?" not in alone["text"]


def test_the_swing_ends_of_the_fibonacci_are_not_a_retracement():
    fib = {**SR, "levels": [], "trendlines": [],
           "fibonacci": {"levels": [{"ratio": 0.0, "price": 110.0}, {"ratio": 1.0, "price": 90.0},
                                    {"ratio": 0.5, "price": 100.0}]}}
    assert lb.anchors(110.0, fib, []) == [] and lb.anchors(90.0, fib, []) == []
    assert lb.anchors(100.0, fib, [])[0]["text"].startswith("피보나치 0.5 되돌림")


def test_the_levels_endpoint_keeps_the_gaps_takes_the_runs_horizon_and_leaves_the_cache_alone(db_url, monkeypatch):
    from desk import api
    from tests.test_api import OWNER, PASSWORD, _wait_done
    from tests.test_prices import _client
    from tests.test_sr import _source

    sr._CACHE.clear()
    source, _ = _source()
    calls = []
    real = lb.find_gaps
    monkeypatch.setattr(lb, "find_gaps", lambda *a, **k: calls.append(a[2]) or real(*a, **k))
    with _client(db_url, source, fake_runs=True) as client:
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        client.get("/api/levels?ticker=NVDA")
        client.get("/api/levels?ticker=NVDA")
        assert calls == ["NVDA"]                                   # found once, kept GAPS_TTL_SEC
        assert all("gaps" not in v[1] and "explain" not in v[1] for v in sr._CACHE.values())
        run_id = client.post("/api/runs", json={"ticker": "NVDA", "horizon": "swing"}).json()["id"]
        _wait_done(client, run_id)
        assert client.get(f"/api/levels?ticker=NVDA&run={run_id}").status_code == 200
        assert client.get(f"/api/levels?ticker=NVDA&run={run_id}&horizon=swing").status_code == 200
        assert client.get(f"/api/levels?ticker=NVDA&run={run_id}&horizon=scalp").status_code == 422

        async def broken(*a, **k):
            raise RuntimeError("source down")

        monkeypatch.setattr(api, "frame_bars", broken)
        out = client.get("/api/levels?ticker=AAPL").json()        # a ticker not found yet: the fetch fails
        assert out["gaps"] == [] and out["gaps_error"] == "RuntimeError" and out["levels"] and out["explain"]
