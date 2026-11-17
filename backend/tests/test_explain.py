"""#493: the analysis for a beginner - the desk's draft, the model's wording kept only when its numbers
are the desk's, the fallback, and the stored explanation reused."""

import json

import pytest

from desk import explain as ex


def _run(bias="long", rating="Overweight"):
    side = {"long": "long", "short": "short"}.get(bias)
    return {"id": "r1", "ticker": "NVDA", "trade_date": "2026-11-16", "status": "done", "plan": {
        "rating": rating, "bias": bias, "entry_price": 150.0, "stop_loss": 146.1, "price_target": 165.0,
        "live_price": 152.4, "invalidation": "주봉 종가가 148 아래면 무효", "valid_until": "2026-11-20",
        "summary": {"text": "지지 구역에서 나눠 담기", "side": side, "stop": 146.1, "stop_pct": 4.1,
                    "zone": {"low": 148.6, "high": 153.1},
                    "ladder": [{"step": 1, "price": 151.0, "share": 0.5, "when": "구역 위쪽"},
                               {"step": 2, "price": 149.0, "share": 0.5, "when": "구역 아래쪽"}],
                    "targets": [{"role": "T1", "price": 160.0, "rr": 1.6}, {"role": "T2", "price": 165.0, "rr": 2.5}]},
        "support_resistance": {"reference_price": 152.4, "levels": [
            {"kind": "support", "price": 150.0, "zone": {"low": 148.6, "high": 153.1}, "touches": 4, "held": 3, "flipped": True},
            {"kind": "support", "price": 140.0, "zone": {"low": 138.0, "high": 141.0}, "touches": 2, "held": 2},
            {"kind": "resistance", "price": 160.0, "zone": {"low": 158.5, "high": 161.0}, "touches": 3, "held": 2}]}}}


FEAR = [{"key": "vix", "name": "VIX", "value": 22.9, "band_ko": "공포"}, {"key": "vkospi", "name": "VKOSPI", "value": None}]


@pytest.mark.parametrize("bias,rating", [("long", "Overweight"), ("short", "Underweight"), ("flat", "Hold")])
def test_the_draft_has_the_fixed_shape_for_long_short_and_hold(bias, rating):
    out = ex.build(_run(bias, rating), FEAR)
    assert out["source"] == "rules" and out["version"] == ex.VERSION
    assert out["hook"].endswith("?") and 3 <= len(out["sections"]) <= 5
    for s in out["sections"]:
        assert s["title"] and s["body"] and s["highlight"] and s["term"]["name"] and s["term"]["explain"]
        assert set(s["chart"]) == {"kind", "levels", "marks"}
    assert [s["label"] for s in out["stages"]] == ["1단계", "2단계", "최대"]
    assert out["stages"][-1]["low"] == 146.1 and all({"q", "a"} <= set(c) for c in out["checklist"])
    assert "예측이 아니라" in out["closing"]
    body = json.dumps(out, ensure_ascii=False)
    assert "148.6 ~ 153.1" in body and "4번 닿아 3번" in body and "VIX 22.9(공포)" in body and "VKOSPI" not in body


def test_a_sentence_with_a_number_the_desk_did_not_give_is_dropped():
    f = ex.facts(_run())
    allowed = ex.allowed_numbers(f, ex.rules(f))
    assert ex.clean("지지는 148.6 ~ 153.1 이에요. 목표는 180 이에요. 손절은 146.1 이에요.", allowed) == \
        "지지는 148.6 ~ 153.1 이에요. 손절은 146.1 이에요."
    assert ex.clean("다음 주에 12% 오를 거예요.", allowed) is None
    assert ex.clean("1단계에서 반을 사요.", allowed) == "1단계에서 반을 사요."


def test_the_models_wording_replaces_the_draft_only_where_it_passes():
    draft_run = _run()

    def write(prompt):
        assert "JSON 만 답해라" in prompt and "148.6" in prompt
        return "답:\n" + json.dumps({
            "hook": "NVDA, 지금 들어가도 괜찮을까요?",
            "sections": [{"title": "결론부터", "body": "지금은 나눠 담는 쪽이에요. 목표는 999 예요.",
                          "highlight": "목표 999", "term": "분석의 한 줄 결론이에요."}],
            "stages": ["구역 위쪽에서 절반"], "checklist": [{"q": "손절 정했나요?", "a": "146.1 아래면 정리해요."}],
            "closing": "조건과 대응이에요."}, ensure_ascii=False)

    out = ex.build(draft_run, None, write)
    assert out["source"] == "ai" and out["hook"] == "NVDA, 지금 들어가도 괜찮을까요?"
    s0 = out["sections"][0]
    assert s0["title"] == "결론부터" and s0["body"] == "지금은 나눠 담는 쪽이에요."      # the 999 sentence dropped
    assert s0["highlight"] == ex.rules(ex.facts(draft_run))["sections"][0]["highlight"]   # nothing left: the draft's
    assert out["stages"][0]["why"] == "구역 위쪽에서 절반" and out["checklist"][0]["a"] == "146.1 아래면 정리해요."
    assert out["sections"][1] == ex.rules(ex.facts(draft_run))["sections"][1]              # not answered: the draft


def test_a_failing_or_garbled_model_leaves_the_desks_wording():
    def boom(prompt):
        raise TimeoutError("slow")
    out = ex.build(_run(), None, boom)
    assert out["source"] == "rules" and out["fallback"] == "TimeoutError"
    assert ex.build(_run(), None, lambda p: "not json")["source"] == "rules"


def test_the_api_writes_once_keeps_it_and_refreshes_on_ask(db_url):
    from tests.test_api import _client, _wait_done

    with _client(db_url) as client:
        assert client.get("/api/runs/nope/explain").status_code == 404
        rid = client.post("/api/runs", json={"ticker": "nvda", "trade_date": "2026-11-03"}).json()["id"]
        assert _wait_done(client, rid)["status"] == "done"
        first = client.get(f"/api/runs/{rid}/explain")
        assert first.status_code == 200 and first.json()["sections"] and first.json()["source"] == "rules"
        again = client.get(f"/api/runs/{rid}/explain").json()
        assert again == first.json()
        kinds = [e["type"] for e in client.get(f"/api/runs/{rid}/events").json()]
        assert kinds.count("explain") == 1                                     # kept once, reused
        client.get(f"/api/runs/{rid}/explain?refresh=1")
        kinds = [e["type"] for e in client.get(f"/api/runs/{rid}/events").json()]
        assert kinds.count("explain") == 2
        client.cookies.clear()
        assert client.get(f"/api/runs/{rid}/explain").status_code == 401
