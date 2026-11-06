"""HTTP API. Run with: uvicorn desk.api:create_app --factory --host 0.0.0.0 --port 8000

Every route needs a login session except the two in OPEN_PATHS. See docs/security.md."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from contextlib import asynccontextmanager, suppress
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from . import (
    events_calendar,
    filings,
    horizons,
    live,
    macro_news,
    otp,
    outcome,
    playbook_live,
    positioning,
    regime,
    review,
    verdict,
)
from .auth import COOKIE, Auth, Busy, LoginThrottle, User, hash_token
from .broker import kis_stream, make_broker
from .consensus import fake_summariser, llm_summariser
from .db import make_engine, migrate, now
from .estimate import CANDLE_INTERVALS, BinanceEstimator
from .favorites import Conflict, Favorites, TooMany
from .indicators import INDICATORS
from .leverage_analyst import fake_analyst, llm_analyst
from .market_flow import MarketFlow
from .portfolio import Portfolio
from .prices import INTERVALS, PriceSource
from .profile import PROFILES
from .related import collect as collect_related
from .runner import ANALYSTS, Hub, RunManager, RunRequest, make_fake_runner, run_tradingagents
from .settings import Settings, load
from .sr import support_resistance
from .store import TERMINAL, Store, iso
from .universe import Universe

MAX_BOARD = 30          # tickers per /api/estimates call
CALENDAR_INTERVAL_SEC = 6 * 3600   # economic calendar re-read (#148)
POSITIONING_PARALLEL = 3   # Cboe chains are ~2 MB each (#154)
POSITIONING_QUOTE_SEC = 5.0
BOARD_PARALLEL = 6      # concurrent Binance / quote lookups per call
KST = timezone(timedelta(hours=9))   # Toss dates (order history from/to) are KST
REVIEW_CACHE_SEC = 60
REVIEW_FAIL_CACHE_SEC = 30
log = logging.getLogger(__name__)
NL2 = "\n\n"   # ends an SSE frame
HEARTBEAT_SEC = 15
OPEN_PATHS = frozenset({"/api/health", "/api/auth/login"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class LoginIn(BaseModel):
    """Step 1: username + password. Step 2 (OTP on): the challenge step 1 returned + a code.
    Both steps use the one route so the ingress fail2ban rule (login 401s) covers code guessing too."""

    username: str | None = Field(default=None, min_length=1, max_length=64)
    password: str | None = Field(default=None, min_length=1, max_length=256)
    challenge: str | None = Field(default=None, min_length=1, max_length=128)
    code: str | None = Field(default=None, min_length=1, max_length=32)

    @model_validator(mode="after")
    def _one_step(self):
        if (self.username is None) != (self.password is None) or (self.challenge is None) != (self.code is None):
            raise ValueError("send username + password, or challenge + code")
        if (self.username is None) == (self.challenge is None):
            raise ValueError("send username + password, or challenge + code")
        return self


class PasswordIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=32)


class DisableOtpIn(BaseModel):
    password: str = Field(min_length=1, max_length=256)
    code: str = Field(min_length=1, max_length=32)


def clean_ticker(v: str) -> str:
    v = v.strip().upper()
    if not v or len(v) > 20 or not all((c.isascii() and c.isalnum()) or c in ".-^=" for c in v):
        raise ValueError("ticker may contain letters, digits and . - ^ = only")
    return v


class RunIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    # Display name from the search (엔비디아). Looked up from the listing when absent.
    name: str | None = Field(default=None, max_length=200)
    # Investing style (desk.profile.PROFILES); the server default when absent.
    profile: str | None = None
    # Trading horizon (desk.horizons.HORIZONS); swing when absent.
    horizon: str | None = None
    trade_date: date | None = None
    analysts: list[str] = Field(default_factory=lambda: list(ANALYSTS))
    use_portfolio: bool = False
    output_language: str | None = None
    max_debate_rounds: int | None = Field(default=None, ge=1, le=5)
    max_risk_rounds: int | None = Field(default=None, ge=1, le=5)

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, v: str) -> str:
        return clean_ticker(v)

    @field_validator("profile")
    @classmethod
    def _profile(cls, v: str | None) -> str | None:
        if v is not None and v not in PROFILES:
            raise ValueError(f"unknown profile; choose from {list(PROFILES)}")
        return v

    @field_validator("horizon")
    @classmethod
    def _horizon(cls, v: str | None) -> str | None:
        if v is not None and v not in horizons.HORIZONS:
            raise ValueError(f"unknown horizon; choose from {list(horizons.HORIZONS)}")
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


class FavoriteIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    name: str | None = Field(default=None, max_length=200)   # looked up from the listing when absent

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, v: str) -> str:
        return clean_ticker(v)


class FavoritesIn(BaseModel):
    # repeats are dropped before the 50-ticker cap (desk.favorites) applies; this only bounds the body
    items: list[FavoriteIn] = Field(max_length=200)


def purge_expired_runs(store: Store, retention_days: int) -> int:
    """Delete finished runs older than ``retention_days`` (0 = keep everything)."""
    if retention_days <= 0:
        return 0
    deleted = store.purge_finished_before(now() - timedelta(days=retention_days))
    if deleted:
        log.info("retention: deleted %d run(s) older than %d days", deleted, retention_days)
    return deleted


def create_app(settings: Settings | None = None, runner=None, prices: PriceSource | None = None,
               estimator: BinanceEstimator | None = None, book: Portfolio | None = None,
               flow: MarketFlow | None = None) -> FastAPI:
    settings = settings or load()
    migrate(settings.database_url)
    engine = make_engine(settings.database_url)
    store = Store(engine)
    auth = Auth(engine, timedelta(hours=settings.session_max_hours), timedelta(hours=settings.session_idle_hours))
    throttle = LoginThrottle()
    otp_box = otp.SecretBox(settings.otp_key)
    if otp_box.error:
        log.error("%s — OTP accounts cannot log in until it is fixed", otp_box.error)
    challenges = otp.Challenges()
    # Failed codes per account, across IPs (docs/security.md §6).
    otp_fails = LoginThrottle(per_ip=otp.USER_FAILS, window_sec=otp.USER_FAIL_WINDOW_SEC)
    universe = Universe(engine)
    favorites = Favorites(engine, lambda ticker: (universe.lookup(ticker) or {}).get("name"))
    hub = Hub()
    broker = make_broker(settings)
    kis_feed = kis_stream.make(settings) if prices is None else None   # tests bring their own prices
    prices = prices or PriceSource(broker, stream=kis_feed)
    estimator = estimator or BinanceEstimator()
    book = book or Portfolio(broker, prices)
    flow = flow or MarketFlow(broker)
    live_streams: dict[str, int] = {}   # open /api/live/stream per session (token hash)
    calendar = live.Calendar(broker)     # market holidays for the live stream, cached per market
    if runner is None:
        runner = make_fake_runner(settings.fake_delay) if settings.fake_runs else run_tradingagents

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stale = store.fail_unfinished("The server restarted while this run was in progress.")
        if stale:
            log.warning("marked %d unfinished run(s) as failed", stale)
        # Scripted runs keep levels on the script's numbers instead of fetching a live quote.
        app.state.manager = RunManager(store, hub, runner, broker, settings.max_concurrent_runs,
                                       prices=None if settings.fake_runs else prices,
                                       # a scripted run stays offline, like its support/resistance
                                       related=None if settings.fake_runs else collect_related,
                                       lever_analyst=fake_analyst if settings.fake_runs else llm_analyst,
                                       summariser=fake_summariser if settings.fake_runs else llm_summariser,
                                       # world news flow: the background cache below, never fetched in a run
                                       macro=None if settings.fake_runs else macro_news.prompt_note,
                                       # #148: the economic calendar (background cache, same-day runs)
                                       calendar=None if settings.fake_runs else events_calendar.prompt_note,
                                       # #148: SEC 8-K / Form 4 of a US company, fetched per run (one or two calls)
                                       filings=None if settings.fake_runs else filings.note_for,
                                       # #154: short volume + options of a US ticker (cached ~15 min)
                                       positioning=None if settings.fake_runs else positioning.note_for)

        async def keep_listing_fresh():
            while True:
                await universe.refresh_if_stale(broker)
                await asyncio.sleep(3600)

        async def purge_old_runs():
            while True:
                try:
                    purge_expired_runs(store, settings.retention_days)
                except Exception:  # a failed sweep must not stop the next one
                    log.exception("retention sweep failed")
                await asyncio.sleep(24 * 3600)

        async def score_plans():
            # How each plan played out, on the bars that came after it (desk.outcome). Starts at boot.
            while True:
                try:
                    scored = await outcome.sweep(store, prices)
                    if scored:
                        log.info("scored %d plan(s)", scored)
                except Exception:  # a failed pass must not stop the next one
                    log.exception("plan scoring failed")
                await asyncio.sleep(settings.score_interval_sec)

        async def refresh_macro():
            # GDELT allows one request every five seconds: a full pass takes a couple of minutes,
            # so it runs here in the background and runs read the snapshot (desk.macro_news).
            while True:
                try:
                    ok = await macro_news.CACHE.refresh()
                    log.info("macro news: %d/%d theme(s) refreshed", ok, len(macro_news.THEMES))
                except Exception:  # a failed pass must not stop the next one
                    log.exception("macro news refresh failed")
                await asyncio.sleep(settings.macro_interval_sec)

        async def refresh_calendar():
            # #148: FOMC + BLS release dates change rarely; a few reads a day keep them current
            while True:
                try:
                    ok = await events_calendar.CACHE.refresh()
                    log.info("economic calendar: %d/2 source(s) refreshed", ok)
                except Exception:  # a failed pass must not stop the next one
                    log.exception("economic calendar refresh failed")
                await asyncio.sleep(CALENDAR_INTERVAL_SEC)

        refresher = asyncio.create_task(keep_listing_fresh())
        macro = (asyncio.create_task(refresh_macro())
                 if settings.macro_interval_sec > 0 and not settings.fake_runs else None)
        # the same switch as the news flow: off in tests and scripted runs
        calendar_task = (asyncio.create_task(refresh_calendar())
                         if settings.macro_interval_sec > 0 and not settings.fake_runs else None)
        # Scripted runs have made-up levels; scoring them against real bars means nothing.
        scorer = (asyncio.create_task(score_plans())
                  if settings.score_interval_sec > 0 and not settings.fake_runs else None)
        purger = asyncio.create_task(purge_old_runs()) if settings.retention_days > 0 else None
        # connects only once a page watches a KIS-streamable ticker (desk.broker.kis_stream)
        kis = asyncio.create_task(kis_feed.run()) if kis_feed is not None else None
        if kis:
            log.info("kis stream on")
        yield
        if kis:
            kis.cancel()
            with suppress(asyncio.CancelledError):
                await kis                   # the socket closes before its http client does
            await kis_feed.aclose()
        refresher.cancel()
        if macro:
            macro.cancel()
        if calendar_task:
            calendar_task.cancel()
        if scorer:
            scorer.cancel()
        if purger:
            purger.cancel()
        await estimator.aclose()
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
        if body.challenge is not None:                      # step 2: the second factor
            user_id = challenges.user_for(body.challenge)
            user = auth.user_by_id(user_id) if user_id is not None else None
            try:
                passed = user is not None and auth.otp_verify(user.id, body.code, otp_box)
            except otp.OtpUnavailable as exc:
                log.error("otp check impossible: %s", exc)
                return JSONResponse({"detail": "OTP is not available on the server"}, status_code=503)
            if not passed:
                if user is not None:
                    otp_fails.attempt(f"user:{user.id}")
                log.warning("login otp failed ip=%s", ip)
                return JSONResponse({"detail": "invalid or expired code"}, status_code=401)
            challenges.close(body.challenge)
            throttle.succeeded(ip)
            otp_fails.succeeded(f"user:{user.id}")
            return _signed_in(user, ip, request)

        try:
            user = auth.authenticate(body.username, body.password)
        except Busy:
            return JSONResponse({"detail": "busy, try again"}, status_code=503, headers={"Retry-After": "5"})
        if user is None:
            log.warning("login failed ip=%s", ip)   # fail2ban reads the ingress log; this is for us
            return JSONResponse({"detail": "invalid username or password"}, status_code=401)
        if auth.otp_enabled(user.id):
            if not otp_box.available:                       # fail closed: never skip the second factor
                log.error("user %s has OTP on but the OTP key is unusable: %s", user.username,
                          otp_box.error or "DESK_OTP_KEY is missing")
                return JSONResponse({"detail": "OTP is not available on the server"}, status_code=503)
            # Spread over many IPs, code guessing is still capped per account. Reaching the cap means
            # someone holds the password: say so loudly.
            wait = otp_fails.blocked_for(f"user:{user.id}")
            if wait:
                log.error("otp failures for user %s over the limit; the password may be known", user.username)
                return JSONResponse({"detail": "too many failed codes", "retry_after": wait},
                                    status_code=429, headers={"Retry-After": str(wait)})
            # No session yet, and the IP's attempt count stays: the code is still to be guessed.
            return JSONResponse({"otp_required": True, "challenge": challenges.issue(user.id)})
        throttle.succeeded(ip)
        return _signed_in(user, ip, request)

    def _signed_in(user: User, ip: str, request: Request) -> Response:
        auth.close_session(request.cookies.get(COOKIE))   # never keep a token issued before this login
        token = auth.open_session(user, ip, request.headers.get("user-agent"))
        log.info("login ok user=%s ip=%s", user.username, ip)
        response = Response(status_code=204)
        response.set_cookie(COOKIE, token, max_age=int(settings.session_max_hours * 3600), path="/",
                            secure=settings.cookie_secure, httponly=True, samesite="strict")
        return response

    def _count_attempt(request: Request) -> JSONResponse | None:
        """Every call that changes the second factor is one attempt for this IP, and none of them
        clears the count — only a full login does. 403 (not 401) on a wrong answer: 401 means
        "session ended" to the page."""
        wait = throttle.attempt(_client_ip(request))
        if wait:
            return JSONResponse({"detail": "too many failed attempts", "retry_after": wait},
                                status_code=429, headers={"Retry-After": str(wait)})
        return None

    def _recheck_password(request: Request, password: str) -> JSONResponse | None:
        """Changing the second factor asks for the password again (a borrowed session is not enough)."""
        refused = _count_attempt(request)
        if refused:
            return refused
        try:
            ok = auth.authenticate(request.state.user.username, password) is not None
        except Busy:
            return JSONResponse({"detail": "busy, try again"}, status_code=503, headers={"Retry-After": "5"})
        if not ok:
            log.warning("otp change: wrong password ip=%s", _client_ip(request))
            return JSONResponse({"detail": "wrong password"}, status_code=403)
        return None

    @app.post("/api/auth/otp/setup")
    def otp_setup(body: PasswordIn, request: Request):
        """New pending secret + otpauth:// address for the QR code. OTP stays off until enable."""
        user = request.state.user
        if not otp_box.available:
            return JSONResponse({"detail": "OTP is not configured on the server (DESK_OTP_KEY)"}, status_code=503)
        if auth.otp_enabled(user.id):
            return JSONResponse({"detail": "OTP is already on; disable it first"}, status_code=409)
        refused = _recheck_password(request, body.password)
        if refused:
            return refused
        return auth.otp_begin(user.id, otp_box, user.username)

    @app.post("/api/auth/otp/enable")
    def otp_enable(body: CodeIn, request: Request):
        """Confirm with one code; returns the recovery codes, shown only this once."""
        user = request.state.user
        refused = _count_attempt(request)
        if refused:
            return refused
        try:
            codes = auth.otp_enable(user.id, body.code.strip(), otp_box, request.cookies.get(COOKIE))
        except otp.OtpUnavailable:
            return JSONResponse({"detail": "OTP is not configured on the server (DESK_OTP_KEY)"}, status_code=503)
        if codes is None:
            log.warning("otp enable: code refused ip=%s", _client_ip(request))
            return JSONResponse({"detail": "code does not match, or setup expired (10 min); start setup again"},
                                status_code=403)
        log.info("otp enabled user=%s", user.username)
        return {"recovery_codes": codes}

    @app.post("/api/auth/otp/disable", status_code=204)
    def otp_disable(body: DisableOtpIn, request: Request):
        user = request.state.user
        refused = _recheck_password(request, body.password)
        if refused:
            return refused
        try:
            if not auth.otp_verify(user.id, body.code, otp_box):
                log.warning("otp disable: code refused ip=%s", _client_ip(request))
                return JSONResponse({"detail": "invalid code"}, status_code=403)
        except otp.OtpUnavailable:
            return JSONResponse({"detail": "OTP is not available on the server"}, status_code=503)
        auth.otp_disable(user.id, keep_token=request.cookies.get(COOKIE))
        log.info("otp disabled user=%s", user.username)
        return Response(status_code=204)

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request):
        auth.close_session(request.cookies.get(COOKIE))
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="strict")
        return response

    @app.get("/api/auth/me")
    def me(request: Request):
        user = request.state.user
        enabled = auth.otp_enabled(user.id)
        return {"username": user.username, "otp_enabled": enabled,
                "recovery_codes_left": auth.recovery_left(user.id) if enabled else None,
                "otp_available": otp_box.available}

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
            "profiles": [{"id": key, "label": value["label"]} for key, value in PROFILES.items()],
            "default_profile": settings.investor_profile,
            "horizons": [h.to_dict() for h in horizons.HORIZONS.values()],
            "default_horizon": horizons.DEFAULT_HORIZON,
        }

    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunIn, request: Request):
        listed = None if body.name else universe.lookup(body.ticker)
        run_request = RunRequest(
            ticker=body.ticker,
            name=body.name or (listed or {}).get("name"),
            profile=body.profile or settings.investor_profile,
            horizon=body.horizon or horizons.DEFAULT_HORIZON,   # stored explicitly, never left implicit
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
    def list_runs(page: int = 1, size: int = 20):
        # Clamp both: a huge page would overflow the OFFSET bigint and surface as a 500.
        page, size = min(max(page, 1), 100_000), min(max(size, 1), 100)
        return {"items": store.list_runs(size, (page - 1) * size), "total": store.count_runs(),
                "page": page, "size": size}

    @app.delete("/api/runs/{run_id}", status_code=204)
    async def delete_run(run_id: str):
        # async on purpose: runs on the loop thread, so it cannot land between RunManager's
        # mark_done() and its last plan/done events (no await between them), and Hub is touched
        # from the loop only.
        result = store.delete_run(run_id)
        if result == "missing":
            raise HTTPException(404, "run not found")
        if result == "active":
            raise HTTPException(409, "run is still in progress")
        hub.notify(run_id)   # wake any open stream so it sees the run is gone and closes
        return Response(status_code=204)

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
                status = store.run_status(run_id)
                if status is None or status in TERMINAL:   # None: deleted while streaming
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

    # Trade review (desk.review). Toss limits order history (ORDER_HISTORY group), so the broker is
    # asked once for the widest window and every ``days`` is cut from that; one fetch at a time,
    # and a failure is remembered briefly too, so changing ``days`` or retrying cannot hammer Toss.
    fills_cache: dict[str, tuple[float, object]] = {}
    fills_lock = asyncio.Lock()

    async def recent_fills(today: date):
        async with fills_lock:
            cached = fills_cache.get("fills")
            if cached:
                ttl = REVIEW_CACHE_SEC if isinstance(cached[1], list) else REVIEW_FAIL_CACHE_SEC
                if time.monotonic() - cached[0] < ttl:
                    return cached[1]
            try:
                value: object = await broker.fills(today - timedelta(days=review.MAX_DAYS), today)
            except NotImplementedError:
                value = NotImplementedError()
            except Exception as exc:  # noqa: BLE001 - a broker outage shows on the page, not as a 500
                log.warning("trade review: broker fills failed: %s", exc)        # full text stays in the log
                value = RuntimeError(f"{type(exc).__name__}: {str(exc).split(':')[0]}")  # no response body
            fills_cache["fills"] = (time.monotonic(), value)
            return value

    @app.get("/api/review")
    async def trade_review(days: int = 90):
        """Executions of the last ``days`` (max 180, KST dates) next to the plan of the time. Read-only."""
        days = min(max(days, 1), review.MAX_DAYS)
        today = datetime.now(KST).date()
        start = today - timedelta(days=days)
        fills = await recent_fills(today)
        if isinstance(fills, NotImplementedError):
            return {"supported": False, "source": broker.name, "fills": [], "summary": None, "errors": []}
        if isinstance(fills, Exception):
            return {"supported": True, "source": broker.name, "fills": [], "summary": None, "errors": [str(fills)]}
        window = [f for f in fills if start.isoformat() <= f.filled_at[:10] <= today.isoformat()]

        async def daily(ticker: str, count: int) -> list[dict]:
            return (await prices.candles(ticker, "1d", count))["bars"]

        result = await review.build_review(window, store.done_runs_for, universe.lookup, daily, start, today)
        return {"supported": True, "source": broker.name, **result}

    @app.get("/api/market/flow")
    async def market_flow(market: str = "KOSPI", interval: str = "1d", count: int = 20):
        """KOSPI / KOSDAQ trading value by investor class, newest first (desk.market_flow). Read-only;
        a broker failure comes back as ``error`` with no records, not as a 5xx."""
        try:
            return await flow.get(market.upper(), interval, count)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/portfolio")
    async def portfolio():
        """Holdings at live prices in KRW and USD (desk.portfolio). Failures land in ``error``."""
        return await book.snapshot()

    @app.get("/api/portfolio/stream")
    async def portfolio_stream(request: Request):
        """The same snapshot every DESK_PORTFOLIO_STREAM_SEC seconds, as SSE."""
        token = request.cookies.get(COOKIE)
        interval = min(max(settings.portfolio_stream_sec, 2.0), 60.0)

        async def frames():
            yield f"retry: {int(interval * 1000)}\n\n"
            while not await request.is_disconnected():
                # Re-check every round: logout, revoke or expiry ends an open stream too.
                if await asyncio.to_thread(auth.resolve, token) is None:
                    return
                snap = await book.snapshot()
                yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
                await asyncio.sleep(interval)

        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/live/stream")
    async def live_stream(request: Request, ticker: str):
        """The live chart's feed (desk.live): quote + newest 1-minute bars every few seconds while the
        ticker's market is open, one ``closed`` frame and a clock check a minute while it is not."""
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        token = request.cookies.get(COOKIE)
        key = hash_token(token or "")
        if live_streams.get(key, 0) >= live.MAX_STREAMS:
            raise HTTPException(429, f"at most {live.MAX_STREAMS} live charts at once")
        live_streams[key] = live_streams.get(key, 0) + 1

        async def frames():
            try:
                yield f"retry: {int(live.TICK_SEC * 1000)}" + NL2
                was_open, failing, checked = None, 0, None
                loop = asyncio.get_running_loop()
                while not await request.is_disconnected():
                    # Re-check every TICK_SEC (logout, revoke or expiry ends the stream), without
                    # counting it as activity: an open tab must not keep the session from idling.
                    slow = checked is None or loop.time() - checked >= live.TICK_SEC - 0.05
                    if slow:
                        if await asyncio.to_thread(auth.resolve, token, False) is None:
                            return
                        checked = loop.time()
                    is_open = await live.is_open(calendar, ticker)
                    fast = bool(is_open) and live.watch(prices, ticker)
                    if is_open:
                        with_bars = slow or not fast
                        data = await live.frame(prices, ticker, bars=with_bars)
                        # a fast round asks no bars: only a round that asked for both can fail
                        if with_bars:
                            failing = failing + 1 if "quote" not in data and "bars" not in data else 0
                    elif was_open is not False:
                        data = {"state": "closed", "ticker": ticker}
                    else:
                        data = None
                    # a comment frame keeps proxies from dropping an idle stream
                    yield (f"data: {json.dumps(data, ensure_ascii=False)}" if data is not None else ": closed") + NL2
                    was_open = is_open
                    pause = (live.FAST_TICK_SEC if fast else live.TICK_SEC) if is_open else live.CLOSED_SEC
                    if failing >= live.FAIL_ROUNDS:
                        # a failing ticker is tried again a minute later, the session still every 15 s
                        for _ in range(max(1, int(live.FAILING_SEC // live.CLOSED_SEC))):
                            await asyncio.sleep(live.CLOSED_SEC)
                            if await asyncio.to_thread(auth.resolve, token, False) is None:
                                return
                    else:
                        await asyncio.sleep(pause)
            finally:
                live_streams[key] -= 1
                if not live_streams[key]:
                    del live_streams[key]

        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @app.get("/api/live/kis")
    async def kis_status():   # on the loop thread: status() reads dicts the stream mutates there
        """#143: is the KIS realtime feed (#125) connected, what it streams and when the last trade
        came per market. No keys: counts, symbols, times and error type names only."""
        if kis_feed is None:
            return {"enabled": False}
        return {"enabled": True, **kis_feed.status()}

    @app.get("/api/quote")
    async def quote(ticker: str):
        """Current price for the plan card: Toss when connected, else Yahoo; cached ~10 s."""
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            return (await prices.quote(ticker)).to_dict()
        except Exception as exc:  # noqa: BLE001 - both sources failed
            log.warning("quote for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no price for {ticker}") from None

    @app.get("/api/candles")
    async def candles(ticker: str, interval: str = "1d", count: int = 120, indicators: str = ""):
        """Bars for the chart, oldest first. 1m/1d: Toss then Yahoo; minute bars, 1w, 1M, 1y
        (and > 200 bars): Yahoo. ``indicators=rsi14`` adds each bar's RSI(14)."""
        wanted = tuple(part.strip() for part in indicators.split(",") if part.strip())
        try:
            ticker = clean_ticker(ticker)
            if interval not in INTERVALS:
                raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
            if set(wanted) - set(INDICATORS):
                raise ValueError(f"indicators must be among {', '.join(INDICATORS)}")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            return await prices.candles(ticker, interval, count, wanted)
        except Exception as exc:  # noqa: BLE001 - both sources failed
            log.warning("candles for %s %s failed: %s", ticker, interval, exc)
            raise HTTPException(503, f"no candles for {ticker}") from None

    @app.get("/api/events/calendar")
    def economic_calendar(days: int = 14):
        """#148: upcoming FOMC decisions and major BLS releases (official calendars, refreshed in the
        background) and the release window a fresh entry should wait out, if one is open now."""
        return events_calendar.CACHE.view(days=min(max(days, 1), 60))

    @app.get("/api/macro/brief")
    def macro_brief(ticker: str | None = None):
        """World news-flow themes (GDELT, refreshed in the background): article count in 24h,
        surge against the week, average tone and headlines; with ``ticker`` its themes first."""
        if ticker is not None:
            try:
                ticker = clean_ticker(ticker)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        return macro_news.CACHE.brief(ticker)

    @app.get("/api/stats/plans")
    def plan_stats(days: int = 90):
        """How the plans of the last ``days`` played out (desk.outcome): by horizon, rating,
        direction, the desk's opinion and analyst set, plus a win-probability calibration.
        Runs older than the retention are gone, so the window stops there."""
        limit = settings.retention_days if settings.retention_days > 0 else 365
        days = min(max(days, 1), limit)
        since = now() - timedelta(days=days)
        result = outcome.stats(store.scored_since(since))
        return {"days": days, "from": iso(since), "to": iso(now()),
                "thresholds": {"win_probability": verdict.MIN_PROBABILITY, "agreeing": verdict.MIN_AGREEING},
                **result}

    @app.get("/api/levels")
    async def levels(ticker: str, horizon: str | None = None):
        """Support/resistance zones (desk.sr) from the horizon's bars - daily and weekly by
        default - cached a minute or a few."""
        try:
            ticker = clean_ticker(ticker)
            if horizon is not None and horizon not in horizons.HORIZONS:
                raise ValueError(f"horizon must be one of {', '.join(horizons.HORIZONS)}")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            return await support_resistance(prices, ticker, horizon=horizon)
        except Exception as exc:  # noqa: BLE001 - no bars or too few
            log.warning("levels for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no levels for {ticker}") from None

    @app.get("/api/playbooks")
    async def live_playbooks(ticker: str | None = None, horizon: str | None = None, run: str | None = None):
        """Long and short playbooks on the bars and the price now (#128). With ``run`` the
        analysis' zones, ladder, stop and targets stay and only conditions, score and state move."""
        run_pb = None
        if run is not None:
            found = store.get_run(run)
            if not found:
                raise HTTPException(404, "no such run")
            plan = found.get("plan") or {}
            run_pb = plan.get("playbooks")
            # the analysis' levels are in its own instrument's prices on its own horizon
            run_horizon = plan.get("horizon") or (found.get("options") or {}).get("horizon")
            if (ticker and ticker.strip().upper() != found["ticker"]) or (horizon and run_horizon and horizon != run_horizon):
                raise HTTPException(422, "ticker and horizon come from the run; leave them out")
            ticker, horizon = found["ticker"], run_horizon
        try:
            if not ticker:
                raise ValueError("ticker or run is required")
            ticker = clean_ticker(ticker)
            if horizon is not None and horizon not in horizons.HORIZONS:
                raise ValueError(f"horizon must be one of {', '.join(horizons.HORIZONS)}")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            return await playbook_live.current(prices, ticker, horizon, run, run_pb)
        except Exception as exc:  # noqa: BLE001 - no bars, too few, or a source down
            log.warning("live playbooks for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no playbooks for {ticker}") from None

    @app.get("/api/estimate")
    async def estimate(ticker: str):
        """Off-hours estimate from the ticker's Binance stock perpetual (desk.estimate).
        ``supported: false`` when Binance lists no contract for it."""
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            result = await estimator.estimate(ticker)
        except Exception as exc:  # noqa: BLE001 - Binance or Yahoo down
            log.warning("estimate for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no estimate for {ticker}") from None
        return result.to_dict() if result else {"ticker": ticker, "supported": False}

    @app.get("/api/estimates")
    async def estimates(tickers: str):
        """The quote board (#123/#124): for each ticker the live price while its market is open,
        else the off-hours estimate (own Binance contract, a proxy, or a leveraged base x multiple),
        else "unavailable". One ticker failing does not fail the others."""
        names = [t for t in (x.strip() for x in tickers.split(",")) if t]
        if not names or len(names) > MAX_BOARD:
            raise HTTPException(422, f"between 1 and {MAX_BOARD} tickers")
        try:
            names = list(dict.fromkeys(clean_ticker(t) for t in names))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        gate = asyncio.Semaphore(BOARD_PARALLEL)

        async def one(ticker: str) -> dict:
            async with gate:
                est, err = None, None
                try:
                    est = await estimator.estimate(ticker)
                except Exception as exc:  # noqa: BLE001 - Binance or Yahoo down for this one
                    err = type(exc).__name__
                    log.warning("board estimate for %s failed: %s", ticker, exc)
                is_open = est.market_state == "open" if est else await live.is_open(calendar, ticker)
                row: dict = {"ticker": ticker, "close": est.last_close if est else None,
                             "close_date": est.last_close_date if est else None,
                             "source": ({"kind": est.method, "contract": est.contract, "basis": est.basis,
                                         "multiple": est.multiple} if est else None)}
                if is_open:
                    live.watch(prices, ticker)       # the board's tickers ride the KIS stream too
                    try:
                        q = await prices.quote(ticker)
                        change = (round((q.price / est.last_close - 1) * 100, 2) if est and est.last_close else None)
                        return {**row, "state": "live", "price": q.price, "change_pct": change,
                                "quote_source": q.source, "fetched_at": q.fetched_at}
                    except Exception as exc:  # noqa: BLE001 - fall back to the estimate below
                        log.warning("board quote for %s failed: %s", ticker, exc)
                if est:
                    return {**row, "state": "estimate", "price": est.estimate, "change_pct": est.change_pct,
                            "fetched_at": est.fetched_at}
                return {**row, "state": "unavailable", "price": None, "change_pct": None, "error": err,
                        "fetched_at": None}

        return {"items": await asyncio.gather(*(one(t) for t in names))}

    @app.get("/api/estimate/candles")
    async def estimate_candles(ticker: str, interval: str = "5m"):
        """Estimated bars since the last regular close (desk.estimate.candles) to continue a chart
        past the last real bar; ``supported: false`` when there is no contract to follow."""
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if interval not in CANDLE_INTERVALS:
            raise HTTPException(422, f"interval must be one of {', '.join(CANDLE_INTERVALS)}")
        try:
            result = await estimator.candles(ticker, interval)
        except Exception as exc:  # noqa: BLE001
            log.warning("estimated candles for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no estimated candles for {ticker}") from None
        return result if result else {"ticker": ticker, "supported": False}

    # --- favorites (#134): per account, ordered ----------------------------------------

    @app.get("/api/favorites")
    def list_favorites(request: Request):
        return favorites.list(request.state.user.id)

    @app.put("/api/favorites")
    def replace_favorites(body: FavoritesIn, request: Request):
        try:
            return favorites.replace(request.state.user.id, [(i.ticker, i.name) for i in body.items])
        except TooMany as exc:
            raise HTTPException(422, str(exc)) from None
        except Conflict as exc:
            raise HTTPException(409, str(exc)) from None

    @app.post("/api/favorites")
    def add_favorite(body: FavoriteIn, request: Request):
        user_id = request.state.user.id
        try:
            added = favorites.add(user_id, body.ticker, body.name)
        except (TooMany, Conflict) as exc:
            raise HTTPException(409, str(exc)) from None
        return JSONResponse(favorites.list(user_id), status_code=201 if added else 200)

    @app.delete("/api/favorites/{ticker}", status_code=204)
    def remove_favorite(ticker: str, request: Request):
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        favorites.remove(request.state.user.id, ticker)

    # --- #154 short selling & options positioning ------------------------------------------

    async def _positioning(ticker: str) -> dict:
        async def spot_of() -> float | None:
            # asked only when the options are not cached (a fully cached batch makes no quote calls)
            try:
                return (await asyncio.wait_for(prices.quote(ticker), POSITIONING_QUOTE_SEC)).price
            except Exception:  # noqa: BLE001 - Cboe carries its own (15-min delayed) price
                return None
        return await positioning.POSITIONING.get(ticker, spot_of=spot_of)

    @app.get("/api/positioning")
    async def ticker_positioning(ticker: str):
        """Short-volume share (FINRA), short interest, option put/call · walls · max pain · implied
        move (Cboe) for a US ticker; ``supported: false`` with the reason for others."""
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return await _positioning(ticker)

    @app.get("/api/positioning/batch")
    async def batch_positioning(tickers: str):
        """The same for up to MAX_BOARD tickers (the sector tab), a few at a time."""
        try:
            names = list(dict.fromkeys(clean_ticker(t) for t in tickers.split(",") if t.strip()))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if not names or len(names) > MAX_BOARD:
            raise HTTPException(422, f"1 to {MAX_BOARD} tickers")
        gate = asyncio.Semaphore(POSITIONING_PARALLEL)

        async def one(ticker: str) -> dict:
            async with gate:
                try:
                    return await _positioning(ticker)
                except Exception as exc:  # noqa: BLE001 - one ticker down, the rest still answer
                    log.warning("positioning %s failed: %s", ticker, type(exc).__name__)
                    return positioning.blank(ticker, errors=[type(exc).__name__])
        return {"items": await asyncio.gather(*(one(t) for t in names))}

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
