"""Loopback-only API for the Redis market stream browser test."""

import asyncio
import json
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import uvicorn

# desk.settings loads dotenv at import time. Never visit a checkout's dotenv files in this fixture.
with patch("dotenv.load_dotenv", return_value=False):
    from desk import api, market_bus, roles, sessions
    from desk.auth import Auth
    from desk.db import make_engine
    from desk.prices import Quote
    from desk.settings import Settings


def deny_external_network(event, args):
    loopback = ("127.0.0.1", "::1")
    if event == "socket.getaddrinfo" and args[0] not in (*loopback, None):
        raise PermissionError("fixture blocks external DNS")
    if event == "socket.connect" and isinstance(args[1], tuple) and args[1][0] not in loopback:
        raise PermissionError("fixture blocks external TCP")


def now():
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def today():
    return datetime.now(sessions.KST).date().isoformat()


def flow_view(price, market="KOSPI", interval="1d"):
    return {"supported": True, "market": market, "interval": interval, "session": "open",
            "index": {"price": price}, "records": [{"date": today(), "provisional": True,
                "in_progress": True, "investors": {}, "institution_breakdown": {}}],
            "fetched_at": now(), "error": None}


def futures_view(price):
    return {"supported": True, "market_open": True, "fetched_at": now(), "investors": None,
            "price": {"price": price, "code": "101W12", "expiry": today(),
                      "days_left": 19, "change": 2.3, "change_pct": 0.5}}


def make_app(directory: Path, redis_port: int):
    rest = {"flow": 2601, "futures": 451}
    collector = market_bus.Bus()

    class Flow:
        async def get(self, market, interval, _count, fresh=False):
            return flow_view(rest["flow"], market, interval)

    class Futures:
        async def get(self):
            return futures_view(rest["futures"])

        async def aclose(self):
            pass

    class Prices:
        async def quote(self, ticker):
            return Quote(ticker, 101, "USD", "kis", now())

    class Estimate:
        async def estimate(self, _ticker):
            return None

        async def aclose(self):
            pass

    async def regular_session(*_args):
        return "regular"

    roles.runs = lambda *_args: False
    sessions.session = regular_session
    market_bus.HEARTBEAT_SEC = 0.5
    db_url = "sqlite:///" + (directory / "desk.sqlite").as_posix()
    settings = Settings(database_url=db_url, fake_runs=True, cookie_secure=True, market_stream=True,
                        role="api", redis_sentinels=(), redis_url=f"redis://127.0.0.1:{redis_port}/0",
                        redis_cache_key=secrets.token_urlsafe(32))
    app = api.create_app(settings, prices=Prices(), estimator=Estimate(), flow=Flow(), futures=Futures())
    with (directory / "account.json").open(encoding="utf-8") as handle:
        account = json.load(handle)
    engine = make_engine(db_url)
    try:
        Auth(engine, timedelta(hours=1), timedelta(hours=1)).create_user(**account)
    finally:
        engine.dispose()

    def keep_quote_live():
        # This quote comes from the API role itself, even when Redis is unavailable.
        market_bus.publish("quote", "AAPL", Quote("AAPL", 182, "USD", "kis", now()).to_dict(), "kis", now())

    async def local_quotes():
        while True:
            keep_quote_live()
            await asyncio.sleep(4)

    api_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def fixture_lifespan(application):
        async with api_lifespan(application):
            collector.configure(settings, "collector")
            publisher = asyncio.create_task(local_quotes())
            try:
                yield
            finally:
                publisher.cancel()
                await asyncio.gather(publisher, return_exceptions=True)
                await collector.aclose()

    app.router.lifespan_context = fixture_lifespan

    async def publish_remote(flow_price, futures_price, *, alert=False):
        bus = market_bus.BUS
        received = bus.stats()["received_remote"]
        published = collector.stats()["published_remote"]
        collector.publish("flow", "KOSPI:1d", flow_view(flow_price), "toss_investors", now())
        collector.publish("futures", "view", futures_view(futures_price), "kis_futures", now())
        if alert:
            collector.publish("alert", "flow_shift", {
                "kind": "flow_shift", "rule": "buy_grow", "market": "kospi", "who": "foreign",
                "trade_date": today(), "severity": "info", "t": now(),
                "text": "Redis recovery marker", "value": 10, "prev": 1,
            }, "desk", now())
        expected = 3 if alert else 2
        async with asyncio.timeout(5):
            while (bus.stats()["received_remote"] < received + expected
                   or collector.stats()["published_remote"] < published + expected):
                await asyncio.sleep(0.025)

    @app.post("/api/_integration/{phase}")
    async def control(phase: str):
        bus = market_bus.BUS
        if phase == "seed":
            async with asyncio.timeout(8):
                while not bus.stats()["subscribed"]:
                    await asyncio.sleep(0.05)
            if bus.mode != "redis" or collector.mode != "redis":
                raise RuntimeError("fixture requires actual Redis transport")
            await publish_remote(2601, 451)
            recorded = app.state.market_alerts.dispatch([{
                "kind": "flow_shift", "rule": "buy_grow", "market": "kospi", "who": "foreign",
                "severity": "info", "t": now(), "value": 48_100_000_000, "prev": 3_000_000_000,
            }])
            if not recorded:
                raise RuntimeError("fixture alert baseline was not recorded")
        elif phase == "healthy":
            await publish_remote(2688, 483)
        elif phase == "prepare-outage":
            rest.update(flow=2699, futures=489)
        elif phase == "recovered":
            if not bus.stats()["subscribed"] or not bus.stats()["reconnects"]:
                raise RuntimeError("API did not lose and restore its Redis subscription")
            await publish_remote(2711, 491, alert=True)
        else:
            raise ValueError(f"unknown fixture phase: {phase}")
        return {"api": bus.stats(), "collector": collector.stats()}

    return app


if __name__ == "__main__":
    sys.addaudithook(deny_external_network)
    fixture_dir, api_port, redis_port = sys.argv[1:]
    with patch("dotenv.load_dotenv", return_value=False):
        uvicorn.run(make_app(Path(fixture_dir), int(redis_port)), host="127.0.0.1", port=int(api_port),
                    log_level="warning", timeout_graceful_shutdown=2)
