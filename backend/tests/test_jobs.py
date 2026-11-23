"""#623 the background loops' runner (desk.jobs) and GET /api/status."""

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta

import pytest

from desk import jobs as jobs_mod
from desk.jobs import Jobs


@pytest.fixture(autouse=True)
def fast(monkeypatch):
    monkeypatch.setattr(Jobs, "min_delay", 0.0)


async def _until(cond, limit=2.0):
    end = asyncio.get_running_loop().time() + limit
    while not cond():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.005)


def test_a_failing_pass_is_logged_and_the_next_one_runs(caplog):
    calls = []

    async def step() -> float:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("secret-looking https://example.invalid?key=abc")
        return 0.0

    async def main():
        jobs = Jobs()
        jobs.add("demo", "데모", "test")
        task = jobs.spawn("demo", jobs.run_forever("demo", step, retry=0.0, failed="demo failed"))
        await _until(lambda: len(calls) >= 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return jobs

    with caplog.at_level(logging.ERROR):
        jobs = asyncio.run(main())
    j = jobs.get("demo")
    assert j.runs >= 3 and j.errors == 1 and j.last_ok is not None
    # the status keeps the type name only, never the message (it could hold a URL or a key)
    assert j.last_error == "RuntimeError" and "example.invalid" not in str(jobs.status())
    assert jobs.status()[0]["state"] == "fresh"           # the last pass succeeded
    assert "demo failed" in caplog.text
    assert j.ended == "cancelled"                         # cancellation is not "stopped"


def test_one_failing_source_marks_only_its_line():
    async def main():
        jobs = Jobs()
        jobs.add("news", "뉴스", "x")
        jobs.add("macro_news", "거시", "GDELT", "news")
        jobs.add("sector_news", "섹터", "GDELT", "news")
        seen = []

        async def step() -> float:
            with jobs.part("macro_news", "macro failed"):
                raise TimeoutError
            with jobs.part("sector_news", "sector failed"):
                seen.append("sector")
            return 60.0

        task = jobs.spawn("news", jobs.run_forever("news", step, retry=60.0))
        await _until(lambda: seen)
        await asyncio.sleep(0.01)
        task.cancel()
        return jobs, seen

    jobs, seen = asyncio.run(main())
    by = {s["name"]: s for s in jobs.status()}
    assert seen == ["sector"]                             # the failed source did not stop the next one
    assert by["macro_news"]["state"] == "error" and by["macro_news"]["last_error"] == "TimeoutError"
    assert by["sector_news"]["state"] == "fresh" and by["news"]["state"] == "fresh"
    assert by["sector_news"]["next_due"] == by["news"]["next_due"] and by["news"]["interval_sec"] == 60


def test_a_pass_overdue_past_its_grace_is_stale_and_unstarted_jobs_are_off():
    now = [datetime(2026, 11, 18, 0, 0, tzinfo=UTC)]
    jobs = Jobs(clock=lambda: now[0])
    j = jobs.add("krx", "KRX", "KRX OPEN API")
    jobs.add("funds", "13F", "SEC")
    j.enabled, j.looped, j.last_ok, j.interval = True, True, now[0], 900.0
    j.next_due = now[0] + timedelta(seconds=900)
    assert jobs.state(j, now[0] + timedelta(seconds=900 + 449)) == "fresh"      # grace = half the interval
    assert jobs.state(j, now[0] + timedelta(seconds=900 + 451)) == "stale"
    assert {s["name"]: s["state"] for s in jobs.status()}["funds"] == "off"
    jobs.mark_error("krx", "stopped: quota")
    assert jobs.state(j, now[0]) == "error" and j.last_error == "stopped: quota"


def test_a_task_that_ends_on_its_own_is_logged_and_shown_stopped(caplog):
    async def boom():
        raise ValueError("x")

    async def main():
        jobs = Jobs()
        jobs.add("kis_stream", "스트림", "KIS")
        task = jobs.spawn("kis_stream", boom())
        with pytest.raises(ValueError):
            await task
        await asyncio.sleep(0)
        return jobs

    with caplog.at_level(logging.ERROR, logger=jobs_mod.__name__):
        jobs = asyncio.run(main())
    assert jobs.status()[0]["state"] == "stopped"
    assert "background job kis_stream ended: ValueError" in caplog.text


def test_status_needs_a_session_and_lists_the_jobs(db_url):
    from tests.test_api import OWNER, PASSWORD, _client

    with _client(db_url, login=False) as client:
        assert client.get("/api/status").status_code == 401
        client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD})
        body = client.get("/api/status").json()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00", body["now"])   # ms: #690 browser clock skew
    names = {j["name"]: j for j in body["jobs"]}
    assert {"listing", "news", "macro_news", "krx", "funds", "scoring", "purge"} <= set(names)
    assert names["news"]["state"] == "off" and names["macro_news"]["parent"] == "news"   # tests: feeds off
    assert names["listing"]["state"] in ("starting", "fresh", "error")                  # always started
    cool = {c["name"]: c for c in body["cooldowns"]}
    assert cool["chatgpt"]["until"] is None and cool["gdelt"]["left_sec"] == 0


def test_a_task_with_its_own_loop_shows_running_while_alive():
    async def main():
        jobs = Jobs()
        jobs.add("prewarm", "미리 읽기", "x")
        task = jobs.spawn("prewarm", asyncio.sleep(10))
        await asyncio.sleep(0)
        state = jobs.status()[0]["state"]
        task.cancel()
        return state

    assert asyncio.run(main()) == "running"


def test_krx_cooldowns_name_the_quota_and_a_refused_service():
    from desk import krx

    desk = krx.KrxDaily.__new__(krx.KrxDaily)
    desk.client = krx.Client("")
    at = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)                  # 12:00 KST
    desk._refused = {"opt_bydd_trd": at - timedelta(hours=1)}
    assert [n for n, _, _ in desk.cooldowns(at)] == ["krx_refused_opt_bydd_trd"]
    desk.client._quota_day = krx.kst_today(at)
    got = {n: u for n, _, u in desk.cooldowns(at)}
    assert got["krx_quota"] == datetime(2026, 11, 19, 0, 0, tzinfo=krx.KST)
    assert got["krx_refused_opt_bydd_trd"] == at + timedelta(hours=5)


def test_a_pass_that_returns_no_wait_keeps_the_loop_on_the_retry_interval(caplog):
    """#628: a step returning None (or nan, or text) used to end its loop at float(None)."""
    answers = [None, float("nan"), "soon", None, 0.0]
    calls = []

    async def step():
        calls.append(1)
        return answers[min(len(calls) - 1, len(answers) - 1)]

    async def main():
        jobs = Jobs()
        jobs.add("odd", "이상한 대기", "test")
        task = jobs.spawn("odd", jobs.run_forever("odd", step, retry=0.0))
        await _until(lambda: len(calls) >= 6)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return jobs

    with caplog.at_level(logging.WARNING):
        jobs = asyncio.run(main())
    j = jobs.get("odd")
    assert j.runs >= 6 and j.errors == 0 and j.ended == "cancelled"
    assert caplog.text.count("odd: a pass returned no wait") == 1          # warned once, not every pass
