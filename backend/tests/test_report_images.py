"""#497: the optional explainer pictures of a report - off without a key, only where they hide no chart, within
the caps (counted before each call, closed when the ledger fails), a failure leaves the report without them,
and the key never shows up in a log, an error or a repr."""

import base64
import json
import logging
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from desk import report_images as ri
from desk import report_plan
from desk import reports as rp

KEY = "sk-test-0123456789abcdefSECRET"
PNG_BYTES = ri.PNG + b"\x00" * 64
RID = "rp_0123456789ab01234567"


def _report(rid: str = RID, symbol: str | None = None) -> dict:
    measured = [rp.measure(s, n, "index", *rp.fake_fetch(s)) for s, n in rp.INDEXES if symbol in (None, s)]
    subject, ranked = rp.pick(measured)
    return {**rp.write(subject, ranked), "id": rid}


def _ok(seen: list | None = None, png: bytes = PNG_BYTES):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png).decode()}]})
    return httpx.MockTransport(handle)


def _now():
    return datetime.now(UTC)


def test_without_a_key_nothing_is_drawn(monkeypatch, tmp_path):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls = []
    pics = ri.from_env(tmp_path)
    pics.transport = _ok(calls)
    assert pics.enabled is False and pics.room() == 0
    assert pics.draw(_report()) == {} and calls == [] and not list(tmp_path.iterdir())
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("DESK_REPORT_IMAGES_PER_MONTH", "0")             # a cap of 0 is off too
    assert ri.from_env(tmp_path).enabled is False
    assert ri.ReportImages("sk-키가-잘못", tmp_path).enabled is False      # a pasted key with a stray character


def test_a_key_with_another_llm_provider_is_warned_about(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.delenv("TRADINGAGENTS_LLM_PROVIDER", raising=False)      # the library's default: openai
    with caplog.at_level(logging.WARNING):
        assert ri.from_env(tmp_path).enabled is True
    assert "analyses use this key" in caplog.text and KEY not in caplog.text
    caplog.clear()
    monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "chatgpt")
    with caplog.at_level(logging.WARNING):
        ri.from_env(tmp_path)
    assert "analyses use this key" not in caplog.text


@pytest.mark.parametrize("symbol", [s for s, _ in rp.INDEXES])
def test_pictures_go_only_where_they_hide_no_chart_and_use_the_reports_words(symbol):
    report = _report(symbol=symbol)
    plan = ri.plan(report)
    assert 1 <= len(plan) <= ri.PER_REPORT
    assert len({p["section"] for p in plan}) == len(plan)                   # one picture a section
    for p in plan:
        section = report["sections"][p["section"]]
        assert section["chart"]["kind"] == "none"                          # the page shows it in the chart's place
        assert ri.NO_OTHER_TEXT in p["prompt"]
        if "staircase" not in p["prompt"]:
            assert section["term"]["name"] in p["prompt"] and section["term"]["name"] in p["alt"]


def test_the_staircase_needs_two_stages_and_a_free_section():
    def sec(kind, term=None):
        return {"title": "t", "body": "b", "term": term or {}, "chart": {"kind": kind, "levels": [], "labels": [], "marks": []}}
    stages = [{"label": "1단계", "low": 2410.0, "high": 2450.0}, {"label": "2단계", "low": 2300.0, "high": 2350.0}]
    report = {"subject": {"name": "코스피"}, "stages": stages,
              "sections": [sec("levels"), sec("zone"), sec("none")]}
    [stairs] = ri.plan(report)
    assert stairs["section"] == 2 and '"1단계": 2,410 ~ 2,450' in stairs["prompt"] and "코스피" in stairs["alt"]
    assert ri.plan({**report, "stages": stages[:1]}) == []                  # one stage is no staircase
    assert ri.plan({**report, "sections": [sec("levels"), sec("zone")]}) == []   # no free section: no picture
    assert ri.plan({"sections": [], "stages": []}) == []


def test_pictures_are_saved_linked_and_counted(tmp_path):
    seen: list[httpx.Request] = []
    report = _report()
    pics = ri.ReportImages(KEY, tmp_path, transport=_ok(seen))
    got = pics.draw(report)
    assert len(got) == len(ri.plan(report)) and len(seen) == len(got)
    for n, (_i, img) in enumerate(sorted(got.items(), key=lambda kv: kv[1]["url"]), 1):
        assert img["url"] == f"/api/reports/{RID}/images/{n}" and img["alt"]
        assert ri.image_path(RID, n, tmp_path).read_bytes() == PNG_BYTES
    body = json.loads(seen[0].content)
    assert body["model"] == ri.DEFAULT_MODEL and body["n"] == 1 and seen[0].headers["authorization"] == f"Bearer {KEY}"
    assert pics.ledger.counts(_now()) == (len(got), len(got))


def test_the_day_and_month_caps_stop_the_pictures(tmp_path):
    calls: list = []
    pics = ri.ReportImages(KEY, tmp_path, per_day=1, per_month=20, transport=_ok(calls))
    assert len(pics.draw(_report())) == 1 and len(calls) == 1               # the day's one picture
    assert pics.draw(_report("rp_0123456789ab89abcdef")) == {} and len(calls) == 1
    monthly = ri.ReportImages(KEY, tmp_path / "m", per_day=10, per_month=3, transport=_ok())
    monthly.ledger.add(_now(), 2)
    assert monthly.room() == 1 and len(monthly.draw(_report())) == 1 and monthly.room() == 0


def test_the_ledger_keeps_this_month_and_a_year_of_months(tmp_path):
    ledger = ri.Ledger(tmp_path / "usage.json")
    old = datetime(2026, 9, 30, tzinfo=UTC)
    for m in range(1, 13):
        ledger.add(datetime(2025, m, 1, tzinfo=UTC))
    ledger.add(old)
    ledger.add(old + timedelta(days=1))                                       # October: September's days go
    raw = json.loads((tmp_path / "usage.json").read_text(encoding="utf-8"))
    assert list(raw["days"]) == ["2026-10-01"] and len(raw["months"]) == 13 and "2025-01" not in raw["months"]


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"error": {"message": f"Incorrect API key provided: {KEY}"}}),
    httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"GIF89a not a png").decode()}]}),
    httpx.Response(200, json={"unexpected": True}),
])
def test_a_failed_picture_leaves_the_report_without_it_and_never_logs_the_key(tmp_path, caplog, response):
    caplog.set_level(logging.DEBUG)
    report = _report()
    pics = ri.ReportImages(KEY, tmp_path, transport=httpx.MockTransport(lambda _r: response))
    assert pics.draw(report) == {}
    tried = len(ri.plan(report))
    assert pics.ledger.counts(_now()) == (tried, tried)                     # asked for, so counted (may be billed)
    assert KEY not in caplog.text and "report image" in caplog.text
    assert KEY not in repr(pics) and KEY not in str(vars(pics).get("transport"))


def test_a_timeout_is_counted_and_a_network_error_is_a_missing_picture(tmp_path, caplog):
    def slow(_r):
        raise httpx.ReadTimeout("slow")
    pics = ri.ReportImages(KEY, tmp_path, per_day=1, transport=httpx.MockTransport(slow))
    assert pics.draw(_report()) == {} and pics.ledger.counts(_now()) == (1, 1) and pics.room() == 0
    def boom(_r):
        raise httpx.ConnectError("refused")
    assert ri.ReportImages(KEY, tmp_path / "b", transport=httpx.MockTransport(boom)).draw(_report()) == {}
    assert KEY not in caplog.text


def test_after_a_failed_picture_the_next_one_is_number_1(tmp_path):
    answers = iter([httpx.Response(500), None])

    def handle(_r):
        got = next(answers)
        return got or httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(PNG_BYTES).decode()}]})
    report = _report()
    assert len(ri.plan(report)) == 2
    got = ri.ReportImages(KEY, tmp_path, transport=httpx.MockTransport(handle)).draw(report)
    [img] = got.values()
    assert img["url"].endswith("/images/1") and ri.image_path(RID, 1, tmp_path).is_file()


def test_a_ledger_that_cannot_be_read_or_written_stops_the_pictures(tmp_path, monkeypatch, caplog):
    calls: list = []
    pics = ri.ReportImages(KEY, tmp_path, transport=_ok(calls))
    (tmp_path / "usage.json").write_text('{"days": [], "months": {}}', encoding="utf-8")    # damaged: closed
    assert pics.room() == 0 and pics.draw(_report()) == {} and calls == []
    (tmp_path / "usage.json").write_text("not json", encoding="utf-8")
    assert pics.room() == 0 and pics.draw(_report()) == {} and calls == []
    (tmp_path / "usage.json").unlink()                                      # missing: starts from zero
    assert pics.room() == 2

    def cannot(*_a, **_k):
        raise OSError("read-only volume")
    monkeypatch.setattr(pics.ledger, "add", cannot)
    assert pics.draw(_report()) == {} and calls == []                       # not counted: not asked for
    assert "cannot be written" in caplog.text


def test_stop_and_the_deadline_end_a_reports_drawing(tmp_path):
    calls: list = []
    pics = ri.ReportImages(KEY, tmp_path, transport=_ok(calls))
    pics.stop.set()
    assert pics.draw(_report()) == {} and calls == []
    late = ri.ReportImages(KEY, tmp_path / "late", transport=_ok(calls), deadline_sec=ri.MIN_CALL_SEC - 1)
    assert late.draw(_report()) == {} and calls == []


def test_an_image_path_never_leaves_the_folder(tmp_path):
    assert ri.image_path(RID, 1, tmp_path) == tmp_path / RID / "1.png"
    for rid, n in (("../etc", 1), ("rp_../../x", 1), (RID + "/..", 1), (RID + "\n", 1), ("rp_0123", 1),
                   (RID, 0), (RID, 3), ("", 1)):
        assert ri.image_path(rid, n, tmp_path) is None
    assert ri.ReportImages(KEY, tmp_path).draw({**_report(), "id": "../x"}) == {}


def _client_with(db_url, pictures):
    from desk.api import create_app
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.runner import make_fake_runner
    from desk.settings import Settings
    from tests.test_api import OWNER, PASSWORD

    client = TestClient(create_app(Settings(database_url=db_url, fake_runs=True, fake_delay=0),
                                   runner=make_fake_runner(0), pictures=pictures), base_url="https://testserver")
    engine = make_engine(db_url)
    Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(OWNER, PASSWORD)
    engine.dispose()
    assert client.post("/api/auth/login", json={"username": OWNER, "password": PASSWORD}).status_code == 204
    return client


def _made(client) -> dict:
    assert client.post("/api/reports").status_code == 202
    deadline = time.monotonic() + 30
    while (listed := client.get("/api/reports").json())["generating"] or not listed["items"]:
        assert time.monotonic() < deadline, "the report was not written"
        time.sleep(0.1)
    return client.get(f"/api/reports/{listed['items'][0]['id']}").json()


@pytest.mark.parametrize("ok", [True, False])
def test_a_new_report_gets_its_pictures_before_the_push_and_keeps_going_without(db_url, tmp_path, monkeypatch, ok):
    from desk.db import make_engine

    # The Saturday wrap-up fixture has charts on every section. Exercise the image path on any day.
    monkeypatch.setattr(report_plan, "kind_for", lambda _now: "daily")
    requests: list[httpx.Request] = []

    def fail(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    transport = _ok(requests) if ok else httpx.MockTransport(fail)
    pics = ri.ReportImages(KEY, tmp_path, transport=transport)
    pushed: list = []
    with _client_with(db_url, pics) as client:
        class Pusher:                    # what the report held when the push went out
            def to_user(self, _owner, payload):
                time.sleep(0.3)          # a slow push: the test must wait for it, not race it
                engine = make_engine(db_url)
                rid = payload["url"].rsplit("/", 1)[-1]
                pushed.append([s["image"] for s in rp.Reports(engine, rp.fake_fetch).get(rid)["sections"]])
                engine.dispose()
        client.app.state.pusher = Pusher()
        full = _made(client)
        deadline = time.monotonic() + 10        # the push goes out after ``generating`` is false again
        while not pushed:
            assert time.monotonic() < deadline, "the report was not pushed"
            time.sleep(0.05)
        images = [s["image"] for s in full["sections"] if s["image"]]
        assert requests
        if ok:
            assert 1 <= len(images) <= 2 and pushed and [i for i in pushed[0] if i] == images
            for img in images:
                r = client.get(img["url"])
                assert r.status_code == 200 and r.headers["content-type"] == "image/png"
            assert all(full["sections"][i]["chart"]["kind"] == "none"
                       for i, s in enumerate(full["sections"]) if s["image"])
        else:
            assert images == [] and pushed and not any(pushed[0])           # the report and its push still go out


def test_the_pictures_are_stored_on_the_report_and_served_with_a_session(db_url, monkeypatch, tmp_path):
    from desk.db import make_engine, migrate
    from tests.test_api import _client

    monkeypatch.setattr(ri, "CACHE_DIR", tmp_path)
    migrate(db_url)
    engine = make_engine(db_url)
    desk = rp.Reports(engine, rp.fake_fetch)
    saved = desk.save(_report())
    img = {"url": f"/api/reports/{saved['id']}/images/1", "alt": "단계별 대응"}
    assert desk.set_images(saved["id"], {1: img, 99: img}) is True             # an index past the end is skipped
    body = desk.get(saved["id"])
    assert body["sections"][1]["image"] == img and all(s["image"] is None for i, s in enumerate(body["sections"]) if i != 1)
    assert desk.set_images("rp_missing", {0: img}) is False and desk.set_images(saved["id"], {}) is False
    engine.dispose()
    (tmp_path / saved["id"]).mkdir()
    (tmp_path / saved["id"] / "1.png").write_bytes(PNG_BYTES)
    with _client(db_url) as client:
        r = client.get(f"/api/reports/{saved['id']}/images/1")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG_BYTES
        assert "max-age=600" in r.headers["cache-control"]
        assert client.get(f"/api/reports/{saved['id']}/images/2").status_code == 404
        assert client.get(f"/api/reports/{saved['id']}/images/3").status_code == 404
        assert client.get("/api/reports/rp_..%2F..%2Fx/images/1").status_code == 404
        client.cookies.clear()
        assert client.get(f"/api/reports/{saved['id']}/images/1").status_code == 401


def test_the_request_uses_a_live_model_and_only_what_gpt_image_models_take(tmp_path, monkeypatch, caplog):
    seen: list = []
    report = _report()
    ri.ReportImages(KEY, tmp_path, transport=_ok(seen)).draw(report)
    body = json.loads(seen[0].content)
    assert body == {"model": ri.DEFAULT_MODEL, "prompt": body["prompt"], "n": 1, "size": "1536x1024",
                    "quality": "medium"}                    # no response_format: GPT image models refuse it
    assert ri.DEFAULT_MODEL not in ri.RETIRED_MODELS and len(body["prompt"]) <= 32000
    for retired in ("gpt-image-1", " dall-e-3 "):           # shut down: every call would be a 400
        monkeypatch.setenv("OPENAI_API_KEY", KEY)
        monkeypatch.setenv("TRADINGAGENTS_LLM_PROVIDER", "chatgpt")
        monkeypatch.setenv("DESK_REPORT_IMAGE_MODEL", retired)
        with caplog.at_level(logging.WARNING):
            assert ri.from_env(tmp_path).model == ri.DEFAULT_MODEL
        assert "is shut down" in caplog.text and KEY not in caplog.text
    monkeypatch.setenv("DESK_REPORT_IMAGE_MODEL", "gpt-image-2.5-sunburst")
    assert ri.from_env(tmp_path).model == "gpt-image-2.5-sunburst"
    caplog.clear()
    monkeypatch.setenv("DESK_REPORT_IMAGE_MODEL", f"{KEY}\nforged line")    # a key pasted in the wrong place
    with caplog.at_level(logging.WARNING):
        assert ri.from_env(tmp_path).model == ri.DEFAULT_MODEL
    assert "not a model name" in caplog.text and KEY not in caplog.text and "forged" not in caplog.text


def test_a_refused_picture_logs_only_the_error_code_type_and_param_and_ends_the_report(tmp_path, caplog):
    seen: list = []

    def refuse(request):
        seen.append(request)
        return httpx.Response(400, json={"error": {
            "message": f"The model has been shut down. key {KEY}", "type": "invalid_request_error",
            "param": "model", "code": "model_not_found"}})
    caplog.set_level(logging.DEBUG)
    report = _report()
    assert len(ri.plan(report)) == 2
    pics = ri.ReportImages(KEY, tmp_path, transport=httpx.MockTransport(refuse))
    assert pics.draw(report) == {}
    assert len(seen) == 1 and pics.ledger.counts(_now()) == (1, 1)        # the second would be refused too
    assert "HTTP 400 (code=model_not_found type=invalid_request_error param=model" in caplog.text
    assert KEY not in caplog.text and "shut down. key" not in caplog.text
    assert ri.plan(report)[0]["prompt"][:40] not in caplog.text


def test_a_blocked_prompt_tries_the_next_picture_and_odd_error_fields_are_not_logged(tmp_path, caplog):
    answers = iter([
        httpx.Response(400, json={"error": {"code": "moderation_blocked", "type": "image_generation_user_error",
                                            "param": None, "message": "blocked"}}),
        httpx.Response(429, json={"error": {"code": f"x {KEY}", "type": "a" * 65, "param": "sk-proj-abc123"}}),
    ])
    seen: list = []

    def handle(request):
        seen.append(request)
        return next(answers)
    caplog.set_level(logging.WARNING)
    assert ri.ReportImages(KEY, tmp_path, transport=httpx.MockTransport(handle)).draw(_report()) == {}
    assert len(seen) == 2
    assert "code=moderation_blocked type=image_generation_user_error param=-" in caplog.text
    assert "HTTP 429 (code=? type=? param=?" in caplog.text and KEY not in caplog.text
    caplog.clear()
    plain = ri.ReportImages(KEY, tmp_path / "b", transport=httpx.MockTransport(lambda _r: httpx.Response(502, text="<html>")))
    plain.draw(_report())
    assert "HTTP 502 (code=- type=- param=-" in caplog.text
