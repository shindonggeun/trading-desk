"""#681 the report plan: the schedule (each US trading day, Saturday, Sunday), the rotation, the three kinds."""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from desk import explain, report_plan
from desk import reports as rp

KST = ZoneInfo("Asia/Seoul")


def _kst(*a) -> datetime:
    return datetime(*a, tzinfo=KST)


def test_the_slots_follow_the_us_close_and_the_weekend():
    keys = {s.key: s.due.astimezone(KST) for s in report_plan.slots(_kst(2026, 11, 20, 12, 0))}
    assert keys["daily:2026-11-20"] == _kst(2026, 11, 21, 6, 40)            # Friday's US close (EST) + 40 min
    assert keys["weekly:2026-11-21"] == _kst(2026, 11, 21, 10, 0)
    assert keys["next_week:2026-11-22"] == _kst(2026, 11, 22, 18, 0)
    assert "daily:2026-11-26" not in keys                                    # Thanksgiving: no US session
    assert keys["daily:2026-11-27"] == _kst(2026, 11, 28, 3, 40)            # the early close (13:00 ET) + 40 min
    assert "daily:2026-11-21" not in keys and "daily:2026-11-22" not in keys
    summer = {s.key: s.due.astimezone(KST) for s in report_plan.slots(_kst(2026, 7, 14, 12, 0))}
    assert summer["daily:2026-07-14"] == _kst(2026, 7, 15, 5, 40)           # EDT: an hour earlier in Korea


def test_due_is_the_latest_unwritten_slot_and_a_long_missed_one_is_skipped():
    now = _kst(2026, 11, 21, 7, 0)
    slot, later = report_plan.due(now, set())
    assert slot.key == "daily:2026-11-20" and later.astimezone(KST) == _kst(2026, 11, 21, 10, 0)
    slot, later = report_plan.due(now, {"daily:2026-11-20"})
    assert slot is None and later.astimezone(KST) == _kst(2026, 11, 21, 10, 0)
    # the desk down from Wednesday: Thursday's slot (more than STALE ago) is not written late, Friday's is
    slot, _ = report_plan.due(_kst(2026, 11, 21, 9, 0), {"daily:2026-11-20"})
    assert slot is None
    slot, _ = report_plan.due(_kst(2026, 11, 22, 19, 0), set())
    assert slot.key == "next_week:2026-11-22"
    assert report_plan.kind_for(_kst(2026, 11, 21, 12, 0)) == "weekly"
    assert report_plan.kind_for(_kst(2026, 11, 22, 12, 0)) == "next_week"
    assert report_plan.kind_for(_kst(2026, 11, 23, 12, 0)) == "daily"


def test_the_rotation_puts_a_subject_written_this_week_behind_the_rest():
    now = datetime(2026, 11, 20, tzinfo=UTC)
    ms = [{"symbol": s, "score": sc} for s, sc in (("SOXL", 5.0), ("NVDA", 3.0), ("KORU", 1.0), ("GDXU", 0.5))]
    recent = {"SOXL": now - timedelta(days=1), "KORU": now - timedelta(days=3), "GDXU": now - timedelta(days=9)}
    assert [m["symbol"] for m in report_plan.rank(ms, recent, now)] == ["NVDA", "GDXU", "KORU", "SOXL"]
    assert report_plan.topics("") == report_plan.TOPICS
    assert report_plan.topics(" SOXL , 005930.KS:삼성전자,") == (("SOXL", "SOXL"), ("005930.KS", "삼성전자"))
    assert report_plan.link_of("000660.KS") == "^KS11" and report_plan.link_of("SOXL") == "NQ=F"
    assert report_plan.link_of("035420.KS") == "^KS11"


def test_the_same_direction_share_matches_days():
    a = [{"day": f"d{i:02d}", "close": 100 + (i % 2)} for i in range(40)]
    b = [{"day": f"d{i:02d}", "close": 50 + (i % 2)} for i in range(40)]
    c = [{"day": f"d{i:02d}", "close": 50 + ((i + 1) % 2)} for i in range(40)]
    assert report_plan.same_direction(a, b) == {"days": 39, "same_pct": 100}
    assert report_plan.same_direction(a, c)["same_pct"] == 0
    assert report_plan.same_direction(a[:10], b[:10]) is None                # too few common days


def test_the_week_ahead_lists_the_calendar_and_the_option_expiries_in_kst():
    now = _kst(2026, 12, 6, 18, 0)                                           # a Sunday
    fomc = {"label": "FOMC 금리 결정", "at": datetime(2026, 12, 9, 19, 0, tzinfo=UTC)}
    far = {"label": "CPI", "at": datetime(2026, 12, 15, 13, 30, tzinfo=UTC)}
    got = report_plan.week_events(now, [fomc, far])
    assert [e["label"] for e in got] == ["FOMC 금리 결정", "코스피200 옵션 만기"]
    assert got[0]["when"] == "12/10(목) 04:00" and got[1]["when"] == "12/10(목)"


def _engine(db_url):
    from desk.db import make_engine, migrate

    migrate(db_url)
    return make_engine(db_url)


def test_a_days_report_leads_with_the_indexes_and_links_its_subject(db_url):
    engine = _engine(db_url)
    desk = rp.Reports(engine, rp.fake_fetch)
    plan = desk.choose("daily")
    slot = report_plan.Slot("daily", "daily:2026-11-20", datetime(2026, 11, 20, 21, 40, tzinfo=UTC))
    out = desk.finish(plan, None, slot)
    assert out["kind"] == "daily" and out["slot"] == "daily:2026-11-20" and out["subject"]["kind"] == "topic"
    s = out["sections"]
    assert 3 <= len(s) <= explain.MAX_SECTIONS and s[0]["title"].startswith("지수부터")
    assert "코스피" in s[0]["body"] and "나스닥100 선물" in s[0]["body"] and s[0]["chart"]["symbol"] in ("^KS11", "NQ=F")
    assert "이어지나" in s[-1]["title"] and all(x.get("term", {}).get("explain") for x in s)
    assert all(x["chart"] is None or "symbol" in x["chart"] for x in s)       # #682 can draw from the spec
    assert desk.done_slots() == {"daily:2026-11-20"} and out["subject"]["symbol"] in desk.recent_subjects()
    # the next day's subject is another one (the rotation)
    assert desk.choose("daily")["subject"]["symbol"] != out["subject"]["symbol"]
    assert desk.due(datetime(2026, 11, 20, 22, 0, tzinfo=UTC))[0] is None    # that slot is written
    engine.dispose()


def test_the_weekly_and_the_week_ahead_reports(db_url):
    engine = _engine(db_url)
    calls = {"kr": {"n": 5, "hit_pct": 60}, "us": {"n": 4, "hit_pct": 50}}
    events = [{"label": "FOMC 금리 결정", "at": datetime.now(UTC) + timedelta(days=8)}]
    desk = rp.Reports(engine, rp.fake_fetch, calls=lambda: calls, events=lambda now: events)
    week = desk.finish(desk.choose("weekly"), None, None)
    assert week["kind"] == "weekly" and week["title"].startswith("이번 주 정리") and week["slot"] is None
    assert any("5번 가운데 60%" in s["body"] for s in week["sections"])
    assert week["subject"] == {"symbol": "NQ=F", "name": "주간 정리", "kind": "weekly"} and week["stages"]
    ahead = desk.finish(desk.choose("next_week"), None, None)
    assert ahead["kind"] == "next_week" and ahead["sections"][0]["title"] == "다음 주 일정"
    assert any(s["title"] == "코스피 볼 자리" for s in ahead["sections"])
    for r in (week, ahead):
        assert 3 <= len(r["sections"]) <= explain.MAX_SECTIONS and r["closing"] and r["checklist"]
        json.dumps(r, ensure_ascii=False)
    # a broken record or calendar never stops the report
    def boom(*_a):
        raise RuntimeError("x")
    broken = rp.Reports(engine, rp.fake_fetch, calls=boom, events=boom)
    assert broken.finish(broken.choose("weekly"))["kind"] == "weekly"
    assert broken.finish(broken.choose("next_week"))["sections"][0]["title"] == "다음 주 일정"
    engine.dispose()


def test_the_models_words_with_a_number_of_its_own_are_dropped_in_every_kind(db_url):
    engine = _engine(db_url)
    desk = rp.Reports(engine, rp.fake_fetch)
    plan = desk.choose("weekly")

    def model(prompt: str) -> str:
        assert "이번 주 지수" in prompt
        return json.dumps({"hook": "코스피가 9999 까지 간다", "title": "이번 주, 지수는 쉬었어요",
                           "sections": [{"title": "한 주", "body": "코스피는 12345 를 넘을 거예요."}]}, ensure_ascii=False)
    out = desk.finish(plan, model)
    assert out["title"] == "이번 주, 지수는 쉬었어요" and out["source"] == "ai"
    assert "9999" not in out["hook"] and "12345" not in out["sections"][0]["body"]
    engine.dispose()


def test_the_report_model_and_reasoning_override_the_deep_tier(monkeypatch):
    cfg = {"llm_provider": "chatgpt", "deep_think_llm": "gpt-6-sol", "quick_think_llm": "gpt-6-luna"}
    assert rp.model_config(cfg) == cfg
    got = rp.model_config(cfg, "gpt-6-astra", "xhigh")
    assert got["deep_think_llm"] == "gpt-6-astra" and got["openai_reasoning_effort"] == "xhigh"
    assert got["anthropic_effort"] == "xhigh" and cfg["deep_think_llm"] == "gpt-6-sol"      # a copy

    seen = {}

    class Llm:
        def invoke(self, prompt):
            return type("M", (), {"content": "답"})()

    class Client:
        def get_llm(self):
            return Llm()

    def tier(config, tier, **extra):
        seen.update(config=config, tier=tier, extra=extra)
        return Client()
    import tradingagents.llm_clients as lc
    monkeypatch.setattr(lc, "create_tier_client", tier)
    assert rp.model_call("gpt-6-astra", "high")("p") == "답"
    assert seen["tier"] == "deep" and seen["config"]["deep_think_llm"] == "gpt-6-astra"
    assert seen["config"]["openai_reasoning_effort"] == "high" and seen["extra"]["max_retries"] == 0
