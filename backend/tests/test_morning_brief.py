"""#571 the morning brief: the inputs, the desk's wording, the model's checked wording, the schedule, the API."""

import asyncio
import json
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from desk import morning_brief as mb
from desk.broker.base import Bar

# Wednesday 2026-11-18 06:40 KST: the US closed at 06:00, a Korean trading day
NOW = datetime(2026, 11, 17, 21, 40, tzinfo=UTC)


def _bars(closes, last=datetime(2026, 11, 17, 5, 0, tzinfo=UTC)):
    n = len(closes)
    return [Bar(at=(last - timedelta(days=n - 1 - i)).isoformat(), open=c, high=c, low=c, close=c, volume=1)
            for i, c in enumerate(closes)]


CLOSES = {"^GSPC": [6650.0, 6700.0], "^IXIC": [22000.0, 22264.0], "^DJI": [47000.0, 46900.0],
          "^SOX": [6000.0, 6150.0], "NQ=F": [25000.0, 24925.0], "ES=F": [6700.0, 6710.0],
          "^KS11": [3900.0, 3916.38], "^KQ11": [900.0, 891.0]}

FEAR = {"items": [{"key": "vix", "name": "VIX", "value": 17.2, "prev_day": 18.1, "band": "neutral"},
                  {"key": "cnn", "name": "CNN 공포탐욕", "value": None, "error": "HTTPError"}]}


def _flow(market):
    rec = {"date": "2026-11-17", "in_progress": False,
           "investors": {"foreigner": {"net": -123_400_000_000}, "institution": {"net": 50_000_000_000},
                         "individual": {"net": 70_000_000_000}}}
    today = {**rec, "date": "2026-11-18", "in_progress": True}
    return {"records": [today, rec], "error": None}


def _month(month):
    def ev(label, at_utc, importance=2):
        at = datetime.fromisoformat(at_utc)
        return {"label": label, "market": "us", "importance": importance, "at": at.isoformat(),
                "at_kst": at.astimezone(mb.KST).isoformat()}
    days = []
    if month == "2026-11":
        days = [
            {"date": "2026-11-17", "us": {"open": True},
             "events": [ev("CPI", "2026-11-17T13:30:00+00:00")],
             "earnings": [{"ticker": "HD", "name": "홈디포", "when": "before", "eps_estimate": 3.84},
                          {"ticker": "NVDA", "name": "엔비디아", "when": "after", "eps_estimate": 1.25},
                          {"ticker": "005930.KS", "name": "삼성전자", "when": "unknown", "eps_estimate": None}]},
            {"date": "2026-11-18", "us": {"open": True},
             "events": [ev("FOMC 의사록", "2026-11-19T19:00:00+00:00")],
             "earnings": [{"ticker": "WMT", "name": "월마트", "when": "before", "eps_estimate": 0.6}]},
        ]
    return {"month": month, "days": days}


def _macro():
    return {"themes": [{"label": "관세", "spike": 2.4, "quiet": False, "stale": False,
                        "headlines": [{"title": "Tariff talk lifts chips 12%"}]},
                       {"label": "조용함", "spike": 9, "quiet": True, "headlines": []}]}


async def _afear():
    return FEAR


async def _aflow(m):
    return _flow(m)


def _sources(**over):
    extra = [{"date": "2026-11-20", "label": "큰 IPO 상장", "market": "kr", "time_kst": "09:00"}]
    base = {"candles": lambda s, i, n: _bars(CLOSES[s]), "fear": _afear, "flow": _aflow, "month": _month,
            "macro": _macro, "day_call": None, "extra": lambda: extra}
    return mb.Sources(**{**base, **over})


def _bundle(**over):
    return mb.bundle(asyncio.run(mb.collect(_sources(**over), NOW)), NOW)


def test_the_bundle_holds_the_closes_the_flow_the_gauges_and_the_schedule():
    b = _bundle()
    assert b["date"] == "2026-11-18"
    assert b["us"]["나스닥"]["change"] == "+1.20%" and b["us"]["S&P500"]["close"] == "6,700"
    assert b["futures"]["나스닥100 선물"]["change"] == "-0.30%"
    assert b["kr"]["코스피"]["close"] == "3,916"
    # the finished day, not today's in-progress record; 억 원
    assert b["flow"]["KOSPI"]["date"] == "2026-11-17"
    assert b["flow"]["KOSPI"]["net"] == {"외국인": "-1,234억", "기관": "+500억", "개인": "+700억"}
    assert [g["name"] for g in b["fear"]] == ["VIX"]            # a failed gauge is left out
    assert [e["label"] for e in b["events_done"]] == ["CPI"]     # 8 hours before: done
    assert any(e["label"] == "FOMC 의사록" for e in b["events_ahead"])
    assert any(e["label"] == "큰 IPO 상장" for e in b["events_ahead"])      # the hand-kept list
    # the US day (Nov 17 ET, closed an hour ago) - no Korean tickers; the next US day ahead
    assert [r["ticker"] for r in b["earnings_done"]] == ["HD", "NVDA"]
    assert [r["ticker"] for r in b["earnings_ahead"]] == ["WMT"]
    assert b["themes"] == [{"label": "관세", "headline": "Tariff talk lifts chips 12%"}]
    assert b["missing"] == []


def test_a_failing_source_is_named_and_the_rest_still_make_a_brief():
    def boom(*_a):
        raise RuntimeError("down")

    async def aboom(*_a):
        raise RuntimeError("down")
    b = _bundle(fear=aboom, flow=aboom, macro=boom)
    assert {"fear", "flow KOSPI", "flow KOSDAQ", "macro"} <= set(b["missing"])
    out = mb.write(b)
    assert out["kr_flow"].startswith("어제 코스피 3,916") and "외국인" not in out["kr_flow"]
    assert out["missing"] == b["missing"]


def test_the_desk_brief_has_every_part_of_the_example_layout():
    out = mb.write(_bundle())
    assert out["source"] == "rules" and out["title"].startswith("간밤 나스닥 +1.20%")
    assert "나스닥 22,264 (+1.20%)" in out["numbers"] and "VIX 17.2 (어제 18.1)" in out["numbers"]
    assert "외국인 -1,234억" in out["kr_flow"]
    assert any("엔비디아(NVDA) 장 후 실적 발표" in d for d in out["done"])
    assert any("FOMC 의사록" in s for s in out["schedule"])
    assert out["good"] and out["bad"] and out["closing"]
    # US up, futures down after the close; foreigners sold KOSPI
    assert any("선물은 -0.30%" in x for x in out["bad"]) and any("팔았어요" in x for x in out["bad"])
    for k in mb.FIELDS:
        assert out[k], k


def test_the_models_wording_is_kept_only_where_its_numbers_are_the_bundles_and_it_is_korean():
    b = _bundle()
    draft = mb.rules(b)
    answer = {
        "title": "나스닥 +1.20% 상승 마감, 반도체 강세",                # numbers from the bundle: kept
        "numbers": ["나스닥 22,264 (+1.20%)", "엔비디아 매출 570억 달러"],   # a made-up number: that item dropped
        "kr_flow": "어제 외국인이 코스피를 -1,234억 팔았습니다。日本語の文です",   # Japanese: the draft stays
        "done": ["헤드라인의 12% 급등"],                            # a number only a headline had: dropped
        "closing": "오늘은 FOMC 의사록 전까지 확인 위주로 대응하세요.",
    }
    seen = []

    def model(prompt):
        seen.append(prompt)
        return "답:\n" + json.dumps(answer, ensure_ascii=False)
    out = mb.write(b, model)
    assert out["source"] == "ai"
    assert out["title"] == answer["title"] and out["closing"] == answer["closing"]
    assert out["numbers"] == ["나스닥 22,264 (+1.20%)"]
    assert out["kr_flow"] == draft["kr_flow"] and out["done"] == draft["done"]
    # the headline is shown as untrusted text, and the data the model sees has no headline in it
    data = seen[0].split("데이터: ", 1)[1].split("\n", 1)[0]
    assert "Tariff" not in data and "Tariff talk" in seen[0] and "_move" not in data


def test_a_failing_model_keeps_the_desk_brief_and_a_rate_limit_is_raised():
    b = _bundle()

    def broken(_p):
        raise TimeoutError()
    out = mb.write(b, broken)
    assert out["source"] == "rules" and out["fallback"] == "TimeoutError"

    class RateLimitError(Exception):
        pass

    def limited(_p):
        raise RateLimitError("429")
    with pytest.raises(RateLimitError):
        mb.write(b, limited)


def test_foreign_text_is_told_apart():
    assert mb.foreign("오늘 코스피는 강세") is None
    assert mb.foreign("今日の市場") == "ja"
    assert mb.foreign("今天市场上涨很多") == "zh"
    assert mb.foreign("반도체(半導體) 강세") is None or mb.foreign("반도체(半導體) 강세") == "zh"   # hanja: #567 decides


@pytest.mark.parametrize(("now_kst", "last", "want"), [
    ((2026, 11, 18, 6, 0), None, (2026, 11, 18)),                  # Wednesday before 07:00: today
    ((2026, 11, 18, 8, 30), None, (2026, 11, 18)),                 # a restart at 08:30: today still (overdue)
    ((2026, 11, 18, 8, 30), date(2026, 11, 18), (2026, 11, 19)),   # today's sent: tomorrow
    ((2026, 11, 18, 11, 30), None, (2026, 11, 19)),                # past the cutoff: tomorrow
    ((2026, 11, 20, 12, 0), None, (2026, 11, 23)),                 # Friday noon: Monday
    ((2026, 12, 24, 12, 0), None, (2026, 12, 28)),                 # Christmas (KRX closed), weekend: Monday
])
def test_the_next_automatic_brief(now_kst, last, want):
    now = datetime(*now_kst, tzinfo=mb.KST)
    due = mb.due_after(last, mb.DEFAULT_AT, now)
    assert due == datetime(*want, 7, 0, tzinfo=mb.KST)


def test_the_time_setting():
    assert mb.parse_at("07:00") == mb.DEFAULT_AT and mb.parse_at("off") is None and mb.parse_at("") is None


def test_the_store_keeps_briefs_by_korean_day(db_url):
    from desk.db import make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    briefs = mb.Briefs(engine)
    b = _bundle()
    first = briefs.save(mb.write(b), mb._public(b), None, "auto")
    second = briefs.save({**mb.write(b), "title": "다시"}, mb._public(b), "gpt-x", "manual")
    assert first["id"].startswith("mb_") and first["date"] == "2026-11-18" and first["model"] is None
    assert briefs.latest()["id"] == second["id"] and briefs.latest(date(2026, 11, 18))["title"] == "다시"
    assert briefs.latest(date(2026, 11, 17)) is None
    assert [x["id"] for x in briefs.list()] == [second["id"], first["id"]]
    assert briefs.count(date(2026, 11, 18), "manual") == 1 and briefs.last_auto_day() == date(2026, 11, 18)
    engine.dispose()


def _client(db_url):
    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD

    client = TestClient(create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0),
                                   runner=make_fake_runner(0)), base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
    return client


def test_the_api_writes_lists_and_pushes_a_brief_and_guards_the_quota(db_url):
    pushed = []
    with _client(db_url) as client:
        class Pusher:
            def broadcast(self, *kinds, payload):
                pushed.append((kinds, payload))
                return 1
        client.app.state.pusher = Pusher()
        assert client.get("/api/briefs/morning").status_code == 404
        assert client.get("/api/briefs/morning?date=2026-13-01").status_code == 422
        for n in range(mb.MANUAL_PER_DAY):
            assert client.post("/api/briefs/morning/run").status_code == 202
            deadline = time.monotonic() + 30
            while (listed := client.get("/api/briefs/morning/list").json())["generating"] or len(listed["items"]) <= n:
                assert time.monotonic() < deadline, "the brief was not written"
                time.sleep(0.05)
        assert client.post("/api/briefs/morning/run").status_code == 429
        got = client.get("/api/briefs/morning").json()
        assert got["trigger"] == "manual" and got["source"] == "rules" and got["generating"] is False
        assert got["next_due"] is None                       # scripted runs: no automatic brief
        for k in mb.FIELDS:
            assert k in got
        assert client.get(f"/api/briefs/morning?date={got['date']}").json()["id"] == got["id"]
        deadline = time.monotonic() + 10
        while len(pushed) < mb.MANUAL_PER_DAY:
            assert time.monotonic() < deadline, "the brief was not pushed"
            time.sleep(0.05)
        kinds, payload = pushed[0]
        assert kinds == ("morning_brief",) and payload["tag"] == "morning_brief"
        assert payload["title"].startswith("아침 시황 · ") and payload["url"].startswith("/#/briefs/morning?date=")
        # without a session
        client.cookies.clear()
        assert client.get("/api/briefs/morning").status_code == 401
        assert client.post("/api/briefs/morning/run").status_code in (401, 403)
