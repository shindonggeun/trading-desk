"""#437: the run's look ahead - #299's projection frozen with the plan, the decision laid on it."""

from __future__ import annotations

import json

from desk import outlook
from tests.test_api import _client, _wait_done

PROJ = outlook.fake_projection(100.0)


def test_a_long_plan_reads_up_through_the_bull_path_with_its_stop_as_the_line():
    plan = {"rating": "Buy", "bias": "long", "stop_loss": 94.0, "consensus": {"conclusion": "추세 위 롱"},
            "direction": {"bias": "long"}}
    out = outlook.build(PROJ, plan, "2026-11-16T12:00:00+00:00", "2026-11-16")
    week, month = out["periods"]["1w"], out["periods"]["1m"]
    assert (week["bars"], month["bars"]) == (5, 20)
    assert week["band68"] == [PROJ["cone"][4]["p16"], PROJ["cone"][4]["p84"]]            # the desk's own cone
    assert month["band95"] == [PROJ["cone"][19]["p2_5"], PROJ["cone"][19]["p97_5"]]
    assert (week["due"], month["due"]) == ("2026-11-23", "2026-12-16")
    d = out["decision"]
    assert (d["direction"], d["scenario"], d["desk_direction"], d["reason"]) == ("up", "bull", "long", "추세 위 롱")
    assert d["scenario_prob"] == next(p["prob"] for p in PROJ["paths"] if p["name"] == "bull")
    assert d["agrees"] == (d["likeliest"] == "bull")
    assert out["invalidation"] == 94.0 and out["price"] == 100.0
    assert outlook.render(out).startswith("전망 고정: 상승 쪽 · 1주 ")


def test_a_flat_plan_is_sideways_on_the_range_with_no_line():
    out = outlook.build(PROJ, {"rating": "Hold", "bias": "flat", "stop_loss": 90.0}, "t", "2026-11-16")
    assert (out["decision"]["direction"], out["decision"]["scenario"], out["invalidation"]) == ("sideways", "range", None)
    assert out["decision"]["reason"] is None


def test_a_short_plan_without_a_bear_path_keeps_the_direction_and_no_scenario():
    proj = {**PROJ, "paths": [p for p in PROJ["paths"] if p["name"] != "bear"]}
    out = outlook.build(proj, {"bias": "short", "stop_loss": 106.0}, "t", "2026-11-16")
    assert (out["decision"]["direction"], out["decision"]["scenario"], out["decision"]["agrees"]) == ("down", None, None)
    assert out["invalidation"] == 106.0


def _run(db_url):
    with _client(db_url) as client:
        run_id = client.post("/api/runs", json={"ticker": "NVDA", "trade_date": "2026-11-03"}).json()["id"]
        run = _wait_done(client, run_id)
        events = client.get(f"/api/runs/{run_id}/events").json()
    return run, events


def test_the_run_detail_carries_the_frozen_outlook(db_url):
    run, events = _run(db_url)
    assert run["status"] == "done"
    got = run["outlook"]
    assert set(got["periods"]) == {"1w", "1m"} and got["trade_date"] == "2026-11-03"
    assert got["decision"]["direction"] == outlook.LEAN[run["plan"]["bias"]]
    assert got["decision"]["rating"] == run["plan"]["rating"]
    kinds = [e["type"] for e in events]
    assert kinds[-3:] == ["outlook", "plan", "done"]                     # read as done, it is already there
    assert next(e for e in events if e["type"] == "outlook")["data"] == got
    text = json.dumps(got).lower()
    assert not any(word in text for word in ("secret", "token", "password", "account"))


def test_a_failed_projection_leaves_the_run_done_with_its_plan_and_no_outlook(db_url, monkeypatch):
    def broken(*_a, **_k):
        raise RuntimeError("no bars")

    monkeypatch.setattr(outlook, "fake_projection", broken)
    run, events = _run(db_url)
    assert run["status"] == "done" and run["plan"]["rating"] == "Overweight"
    assert run["outlook"] is None and "outlook" not in [e["type"] for e in events]
