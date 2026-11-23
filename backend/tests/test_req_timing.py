"""#536 step 1: every response says how long it took (Server-Timing), slow ones log the route template only,
and per-route p50 / p95 can be read back - without changing any response."""

import asyncio
import logging

from desk import req_timing as rt


def _app(status=200, content_type=b"application/json", delay=0.0, route_path="/api/runs/{run_id}", cache=None,
         note=None):
    class Route:
        path = route_path

    async def app(scope, receive, send):
        await asyncio.sleep(delay)
        scope["route"] = Route()
        if cache:
            scope.setdefault("state", {})["cache"] = cache
        if note is not None:
            scope.setdefault("state", {})["timing_note"] = note
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", content_type)]})
        await send({"type": "http.response.body", "body": b"{}"})
    return app


def _call(mw, path="/api/runs/abc123", query=b"token=secret"):
    sent = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.request"}
    asyncio.run(mw({"type": "http", "method": "GET", "path": path, "query_string": query}, receive, send))
    return sent


def test_a_slow_request_logs_the_route_template_not_the_ids_or_the_query(caplog):
    t = rt.Timings()
    mw = rt.TimingMiddleware(_app(cache="miss"), t, slow_ms=0)
    with caplog.at_level(logging.WARNING, logger="desk.req_timing"):
        sent = _call(mw)
    head = dict(sent[0]["headers"])
    assert head[b"server-timing"].startswith(b"app;dur=") and sent[1]["body"] == b"{}"
    msg = " ".join(r.getMessage() for r in caplog.records)
    assert "GET /api/runs/{run_id}" in msg and "status 200" in msg and "cache miss" in msg
    assert "abc123" not in msg and "secret" not in msg and "token" not in msg
    assert t.stats()[0]["route"] == "/api/runs/{run_id}" and t.stats()[0]["count"] == 1


def test_a_slow_request_carries_the_handlers_counts_and_nothing_else(caplog):
    """#858 a batch's kind and how many tickers - only those keys, only short ASCII words or counts."""
    notes = [{"kind": "estimate-candles", "n": 24, "tickers": "NVDA,AAPL", "ticker": "NVDA", "flag": True},
             "kind=levels",                                            # only a dict is read
             {"ticker": "NVDA", "symbol": "005930"},                    # a single ticker under any other key
             {"kind": "레벨", "n": "x" * 25},                           # not ASCII, too long
             {"kind": "levels", "n": True}]                            # a bool is not a count
    with caplog.at_level(logging.WARNING, logger="desk.req_timing"):
        for note in notes:
            _call(rt.TimingMiddleware(_app(route_path="/api/batch/{kind}", note=note), rt.Timings(), slow_ms=0),
                  path="/api/batch/estimate-candles", query=b"tickers=NVDA,AAPL")
    first, *rest = (r.getMessage() for r in caplog.records)
    assert "GET /api/batch/{kind}" in first and "status 200 kind=estimate-candles n=24" in first
    assert "NVDA" not in first and "flag" not in first and "ticker" not in first
    assert [m.split("status 200", 1)[1] for m in rest] == ["", "", "", " kind=levels"]
    assert not any("NVDA" in m or "005930" in m or "레벨" in m for m in rest)


def test_the_batch_and_the_board_tell_the_slow_line_their_counts_not_their_tickers(db_url, caplog, monkeypatch):
    from tests.test_api import _client

    monkeypatch.setattr(rt.TimingMiddleware.__init__, "__defaults__", (0.0,))      # every request is "slow"
    with _client(db_url) as client, caplog.at_level(logging.WARNING, logger="desk.req_timing"):
        client.get("/api/batch/gaps?tickers=ZZTEST1,ZZTEST2")
        client.get("/api/estimates?tickers=ZZTEST1,ZZTEST2,ZZTEST3")
    lines = [r.getMessage() for r in caplog.records if "/api/batch/" in r.getMessage() or "/api/estimates" in r.getMessage()]
    assert any("GET /api/batch/{kind}" in m and "kind=gaps n=2" in m for m in lines)
    assert any("GET /api/estimates" in m and " n=3" in m for m in lines)
    assert not any("ZZTEST" in m for m in lines)


def test_a_fast_request_is_counted_but_not_logged_and_a_stream_is_left_out(caplog):
    t = rt.Timings()
    with caplog.at_level(logging.WARNING, logger="desk.req_timing"):
        _call(rt.TimingMiddleware(_app(), t))
        sent = _call(rt.TimingMiddleware(_app(content_type=b"text/event-stream", route_path="/api/runs/{run_id}/stream"), t,
                                         slow_ms=0))
    assert not caplog.records
    assert dict(sent[0]["headers"])[b"server-timing"]                 # the header still, the numbers not
    assert [s["route"] for s in t.stats()] == ["/api/runs/{run_id}"]


def test_percentiles_per_route_slowest_first():
    t = rt.Timings(keep=100)
    for ms in range(1, 101):
        t.add("/api/a", float(ms))
    t.add("/api/b", 5.0)
    a, b = t.stats()
    assert (a["route"], a["count"], a["p50_ms"], a["p95_ms"], a["max_ms"]) == ("/api/a", 100, 50.0, 95.0, 100.0)
    assert b["route"] == "/api/b" and b["p95_ms"] == 5.0
    t.reset()
    assert t.stats() == []


def test_the_api_carries_the_header_and_reads_the_numbers_back(db_url):
    from tests.test_api import _client

    with _client(db_url) as client:
        r = client.get("/api/health")
        assert r.status_code == 200 and r.json() == {"ok": True} and r.headers["server-timing"].startswith("app;dur=")
        client.get("/api/runs/nope")
        got = client.get("/api/perf/timings").json()
        routes = {x["route"] for x in got["routes"]}
        assert "/api/health" in routes and "/api/runs/{run_id}" in routes and not any("nope" in x for x in routes)
        assert got["slow_ms"] == rt.SLOW_MS
        assert client.get("/api/perf/timings?reset=1").status_code == 200
        after = [x["route"] for x in client.get("/api/perf/timings").json()["routes"]]
        assert after == ["/api/perf/timings"]                             # only the reset call itself since
        client.cookies.clear()
        assert client.get("/api/perf/timings").status_code == 401
