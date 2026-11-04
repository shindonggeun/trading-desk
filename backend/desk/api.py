"""HTTP API. Run with: uvicorn desk.api:create_app --factory --host 0.0.0.0 --port 8000

Every route needs a login session except the two in OPEN_PATHS. See docs/security.md."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, timedelta
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from . import regime
from .auth import COOKIE, Auth, Busy, LoginThrottle
from .broker import make_broker
from .db import make_engine, migrate
from .runner import ANALYSTS, Hub, RunManager, RunRequest, make_fake_runner, run_tradingagents
from .settings import Settings, load
from .store import TERMINAL, Store, iso
from .universe import Universe

log = logging.getLogger(__name__)
HEARTBEAT_SEC = 15
OPEN_PATHS = frozenset({"/api/health", "/api/auth/login"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RunIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    # Display name from the search (엔비디아). Looked up from the listing when absent.
    name: str | None = Field(default=None, max_length=200)
    trade_date: date | None = None
    analysts: list[str] = Field(default_factory=lambda: list(ANALYSTS))
    use_portfolio: bool = False
    output_language: str | None = None
    max_debate_rounds: int | None = Field(default=None, ge=1, le=5)
    max_risk_rounds: int | None = Field(default=None, ge=1, le=5)

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, v: str) -> str:
        v = v.strip().upper()
        if not all(c.isalnum() or c in ".-^=" for c in v):
            raise ValueError("ticker may contain letters, digits and . - ^ = only")
        return v

    @field_validator("analysts")
    @classmethod
    def _analysts(cls, v: list[str]) -> list[str]:
        unknown = set(v) - set(ANALYSTS)
        if unknown:
            raise ValueError(f"unknown analysts: {sorted(unknown)}; choose from {list(ANALYSTS)}")
        if not v:
            raise ValueError("choose at least one analyst")
        return v


def create_app(settings: Settings | None = None, runner=None) -> FastAPI:
    settings = settings or load()
    migrate(settings.database_url)
    engine = make_engine(settings.database_url)
    store = Store(engine)
    auth = Auth(engine, timedelta(hours=settings.session_max_hours), timedelta(hours=settings.session_idle_hours))
    throttle = LoginThrottle()
    universe = Universe(engine)
    hub = Hub()
    broker = make_broker(settings)
    if runner is None:
        runner = make_fake_runner(settings.fake_delay) if settings.fake_runs else run_tradingagents

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stale = store.fail_unfinished("The server restarted while this run was in progress.")
        if stale:
            log.warning("marked %d unfinished run(s) as failed", stale)
        app.state.manager = RunManager(store, hub, runner, broker, settings.max_concurrent_runs)

        async def keep_listing_fresh():
            while True:
                await universe.refresh_if_stale(broker)
                await asyncio.sleep(3600)

        refresher = asyncio.create_task(keep_listing_fresh())
        yield
        refresher.cancel()
        engine.dispose()

    docs = settings.enable_docs
    app = FastAPI(title="Trading Desk", lifespan=lifespan, docs_url="/docs" if docs else None,
                  redoc_url=None, openapi_url="/openapi.json" if docs else None)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def require_session(request: Request, call_next):
        # Cross-site writes: the SameSite=Strict cookie already stays home; this also stops login CSRF.
        if request.method in UNSAFE_METHODS and not _same_origin(request):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        if request.url.path not in OPEN_PATHS:
            user = await asyncio.to_thread(auth.resolve, request.cookies.get(COOKIE))
            if user is None:
                return JSONResponse({"detail": "login required"}, status_code=401)
            request.state.user = user
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"ok": True}

    # --- login -------------------------------------------------------------------------

    @app.post("/api/auth/login", status_code=204)
    def login(body: LoginIn, request: Request):
        ip = _client_ip(request)
        wait = throttle.attempt(ip)
        if wait:
            return JSONResponse({"detail": "too many failed logins", "retry_after": wait},
                                status_code=429, headers={"Retry-After": str(wait)})
        try:
            user = auth.authenticate(body.username, body.password)
        except Busy:
            return JSONResponse({"detail": "busy, try again"}, status_code=503, headers={"Retry-After": "5"})
        if user is None:
            log.warning("login failed ip=%s", ip)   # fail2ban reads the ingress log; this is for us
            return JSONResponse({"detail": "invalid username or password"}, status_code=401)
        throttle.succeeded(ip)
        auth.close_session(request.cookies.get(COOKIE))   # never keep a token issued before this login
        token = auth.open_session(user, ip, request.headers.get("user-agent"))
        log.info("login ok user=%s ip=%s", user.username, ip)
        response = Response(status_code=204)
        response.set_cookie(COOKIE, token, max_age=int(settings.session_max_hours * 3600), path="/",
                            secure=settings.cookie_secure, httponly=True, samesite="strict")
        return response

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request):
        auth.close_session(request.cookies.get(COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict")
        return response

    @app.get("/api/auth/me")
    def me(request: Request):
        return {"username": request.state.user.username}

    # --- desk --------------------------------------------------------------------------

    @app.get("/api/config")
    def config():
        return {
            "analysts": list(ANALYSTS),
            "provider": os.getenv("TRADINGAGENTS_LLM_PROVIDER", "openai"),
            "deep_model": os.getenv("TRADINGAGENTS_DEEP_THINK_LLM"),
            "quick_model": os.getenv("TRADINGAGENTS_QUICK_THINK_LLM"),
            "output_language": os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE", "English"),
            "fake_runs": settings.fake_runs,
            "broker": broker.name,
        }

    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunIn, request: Request):
        listed = None if body.name else universe.lookup(body.ticker)
        run_request = RunRequest(
            ticker=body.ticker,
            name=body.name or (listed or {}).get("name"),
            trade_date=(body.trade_date or date.today()).isoformat(),
            analysts=body.analysts,
            use_portfolio=body.use_portfolio,
            output_language=body.output_language,
            max_debate_rounds=body.max_debate_rounds,
            max_risk_rounds=body.max_risk_rounds,
        )
        run_id = request.app.state.manager.submit(run_request)
        return store.get_run(run_id)

    @app.get("/api/runs")
    def list_runs(limit: int = 50):
        return store.list_runs(min(max(limit, 1), 200))

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str):
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return run

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str, after: int = 0):
        if not store.get_run(run_id):
            raise HTTPException(404, "run not found")
        return store.events_after(run_id, after)

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: str, request: Request, after: int = 0):
        if not store.get_run(run_id):
            raise HTTPException(404, "run not found")
        last_id = request.headers.get("last-event-id")
        start = int(last_id) if last_id and last_id.isdigit() else after

        token = request.cookies.get(COOKIE)

        async def frames():
            seq = start
            while not await request.is_disconnected():
                # Re-check every round: logout, revoke or expiry ends an open stream too.
                if await asyncio.to_thread(auth.resolve, token) is None:
                    return
                waiter = hub.listen(run_id)          # before reading, so nothing slips between
                events = store.events_after(run_id, seq)
                for event in events:
                    seq = event["seq"]
                    yield f"id: {seq}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if events:
                    continue
                if store.run_status(run_id) in TERMINAL:
                    return
                try:
                    await asyncio.wait_for(waiter.wait(), HEARTBEAT_SEC)
                except TimeoutError:
                    yield ": ping\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/instruments")
    def search_instruments(q: str = "", limit: int = 20):
        return universe.search(q[:50], min(max(limit, 1), 50))

    @app.get("/api/instruments/status")
    def instruments_status():
        status = universe.status()
        return {"source": broker.name, "count": status["count"],
                "updated_at": iso(status["updated_at_dt"]), "error": status["error"]}

    @app.get("/api/portfolio")
    async def portfolio():
        try:
            holdings = await broker.holdings()
        except Exception as exc:  # noqa: BLE001 - a broker outage shows in the panel, not as a 500
            return {"broker": broker.name, "error": f"{type(exc).__name__}: {exc}", "holdings": []}
        return {"broker": broker.name, "holdings": [h.to_dict() for h in holdings]}

    @app.get("/api/regime")
    def regime_view():
        return regime.snapshot(settings.semibot_db_path)

    return app


def _client_ip(request: Request) -> str:
    # uvicorn --proxy-headers already replaced the peer with X-Forwarded-For, which the web
    # container's nginx sets to the address the ingress vouched for (frontend/nginx.conf).
    return request.client.host if request.client else "unknown"


def _same_origin(request: Request) -> bool:
    """Browsers send Origin on every cross-site write. No Origin = not a browser form/fetch."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc.lower() == request.headers.get("host", "").lower()
