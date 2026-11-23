"""#859 the event loop stays free: the lag monitor names what held it, background file writes and TLS
contexts run off the loop, and the slow-request log says how long the loop was away."""

import asyncio
import logging
import os
import ssl
import sys
import threading
import time
import types

from desk import earnings, loop_lag, macro_news, net
from desk import req_timing as rt


def _blocker(seconds: float) -> None:
    time.sleep(seconds)                     # a synchronous call on the loop - what the monitor must catch


def test_a_blocking_call_on_the_loop_is_measured_and_named(caplog):
    lag = loop_lag.LoopLag(interval=0.01, stall_ms=200, poll=0.01)

    async def scenario():
        task = asyncio.create_task(lag.run())
        await asyncio.sleep(0.1)
        before = time.monotonic()
        await asyncio.sleep(0.05)
        start = time.monotonic()
        _blocker(0.6)
        await asyncio.sleep(0.15)           # the loop is back: the watcher closes the stall
        waited, earlier = lag.max_since(start), lag.max_since(time.monotonic() + 1)
        _blocker(0.6)                       # the same place again within a minute: kept, not logged again
        await asyncio.sleep(0.15)
        task.cancel()
        return waited, earlier, before

    with caplog.at_level(logging.WARNING, logger="desk.loop_lag"):
        waited, earlier, _ = asyncio.run(scenario())
    stats = lag.stats()
    assert waited >= 500 and earlier < 200         # a request that started after the stall did not wait for it
    assert stats["max_ms"] >= 500 and stats["stalls_recent"] >= 2
    mine = [x for x in stats["sites"] if "_blocker" in x["site"]]
    assert mine and "test_loop_lag.py" in mine[0]["site"] and mine[0]["count"] >= 2 and mine[0]["max_ms"] >= 500
    logged = [r.getMessage() for r in caplog.records if "_blocker" in r.getMessage()]
    assert len(logged) == 1 and "event loop blocked" in logged[0]


def test_awaiting_does_not_count_as_a_stall():
    lag = loop_lag.LoopLag(interval=0.01, stall_ms=200, poll=0.01)

    async def scenario():
        task = asyncio.create_task(lag.run())
        start = time.monotonic()
        await asyncio.sleep(0.3)            # waiting is not blocking
        waited = lag.max_since(start)
        task.cancel()
        return waited

    assert asyncio.run(scenario()) < 200
    stats = lag.stats()
    assert stats["stalls_recent"] == 0 and stats["sites"] == [] and stats["samples"] > 5


def test_the_site_is_the_innermost_frame_of_this_package():
    # a function whose code claims to live in desk/ is the place named, not this test that called it
    def inner():
        return sys._getframe()

    path = os.path.join(loop_lag._PACKAGE, "pretend_module.py")
    pretend = types.FunctionType(inner.__code__.replace(co_filename=path), globals())

    def outer():
        return pretend()

    site = loop_lag.site_of(outer())
    assert site.startswith("pretend_module.py:") and site.endswith(" inner")
    assert loop_lag.site_of(None) == "?"


def test_a_slow_request_log_ends_with_the_loop_wait(caplog):
    from tests.test_req_timing import _app, _call

    mw = rt.TimingMiddleware(_app(), rt.Timings(), slow_ms=0, loop_max=lambda since: 412.4)
    with caplog.at_level(logging.WARNING, logger="desk.req_timing"):
        _call(mw)
    msg = caplog.records[-1].getMessage()
    assert msg.endswith(" loop_max=412ms") and "status 200" in msg


def test_clients_reuse_their_threads_tls_context(monkeypatch):
    monkeypatch.setattr(net, "_by_thread", {})
    made = []
    real = ssl.create_default_context
    monkeypatch.setattr(ssl, "create_default_context", lambda *a, **kw: made.append(threading.get_ident()) or real(*a, **kw))

    async def several():
        await net.warm()                    # the loop thread's context, built in a worker thread
        built = list(made)
        for _ in range(3):
            async with net.async_client(timeout=1):
                pass
        return built

    built = asyncio.run(several())
    assert len(built) == 1 and built[0] != threading.get_ident() and made == built   # none per client
    with net.client(timeout=1):
        pass
    assert made == built                    # the sync client on this (the loop's) thread shares it too
    other = []
    worker = threading.Thread(target=lambda: other.append(net.ssl_context()))
    worker.start()
    worker.join()
    assert len(made) == 2 and other[0] is not net.ssl_context()      # another thread builds its own


def test_macro_news_writes_its_files_off_the_loop(monkeypatch):
    threads: dict[str, set] = {"loop": set(), "history": set(), "save": set()}
    monkeypatch.setattr(macro_news.macro_history, "record_daily",
                        lambda *a, **kw: threads["history"].add(threading.get_ident()) or 0)
    monkeypatch.setattr(macro_news.Cache, "save", lambda self: threads["save"].add(threading.get_ident()))

    class Down:
        sent = 0

        async def get(self, *a, **kw):
            raise ValueError("over the limit")

    async def rss(theme, now):
        threads["loop"].add(threading.get_ident())
        return {"id": theme.id, "label": theme.label, "articles_24h": 1, "spike": None, "tone": None,
                "headlines": [], "why": theme.why, "source": "google-news", "_daily": ({"2026-11-05": 1.0}, {})}

    asyncio.run(macro_news.Cache().refresh(gdelt=Down(), now=macro_news.datetime(2026, 11, 6, 12,
                                                                                  tzinfo=macro_news.UTC), rss=rss))
    assert threads["history"] and threads["save"]
    assert not threads["history"] & threads["loop"] and not threads["save"] & threads["loop"]


def test_the_gdelt_history_is_kept_off_the_loop():
    seen: dict[str, int] = {}

    class Gdelt:
        async def get(self, *a, **kw):
            seen.setdefault("loop", threading.get_ident())
            return {}

    theme = macro_news.THEMES[0]
    asyncio.run(macro_news.fetch_theme(Gdelt(), theme, macro_news.datetime.now(macro_news.UTC), headlines_too=False,
                                       keep=lambda *a: seen.setdefault("keep", threading.get_ident())))
    assert seen["keep"] != seen["loop"]


def test_earnings_saves_its_cache_off_the_loop(monkeypatch):
    seen: dict[str, int] = {}
    monkeypatch.setattr(earnings.Earnings, "save", lambda self: seen.setdefault("save", threading.get_ident()))

    async def scenario():
        seen["loop"] = threading.get_ident()
        cache = earnings.Earnings(yahoo=lambda t: None, yahoo_calendar=lambda first, last: {})     # no network
        cache.refused_at = earnings.datetime.now(earnings.UTC).isoformat()   # skip Nasdaq
        return await cache.refresh(client=_NoNet(), today=earnings.date(2026, 11, 6))

    asyncio.run(scenario())
    assert seen["save"] != seen["loop"]


class _NoNet:
    async def get(self, *a, **kw):
        raise ConnectionError("offline")

    async def aclose(self):
        pass


def test_status_reports_the_loop_lag(db_url):
    from tests.test_api import _client

    with _client(db_url) as client:
        assert client.get("/api/health").json() == {"ok": True}
        time.sleep(0.3)                     # the heartbeat runs inside the app's lifespan
        got = client.get("/api/status").json()["loop_lag"]
    assert {"p50_ms", "p95_ms", "max_ms", "stall_ms", "stalls_recent", "sites"} <= set(got)
    assert got["stall_ms"] == loop_lag.STALL_MS and got["samples"] > 0
    deadline = time.monotonic() + 5
    while any(t.name == "loop-lag" for t in threading.enumerate()) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not any(t.name == "loop-lag" for t in threading.enumerate())   # the watcher stops with the app
