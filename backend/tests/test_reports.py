"""#495: the beginner report - the subject that moved most, its draft from the desk's numbers only, the
model's wording kept only where its numbers are the desk's, the schedule and the API."""

import json
import time
from datetime import UTC, date, datetime, timedelta

from desk import reports as rp


def _bars(closes, volume=1e6, last_volume=None):
    start = date(2023, 1, 2)
    out = []
    for i, c in enumerate(closes):
        v = last_volume if last_volume and i >= len(closes) - rp.WEEK else volume
        out.append({"at": f"{start + timedelta(days=i)}T00:00:00+00:00", "open": c, "high": c * 1.005,
                    "low": c * 0.995, "close": c, "volume": v})
    return out


def _calm(n=300):
    return [100 + (i % 10) * 0.3 for i in range(n)]


def _drop(n=300):
    closes = _calm(n)
    for k in range(rp.WEEK):            # the last week falls about 9%
        closes[n - rp.WEEK + k] = closes[n - rp.WEEK - 1] * (1 - 0.019 * (k + 1))
    return closes


def test_the_subject_is_the_one_that_moved_most_and_says_why():
    calm = rp.measure("^GSPC", "S&P500", "index", _bars(_calm()), [])
    hit = rp.measure("NVDA", "엔비디아", "favorite", _bars(_drop(), last_volume=3e6), [])
    subject, ranked = rp.pick([calm, hit, None])
    assert subject["symbol"] == "NVDA" and [c["symbol"] for c in ranked] == ["NVDA", "^GSPC"]
    assert subject["week_pct"] < -8 and subject["volume_x"] == 3.0
    assert any(r.startswith("1주 -") for r in subject["reasons"]) and "거래량 평소의 3.0배" in subject["reasons"]
    assert "전저점 아래" in subject["reasons"]
    assert rp.measure("X", "x", "index", _bars(_calm(40)), []) is None        # too short a history


def test_past_scenes_come_from_the_price_history_a_month_apart():
    closes = _calm(400)
    for at in (100, 105, 200):           # three earlier one-week drops of about 9%, two of them too close together
        for k in range(rp.WEEK):
            closes[at + k] = closes[at - 1] * (1 - 0.019 * (k + 1))
        for k in range(rp.WEEK, rp.WEEK + rp.AFTER + 5):
            closes[at + k] = closes[at + rp.WEEK - 1] * 1.05
    got = rp.similar(_bars(closes), -9.0)
    assert 1 <= len(got) <= rp.MAX_SIMILAR
    dates = [g["date"] for g in got]
    assert dates == sorted(dates, reverse=True)
    assert all(g["what"].startswith("1주 -") and "20거래일 뒤" in g["after"] for g in got)
    assert rp.similar(_bars(closes), 1.0) == []                               # a small move: no scenes


def test_the_draft_has_the_contract_shape_and_only_the_desks_numbers():
    subject, ranked = rp.pick([rp.measure(s, n, "index", *rp.fake_fetch(s)) for s, n in rp.INDEXES])
    out = rp.write(subject, ranked)
    assert out["source"] == "rules" and out["version"] == rp.VERSION and out["title"].endswith("?")
    assert 3 <= len(out["sections"]) <= 5
    for s in out["sections"]:
        assert s["title"] and s["body"] and s["term"]["name"] and s["image"] is None
        assert set(s["chart"]) == {"kind", "levels", "labels", "marks"}
    assert out["stages"][-1]["label"] == "최대" and out["checklist"]
    assert out["subject"] == {"symbol": subject["symbol"], "name": subject["name"], "kind": "index"}
    assert out["why"]["candidates"][0]["symbol"] == subject["symbol"] and len(out["why"]["candidates"]) == 4
    assert "예측이 아니라" in out["closing"] and isinstance(out["similar"], list)


def test_the_models_wording_is_kept_only_where_its_numbers_are_the_desks():
    subject, ranked = rp.pick([rp.measure("^KS11", "코스피", "index", *rp.fake_fetch("^KS11"))])
    draft = rp.write(subject, ranked)

    def model(prompt):
        assert "JSON 만 답해라" in prompt and "title" in prompt
        return json.dumps({"title": "코스피, 지금 겁먹어야 할까?", "summary": "다음 주 15% 오를 거예요.",
                           "hook": "코스피, 지금 겁먹어야 할까?",
                           "sections": [{"body": "많이 빠졌어요. 목표는 9999 예요."}]}, ensure_ascii=False)

    out = rp.write(subject, ranked, model)
    assert out["source"] == "ai" and out["title"] == "코스피, 지금 겁먹어야 할까?"
    assert out["summary"] == draft["summary"]                                 # its only sentence had a new number
    assert out["sections"][0]["body"] == "많이 빠졌어요."
    assert out["sections"][1] == draft["sections"][1]

    def boom(prompt):
        raise TimeoutError("slow")
    failed = rp.write(subject, ranked, boom)
    assert failed["source"] == "rules" and failed["fallback"] == "TimeoutError"


def test_the_next_report_waits_for_a_korean_trading_day_after_the_close():
    kst = rp.market_calendar.KST
    monday = datetime(2026, 11, 16, 16, 31, tzinfo=kst)
    assert rp.due_after(monday, 3, monday).astimezone(kst) == datetime(2026, 11, 19, 16, 30, tzinfo=kst)
    thursday = datetime(2026, 11, 19, 16, 31, tzinfo=kst)                    # +3 is a Sunday: Monday
    assert rp.due_after(thursday, 3, thursday).astimezone(kst) == datetime(2026, 11, 23, 16, 30, tzinfo=kst)
    now = datetime(2026, 11, 17, 3, 0, tzinfo=UTC)                           # none yet: today's close
    assert rp.due_after(None, 3, now).astimezone(kst) == datetime(2026, 11, 17, 16, 30, tzinfo=kst)


def test_the_api_writes_one_in_the_background_lists_and_reads_it(db_url):
    from tests.test_api import _client

    with _client(db_url) as client:
        first = client.get("/api/reports").json()
        assert first["items"] == [] and first["generating"] is False and first["next_before"] is None
        assert client.post("/api/reports").status_code == 202
        deadline = time.monotonic() + 30
        while (listed := client.get("/api/reports").json())["generating"] or not listed["items"]:
            assert time.monotonic() < deadline, "the report was not written"
            time.sleep(0.1)
        item = listed["items"][0]
        assert set(item) == {"id", "created_at", "title", "summary", "subject", "source"}
        assert item["subject"]["kind"] == "index" and item["created_at"].endswith("Z")
        full = client.get(f"/api/reports/{item['id']}").json()
        assert full["id"] == item["id"] and 3 <= len(full["sections"]) <= 5 and full["why"]["text"]
        assert all(s["image"] is None for s in full["sections"])
        assert client.get("/api/reports/nope").status_code == 404
        assert client.post("/api/reports").status_code == 429                 # one just made: not again so soon
        client.cookies.clear()
        assert client.get("/api/reports").status_code == 401
        assert client.post("/api/reports").status_code in (401, 403)


def test_only_the_owners_favorites_are_candidates(db_url):
    from desk.auth import Auth
    from desk.db import favorites, make_engine, migrate, now

    migrate(db_url)
    engine = make_engine(db_url)
    auth = Auth(engine, timedelta(hours=1), timedelta(hours=1))
    auth.create_user("owner@example.com", "pw-owner-1234")
    auth.create_user("guest@example.com", "pw-guest-1234")
    with engine.begin() as conn:
        conn.execute(favorites.insert(), [
            {"user_id": 1, "ticker": "NVDA", "name": "엔비디아", "position": 0, "created_at": now()},
            {"user_id": 2, "ticker": "TSLA", "name": "테슬라", "position": 0, "created_at": now()}])
    desk = rp.Reports(engine, rp.fake_fetch)
    got = [c[0] for c in desk.candidates()]
    engine.dispose()
    assert got[:len(rp.INDEXES)] == [s for s, _ in rp.INDEXES] and "NVDA" in got and "TSLA" not in got


def test_the_last_line_is_always_under_the_close_even_below_the_prior_low():
    f = rp._facts(rp.measure("^KS11", "코스피", "index", _bars(_drop()), []))
    f["supports"], f["prior_low"], f["atr"] = [], f["close"] * 1.05, 1.0     # broke the low, nothing under it
    out = rp.rules(f)
    assert out["title"].startswith("코스피, 전저점이 깨졌")
    assert all(s["high"] < f["close"] for s in out["stages"])
    assert out["stages"][-1]["label"] == "최대" and 3 <= len(out["sections"]) <= 5


def test_an_ordinary_week_of_a_3x_fund_does_not_beat_an_unusual_index_week():
    wild = [100 * (1 + 0.12 * (1 if (i // 5) % 2 else -1) / 5) ** (i % 5) for i in range(300)]
    lever = rp.measure("SOXL", "SOXL", "favorite", _bars(wild), [])
    index = rp.measure("^GSPC", "S&P500", "index", _bars(_drop()), [])
    assert abs(lever["week_pct"]) > abs(index["week_pct"]) * 0.8
    assert rp.pick([lever, index])[0]["symbol"] == "^GSPC"


def test_korean_bars_are_dated_on_the_korean_day():
    bars = _bars(_drop())
    for b in bars:                        # a KRX daily bar opens at 15:00 UTC the day before
        d = datetime.fromisoformat(b["at"]) - timedelta(hours=9)
        b["at"] = d.isoformat()
    m = rp.measure("^KS11", "코스피", "index", bars, [])
    assert m["as_of"] == _bars(_drop())[-1]["at"][:10]


def test_a_rate_limit_reaches_the_slot_and_an_odd_answer_falls_back():
    subject, ranked = rp.pick([rp.measure("^KS11", "코스피", "index", *rp.fake_fetch("^KS11"))])

    def limited(prompt):
        raise RuntimeError("429 rate limit reached: usage_limit_reached")
    if rp.runner.rate_limited(RuntimeError("429 rate limit reached: usage_limit_reached")):
        try:
            rp.write(subject, ranked, limited)
            raise AssertionError("the rate limit was swallowed")
        except RuntimeError:
            pass
    odd = rp.write(subject, ranked, lambda p: json.dumps({"sections": 7, "stages": "x", "checklist": [3]}))
    assert odd["source"] in ("rules", "ai") and len(odd["sections"]) >= 3


def test_an_unknown_page_cursor_is_an_empty_page(db_url):
    from desk.db import make_engine, migrate

    migrate(db_url)
    engine = make_engine(db_url)
    desk = rp.Reports(engine, rp.fake_fetch, every_days=0)
    made = [desk.make() for _ in range(3)]
    page = desk.list(limit=2)
    assert [i["id"] for i in page["items"]] == [made[2]["id"], made[1]["id"]] and page["next_before"] == made[1]["id"]
    assert [i["id"] for i in desk.list(limit=2, before=page["next_before"])["items"]] == [made[0]["id"]]
    assert desk.list(before="rp_nope")["items"] == [] and page["next_due"] is None
    engine.dispose()
