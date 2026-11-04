"""HTTP API. Run with: uvicorn desk.api:create_app --factory --host 0.0.0.0 --port 8000"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import date

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator

from . import regime
from .broker import make_broker
from .runner import ANALYSTS, Hub, RunManager, RunRequest, make_fake_runner, run_tradingagents
from .settings import Settings, load
from .store import TERMINAL, Store

log = logging.getLogger(__name__)
HEARTBEAT_SEC = 15


class RunIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
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
    store = Store(settings.db_path)
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
        yield

    app = FastAPI(title="Trading Desk", lifespan=lifespan)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def require_token(request: Request, call_next):
        # EventSource cannot send headers, so the token is also accepted as ?token=.
        if settings.api_token and request.url.path.startswith("/api/") and request.url.path != "/api/health":
            given = request.headers.get("authorization", "").removeprefix("Bearer ").strip() \
                or request.query_params.get("token", "")
            if not hmac.compare_digest(given, settings.api_token):
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

    @app.get("/api/health")
    def health():
        return {"ok": True, "fake_runs": settings.fake_runs, "broker": broker.name,
                "auth": bool(settings.api_token)}

    @app.get("/api/config")
    def config():
        return {
            "analysts": list(ANALYSTS),
            "provider": os.getenv("TRADINGAGENTS_LLM_PROVIDER", "openai"),
            "deep_model": os.getenv("TRADINGAGENTS_DEEP_THINK_LLM"),
            "quick_model": os.getenv("TRADINGAGENTS_QUICK_THINK_LLM"),
            "output_language": os.getenv("TRADINGAGENTS_OUTPUT_LANGUAGE", "English"),
            "fake_runs": settings.fake_runs,
        }

    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunIn, request: Request):
        run_request = RunRequest(
            ticker=body.ticker,
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

        async def frames():
            seq = start
            while not await request.is_disconnected():
                waiter = hub.listen(run_id)          # before reading, so nothing slips between
                events = store.events_after(run_id, seq)
                for event in events:
                    seq = event["seq"]
                    yield f"id: {seq}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if events:
                    continue
                if store.get_run(run_id)["status"] in TERMINAL:
                    return
                try:
                    await asyncio.wait_for(waiter.wait(), HEARTBEAT_SEC)
                except TimeoutError:
                    yield ": ping\n\n"

        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
