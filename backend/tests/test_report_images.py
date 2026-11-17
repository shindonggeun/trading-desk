"""#497: the optional explainer pictures of a report - off without a key, within the caps, a failure leaves
the report without them, and the key never shows up in a log, an error or a repr."""

import base64
import json
import logging
from datetime import UTC, datetime

import httpx
import pytest

from desk import report_images as ri
from desk import reports as rp

KEY = "sk-test-0123456789abcdefSECRET"
PNG_BYTES = ri.PNG + b"\x00" * 64
RID = "rp_0123456789ab01234567"


def _report(rid: str = RID) -> dict:
    subject, ranked = rp.pick([rp.measure(s, n, "index", *rp.fake_fetch(s)) for s, n in rp.INDEXES])
    return {**rp.write(subject, ranked), "id": rid}


def _ok(seen: list | None = None, png: bytes = PNG_BYTES):
    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(png).decode()}]})
    return httpx.MockTransport(handle)


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


def test_the_pictures_use_only_the_reports_labels_and_numbers():
    report = _report()
    plan = ri.plan(report)
    assert 1 <= len(plan) <= ri.PER_REPORT
    stairs = plan[0]
    for s in report["stages"][:3]:
        assert s["label"] in stairs["prompt"]
    assert ri.NO_OTHER_TEXT in stairs["prompt"] and report["subject"]["name"] in stairs["alt"]
    assert len({p["section"] for p in plan}) == len(plan)                   # one picture a section
    assert all(0 <= p["section"] < len(report["sections"]) for p in plan)
    assert ri.plan({"sections": [], "stages": []}) == []


def test_pictures_are_saved_linked_and_counted(tmp_path):
    seen: list[httpx.Request] = []
    pics = ri.ReportImages(KEY, tmp_path, transport=_ok(seen))
    got = pics.draw(_report())
    assert len(got) == len(ri.plan(_report())) and len(seen) == len(got)
    for n, (_i, img) in enumerate(sorted(got.items(), key=lambda kv: kv[1]["url"]), 1):
        assert img["url"] == f"/api/reports/{RID}/images/{n}" and img["alt"]
        assert ri.image_path(RID, n, tmp_path).read_bytes() == PNG_BYTES
    body = json.loads(seen[0].content)
    assert body["model"] == ri.DEFAULT_MODEL and body["n"] == 1 and seen[0].headers["authorization"] == f"Bearer {KEY}"
    now = datetime.now(UTC)
    assert pics.ledger.counts(now) == (len(got), len(got))


def test_the_day_and_month_caps_stop_the_pictures(tmp_path):
    calls: list = []
    pics = ri.ReportImages(KEY, tmp_path, per_day=1, per_month=20, transport=_ok(calls))
    assert len(pics.draw(_report())) == 1 and len(calls) == 1               # the day's one picture
    assert pics.draw(_report("rp_0123456789ab89abcdef")) == {} and len(calls) == 1
    monthly = ri.ReportImages(KEY, tmp_path / "m", per_day=10, per_month=3, transport=_ok())
    monthly.ledger.add(datetime.now(UTC), 2)
    assert monthly.room() == 1 and len(monthly.draw(_report())) == 1 and monthly.room() == 0


@pytest.mark.parametrize("response", [
    httpx.Response(500, json={"error": {"message": f"Incorrect API key provided: {KEY}"}}),
    httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(b"GIF89a not a png").decode()}]}),
    httpx.Response(200, json={"unexpected": True}),
])
def test_a_failed_picture_leaves_the_report_without_it_and_never_logs_the_key(tmp_path, caplog, response):
    caplog.set_level(logging.DEBUG)
    pics = ri.ReportImages(KEY, tmp_path, transport=httpx.MockTransport(lambda _r: response))
    assert pics.draw(_report()) == {}
    assert pics.ledger.counts(datetime.now(UTC)) == (0, 0)                   # nothing drawn, nothing counted
    assert KEY not in caplog.text and "report image" in caplog.text
    assert KEY not in repr(pics) and KEY not in str(vars(pics).get("transport"))


def test_a_network_error_is_a_missing_picture(tmp_path, caplog):
    def boom(_r):
        raise httpx.ConnectError("refused")
    pics = ri.ReportImages(KEY, tmp_path, transport=httpx.MockTransport(boom))
    assert pics.draw(_report()) == {} and KEY not in caplog.text


def test_an_image_path_never_leaves_the_folder(tmp_path):
    assert ri.image_path(RID, 1, tmp_path) == tmp_path / RID / "1.png"
    for rid, n in (("../etc", 1), ("rp_../../x", 1), (RID + "/..", 1), ("rp_0123", 1), (RID, 0), (RID, 3), ("", 1)):
        assert ri.image_path(rid, n, tmp_path) is None
    assert ri.ReportImages(KEY, tmp_path).draw({**_report(), "id": "../x"}) == {}


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
        assert client.get(f"/api/reports/{saved['id']}/images/2").status_code == 404
        assert client.get(f"/api/reports/{saved['id']}/images/3").status_code == 404
        assert client.get("/api/reports/rp_..%2F..%2Fx/images/1").status_code == 404
        client.cookies.clear()
        assert client.get(f"/api/reports/{saved['id']}/images/1").status_code == 401
