"""HTTP API. Run with: uvicorn desk.api:create_app --factory --host 0.0.0.0 --port 8000

Every route needs a login session except the two in OPEN_PATHS. See docs/security.md."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import os
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as dtime
from functools import partial
from typing import Annotated, Literal
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi import Path as FastPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import not_, select
from sqlalchemy.engine import Connection
from starlette.concurrency import run_in_threadpool

from . import (
    accumulation_live,
    alert_tone,
    backtest_reports,
    binance_funding,
    bottom_zone,
    calibration,
    channels,
    chart_memos,
    chart_read,
    chat,
    credit,
    daily_resistance,
    day_calls,
    day_sectors,
    earnings,
    earnings_preview,
    entry_now,
    events_calendar,
    explain,
    extreme_fear_reports,
    fear,
    filings,
    flow_intraday,
    funds13f,
    futures_quote,
    gap_fill,
    halving,
    holding_watch,
    horizons,
    kr_flow_data,
    kr_short_sales,
    krx,
    krx_etp,
    language,
    line_basis,
    line_signals,
    line_watch_bars,
    lines_bt,
    live,
    live_bars,
    log_setup,
    long_lines,
    loop_lag,
    macro_news,
    market_bus,
    market_calendar,
    market_outlook,
    market_outlook_sources,
    market_watchboard,
    morning_brief,
    net,
    news_digest,
    notify,
    nq_fast,
    nq_watch,
    nq_weekly_alerts,
    options_detail,
    oscillator,
    otp,
    outbound,
    outcome,
    outlook,
    paper,
    playbook_live,
    positioning,
    prewarm,
    primary_trend,
    program_flow,
    program_trade,
    projection,
    regime,
    regime_live,
    report_images,
    report_plan,
    reports,
    req_timing,
    review,
    risk_regime,
    roles,
    run_events,
    run_queue,
    sector_movers,
    sector_news,
    session_progress,
    sessions,
    shared_cache,
    stock_social,
    tips,
    trend_follow,
    verdict,
    web_search,
    weekly_chart_evidence,
    weekly_map,
)
from .account_owner import AccountOwnerStore, OwnerBinding
from .auth import COOKIE, Auth, Busy, LoginThrottle, User, hash_token
from .broker import kis_stream, make_broker
from .broker_issuer_lock import BrokerIssuerLock
from .consensus import fake_summariser, llm_summariser
from .db import account_owner_binding, make_engine, migrate, now, require_schema_at_head
from .db import runs as run_rows
from .estimate import CANDLE_INTERVALS, BinanceEstimator, naver_overtime, trading_soon
from .favorites import Conflict, Favorites, TooMany
from .futures_flow import KST as futures_kst
from .futures_flow import FuturesFlow, futures_open
from .futures_flow import make as make_kis_rest
from .futures_flow import off_reason as futures_off
from .futures_flow import prompt_note as futures_note
from .futures_history import FuturesHistory, closing_window, interval_min, premarket_window, seconds_to_next
from .futures_oi import FUT_API, OPT_API, FuturesOi
from .futures_oi import MAX_DAYS as OI_MAX_DAYS
from .indicators import normalize as normalize_indicators
from .jobs import Jobs
from .leverage_analyst import fake_analyst, llm_analyst
from .longshort import LongShort
from .market_flow import MarketFlow
from .owner_attestation import TossAttestationStore, TossSource
from .portfolio import Portfolio
from .prices import INTERVALS, PriceSource, kis_quote, stale_ok
from .privacy import OwnerResources, PolicyChanged, ProcessPolicy
from .program_latest import ProgramLatest
from .related import collect as collect_related
from .run_access import account_run, reads_account
from .run_read import RunReader, last_done_seq
from .runner import ANALYSTS, Hub, RunManager, RunRequest, make_fake_runner, run_tradingagents
from .runner import rate_limited as run_rate_limited
from .sectors import Duplicate, NotFound, Sectors, clean_name, clean_tickers
from .settings import Settings, load
from .sr import frame_bars, support_resistance
from .stock_profile import MAX_TICKERS as PROFILE_MAX
from .stock_profile import Profiles
from .store import TERMINAL, AccountBindingChanged, Store, iso
from .token_store import TokenStore, TokenUnavailable
from .ttl_cache import TTLCache
from .universe import KR_SUFFIX, Universe

MAX_BOARD = 30          # tickers per /api/estimates call
CALENDAR_INTERVAL_SEC = 6 * 3600   # economic calendar re-read (#148)
POSITIONING_PARALLEL = 3   # Cboe chains are ~2 MB each (#154)
POSITIONING_QUOTE_SEC = 5.0
TOKEN_TICK_SEC = 5.0       # #688 the minting process looks at the shared broker tokens this often
TOKEN_AHEAD_SEC = 600.0    # and renews one this long before it expires (readers trim 300 s)
CREDIT_INTERVAL_SEC = 6 * 3600   # KOFIA posts the day's numbers the next business day (#155)
CREDIT_RETRY_SEC = 15 * 60
BOARD_PARALLEL = 6      # concurrent Binance / quote lookups per call
BATCH_PARALLEL = 4      # #705 /api/batch/{kind}: tickers worked on at once, for the whole app (as a browser asked before)
BATCH_PARALLEL_BARS = 8  # bars (candles · estimate-candles) are light and cached: the market tiles draw sooner
# #858 batch items worked on at once for the whole app, over the kinds' own gates: in the dashboard's first-look
# burst their thread work (Yahoo reads and parsing, levels) took every thread of the loop's default executor (20 at
# 16 CPUs) and every other route's to_thread queued behind it (a chart, the paper desk: 2-3 s). Measured on a cold
# burst: 14 keeps the others at their own source time and costs the burst ~30%; 10 the same for the others at ~50%.
# The quote board is not under it (BOARD_PARALLEL; in the session its rows are async reads and cached closes)
SCREEN_PARALLEL = 14
BATCH_MAX_COUNT = 500    # bars a ticker in one batch (the chart's own path allows more, one ticker at a time)
# #858 /api/batch/candles takes bars up to this long past their TTL at once while a background read brings new ones
# (desk.prices.STALE_OK): one minute's poll of the market tiles and a margin - a page back within that no longer
# waits on dozens of Yahoo reads, one back after longer reads anew. Only the bars kind: the levels, playbooks and
# projections are kept in caches of their own and are never built from old bars
BATCH_STALE_SEC = 90.0
FUNDS_TICKERS_MAX = 30   # #483 tickers per /api/funds/changes call (the favorites' badges)
# #240: the bottom-zone line of the weekly note waits this long (the scalp budget is 5 s; the line is
# skipped, not the weekly map, when a cold cache is slower)
BOTTOM_NOTE_SEC = 4.0
BOTTOM_QUOTE_SEC = 2.0   # #310: the price the bottom-zone ladder is cut at, per lookup
SECTORS_WAIT_SEC = 20.0  # #620: a first /api/day-call/sectors waits this long for the bars, then says "loading"
TREND_WAIT_SEC = 8.0     # #460: /api/tips waits this long for the indices' trend, then answers without it
GAPS_TTL_SEC = 300.0     # #465: a ticker's unfilled gaps are kept as long as its swing levels (desk.horizons sr_ttl_sec)
GAP_FILL_MAX = 4          # #577: background reads of the gap odds at once (the entry summary never waits for one)
# #257: US pre / after / overnight trades on the board - a last trade this fresh still counts as live
# (thin ETFs trade minutes apart off hours), and a newly watched ticker waits this long for its first
OFF_HOURS_FRESH_SEC = 600.0
# #250: a broker quote off hours counts as a trade only this far (as a fraction) off the last close
OFF_HOURS_MOVE = 0.0001
FIRST_TICK_WAIT_SEC = 0.4      # #643 a short wait: the prewarm subscribes the favorites ahead, the next poll fills in
FIRST_TICK_POLL_SEC = 0.25
FIRST_TICK_RETRY_SEC = 600.0     # a ticker the board waited for is not waited for again before this
KST = timezone(timedelta(hours=9))   # Toss dates (order history from/to) are KST
# #858 every request's policy and session checks (small DB reads) run on threads of their own: on the loop's
# default executor they queued behind the sources' blocking reads (Yahoo ~1 s each, dozens in the dashboard's
# first-look burst) and every request in that burst - a /api/auth/me included - waited seconds before its handler
_ADMISSION = ThreadPoolExecutor(max_workers=4, thread_name_prefix="desk-admission")


async def _admit(fn, /, *args, **kwargs):
    """``asyncio.to_thread`` on the admission threads (the caller's context goes along, as to_thread's does)."""
    ctx = contextvars.copy_context()
    return await asyncio.get_running_loop().run_in_executor(_ADMISSION, partial(ctx.run, fn, *args, **kwargs))
REVIEW_CACHE_SEC = 60
REVIEW_FAIL_CACHE_SEC = 30
log = logging.getLogger(__name__)
NL2 = "\n\n"   # ends an SSE frame
MARKET_STREAMS_PER_SESSION = 3    # #690 open market streams per session (one per tab)
MARKET_STREAM_TICKERS = 50        # quote channel tickers per stream
HEARTBEAT_SEC = 15
_RUN_READ_CACHE = {"Cache-Control": "private, no-store"}
OPEN_PATHS = frozenset({"/api/health", "/api/auth/login"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
# #762 a request the screen sent with no one at it (the client marks it - frontend/src/activity.ts): it is
# checked like any other but is not activity, so a tab left open still goes idle. It can only shorten a
# session - a forged header gains nothing.
BACKGROUND_HEADER = "x-desk-background"
# #762 an open stream re-checks the session at least this often, whatever its own round (docs/security.md §2)
STREAM_RECHECK_SEC = 15.0


def is_activity(request: Request) -> bool:
    """#762 whether a request counts as the person's activity: not one the screen marked as sent with no one
    at it, and not a GET of an SSE stream - an EventSource cannot carry the header and reconnects by itself
    (a restart, a network change), while the screen that opens it has already made its own requests."""
    if request.headers.get(BACKGROUND_HEADER) == "1":
        return False
    path = request.url.path
    return not (request.method == "GET" and (path.endswith("/stream") or path == "/api/stream/market"))


# #431 what a run cut off by a restart (a deploy) says on its failed screen
RESTART_REASON = "서버가 다시 시작돼 분석이 중단됐습니다."


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


# #477: a bare KRX code (005930, or the newer 0126Z0) is a Korean stock - Yahoo and the page want it
# with its market's suffix. ``kr_market`` (set by create_app: the instrument list, desk.universe)
# says KOSPI or KOSDAQ; a code it does not know gets .KS, and /api/candles tries .KQ once when .KS
# comes back empty (``learned_kr`` then keeps the answer for every other path). The retry only sees
# an empty answer where Yahoo serves the bars: on a Toss-first path (daily bars) Toss answers the bare
# code whatever its market, so an unlisted KOSDAQ code stays labelled .KS there - which only happens
# while the instrument list does not know it (first boot, a Toss outage, a new listing).
KR_CODE = re.compile(r"^[0-9][0-9A-Z]{5}$")
kr_market: Callable[[str], str | None] = lambda code: None   # replaced by create_app (Universe.market_of)
learned_kr: dict[str, str] = {}


def kr_ticker(code: str) -> tuple[str, bool]:
    """(the ticker of a code-shaped symbol, whether its suffix was only a guess). The instrument
    list first (a code it lists on another market - none today - is left alone), then a market
    learned from the bars, then .KS."""
    try:
        market = kr_market(code)
    except Exception:  # noqa: BLE001 - the list is a help, not a gate: guess
        market = None
    if market:
        suffix = KR_SUFFIX.get(market)
        return (code + suffix, False) if suffix else (code, False)
    learned = learned_kr.get(code)       # one read: another thread may clear the dict
    return (code + learned, False) if learned else (code + ".KS", True)


def resolve_ticker(v: str) -> tuple[str, bool]:
    """``clean_ticker`` and whether a KRX suffix in it was guessed (/api/candles retries .KQ then)."""
    v = v.strip().upper()
    if not v or len(v) > 20 or not all((c.isascii() and c.isalnum()) or c in ".-^=" for c in v):
        raise ValueError("ticker may contain letters, digits and . - ^ = only")
    return kr_ticker(v) if KR_CODE.match(v) else (v, False)


def clean_ticker(v: str) -> str:
    return resolve_ticker(v)[0]


class RunIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=20)
    # Display name from the search (엔비디아). Looked up from the listing when absent.
    name: str | None = Field(default=None, max_length=200)
    # #696: no investing style any more - still accepted from older pages, and ignored
    profile: str | None = Field(default=None, max_length=40)
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


class ChatThreadIn(BaseModel):
    title: str | None = Field(default=None, max_length=chat.MAX_TITLE)


class ChatMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=chat.MAX_TEXT)
    ticker: str | None = Field(default=None, max_length=20)
    model: str | None = Field(default=None, max_length=64)   # #414: one of GET /api/chat/models

    @field_validator("ticker")
    @classmethod
    def _ticker(cls, v: str | None) -> str | None:
        return clean_ticker(v) if v else None


class PaperSettingsIn(BaseModel):
    """#527 a change to the paper account's settings: only the fields sent change (ranges: desk.paper)."""
    start_capital: int | None = None
    start_date: str | None = None
    sizing: Literal["risk", "weight"] | None = None
    risk_pct: float | None = None
    weight_pct: float | None = None
    max_positions: int | None = None
    fee_kr_pct: float | None = None
    fee_us_pct: float | None = None
    tax_kr_pct: float | None = None
    slippage_pct: float | None = None
    allow_short: bool | None = None


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


class SectorIn(BaseModel):
    name: str = Field(min_length=1, max_length=40)
    tickers: list[FavoriteIn] = Field(default_factory=list, max_length=200)   # repeats dropped, then 30 max


class SectorPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=40)
    tickers: list[FavoriteIn] | None = Field(default=None, max_length=200)


SectorId = Annotated[int, Field(ge=1, le=2**31 - 1)]   # a huge number is a 422, not a driver overflow


class SectorOrderIn(BaseModel):
    ids: list[SectorId] = Field(max_length=50)


def purge_expired_runs(store: Store, retention_days: int) -> int:
    """Delete finished runs older than ``retention_days`` (0 = keep everything)."""
    if retention_days <= 0:
        return 0
    deleted = store.purge_finished_before(now() - timedelta(days=retention_days))
    if deleted:
        log.info("retention: deleted %d run(s) older than %d days", deleted, retention_days)
    return deleted


PREMARKET_CACHE_SEC = 30.0   # #677 the pre-market block of /api/market/flow, read from the snapshots
PREMARKET_FINAL = dtime(9, 1)   # read after this, the block is the day's
SPOT_TIMEOUT_SEC = 10.0      # #359 the spot flows fetched with each futures snapshot


def create_app(settings: Settings | None = None, runner=None, prices: PriceSource | None = None,
               estimator: BinanceEstimator | None = None, book: Portfolio | None = None,
               flow: MarketFlow | None = None, futures: FuturesFlow | None = None,
               weekly: weekly_map.Maps | None = None, bottoms: bottom_zone.Zones | None = None,
               longshort: LongShort | None = None, chatter: chat.Chatter | None = None,
               pictures: report_images.ReportImages | None = None, *, protect_run_reads: bool | None = None,
               owner_resources: OwnerResources | None = None,
               options_provider: options_detail.OptionSource | None = None,
               market_outlook_service: market_outlook.MarketOutlook | None = None,
               funding_history: binance_funding.FundingHistory | None = None,
               short_sales: kr_short_sales.KrxShortSales | None = None) -> FastAPI:
    settings = settings or load()
    log_setup.configure(settings.log_level)     # #676 the desk.* INFO lines reach the container log
    if settings.role in ("all", "api"):
        migrate(settings.database_url)
    else:
        require_schema_at_head(settings.database_url)
    engine = make_engine(settings.database_url)
    # #688 Only all and collector can issue broker tokens, while they hold the issuer lock.
    active_issuer: BrokerIssuerLock | None = None

    def issuer_lock_held() -> bool:
        issuer = active_issuer
        return issuer is not None and issuer.check()

    def fence_broker_token(conn: Connection, name: str) -> None:
        policy.lock(conn)
        if name == "toss" and approved_binding is not None:
            occupied = select(account_owner_binding.c.id).where(
                account_owner_binding.c.id == 1,
                account_owner_binding.c.user_id == approved_binding.user_id,
                account_owner_binding.c.generation == approved_binding.generation,
            ).with_for_update(read=True)
            if conn.scalar(occupied) is None:
                raise PolicyChanged("process privacy approval unavailable")
        if not issuer_lock_held():
            raise TokenUnavailable("broker issuer lock not held")

    try:
        policy = ProcessPolicy.open(engine, protect_run_reads)
        protected = policy.protected
        owners = AccountOwnerStore(engine) if protected else None
        approved_binding: OwnerBinding | None = None
        toss_seal_material: str | None = None
        if owners is not None and protect_run_reads is None:
            binding = owners.binding()
            if settings.broker != "toss" or binding is None:
                raise PolicyChanged("process privacy approval unavailable")
            source = TossSource(settings.toss_client_id, settings.toss_client_secret, settings.toss_account_seq)
            verified = TossAttestationStore(engine).verify(expected=policy.expected, binding=binding, source=source)
            if verified is None:
                raise PolicyChanged("process privacy approval unavailable")
            approved_binding, toss_seal_material = binding, verified.seal_material
        tokens = TokenStore(engine, role=settings.role, toss_seal_material=toss_seal_material,
                            fence_issue=fence_broker_token)
        if approved_binding is not None:
            # The deployed factory approves this broker only. Rehearsals can pass explicit resources.
            broker = make_broker(settings, tokens, redact_toss_errors=True)
            owner_resources = OwnerResources(approved_binding, broker)
        else:
            broker = make_broker(settings, tokens)
    except Exception:
        engine.dispose()
        raise
    store = Store(engine, policy)
    run_reader = RunReader(engine) if protected else None
    public_calibration = calibration.Table.load() if protected else None

    def current_resources() -> OwnerResources | None:
        if owners is not None and owner_resources is not None and policy.current() and owner_resources.current(owners):
            return owner_resources
        return None

    def account_broker():
        approved = current_resources()
        return approved.broker if approved is not None else None

    def send_notice(pusher, *kinds, payload, user_id=None, still_owner=None):
        # An account notice must identify the current owner. The protected dispatcher
        # never consults stored Telegram settings, even for a public notice.
        if protected and user_id is not None:
            approved = current_resources()
            if approved is None or approved.binding.user_id != user_id:
                return 0
        def allowed():
            return policy.current() and (still_owner is None or still_owner())

        if still_owner is not None and not allowed():
            return 0
        sent = notify.send(pusher, *kinds, payload=payload, user_id=user_id,
                           use_channels=not protected, allowed=allowed)
        approved = current_resources()
        if approved is not None:
            for destination in approved.destinations:
                if not set(kinds) <= destination.kinds:
                    continue
                try:
                    if current_resources() is approved and (still_owner is None or still_owner()):
                        destination.deliver(payload)
                except Exception as exc:  # noqa: BLE001 - a destination's details stay out of the logs
                    log.warning("approved destination delivery failed: %s", type(exc).__name__)
        return sent
    futures_history = FuturesHistory(engine)    # #254
    program_latest = ProgramLatest(futures_history, snapshot_min=settings.futures_snapshot_min)
    futures_oi = FuturesOi(engine)              # #603 KRX open interest · basis · put/call per day
    auth = Auth(engine, timedelta(hours=settings.session_max_hours), timedelta(hours=settings.session_idle_hours))
    throttle = LoginThrottle()
    otp_box = otp.SecretBox(settings.otp_key)
    if otp_box.error:
        log.error("%s — OTP accounts cannot log in until it is fixed", otp_box.error)
    challenges = otp.Challenges()
    # Failed codes per account, across IPs (docs/security.md §6).
    otp_fails = LoginThrottle(per_ip=otp.USER_FAILS, window_sec=otp.USER_FAIL_WINDOW_SEC)
    universe = Universe(engine)
    global kr_market
    kr_market = universe.market_of   # #477

    def listing_name(ticker: str) -> str | None:
        return (universe.lookup(ticker) or {}).get("name")

    earnings.CACHE.name_of = listing_name      # #308: a Korean report shows its listed name

    favorites = Favorites(engine, listing_name)
    # #514 the bitcoin halving cycles (a made-up history with scripted runs: no network)
    halving_cache = halving.Cache(halving.fake_closes if settings.fake_runs else halving.yahoo_closes)
    # #495 the beginner reports (made-up prices with scripted runs: no network)
    # (scripted runs: no automatic reports, so no "next due" either)
    report_desk = reports.Reports(engine, reports.fake_fetch if settings.fake_runs else reports.yahoo_fetch,
                                  0 if settings.fake_runs else settings.report_every_days,
                                  topics=report_plan.topics(settings.report_topics))
    # #681 the report's one model call: the deep tier, or DESK_REPORT_MODEL / DESK_REPORT_REASONING
    report_model = reports.model_call(settings.report_model, settings.report_reasoning)
    report_tasks: set[asyncio.Task] = set()
    # #497 explainer pictures (OpenAI image API): off without OPENAI_API_KEY, and always with scripted runs
    if pictures is None:
        pictures = report_images.ReportImages(None) if settings.fake_runs else report_images.from_env()

    async def make_report(auto: bool, taken: bool = False, slot: report_plan.Slot | None = None) -> str:
        """One report (#495): ``made`` · ``none`` (no subject had data) · ``busy`` (one being written, or -
        automatic only - no free ChatGPT slot: try later; a manual one is written in the desk's words)."""
        if not taken:                    # ``taken``: the caller already holds the one-at-a-time flag
            if report_desk.generating:
                return "busy"
            report_desk.generating = True
        # #681 the slot's kind (a manual one: the weekly wrap-up on Saturday, the week ahead on Sunday, else the day's)
        kind = slot.kind if slot else report_plan.kind_for(datetime.now(UTC))
        try:
            if settings.fake_runs:
                out = await asyncio.to_thread(report_desk.make, None, kind, slot)
            else:
                if auto:                 # a run going: no price fetches either, try later
                    async with app.state.manager.spare_slot() as free:
                        if not free:
                            return "busy"
                got = await asyncio.to_thread(report_desk.choose, kind)   # the fetches, without holding the slot
                if got is None:
                    return "none"
                async with app.state.manager.spare_slot() as free:  # the slot only around the one model call
                    if not free and auto:
                        return "busy"
                    out = await asyncio.to_thread(report_desk.finish, got, report_model if free else None, slot)
                # #682 the candle charts' price fetches out of the ChatGPT slot; before the push, so it opens whole
                try:
                    out = await asyncio.to_thread(report_desk.attach_charts, out)
                except Exception as exc:  # noqa: BLE001 - a chart never costs the report
                    log.warning("report charts failed: %s", type(exc).__name__)
            if out is not None and pictures.enabled:   # out of the ChatGPT slot; before the push, so it opens whole
                try:
                    drawn = await asyncio.to_thread(pictures.draw, out)
                    await asyncio.to_thread(report_desk.set_images, out["id"], drawn)
                except Exception as exc:  # noqa: BLE001 - a picture never costs the report
                    log.warning("report images failed: %s", type(exc).__name__)
        finally:
            report_desk.generating = False
        if out is None:
            return "none"
        log.info("report %s on %s (%s)", out["id"], out["subject"]["symbol"], out["source"])
        try:                             # a push problem never loses the report
            owner = None if policy.protected else await asyncio.to_thread(report_desk.owner)
            if owner is not None and policy.current():        # the owner only: the subject may be one of the owner's favorites
                await asyncio.to_thread(app.state.pusher.to_user, owner, {
                    "title": out["title"], "body": out.get("summary") or "", "tag": "report",
                    "url": f"/#/reports/{out['id']}"})
        except Exception as exc:  # noqa: BLE001
            log.warning("report push failed: %s", exc)
        return "made"
    # #571 the morning brief (made-up numbers with scripted runs: no network, no model)
    brief_desk = morning_brief.Briefs(engine, None if settings.fake_runs else morning_brief.parse_at(settings.morning_brief_at))
    brief_tasks: set[asyncio.Task] = set()

    def brief_sources() -> morning_brief.Sources:
        if settings.fake_runs:
            return morning_brief.fake_sources()
        return morning_brief.Sources(
            candles=lambda *a: prices._yahoo_candles(*a),
            fear=fear_gauges.get if fear_gauges is not None else None,
            flow=lambda m: flow.get(m, "1d", 5),
            month=market_calendar.month_view,
            macro=lambda: macro_news.CACHE.brief(None),
            day_call=morning_brief.day_call_source(),
            extra=morning_brief.hand_kept,
            krx=lambda: krx_etp.summary(engine, datetime.now(krx.KST).date()),
            sectors=(lambda: sector_cache.read("kr", sector_candles)) if sector_candles is not None else None,
            # #680 the US sectors' big days of the last session and the stocks that drove them
            movers=(lambda: asyncio.gather(*(sector_movers.read(k, prices, mover_causes, mover_name)
                                             for k, sec in sector_movers.SECTORS.items() if sec["market"] == "US")))
            if sector_candles is not None else None)

    async def make_brief(trigger: str, desk_only: bool = False, taken: bool = False) -> str:
        """One brief (#571): ``made`` or ``busy`` (one being written, or - automatic only - no free ChatGPT
        slot: try later). ``desk_only`` writes it in the desk's words without the model (still on time)."""
        if not taken:
            if brief_desk.generating:
                return "busy"
            brief_desk.generating = True
        try:
            raw = await morning_brief.collect(brief_sources())          # the fetches, without the slot
            b = morning_brief.bundle(raw)
            if settings.fake_runs or desk_only:
                out = await asyncio.to_thread(morning_brief.write, b, None)
            else:
                try:
                    async with app.state.manager.spare_slot() as free:  # the slot only around the one model call
                        if not free and trigger == "auto":
                            return "busy"
                        out = await asyncio.to_thread(morning_brief.write, b, projection.llm_note if free else None)
                except Exception as exc:
                    if trigger == "auto" or not run_rate_limited(exc):
                        raise
                    out = await asyncio.to_thread(morning_brief.write, b, None)   # asked for now: the desk's words
            model = None if out["source"] == "rules" else os.getenv("TRADINGAGENTS_QUICK_THINK_LLM") or "quick"
            saved = await asyncio.to_thread(brief_desk.save, out, morning_brief._public(b), model, trigger)
        finally:
            brief_desk.generating = False
        log.info("morning brief %s (%s, %s)", saved["id"], saved["source"], trigger)
        try:                             # a push problem never loses the brief
            await asyncio.to_thread(send_notice, app.state.pusher, "morning_brief", payload={   # #580 every channel
                "title": "아침 시황 · " + (saved.get("title") or ""), "body": (saved.get("numbers") or [""])[0],
                "tag": "morning_brief", "url": f"/#/briefs/morning?date={saved['date']}"})
        except Exception as exc:  # noqa: BLE001
            log.warning("morning brief push failed: %s", type(exc).__name__)
        return "made"
    sectors = Sectors(engine)
    hub = Hub()
    kis_feed = kis_stream.make(settings, tokens) if prices is None else None   # tests bring their own prices
    prices = prices or PriceSource(broker, stream=kis_feed)
    # #572 today's direction calls: made in their windows from Yahoo bars. Off (stored calls still served)
    # with the same switch as the news flow and the prewarm: scripted runs and tests fetch nothing
    day_desk = day_calls.DayCalls(engine, None if settings.fake_runs or settings.macro_interval_sec <= 0
                                  else (lambda *a: prices._yahoo_candles(*a)))

    # #681 the week's day-call record for Saturday's report
    report_desk.calls = lambda: reports.week_calls(lambda market, days: day_desk.stats(market, days=days))
    # the market-wide macro releases (FOMC, CPI, jobs) for Sunday's week ahead
    report_desk.events = lambda now: [{"label": e.label, "at": e.at}
                                      for e in events_calendar.CACHE.upcoming(now, days=14, importance=2)]
    # #620 today's strong / weak sectors per market, from the sector ETFs' daily bars (the same switch)
    sector_candles = None if settings.fake_runs or settings.macro_interval_sec <= 0 else day_sectors.yahoo_adjusted
    sector_cache = day_sectors.Cache()
    # #680 a big day's culprits: a Korean member's listed name, and why a member moved (an earnings report)

    def mover_name(t: str) -> str | None:
        return listing_name(t) if t.upper().endswith((".KS", ".KQ")) else None

    def mover_causes(tickers: set[str], day) -> dict[str, str]:
        rows = earnings.CACHE.for_days(day - timedelta(days=1), day, set(tickers))
        return sector_movers.causes_from_earnings(rows, set(tickers), day)

    movers_cache: TTLCache[str, dict] = TTLCache(len(sector_movers.SECTORS) + 2, 60.0)
    sector_tasks: dict[str, asyncio.Task] = {}
    # #601 KRX OPEN API end-of-day history: only with a key, and off in scripted runs and tests
    krx_desk = (krx.KrxDaily(engine, krx.Client(settings.krx_api_key))
                if krx.off_reason(settings) is None else None)           # #601 no key: off, nothing else changes
    # #462: VIX · CNN / crypto Fear & Greed · VKOSPI, cached per source (fakes stay offline in tests)
    fear_gauges = (fear.Fear(candles=lambda *a: prices._yahoo_candles(*a),   # read when asked, as risk does
                             kis=lambda: getattr(futures, "rest", None),         # #486 VKOSPI via the futures KIS client
                             history=lambda: fear.krx_history(engine))           # #602 its KRX closes (#601 krx_daily)
                   if not settings.fake_runs else None)
    # #146: the market's selling score from the same Yahoo source as the prices (cached ~15 min)
    risk_cache = None if settings.fake_runs else risk_regime.Cache(
        fetcher=lambda symbols: risk_regime.fetch(symbols, candles=prices._yahoo_candles))
    risk = risk_cache.current if risk_cache is not None else None
    # #330: rate / dollar regime and its tips (^IRX ^TNX DX-Y.NYB QQQ, cached 3 h)
    tip_cache = tips.Cache(fetcher=lambda symbols: risk_regime.fetch(symbols, candles=prices._yahoo_candles,
                                                                     count=tips.HISTORY_BARS))
    funds = funds13f.Funds()   # #483 the well-known funds' 13F holdings (SEC EDGAR), refreshed by refresh_funds
    market_outlook_reader = market_outlook_service if market_outlook_service is not None else market_outlook.MarketOutlook(
        candles=None if settings.fake_runs else getattr(prices, "candles", None),
        filing=None if settings.fake_runs else partial(market_outlook_sources.read_duquesne, funds),
        views=market_outlook_sources.read_views,
    )
    # #460: the indices' primary trend (^KS11 ^NDX ^GSPC ^SOX, about ten years of dailies, cached 3 h)
    trend_cache = primary_trend.Cache(fetcher=lambda symbols: risk_regime.fetch(
        symbols, candles=prices._yahoo_candles, count=primary_trend.HISTORY_BARS))
    gap_cache: dict[str, tuple[float, list[dict]]] = {}   # #465: ticker -> (when, desk.line_basis.find_gaps)
    # #203: years of Yahoo bars for the RSI / Stochastic extremes and what followed them
    # (read when called: a test's price source may have no Yahoo at all)
    oscillator_fetch = oscillator.yahoo_fetch(lambda *args: prices._yahoo_candles(*args))
    # #577 the session gaps' fill odds: about eight years of daily bars, the macro calendar for the causes
    gap_fetch = gap_fill.yahoo_fetch(lambda *args: prices._yahoo_candles(*args))

    def gap_events():
        return events_calendar.CACHE.events()

    gap_tasks: set[asyncio.Task] = set()   # background reads for the entry summary (#577)
    estimator = estimator or BinanceEstimator(overtime=naver_overtime)   # #283: KRX after-hours, live only
    # #271: Binance futures traders' long / short split, on the estimate's own contract mapping
    longshort = longshort or LongShort(lambda ticker: estimator.resolve(ticker))   # read when asked (test fakes)
    book = book or Portfolio(broker, prices)
    profiles = Profiles(prices, universe.lookup)   # #295 the stock card
    flow = flow or MarketFlow(broker)
    # KOSPI200 futures from KIS REST (#160); without the KIS keys it answers supported: false
    futures = futures or FuturesFlow(make_kis_rest(settings, tokens) if not settings.fake_runs else None, flow,
                                     off=futures_off(settings))
    # #443 day / night session price and minute bars on the same KIS client (one token)
    futures_quotes = futures_quote.FuturesQuote(getattr(futures, "rest", None), off=futures_off(settings))
    # #676 today's spot investors from KIS first, on the same client (one token)
    if isinstance(flow, MarketFlow) and flow.kis is None and getattr(futures, "rest", None) is not None:
        flow.kis = futures.rest
    # #979 domestic short-sale shares from KIS' daily trend, on the same client (one token); KIS has no
    # net balance, so that series says source_unavailable. No KIS client: the whole card says so.
    short_sales = short_sales or kr_short_sales.connect(getattr(futures, "rest", None))

    async def _futures_note() -> str | None:
        return futures_note(await futures.get())

    # #202: the weekly market map of the main indices (Yahoo weekly bars, cached an hour)
    weekly = weekly or weekly_map.Maps()

    # #240: the long-term bottom zone of the ticker itself (Yahoo weekly bars, cached an hour)
    bottoms = bottoms or bottom_zone.Zones()

    async def _price_now(ticker: str, daily: bool = True) -> tuple[float | None, str | None, str | None]:
        """#310: (price, source, at) for the bottom-zone ladder - the live quote, else (``daily``) the
        last daily close; (None, None, None) leaves the weekly close in charge."""
        try:
            q = await asyncio.wait_for(prices.quote(ticker), BOTTOM_QUOTE_SEC)
            return q.price, q.source, q.fetched_at
        except Exception as exc:  # noqa: BLE001 - no quote: the daily close, then the weekly one
            log.info("bottom zone quote for %s failed: %s", ticker, type(exc).__name__)
        if daily:
            try:
                bars = (await asyncio.wait_for(prices.candles(ticker, "1d", 5), BOTTOM_QUOTE_SEC))["bars"]
                if bars:
                    return bars[-1]["close"], "daily_close", bars[-1]["at"]
            except Exception as exc:  # noqa: BLE001
                log.info("bottom zone daily close for %s failed: %s", ticker, type(exc).__name__)
        return None, None, None

    async def _bottom_note(ticker: str) -> str | None:
        price, source, _at = await _price_now(ticker, daily=False)    # the note's budget is short
        return await asyncio.to_thread(bottoms.note_for, ticker, price, source)

    async def _weekly_note(ticker: str) -> str | None:
        # the index's weekly map (#202) and the ticker's own long-term bottom zone (#240), one note.
        # The bottom zone gets its own short budget (a cold cache fetches ~20 years of weekly bars)
        # so it never makes the weekly map late, and either one failing leaves the other.
        lines = await asyncio.gather(
            asyncio.to_thread(weekly.note_for, ticker),
            asyncio.wait_for(_bottom_note(ticker), BOTTOM_NOTE_SEC),
            return_exceptions=True)
        for line in lines:
            if isinstance(line, BaseException):
                log.info("weekly / bottom note for %s skipped: %s", ticker, type(line).__name__)
        return "\n".join(x for x in lines if isinstance(x, str) and x) or None

    live_streams: dict[str, int] = {}   # open /api/live/stream per session (token hash)
    calendar = live.Calendar(broker)     # market holidays for the live stream, cached per market
    # #653 the analysis chart's last bar carried on with the price now (short cache, one fetch per key)
    chart_live = live_bars.LiveBars(prices, estimator, lambda t: sessions.session(calendar, t),
                                    watch=lambda t: live.watch(prices, t),
                                    off_hours=lambda t, s, c: _chart_off_hours(t, s, c))   # defined below
    if runner is None:
        runner = make_fake_runner(settings.fake_delay) if settings.fake_runs else run_tradingagents
    runner_for = None
    if protected and runner is run_tradingagents:
        runner = partial(run_tradingagents, isolate_artifacts=True)
    elif runner is run_tradingagents:
        # #775 legacy runs keep TradingAgents' memory: a ticker's past decisions (and other tickers' reflections)
        # reach the next run's prompt. The default memory file has the owner's account runs' decisions, so only
        # those runs (owner-only to read) keep it; the owner's other runs keep a public one; another account's
        # run - or one with no recorded submitter (an older run, a deleted account) - keeps none.
        isolated_runner = partial(run_tradingagents, isolate_artifacts=True)
        public_runner = partial(run_tradingagents, public_memory=True)

        def runner_for(run_id: str):
            if store.submitter_of(run_id) != auth.owner_id():
                return isolated_runner
            run = store.get_run(run_id)
            if run is None:
                return isolated_runner
            # named, never None: an undecided run must not fall back on the account memory
            return run_tradingagents if reads_account(run) else public_runner

    async def run_outlook(ticker: str, trade_date: str, _price: float | None, _sr: dict | None) -> dict:
        """#437: #299's projection on the daily (swing) frame up to the run's date, whatever the
        run's horizon - the outlook reads one week and one month ahead."""
        daily = horizons.get(None)
        levels = await support_resistance(prices, ticker, trade_date, horizon=daily.id)
        bars = (await frame_bars(prices, ticker, trade_date, daily))[0]
        return await asyncio.to_thread(projection.project, levels, bars, outlook.TIMEFRAME, levels.get("reference_price"))

    async def fake_outlook(_ticker: str, _trade_date: str, price: float | None, sr: dict | None) -> dict | None:
        """DESK_FAKE_RUNS: the projection on a scripted walk around the plan's price (no prices source)."""
        return outlook.fake_projection(price, sr) if price else None

    # #623 the background jobs and what GET /api/status shows of them (desk.jobs). Parts are the sources
    # read inside one loop's pass, each with its own line
    # #688 the list itself, with the process role that runs each job, is desk.roles.JOBS
    jobs = Jobs()
    # #859 how late the event loop runs and what held it (/api/status, the slow-request log)
    lag = loop_lag.LoopLag()
    for name, label, source, parent, _role in roles.JOBS:
        jobs.add(name, label, source, parent)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not policy.current():
            raise PolicyChanged("process privacy mode changed")
        if approved_binding is not None and owners.binding() != approved_binding:
            raise PolicyChanged("process privacy approval unavailable")
        lag_task = asyncio.create_task(lag.run())
        await net.warm()                   # #859 the shared TLS context, built off the loop before any client
        shared_cache.configure(settings)   # #689 Redis behind some caches; nothing configured = memory only
        # #690 the market stream: in this process (all), or over Redis pub/sub when split (docs/market-stream.md)
        market_bus.BUS.configure(settings, settings.role)
        market_bus.BUS.expect("futures", lambda: futures_open(datetime.now(UTC)))
        market_bus.BUS.expect("flow", lambda: premarket_window(datetime.now(UTC)) or futures_open(datetime.now(UTC)))
        if kis_feed is not None:
            # source_at stays None: a KIS trade carries no exchange time here, and received_at is the desk's own
            # (to the second) - as a source stamp it would show up as a fake source lag in /api/status (#690)
            kis_feed.on_tick = lambda ticker, tick: market_bus.publish(
                "quote", ticker, kis_quote(ticker, tick).to_dict(), "kis")
        # #688 only a process that runs the runs it is handed (all) fails what its predecessor left; the analyst
        # does it in desk.run_queue once it holds the queue, the api and the collector never. all holds the
        # queue's lock too (PostgreSQL), so an analyst started beside it by mistake claims none of its runs.
        all_lock = None
        if settings.role == "all":
            all_lock = run_queue.Holder(engine)
            if await asyncio.to_thread(all_lock.hold):
                stale = store.fail_unfinished(RESTART_REASON)
                if stale:
                    log.warning("marked %d unfinished run(s) as failed", stale)
            else:
                log.error("another process (an analyst?) holds the run queue on this database: this one runs the "
                          "runs it is handed but leaves unfinished ones alone - run all or the split, not both")
        # Scripted runs keep levels on the script's numbers instead of fetching a live quote.
        app.state.manager = RunManager(store, hub, runner, broker, settings.max_concurrent_runs,
                                       prices=None if settings.fake_runs else prices,
                                       # a scripted run stays offline, like its support/resistance
                                       related=None if settings.fake_runs else collect_related,
                                       lever_analyst=fake_analyst if settings.fake_runs else llm_analyst,
                                       summariser=fake_summariser if settings.fake_runs else llm_summariser,
                                       # #567: a scripted run spends no quota on translation
                                       translator=None if settings.fake_runs else language.llm_translator,
                                       # #437: the run's frozen look ahead (#299's daily projection)
                                       outlook=fake_outlook if settings.fake_runs else run_outlook,
                                       # world news flow: the background cache below, never fetched in a run
                                       macro=None if settings.fake_runs else macro_news.prompt_note,
                                       tips=None if settings.fake_runs else tip_cache.note,
                                       # #148: the economic calendar (background cache, same-day runs)
                                       calendar=None if settings.fake_runs else events_calendar.prompt_note,
                                       # #155: margin debt vs deposits (background cache, Korean same-day runs)
                                       credit=None if settings.fake_runs else credit.prompt_note,
                                       # #148: SEC 8-K / Form 4 of a US company, fetched per run (one or two calls)
                                       filings=None if settings.fake_runs else filings.note_for,
                                       # #154: short volume + options of a US ticker (cached ~15 min)
                                       positioning=None if settings.fake_runs else positioning.note_for,
                                       # #160: KOSPI200 futures from the cached KIS view (Korean same-day runs)
                                       futures=None if settings.fake_runs else _futures_note,
                                       # #203: overbought / oversold and its record (same-day runs)
                                       oscillator=None if settings.fake_runs else (
                                           lambda ticker: oscillator.note_for(oscillator_fetch, ticker)),
                                       # #577: open session gaps and their past fill odds (same day)
                                       gaps=None if settings.fake_runs else (
                                           lambda ticker: gap_fill.note_for(gap_fetch, ticker, gap_events)),
                                       # #202: the index map behind a leveraged / sector ticker (same day)
                                       weekly=None if settings.fake_runs else _weekly_note,
                                       risk=risk,
                                       # #171: recent news/fundamentals reports taken over (0 = off)
                                       reuse_minutes=settings.reuse_reports_min,
                                       rate_limit_cooldown_min=settings.rate_limit_cooldown_min,
                                       direct=settings.role == "all", protect_account_runs=protected,
                                       calibration_table=public_calibration, account_broker=account_broker,   # #688 split queues for the analyst
                                       runner_for=runner_for)

        # #623 every loop below is a step that returns its next wait, run by jobs.run_forever: an exception
        # is logged and the loop goes on after its retry wait; GET /api/status shows each one's state
        token_rest = getattr(futures, "rest", None)
        token_owners = [o for o in (broker, token_rest) if callable(getattr(o, "keep_token", None))]

        async def keep_tokens() -> float:
            if not await asyncio.to_thread(lambda: issuer_lock_held() and policy.current()):
                raise RuntimeError("broker issuer lock lost")
            # #688 the minting process renews the shared broker tokens ahead of expiry and answers the
            # other processes' requests (desk.token_store); the KIS approval key only when one was asked for
            keeps = [(type(o).__name__, o.keep_token) for o in token_owners]
            if kis_feed is not None:
                keeps.append(("KisStream", kis_feed.keep_approval))
            failed = []
            for who, keep in keeps:
                try:                       # one provider down does not hold up the others' renewals
                    await keep(TOKEN_AHEAD_SEC)
                except Exception as exc:  # noqa: BLE001 - the type only: a message may carry a request
                    log.warning("broker token renewal %s failed: %s", who, type(exc).__name__)
                    failed.append(who)
            if failed:                     # GET /api/status shows the job in error (names only)
                raise RuntimeError(f"renewal failed: {', '.join(failed)}")
            return TOKEN_TICK_SEC

        async def keep_listing_fresh() -> float:
            await universe.refresh_if_stale(broker)
            return 3600

        async def purge_old_runs() -> float:
            await asyncio.to_thread(purge_expired_runs, store, settings.retention_days)
            return 24 * 3600

        scoring = {"fresh": True}                         # the live record is read once at boot

        async def score_plans() -> float:
            # How each plan played out, on the bars that came after it (desk.outcome). Starts at boot.
            scored = await outcome.sweep(store, prices)
            if scored:
                log.info("scored %d plan(s)", scored)
            if scored or scoring["fresh"]:
                # #147: the playbooks' live record on top of the backtest prior
                since = now() - timedelta(days=calibration.LIVE_DAYS)
                runs_scored = await asyncio.to_thread(store.scored_since, since, public_only=protected)
                (public_calibration or calibration.TABLE).set_live(runs_scored)
                scoring["fresh"] = False
            return settings.score_interval_sec

        async def refresh_macro() -> float:
            # GDELT allows one request every five seconds: a full pass takes a couple of minutes,
            # so it runs here in the background and runs read the snapshot (desk.macro_news).
            with jobs.part("macro_news", "macro news refresh failed"):
                ok = await macro_news.CACHE.refresh()
                log.info("macro news: %d/%d theme(s) refreshed", ok, len(macro_news.THEMES))
                # #242: Korean titles and a market read, only while no analysis holds the plan
                read = await news_digest.DIGEST.refresh(macro_news.CACHE.themes, slot=app.state.manager.spare_slot)
                if read:
                    log.info("news digest: %d headline(s) read", read)
            with jobs.part("sector_news", "sector news refresh failed"):   # #244: sector / key-person surges
                ok = await sector_news.CACHE.refresh()
                read = await news_digest.DIGEST.refresh(sector_news.CACHE.as_themes(),
                                                        slot=app.state.manager.spare_slot)
                log.info("sector news: %d topic(s) refreshed, %d headline(s) read", ok, read)
            return settings.macro_interval_sec

        async def refresh_calendar() -> float:
            # #148: FOMC + BLS release dates change rarely; a few reads a day keep them current
            with jobs.part("economic_calendar", "economic calendar refresh failed"):
                ok = await events_calendar.CACHE.refresh()
                log.info("economic calendar: %d/2 source(s) refreshed", ok)
            with jobs.part("earnings", "earnings calendar refresh failed"):   # #243: this month and the next
                ok = await earnings.CACHE.refresh()
                log.info("earnings calendar: %d day(s)/ticker(s) refreshed", ok)
            # #308: a failed earnings pass comes back sooner (30 min, 1 h, 2 h, 4 h)
            return earnings.CACHE.next_delay(CALENDAR_INTERVAL_SEC)

        async def refresh_credit() -> float:
            # #155: margin loans and investor deposits (KOFIA) - a few reads a day catch the new day
            ok = await credit.CACHE.refresh_and_publish()
            log.info("KOFIA credit: %s", "refreshed" if ok else f"failed ({credit.CACHE.error})")
            if not ok:
                jobs.mark_error("credit", "refresh failed")
            # A failed source refresh is retried sooner and leaves the last good data in place.
            return CREDIT_INTERVAL_SEC if ok else CREDIT_RETRY_SEC

        snapshot_marks: dict[str, date | None] = {"pruned_on": None, "closed_on": None}
        futures_every = interval_min(settings.futures_snapshot_min)

        async def snapshot_futures() -> float:
            # #254: the futures investor flows through the session, every DESK_FUTURES_SNAPSHOT_MIN
            # minutes (desk.futures_history); outside the session it only checks the clock
            # on the wall clock's grid from 08:45 (#677 spot only from 08:00), plus one closing snapshot just after 15:45 (the
            # day's final totals: its daily row); each one fetches fresh, never a cached pre-open value
            moment = datetime.now(UTC)
            today_kst = moment.astimezone(futures_kst).date()

            async def spot_now() -> dict | None:
                # #359 the spot flows at the same moment (a broker failure is an ``error`` body)
                # (bounded: a hung broker must not cost the futures snapshot); #673 fetched fresh, so the
                # stored figures are the minute's and the card's cache is renewed - first, so the futures
                # view's own KOSPI read (the foreign split) is served from that cache
                try:
                    got = await asyncio.wait_for(asyncio.gather(
                        flow.get("KOSPI", "1d", 1, fresh=True), flow.get("KOSDAQ", "1d", 1, fresh=True)),
                        SPOT_TIMEOUT_SEC)
                    return {"kospi": got[0], "kosdaq": got[1]}
                except TimeoutError:
                    log.warning("spot flow for the futures snapshot timed out")
                    return None

            async def program_now(at: datetime) -> dict | None:
                # #677 KRX program trading from the 09:00 bell, on the futures' KIS client (bounded, never raises)
                rest = getattr(futures, "rest", None)
                if rest is None or not program_trade.hours(at):
                    return None
                try:
                    return await program_trade.fetch(rest, at)
                except Exception as exc:  # noqa: BLE001 - the snapshot goes on without it
                    log.warning("program trade failed: %s", exc)
                    return None

            if premarket_window(moment):
                # #677 the pre-market: the spot only (no futures, no alerts - the session's rules start later)
                await asyncio.to_thread(futures_history.record_spot, await spot_now(), moment)
                if futures_open(datetime.now(UTC)):
                    return 1.0               # woke just before 08:45 and it has passed: the open's snapshot
            elif futures_open(moment) or (closing_window(moment) and snapshot_marks["closed_on"] != today_kst):
                spot = await spot_now()
                view = await futures.get(fresh=True)
                # after the futures read: a KIS token refreshed on a 401 is that (unbounded) call's to do
                program = await program_now(moment)
                await program_latest.record(view, spot, program)
                try:                     # #361: a push problem never stops the snapshots
                    await asyncio.to_thread(app.state.flow_alerts.after_snapshot, futures_history.intraday)
                except Exception as exc:  # noqa: BLE001
                    log.warning("flow turn alerts failed: %s", exc)
                try:                     # #579 the flow shifts, the same way
                    await asyncio.to_thread(app.state.market_alerts.after_snapshot, futures_history)
                except Exception as exc:  # noqa: BLE001
                    log.warning("flow shift alerts failed: %s", exc)
                if closing_window(moment):
                    snapshot_marks["closed_on"] = today_kst
            today = datetime.now(UTC).date()
            if snapshot_marks["pruned_on"] != today:
                dropped = await asyncio.to_thread(futures_history.prune)
                if dropped:
                    log.info("futures history: thinned %d old snapshot(s)", dropped)
                snapshot_marks["pruned_on"] = today
            return seconds_to_next(datetime.now(UTC), futures_every)

        async def watch_nq() -> float:
            # #579 NQ=F selling alarms (desk.nq_watch) on Yahoo's 5-minute bars while CME trades -
            # just after each bar closes on the 5-minute grid, so every finished bar is judged once.
            # #674 the late check behind nq_fast: a move it already sent is not sent again
            now_ = datetime.now(UTC)
            if nq_watch.cme_open(now_):
                got = await prices.candles(nq_watch.TICKER, "5m", nq_watch.BARS)
                await asyncio.to_thread(app.state.market_alerts.nq_pass, got["bars"])
                done = [b for b in got["bars"] if datetime.fromisoformat(b["at"]) + nq_watch.BAR <= now_]
                if done:
                    end = datetime.fromisoformat(done[-1]["at"]) + nq_watch.BAR
                    jobs.get("nq_watch").note = {
                        "at": now_.isoformat(timespec="seconds"), "source": "yahoo",
                        "price_at": end.astimezone(UTC).isoformat(timespec="seconds"),
                        "delay_sec": round((now_ - end).total_seconds())}     # the bar's real end: lag included
            return nq_watch.seconds_to_next(datetime.now(UTC))

        async def qqq_live() -> dict:
            # #653's live price of QQQ (the KIS stream in regular hours) for nq_fast
            got = await chart_live.get(nq_fast.QQQ, "1m")
            return {"price": got["price"], "at": got["price_at"], "source": got["source"],
                    "live": got["live"], "estimated": got["estimated"]}

        async def qqq_regular() -> bool:
            return await sessions.session(calendar, nq_fast.QQQ) == "regular"

        nq_fast_watch = nq_fast.NqFast(prices, estimator, qqq_live, qqq_regular)

        async def watch_nq_fast() -> float:
            # #674 NQ=F alarms each TICK_SEC on a live price (QQQ in US hours, else the Binance contract),
            # through the same market_alerts waits as the Yahoo pass
            _, got = await nq_fast_watch.run_pass()
            if got:
                await asyncio.to_thread(app.state.market_alerts.dispatch, got)
            if nq_fast_watch.last is not None:
                jobs.get("nq_fast").note = nq_fast_watch.last
            return nq_fast.TICK_SEC

        movers_said: set[tuple[str, str, str]] = set()     # (sector, date, rule) alerted: not read again

        async def watch_sector_movers() -> float:
            # #680 each sector's big day while its market trades: the index first, the members only then
            now_ = datetime.now(UTC)
            for key, sector in sector_movers.SECTORS.items():
                if not await live.is_open(calendar, next(iter(sector["members"])), now_):
                    continue
                m = await sector_movers.big_day(key, prices, mover_causes, mover_name, now_, movers_said)
                if m is not None:
                    await asyncio.to_thread(app.state.market_alerts.dispatch, sector_movers.alerts(m, now_))
                    movers_said.add((key, m["date"], sector_movers.rule_of(m["move_pct"])))
            week_ago = (now_ - timedelta(days=7)).date().isoformat()
            movers_said.difference_update({s for s in movers_said if s[1] < week_ago})
            return sector_movers.SCAN_SEC

        async def watch_holdings() -> float:
            # #694 each holding's stop / take-profit by its regime, while its market trades
            await app.state.holding_watch.run_pass(book, prices, calendar, app.state.market_alerts.dispatch)
            return holding_watch.TICK_SEC

        async def watch_lines() -> float:
            # #613 channel / trendline signals on the watched tickers, each just after its bar closes
            await line_signals.run_pass(app.state.line_watch, prices, app.state.pusher)
            return line_signals.TICK_SEC

        async def refresh_funds() -> float:
            # #483: the funds' filings every few hours around the 45-day deadline, else daily
            ok = await asyncio.to_thread(funds.refresh)
            log.info("13F: %d/%d fund(s) refreshed", ok, len(funds13f.FUNDS))
            return funds.next_delay()

        report_failures = {"n": 0}

        async def write_reports() -> float:
            # #681 the slots of desk.report_plan (each US trading day after the close, Saturday, Sunday); while a
            # run holds the ChatGPT slot it waits and tries again; a failure (no prices, the model, the DB) tries
            # again later and later (30 min doubling to 8 h) so a lasting fault never burns the quota or Yahoo
            now = datetime.now(UTC)
            slot, later = await asyncio.to_thread(report_desk.due, now)
            if slot is None:
                return max(60.0, min((later - now).total_seconds() if later else 3600.0, 3600.0))
            failed: BaseException | None = None
            try:
                got = await make_report(auto=True, slot=slot)
            except Exception as exc:  # a failed pass must not stop the next one
                log.exception("report failed")
                failed, got = exc, "failed"
            if got == "made":
                report_failures["n"] = 0
                return 0
            if got == "busy":
                return reports.RETRY_SEC
            jobs.mark_error("reports", failed or f"report {got}")
            report_failures["n"] += 1
            return reports.RETRY_SEC * 2 ** min(report_failures["n"] - 1, 4)

        async def make_day_calls() -> float:
            # #572: every TICK_SEC - a round inside its window gets its calls (both markets), the closed
            # sessions get scored; each market's first call of the day is pushed (kind day_call), and
            # (#619) an intraday round whose stage jumped two or more from the round before
            made = []
            with jobs.part("day_calls", "day calls failed"):
                made = await asyncio.wait_for(asyncio.to_thread(day_desk.run), 180)
            for row in made:
                try:                     # a failed push loses only itself
                    push = await asyncio.to_thread(day_desk.push_of, row)
                    if push is None:     # #619 an intraday round only when its stage jumped two or more
                        continue
                    await asyncio.to_thread(send_notice, app.state.pusher, "day_call", payload={
                        "title": push[0], "body": push[1],
                        "tag": f"day_call-{row['market']}", "url": "/#/day-call", "kind": "day_call"})
                except Exception:
                    log.exception("day call push failed")
            with jobs.part("day_call_scoring", "day call scoring failed"):
                await asyncio.wait_for(asyncio.to_thread(day_desk.score), 180)
                # #604 the Korean answers again on KRX's bars once krx_daily has them
                await asyncio.wait_for(asyncio.to_thread(day_desk.recheck_krx), 180)
            return day_calls.TICK_SEC

        oi_pending = {"due": False}

        def derive_futures_oi():
            # #603: the collected days again (the catch-up window and the day before it), then KRX vs
            # the KIS closing snapshots of the same days - a gap is a warning in the log only
            last = krx.kst_today(datetime.now(UTC)) - timedelta(days=1)
            written = futures_oi.derive(last - timedelta(days=krx.CATCH_UP_DAYS + 1), last)
            log.info("futures oi: %d day(s) derived", written)
            futures_oi.check(futures_history.daily(krx.CATCH_UP_DAYS))

        async def collect_krx() -> float:
            # #601: every TICK_SEC - each service's missing business days of the last two weeks, once KRX
            # has published them (08:30 KST the next business day); a refused service waits 6 h
            results = await asyncio.wait_for(asyncio.to_thread(krx_desk.run), 600)
            for result in results:
                log.info("krx %s: %s day(s), %s row(s), stopped %s", result["api"], result["stored"],
                         result["rows"], result["stopped"])
            stopped = sorted({r["stopped"] for r in results if r["stopped"] in ("refused", "quota", "error")})
            if stopped:
                jobs.mark_error("krx", "stopped: " + ", ".join(stopped))
            if any(r["api"] in (FUT_API, OPT_API) and r["stored"] for r in results):
                oi_pending["due"] = True
            if oi_pending["due"]:            # also again after a failed derive, on the next pass
                await asyncio.to_thread(derive_futures_oi)
                oi_pending["due"] = False
            return krx.TICK_SEC

        brief_failures = {"n": 0}

        async def write_briefs() -> float:
            # #571: at DESK_MORNING_BRIEF_AT on Korean trading days; while a run holds the ChatGPT slot it
            # tries again every few minutes, and past DESK_AFTER_SEC it goes out in the desk's own words
            failed: BaseException | None = None
            try:                 # a database blip must not end the loop for good
                due = await asyncio.to_thread(brief_desk.next_due)
                wait = (due - datetime.now(UTC)).total_seconds()
                if wait > 0:
                    return min(wait, 3600)
                got = await make_brief("auto", desk_only=-wait >= morning_brief.DESK_AFTER_SEC)
            except Exception as exc:  # a failed pass must not stop the next one
                log.exception("morning brief failed")
                failed, got = exc, "failed"
            if got == "made":
                brief_failures["n"] = 0
                return 0
            if got == "busy":
                return morning_brief.RETRY_SEC
            jobs.mark_error("briefs", failed or f"brief {got}")
            brief_failures["n"] += 1
            return morning_brief.RETRY_SEC * 2 ** min(brief_failures["n"] - 1, 3)

        def prewarm_jobs() -> dict[str, prewarm.Job]:
            # #536: the dashboard's shared data, refetched before it expires (desk.prewarm) - #864 on the prewarm's
            # own workers (prewarm.off), not the pages' to_thread ones
            ahead = prewarm.AHEAD_SEC
            jobs: dict[str, prewarm.Job] = {
                "tips": lambda: prewarm.off(tip_cache.regime, ahead),
                "trend": lambda: prewarm.off(trend_cache.view, ahead),
                "weekly": lambda: prewarm.off(weekly.all, ahead),
                "halving": lambda: prewarm.off(halving_cache.get, ahead),
                # #577: the tips page's gap lines read only what is cached
                "gaps": lambda: asyncio.gather(*(prewarm.off(gap_fill.read, gap_fetch, t, gap_events,
                                                                   ahead=ahead) for t in gap_fill.WATCH)),
            }
            if sector_candles is not None:   # #620 both markets' sector picks, so a first look is instant
                jobs["sectors"] = lambda: asyncio.gather(*(prewarm.off(sector_cache.read, m, sector_candles,
                                                                             None, ahead) for m in day_sectors.MARKETS))
            if risk_cache is not None:
                jobs["risk_us"] = lambda: prewarm.off(risk_cache.series, False, ahead)
                jobs["risk_kr"] = lambda: prewarm.off(risk_cache.series, True, ahead)
            if fear_gauges is not None:
                jobs["fear"] = lambda: fear_gauges.get(ahead)
            jobs["board"] = lambda: _warm_board(prewarm.LIVE_AHEAD_SEC)
            # #643 the dashboard's bottom-zone card: the main indices and the favorites (1 h cache)
            jobs["bottoms"] = lambda: prewarm.off(
                lambda: bottoms.warm(["^KS11", "^NDX", "^SOX", *favorites.tickers(prewarm.FAVORITES_MAX)], ahead))
            return jobs

        def prewarm_then() -> dict[str, prewarm.Job]:
            # after the shared jobs: a playbook computed before the selling score is warm would be cached
            # without its #146 gate until the next pass
            return {"playbooks": lambda: _warm_playbooks(prewarm.LIVE_AHEAD_SEC)}

        async def _warm_board(ahead: float) -> int:
            # the quote board is the favorites: the first FAVORITES_MAX of them, while their market trades
            tickers = await asyncio.to_thread(favorites.tickers, prewarm.FAVORITES_MAX)
            # #643 off US hours the board waited for each new US ticker's first KIS trade (up to seconds):
            # ask the stream for them now, so the dashboard's first look finds them subscribed
            stream = getattr(prices, "stream", None)
            if stream is not None and kis_stream.us_session() in ("pre", "post", "overnight"):
                for t in tickers:
                    if kis_stream.market_of(t) == "US":
                        stream.want(t)
            return await estimator.warm(tickers, ahead)

        async def _warm_playbooks(ahead: float) -> int:
            # the default horizon's live playbooks of the first favorites, one at a time (prices merges asks)
            done, now = 0, datetime.now(UTC)
            for ticker in await asyncio.to_thread(favorites.tickers, prewarm.FAVORITES_MAX):
                if not trading_soon(ticker, now):           # closed: its levels and price stand still
                    continue
                try:
                    with calibration.use_table(public_calibration):
                        await playbook_live.current(prices, ticker, risk=risk, ahead=ahead)
                    done += 1
                except Exception as exc:  # noqa: BLE001 - that ticker only (its failure is cached briefly)
                    log.info("prewarm playbooks %s failed: %s", ticker,   # #861 why (a timeout's message is empty)
                             f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)
            return done

        def prewarm_tickers() -> list[str]:
            # the favorites' US tickers (positioning has nothing for the others; a few more are read for that)
            return [t for t in favorites.tickers(prewarm.FAVORITES_MAX * 4) if positioning.is_us_ticker(t)]

        app.state.prewarm_jobs, app.state.prewarm_tickers = prewarm_jobs, prewarm_tickers   # tests run them once
        app.state.prewarm_then = prewarm_then
        # the same switch as the news flow: off in tests and scripted runs
        # #623 the jobs: each loop through jobs.run_forever (a failed pass is logged, the next one runs),
        # started only when its switch is on - the others show "off" in GET /api/status
        feeds_on = settings.macro_interval_sec > 0 and not settings.fake_runs   # the news flow's switch
        started: list[asyncio.Task] = []

        # #688 and only the jobs of this process's role (DESK_ROLE, desk.roles; all = every one)
        def here(name: str) -> bool:
            return roles.runs(settings.role, name)

        def start(name: str, on: bool, step, retry, failed: str | None = None) -> None:
            if here(name) and on:
                started.append(jobs.spawn(name, jobs.run_forever(name, step, retry, failed)))

        start("tokens", tokens.minter and bool(token_owners or kis_feed), keep_tokens, TOKEN_TICK_SEC,
              "broker token renewal failed")
        start("listing", True, keep_listing_fresh, 3600, "listing refresh failed")
        start("futures_snapshot", interval_min(settings.futures_snapshot_min) > 0
              and getattr(futures, "rest", None) is not None,
              snapshot_futures, lambda: seconds_to_next(datetime.now(UTC), futures_every), "futures snapshot failed")
        start("nq_watch", feeds_on, watch_nq, lambda: nq_watch.seconds_to_next(datetime.now(UTC)), "NQ alarms failed")
        start("nq_weekly", feeds_on, weekly_alerts.step, nq_weekly_alerts.TICK_SEC, "NQ weekly alerts failed")
        start("nq_fast", feeds_on and estimator is not None, watch_nq_fast, nq_fast.TICK_SEC * 3,
              "NQ 1-minute alarms failed")
        start("line_signals", feeds_on, watch_lines, line_signals.TICK_SEC, "line signals failed")
        start("holding_watch", feeds_on and owners is None, watch_holdings, holding_watch.TICK_SEC, "holding watch failed")

        async def read_futures_close() -> float:
            # #713 each futures session's quote read 30 s before it ends: its close is what stays (held)
            await futures_quotes.read_close()
            return futures_quote.seconds_to_close_read(datetime.now(UTC))

        start("futures_close_quote", getattr(futures, "rest", None) is not None, read_futures_close,
              futures_quote.CLOSE_READ_SEC, "futures close quote failed")
        start("sector_movers", feeds_on, watch_sector_movers, sector_movers.SCAN_SEC, "sector movers failed")
        start("credit", feeds_on, refresh_credit, CREDIT_RETRY_SEC, "KOFIA credit refresh failed")
        if feeds_on:
            # #428: the pass saved before a restart answers until the first one here finishes
            # #308: the earnings saved before a restart show until the first pass answers
            # (a damaged file costs only that snapshot, never the start)
            for what, cache in (("macro news", macro_news.CACHE), ("earnings calendar", earnings.CACHE)):
                try:
                    if await asyncio.to_thread(cache.load):
                        log.info("%s: last saved snapshot loaded", what)
                except Exception:
                    log.exception("%s: saved snapshot not loaded", what)
        start("news", feeds_on, refresh_macro, settings.macro_interval_sec)
        start("calendar", feeds_on, refresh_calendar, CALENDAR_INTERVAL_SEC)
        if here("prewarm") and feeds_on:
            started.append(jobs.spawn("prewarm", prewarm.run(prewarm_jobs, prewarm_tickers, _positioning,
                                                             then=prewarm_then)))
        start("briefs", brief_desk.at is not None, write_briefs, morning_brief.RETRY_SEC)
        day_calls.bind(day_desk)          # desk.day_call.today_call (#571) reads this app's calls
        start("day_call", day_desk.candles is not None, make_day_calls, day_calls.TICK_SEC)
        start("krx", krx_desk is not None, collect_krx, krx.TICK_SEC, "krx collect failed")
        start("reports", settings.report_every_days > 0 and not settings.fake_runs, write_reports, reports.RETRY_SEC)
        start("funds", feeds_on, refresh_funds, lambda: funds.next_delay(), "13F refresh failed")
        # Scripted runs have made-up levels; scoring them against real bars means nothing.
        start("scoring", settings.score_interval_sec > 0 and not settings.fake_runs, score_plans,
              settings.score_interval_sec, "plan scoring failed")
        start("purge", settings.retention_days > 0, purge_old_runs, 24 * 3600, "retention sweep failed")
        # #688 the analyst claims the runs the api queued (desk.run_queue); all runs its own straight away
        queue = None
        if here("run_queue") and settings.role == "analyst":
            queue = run_queue.RunQueue(store, app.state.manager, run_queue.Holder(engine))

            async def serve_queue() -> float:
                wait = await queue.step()
                if queue.blocked:
                    jobs.mark_error("run_queue", "another analyst holds the run queue")
                return wait

            start("run_queue", True, serve_queue, run_queue.POLL_SEC, "run queue failed")
        # connects only once a page watches a KIS-streamable ticker (desk.broker.kis_stream)
        kis = jobs.spawn("kis_stream", kis_feed.run()) if here("kis_stream") and kis_feed is not None else None
        if kis:
            log.info("kis stream on")
        # #688 an event another process (the analyst) writes wakes this process's streams: PostgreSQL only
        if here("run_events") and run_events.shared(engine):
            def listening(up: bool, error: str | None) -> None:
                job = jobs.get("run_events")
                job.note = {"listening": up}
                if up:
                    job.ok(jobs.clock())
                else:
                    jobs.mark_error("run_events", error or "not listening")

            # judged like a loop (starting -> fresh | error), not "running" whatever the connection does
            jobs.get("run_events").looped = True
            started.append(jobs.spawn("run_events", run_events.follow(hub, engine, listening)))
        yield
        if queue is not None:
            await queue.close()             # #688 first: claim nothing more, put back a claim under way, unlock
        if all_lock is not None:
            await asyncio.to_thread(all_lock.release)
        if not protected:
            notify.shutdown()            # #580 the channels' queued sends go with the process
        if kis:
            kis.cancel()
            with suppress(asyncio.CancelledError):
                await kis                   # the socket closes before its http client does
            await kis_feed.aclose()
        for task in started:
            task.cancel()
        day_calls.bind(None)
        pictures.stop.set()              # a report being drawn takes no next picture
        for task in list(report_tasks):  # a manual report still being written
            task.cancel()
        for task in list(gap_tasks):   # #577 a gap read still running
            task.cancel()
        for task in list(brief_tasks):   # a manual brief still being written
            task.cancel()
        await estimator.aclose()
        await longshort.aclose()
        await futures.aclose()
        await market_bus.BUS.aclose()    # #690 its streams are told reset; its subscription and publishes stop
        await shared_cache.aclose()      # #689 after every task that reads or writes it has stopped
        lag_task.cancel()

    @asynccontextmanager
    async def issuer_lifespan(app: FastAPI):
        nonlocal active_issuer
        try:
            if not tokens.minter:
                async with lifespan(app):
                    yield
                return

            if not policy.current():
                raise PolicyChanged("process privacy mode changed")
            issuer = BrokerIssuerLock(engine)
            try:
                if not await asyncio.to_thread(issuer.acquire):
                    raise RuntimeError("broker issuer lock unavailable")
                active_issuer = issuer
                async with lifespan(app):
                    yield
            finally:
                active_issuer = None
                await asyncio.to_thread(issuer.release)
        finally:
            engine.dispose()

    docs = settings.enable_docs
    app = FastAPI(title="Trading Desk", lifespan=issuer_lifespan, docs_url="/docs" if docs else None,
                  redoc_url=None, openapi_url="/openapi.json" if docs else None)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def require_session(request: Request, call_next):
        def unavailable():
            return JSONResponse({"detail": "service temporarily unavailable"}, status_code=503, headers=_RUN_READ_CACHE)

        if not await _admit(policy.current):
            return unavailable()
        # Cross-site writes: the SameSite=Strict cookie already stays home; this also stops login CSRF.
        if request.method in UNSAFE_METHODS and not _same_origin(request):
            return JSONResponse({"detail": "cross-origin request refused"}, status_code=403)
        if request.url.path not in OPEN_PATHS:
            token = request.cookies.get(COOKIE)
            # #858 no cookie, no session to look up (auth.resolve answers None for it without the DB): no thread
            user = await _admit(auth.resolve, token, touch=is_activity(request)) if token else None
            if user is None:
                return JSONResponse({"detail": "login required"}, status_code=401)
            request.state.user = user
        try:
            with calibration.use_table(public_calibration):
                response = await call_next(request)
        except PolicyChanged:
            return unavailable()
        if not await _admit(policy.current):
            return unavailable()
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            # call_next has only seen the headers. Do not release buffered frames
            # from an isolated epoch, including legacy streams already open.
            frames = response.body_iterator

            async def current_frames():
                async for frame in frames:
                    if not await _admit(policy.current):
                        return
                    yield frame

            response.body_iterator = current_frames()
        return response

    def active_session(request: Request) -> bool:
        if not policy.current():
            return False
        user = auth.resolve(request.cookies.get(COOKIE), touch=False)
        return user is not None and user.id == request.state.user.id

    async def session_is_current(request: Request) -> bool:
        return await asyncio.to_thread(active_session, request)

    def owner_binding(request: Request) -> OwnerBinding | None:
        binding = owners.binding() if owners is not None else None
        return binding if binding is not None and binding.user_id == request.state.user.id else None

    def owns(request: Request) -> bool:
        if owners is not None:
            return owner_binding(request) is not None
        return request.state.user.id == auth.owner_id()

    def must_own_account(request: Request) -> None:
        if not owns(request):
            raise HTTPException(403, "only the desk's owner sees the account")

    @app.get("/api/health")
    async def health():                  # #859 on the loop, no anyio threadpool hop (desk.loop_lag watches the loop)
        return {"ok": True}

    def cooldowns() -> list[dict]:
        """#623 the waits after a provider said "too many": the model plan (runs · chat · briefs), GDELT,
        KRX's daily quota and a KRX service the key was refused for. ``until`` null when not waiting."""
        at = datetime.now(UTC)
        iso = lambda d: d.astimezone(UTC).isoformat(timespec="seconds") if d else None
        manager = getattr(app.state, "manager", None)
        wait = manager.rate_limit_wait() if manager is not None else 0.0
        out = [{"name": "chatgpt", "label": "ChatGPT 플랜(분석 · 채팅 · 아침 시황)",
                "until": iso(at + timedelta(seconds=wait)) if wait > 0 else None, "left_sec": max(0, round(wait))}]
        resume = macro_news.CACHE.gdelt_resume_at
        left = (resume - at).total_seconds() if resume else 0.0
        out.append({"name": "gdelt", "label": "GDELT(뉴스 — 그동안 구글 뉴스)",
                    "until": iso(resume) if left > 0 else None, "left_sec": max(0, round(left))})
        if krx_desk is not None:
            for name, label, until in krx_desk.cooldowns(at):
                out.append({"name": name, "label": label, "until": iso(until),
                            "left_sec": max(0, round((until - at).total_seconds()))})
        return out

    @app.get("/api/status")
    def status():
        """#623 the background jobs (state fresh · stale · error · starting · stopped · off, last success,
        last error as a type name, next pass) and the providers' rate-limit waits. Session required."""
        # now to the millisecond: the browser's latency probe (#690 marketLatency.ts) sets its clock skew by it
        return {"now": datetime.now(UTC).isoformat(timespec="milliseconds"), "role": settings.role,
                "jobs": jobs.status(), "cooldowns": cooldowns(),
                # #689 this process's shared-cache hits and its own calls to the brokers (Toss · KIS REST)
                "shared_cache": shared_cache.stats(), "outbound": outbound.snapshot(),
                "market_stream": market_bus.BUS.stats(),
                "loop_lag": lag.stats(),             # #859 the event loop's lateness and what held it
                "prewarm": prewarm.LAST or None}   # #864 the last pass: when, how long, each job

    # #536 per-route request times (desk.req_timing), read in production without parsing logs
    timings = req_timing.Timings()
    app.state.timings = timings

    @app.get("/api/perf/timings")
    def perf_timings(reset: bool = False):
        """#536: since the start (or the last ``reset=1``): per route the count, p50 · p95 · max ms, slowest p95 first."""
        out = {"since": datetime.fromtimestamp(timings.since, UTC).isoformat(timespec="seconds"),
               "slow_ms": req_timing.SLOW_MS, "routes": timings.stats()}
        if reset:
            timings.reset()
        return out

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
                log.warning("login otp failed ip=%s", log_setup.mask_ip(ip))
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
            log.warning("login failed ip=%s", log_setup.mask_ip(ip))   # fail2ban reads the ingress log (#712)
            return JSONResponse({"detail": "invalid username or password"}, status_code=401)
        if auth.otp_enabled(user.id):
            if not otp_box.available:                       # fail closed: never skip the second factor
                log.error("user #%s has OTP on but the OTP key is unusable: %s", user.id,
                          otp_box.error or "DESK_OTP_KEY is missing")
                return JSONResponse({"detail": "OTP is not available on the server"}, status_code=503)
            # Spread over many IPs, code guessing is still capped per account. Reaching the cap means
            # someone holds the password: say so loudly.
            wait = otp_fails.blocked_for(f"user:{user.id}")
            if wait:
                log.error("otp failures for user #%s over the limit; the password may be known", user.id)
                return JSONResponse({"detail": "too many failed codes", "retry_after": wait},
                                    status_code=429, headers={"Retry-After": str(wait)})
            # No session yet, and the IP's attempt count stays: the code is still to be guessed.
            return JSONResponse({"otp_required": True, "challenge": challenges.issue(user.id)})
        throttle.succeeded(ip)
        return _signed_in(user, ip, request)

    def _signed_in(user: User, ip: str, request: Request) -> Response:
        auth.close_session(request.cookies.get(COOKIE))   # never keep a token issued before this login
        token = auth.open_session(user, ip, request.headers.get("user-agent"))
        log.info("login ok user=#%s ip=%s", user.id, log_setup.mask_ip(ip))
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
            log.warning("otp change: wrong password ip=%s", log_setup.mask_ip(_client_ip(request)))
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
            log.warning("otp enable: code refused ip=%s", log_setup.mask_ip(_client_ip(request)))
            return JSONResponse({"detail": "code does not match, or setup expired (10 min); start setup again"},
                                status_code=403)
        log.info("otp enabled user=#%s", user.id)
        return {"recovery_codes": codes}

    @app.post("/api/auth/otp/disable", status_code=204)
    def otp_disable(body: DisableOtpIn, request: Request):
        user = request.state.user
        refused = _recheck_password(request, body.password)
        if refused:
            return refused
        try:
            if not auth.otp_verify(user.id, body.code, otp_box):
                log.warning("otp disable: code refused ip=%s", log_setup.mask_ip(_client_ip(request)))
                return JSONResponse({"detail": "invalid code"}, status_code=403)
        except otp.OtpUnavailable:
            return JSONResponse({"detail": "OTP is not available on the server"}, status_code=503)
        auth.otp_disable(user.id, keep_token=request.cookies.get(COOKIE))
        log.info("otp disabled user=#%s", user.id)
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
            # #696: no style to choose (the instrument's class says the risk); kept empty for older pages
            "profiles": [],
            "default_profile": None,
            "horizons": [h.to_dict() for h in horizons.HORIZONS.values()],
            "default_horizon": horizons.DEFAULT_HORIZON,
        }

    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunIn, request: Request, response: Response):
        listed = None if body.name else universe.lookup(body.ticker)
        approved = approved_account(request) if body.use_portfolio else None
        binding = approved.binding if approved is not None else None
        use_portfolio = body.use_portfolio and (
            binding is not None if owners is not None else owns(request)
        )
        run_request = RunRequest(
            ticker=body.ticker,
            name=body.name or (listed or {}).get("name"),
            horizon=body.horizon or horizons.DEFAULT_HORIZON,   # stored explicitly, never left implicit
            trade_date=(body.trade_date or date.today()).isoformat(),
            analysts=body.analysts,
            use_portfolio=use_portfolio,
            output_language=body.output_language,
            max_debate_rounds=body.max_debate_rounds,
            max_risk_rounds=body.max_risk_rounds,
        )
        try:
            run_id = request.app.state.manager.submit(
                run_request, authenticated_submitter_id=request.state.user.id,
                owner_generation=binding.generation if binding is not None else None,
            )
        except AccountBindingChanged:
            raise HTTPException(409, "account authorization changed", headers=_RUN_READ_CACHE) from None
        if run_reader is not None:
            response.headers.update(_RUN_READ_CACHE)
            return guarded_detail(request, run_id)
        return with_queue([store.get_run(run_id)])[0]

    def with_queue(items: list[dict]) -> list[dict]:
        """#688 a queued run says where it stands: ``queue: {ahead, running, analyst}`` - how many queued runs an
        analyst claims before it, how many run now, and (api role on PostgreSQL) whether an analyst holds the
        queue ("up" | "down"; None under all or on SQLite). Other runs carry no ``queue``."""
        if not any(r["status"] == "queued" for r in items):
            return items
        order, running = store.queue_view()
        place = {run_id: i for i, run_id in enumerate(order)}
        analyst = None
        if settings.role == "api":
            up = run_queue.held(engine)
            analyst = None if up is None else ("up" if up else "down")
        return [{**r, "queue": {"ahead": place.get(r["id"], 0), "running": running, "analyst": analyst}}
                if r["status"] == "queued" else r for r in items]

    @app.get("/api/runs")
    def list_runs(request: Request, response: Response, page: int = 1, size: int = 20):
        # Clamp both: a huge page would overflow the OFFSET bigint and surface as a 500.
        page, size = min(max(page, 1), 100_000), min(max(size, 1), 100)
        if run_reader is not None:
            items, total = run_reader.page(request.state.user.id, limit=size, offset=(page - 1) * size)
            response.headers.update(_RUN_READ_CACHE)
            return {"items": items, "total": total, "page": page, "size": size}
        scope = account_scope(request)
        return {"items": with_queue(store.list_runs(size, (page - 1) * size, scope=scope)),
                "total": store.count_runs(scope=scope), "page": page, "size": size}

    @app.delete("/api/runs/{run_id}", status_code=204)
    async def delete_run(run_id: str, request: Request):
        # async on purpose: runs on the loop thread, so it cannot land between RunManager's
        # mark_done() and its last plan/done events (no await between them), and Hub is touched
        # from the loop only.
        scope = run_reader.change_scope(request.state.user.id) if run_reader is not None else account_scope(request)
        cache = _RUN_READ_CACHE if run_reader is not None else None
        result = store.delete_run(run_id, scope=scope)
        if result == "missing":
            raise HTTPException(404, "run not found", headers=cache)
        if result == "active":
            raise HTTPException(409, "run is still in progress", headers=cache)
        hub.notify(run_id)   # wake any open stream so it sees the run is gone and closes
        return Response(status_code=204, headers=cache)

    def guarded_detail(request: Request, run_id: str) -> dict:
        assert run_reader is not None
        visible = run_reader.detail(request.state.user.id, run_id)
        if visible is None:
            raise HTTPException(404, "run not found", headers=_RUN_READ_CACHE)
        return visible

    def account_scope(request: Request):
        """Legacy #775: a run that read the broker account (use_portfolio) is the owner's alone - listed,
        counted and deleted by no other account. None for the owner."""
        return None if owns(request) else not_(account_run(run_rows.c.options))

    def legacy_run(request: Request, run_id: str) -> dict | None:
        """Legacy #775: the stored run, or None when it is missing or another account's view of an account
        run - one 404 for both."""
        run = store.get_run(run_id)
        return run if run is not None and (not reads_account(run) or owns(request)) else None

    def full_run(request: Request, run_id: str) -> dict | None:
        if run_reader is not None:
            return run_reader.full_run(request.state.user.id, run_id)
        return legacy_run(request, run_id)

    async def verify_full_run(request: Request, run_id: str) -> None:
        if run_reader is not None:
            if not await session_is_current(request):
                raise HTTPException(401, "login required", headers=_RUN_READ_CACHE)
            if not run_reader.has_full_read(request.state.user.id, run_id):
                raise HTTPException(404, "run not found", headers=_RUN_READ_CACHE)

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, request: Request, response: Response):
        if run_reader is not None:
            response.headers.update(_RUN_READ_CACHE)
            return guarded_detail(request, run_id)
        run = legacy_run(request, run_id)
        if not run:
            raise HTTPException(404, "run not found")
        # #437: the look ahead frozen when the run ended (an ``outlook`` event), None before / without one
        return {**with_queue([run])[0], "outlook": store.last_event_data(run_id, "outlook")}

    explain_locks: dict[str, asyncio.Lock] = {}

    @app.get("/api/runs/{run_id}/explain")
    async def run_explain(run_id: str, request: Request, response: Response, refresh: bool = False):
        """#493: the analysis for a beginner (desk.explain) - written once, kept as the run's ``explain``
        event and reused; ``refresh=1`` writes it again. The model words it only while a run slot is
        free; otherwise (or when it fails) the desk's own wording comes back, not kept, so a later call
        tries again."""
        run = full_run(request, run_id)
        cache = _RUN_READ_CACHE if run_reader is not None else None
        if not run:
            raise HTTPException(404, "run not found", headers=cache)
        if cache is not None:
            response.headers.update(cache)
        if run.get("status") != "done" or not run.get("plan"):
            raise HTTPException(409, "the analysis has no plan yet", headers=cache)
        lock = explain_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            await verify_full_run(request, run_id)
            viewer_id = request.state.user.id
            can_cache = run_reader is None or run_reader.may_change(viewer_id, run_id)
            if refresh and not can_cache:
                raise HTTPException(403, "cannot change this analysis", headers=_RUN_READ_CACHE)
            kept = None if refresh else store.last_event_data(run_id, "explain")
            if kept:
                return kept
            fear_items = None
            if fear_gauges is not None:
                try:
                    fear_items = (await asyncio.wait_for(fear_gauges.get(), 10))["items"]
                except Exception:  # noqa: BLE001 - the explanation goes without the market mood
                    fear_items = None
            if settings.fake_runs or not can_cache:
                await verify_full_run(request, run_id)
                out = await asyncio.to_thread(explain.build, run, fear_items, None)
            else:
                async def work():
                    async with app.state.manager.spare_slot() as free:
                        await verify_full_run(request, run_id)
                        return free, await asyncio.to_thread(explain.build, run, fear_items,
                                                             projection.llm_note if free else None)
                # shielded: a client that goes away mid-call must not free the slot while the model call runs
                free, out = await asyncio.shield(asyncio.create_task(work()))
                await verify_full_run(request, run_id)
                if not free:
                    return {**out, "pending": True}
            await verify_full_run(request, run_id)
            if out.get("fallback") or not can_cache:
                return out
            event = {"type": "explain", "agent": "explain", "content": None, "data": out}
            if run_reader is None:
                await asyncio.to_thread(store.append_event, run_id, event)
            else:
                stored = await asyncio.to_thread(store.append_scoped_event, run_id, event,
                                                 run_reader.change_scope(viewer_id))
                await verify_full_run(request, run_id)
                if refresh and not stored:
                    raise HTTPException(403, "cannot change this analysis", headers=_RUN_READ_CACHE)
            return out

    @app.get("/api/reports")
    def list_reports(limit: int = 20, before: str | None = None):
        """#495 the beginner reports, newest first (``next_before`` pages back); ``generating`` while one is written."""
        return report_desk.list(limit, before)

    @app.get("/api/reports/{report_id}")
    def get_report(report_id: str):
        got = report_desk.get(report_id)
        if got is None:
            raise HTTPException(404, "report not found")
        return got

    @app.get("/api/reports/{report_id}/images/{n}")
    def report_image(report_id: str, n: int):
        """#497 an explainer picture of a report (``sections[].image.url``)."""
        path = report_images.image_path(report_id, n, pictures.root)
        if path is None or not path.is_file():
            raise HTTPException(404, "image not found")
        return Response(path.read_bytes(), media_type="image/png", headers={"Cache-Control": "private, max-age=600"})

    @app.post("/api/reports", status_code=202)
    async def new_report():
        """#495 write one now, in the background (one at a time): poll ``GET /api/reports`` for ``generating``."""
        if report_desk.generating:
            raise HTTPException(409, "이미 만드는 중이에요")
        last = await asyncio.to_thread(report_desk.last_at)
        if last is not None and (datetime.now(UTC) - last).total_seconds() < reports.MANUAL_GAP_SEC:
            raise HTTPException(429, "방금 만든 리포트가 있어요. 10분 뒤에 다시 만들 수 있어요")

        async def work():
            try:
                await make_report(auto=False, taken=True)
            except Exception:  # logged; the page sees generating go false
                log.exception("manual report failed")

        report_desk.generating = True     # taken now: a second click right away gets 409
        task = asyncio.create_task(work())
        report_tasks.add(task)
        task.add_done_callback(report_tasks.discard)
        return {"generating": True}

    # #571 the morning market brief
    @app.get("/api/briefs/morning")
    def morning_brief_of(date: str | None = None):
        """The newest brief (of ``date`` YYYY-MM-DD, Korean day, when given); ``generating`` while one is written."""
        day = None
        if date is not None:
            try:
                day = datetime.strptime(date, "%Y-%m-%d").date()
            except ValueError:
                raise HTTPException(422, "date must be YYYY-MM-DD") from None
        got = brief_desk.latest(day)
        if got is None:
            raise HTTPException(404, "아직 아침 시황이 없어요")
        due = brief_desk.next_due()
        return {**got, "generating": brief_desk.generating, "next_due": morning_brief._iso(due)}

    @app.get("/api/briefs/morning/list")
    def morning_brief_list(limit: int = 20):
        """The briefs newest first (id, date, title, source, trigger)."""
        return {"items": brief_desk.list(limit), "generating": brief_desk.generating}

    @app.post("/api/briefs/morning/run", status_code=202)
    async def morning_brief_run():
        """Write one now in the background (one at a time, MANUAL_PER_DAY a Korean day - the plan's quota):
        poll ``GET /api/briefs/morning`` for ``generating``."""
        if brief_desk.generating:
            raise HTTPException(409, "이미 만드는 중이에요")
        brief_desk.generating = True      # taken now (no await since the check): a second click gets 409
        today = datetime.now(market_calendar.KST).date()
        try:
            used = await asyncio.to_thread(brief_desk.count, today, "manual")
        except Exception:
            brief_desk.generating = False
            raise
        if used >= morning_brief.MANUAL_PER_DAY:
            brief_desk.generating = False
            raise HTTPException(429, f"오늘은 직접 만들기를 {morning_brief.MANUAL_PER_DAY}번 다 썼어요. 내일 다시 할 수 있어요")

        async def work():
            try:
                await make_brief("manual", taken=True)
            except Exception:  # logged; the page sees generating go false
                log.exception("manual morning brief failed")

        task = asyncio.create_task(work())
        brief_tasks.add(task)
        task.add_done_callback(brief_tasks.discard)
        return {"generating": True}

    @app.get("/api/runs/{run_id}/events")
    def get_events(run_id: str, request: Request, response: Response, after: int = 0):
        if run_reader is not None:
            batch = run_reader.event_batch(request.state.user.id, run_id, after=after)
            if batch is None:
                raise HTTPException(404, "run not found", headers=_RUN_READ_CACHE)
            response.headers.update(_RUN_READ_CACHE)
            return batch.items
        if not legacy_run(request, run_id):
            raise HTTPException(404, "run not found")
        return store.events_after(run_id, after)

    @app.get("/api/runs/{run_id}/stream")
    async def stream(run_id: str, request: Request, after: int = 0):
        viewer_id = request.state.user.id
        if run_reader is not None:
            if not await asyncio.to_thread(run_reader.has_full_read, viewer_id, run_id):
                raise HTTPException(404, "run not found", headers=_RUN_READ_CACHE)
        elif not legacy_run(request, run_id):
            raise HTTPException(404, "run not found")
        last_id = request.headers.get("last-event-id")
        start = int(last_id) if last_id and last_id.isdigit() else after

        token = request.cookies.get(COOKIE)

        async def frames():
            seq = start
            terminal_wait_expired = False
            while not await request.is_disconnected():
                # Re-check every round: logout, revoke or expiry ends an open stream too.
                user = await asyncio.to_thread(auth.resolve, token, touch=False)
                if (user is None or (run_reader is not None and user.id != viewer_id)
                        or not await asyncio.to_thread(policy.current)):
                    return
                waiter = hub.listen(run_id)          # before reading, so nothing slips between
                batch = None
                if run_reader is not None:
                    batch = await asyncio.to_thread(run_reader.event_batch, viewer_id, run_id, after=seq, limit=100)
                    if batch is None:
                        return
                    events = batch.items
                else:
                    events = store.events_after(run_id, seq)
                for event in events:
                    seq = event["seq"]
                    yield f"id: {seq}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                if events:
                    terminal_wait_expired = False
                    continue
                status = batch.status if batch is not None else store.run_status(run_id)
                if status is None:
                    return
                if status in TERMINAL:
                    terminal_seq = (batch.terminal_seq if batch is not None
                                    else await asyncio.to_thread(last_done_seq, engine, run_id))
                    if terminal_wait_expired or (terminal_seq is not None and seq >= terminal_seq):
                        return
                try:
                    await asyncio.wait_for(waiter.wait(), HEARTBEAT_SEC)
                except TimeoutError:
                    # A terminal row can commit before its final events. Re-read
                    # once after the wait even if no wake-up reached this process.
                    if status in TERMINAL:
                        terminal_wait_expired = True
                    else:
                        yield ": ping\n\n"

        cache = _RUN_READ_CACHE if run_reader is not None else {"Cache-Control": "no-cache"}
        return StreamingResponse(frames(), media_type="text/event-stream", headers={**cache, "X-Accel-Buffering": "no"})

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
    async def trade_review(request: Request, days: int = 90):
        """Executions of the last ``days`` (max 180, KST dates) next to the plan of the time. Read-only.
        #775 the owner's only."""
        must_own_account(request)
        # Historical broker fills carry no verified desk owner generation.
        if owners is not None:
            raise HTTPException(403, "historical account data is unavailable", headers=_RUN_READ_CACHE)
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

    @app.get("/api/market/credit")
    async def market_credit(days: int = 90):
        """#155: Korean margin loans (신용융자), investor deposits (투자자예탁금), their ratio (빚투 비율)
        with its 1-year percentile, and forced sales (KOFIA, refreshed in the background)."""
        return await credit.CACHE.shared_view(min(max(days, 7), 400))

    def _day_market(market: str) -> str:
        if market not in day_calls.dc.MARKETS:
            raise HTTPException(422, "market must be kr or us")
        return market

    @app.get("/api/day-call")
    def day_call_today(market: str = "kr"):
        """#572: the market's latest session calls (prev_close and open) with their records."""
        return day_desk.view(_day_market(market))

    @app.get("/api/day-call/history")
    def day_call_history(market: str = "kr", days: int = day_calls.RECORD_DAYS, call: str | None = None):
        if call not in (None, "prev_close", "open", "to_close"):
            raise HTTPException(422, "call must be prev_close, open or to_close")
        return day_desk.history(_day_market(market), min(max(days, 1), 365), call=call)

    @app.get("/api/day-call/stats")
    def day_call_stats(market: str = "kr", days: int = day_calls.RECORD_DAYS):
        return day_desk.stats(_day_market(market), min(max(days, 1), 365))

    async def sectors_view(market: str, wait: float) -> dict:
        """#620 the market's sector call (desk.day_sectors) from the cache, else built in the background -
        waited for ``wait`` seconds (0: not at all), then answered ``loading``."""
        now = datetime.now(UTC)
        if (v := sector_cache.cached(market, now)) is not None:
            return v
        if sector_candles is None:
            raise HTTPException(503, "섹터 판정은 시세 연결이 필요합니다")
        task = sector_tasks.get(market)
        if task is None or task.done():
            task = sector_tasks[market] = asyncio.create_task(
                asyncio.to_thread(sector_cache.read, market, sector_candles, now))
        loading = {"market": market, "ready": False, "loading": True,
                   "note": "섹터 ETF 일봉을 받는 중입니다 — 잠시 뒤 다시 보세요."}
        if wait <= 0:
            return loading            # #705 the board: the build runs on, the sectors path then waits for it
        try:
            return await asyncio.wait_for(asyncio.shield(task), wait)
        except TimeoutError:
            return loading
        except Exception as exc:  # noqa: BLE001 - a source down: said, not raised
            log.warning("day sectors %s failed: %s", market, type(exc).__name__)
            raise HTTPException(503, "섹터 판정을 만들지 못했습니다") from None

    @app.get("/api/day-call/sectors")
    async def day_call_sectors(market: str = "kr"):
        """#620 the market's sectors most and least likely to beat its benchmark today (desk.day_sectors):
        probability, reasons, the last 90 sessions' record against following yesterday's strong
        sectors, and today's score after the close. Built from the sector ETFs' daily bars in the
        background - a first ask waits SECTORS_WAIT_SEC, then answers ``loading``."""
        if market not in day_sectors.MARKETS:
            raise HTTPException(422, "market must be kr or us")
        return await sectors_view(market, SECTORS_WAIT_SEC)

    @app.get("/api/day-call/board")
    async def day_call_board(sectors: str | None = None):
        """#705 the dashboard's 오늘 방향 card in one call (it asked three): every market's calls as
        /api/day-call answers them, and with ``sectors=<market>`` that market's sector call as
        /api/day-call/sectors does, but only from the cache: not built yet, it is ``loading`` at once (the build
        starts; the card then asks the sectors path, which waits for it) - the calls, the dashboard's largest text,
        never wait for cold sector bars. A part that fails is ``{"error": status, "detail": ...}``, the rest still
        come. The calls run in the same worker pool as /api/day-call (not the loop's executor prewarm fills)."""
        if sectors is not None and sectors not in day_sectors.MARKETS:
            raise HTTPException(422, "sectors must be kr or us")

        async def part(work):
            try:
                return await work
            except HTTPException as exc:
                return {"error": exc.status_code, "detail": exc.detail}
            except Exception as exc:  # one part failing leaves the others (logged with its traceback)
                log.warning("day-call board part failed: %s", type(exc).__name__, exc_info=True)
                return {"error": 503, "detail": "오늘 방향을 읽지 못했습니다"}      # the type name stays in the log

        markets = list(day_calls.dc.MARKETS)
        got = await asyncio.gather(*(part(run_in_threadpool(day_desk.view, m)) for m in markets),
                                   *([part(sectors_view(sectors, 0))] if sectors else []))
        out: dict = {"calls": dict(zip(markets, got[:len(markets)], strict=True))}
        if sectors:
            out["sectors"] = got[-1]
        return out

    @app.get("/api/market/fear")
    async def market_fear():
        """#462: the fear gauges (desk.fear); a failed source is that item's ``error``, never a 5xx."""
        if fear_gauges is None:
            return {"items": [], "error": "fake runs: no outside requests"}
        return await fear_gauges.get()

    @app.get("/api/crypto/halving")
    async def crypto_halving():
        """#514: the bitcoin halving cycles side by side and where this one stands (desk.halving; a day's cache)."""
        got = await asyncio.to_thread(halving_cache.get)
        if got is None:
            raise HTTPException(503, "비트코인 가격을 받지 못했습니다. 잠시 뒤 다시 시도해 주세요")
        return got

    premarket_cache: dict[str, tuple[float, list[dict], bool]] = {}

    @app.get("/api/market/flow")
    async def market_flow(market: str = "KOSPI", interval: str = "1d", count: int = 20):
        """KOSPI / KOSDAQ trading value by investor class, newest first (desk.market_flow). Read-only;
        a broker failure comes back as ``error`` with no records, not as a 5xx. #677 the 1d answer carries
        ``premarket``: today's spot flow before the bell (desk.flow_intraday.premarket) once there is one, and
        ``program``: today's newest KRX program trading net buy (desk.program_trade, from the snapshots), and
        ``expected``: the pre-open hour's expected index / NXT large caps (desk.broker.kis_stream's standing
        subscriptions in this api process; None without a KIS stream or before any came today)."""
        try:
            got = await flow.get(market.upper(), interval, count)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if interval != "1d":
            return got
        local = datetime.now(UTC).astimezone(futures_kst)
        day = local.date().isoformat()
        hit = premarket_cache.get(day)
        # one read a half minute for all markets; one read after 09:01 is the day's (nothing before 09:00 changes)
        if not hit or not (hit[2] or time.monotonic() - hit[0] < PREMARKET_CACHE_SEC):
            final = local.time() >= PREMARKET_FINAL
            hit = (time.monotonic(), await asyncio.to_thread(futures_history.premarket, local.date()), final)
            premarket_cache.clear()
            premarket_cache[day] = hit
        return {**got, "premarket": flow_intraday.premarket(hit[1], market.lower()),
                "program": await program_latest.read(local.date(), market.lower()),
                "expected": kis_feed.expected(market.lower()) if kis_feed is not None else None}

    @app.get("/api/market/flow-board/kr")
    async def korean_flow_board(request: Request, market: Literal["kospi", "kosdaq"] = "kospi",
                                days: Annotated[int, Query(ge=1, le=22)] = 22):
        """Load the closed-session KRX board and the current account's favorites."""
        starred = await asyncio.to_thread(favorites.list, request.state.user.id)
        result = await kr_flow_data.read(engine, market, days, starred, datetime.now(UTC))
        return JSONResponse(result, headers={"Cache-Control": "private, no-store"})

    @app.get("/api/market/futures")
    async def market_futures():
        """KOSPI200 front-month futures: price, open interest, basis, net contracts by investor
        class, and the foreign spot/futures split (desk.futures_flow). A KIS failure or missing
        keys come back as ``error`` / ``supported: false``, not as a 5xx."""
        return await futures.get()

    @app.get("/api/market/futures/quote")
    async def market_futures_quote(session: str | None = None):
        """#443 KOSPI200 front month in the day (08:45~15:45) or night (18:00~06:00) session - the one
        trading now, or the last one, unless ``session`` asks for one. Change and rate are KIS'
        against the previous settlement (day) or the day's close (night). #713 without ``session`` it
        also carries ``sessions`` (day and night, each open or closed with its last quote) and ``current``."""
        if session is not None and session not in futures_quote.SESSIONS:
            raise HTTPException(422, "session must be day or night")
        try:
            return await (futures_quotes.quote(session) if session else futures_quotes.both())
        except Exception as exc:  # noqa: BLE001 - KIS down with nothing cached: said, not a 5xx
            return {"supported": True, "error": f"선물 시세를 받지 못했습니다 — {futures_quote._short(exc)}",
                    "stale": False, "session": session}

    @app.get("/api/market/futures/candles")
    async def market_futures_candles(session: str | None = None, interval: str = "1m"):
        """#443 the session's minute bars (1m / 5m / 15m from the session's start), oldest first."""
        if session is not None and session not in futures_quote.SESSIONS:
            raise HTTPException(422, "session must be day or night")
        if interval not in futures_quote.INTERVALS:
            raise HTTPException(422, f"interval must be one of {', '.join(futures_quote.INTERVALS)}")
        try:
            return await futures_quotes.candles(session, interval)
        except Exception as exc:  # noqa: BLE001
            return {"supported": True, "error": f"선물 분봉을 받지 못했습니다 — {futures_quote._short(exc)}",
                    "stale": False, "session": session, "bars": []}

    @app.get("/api/market/futures/daily")
    async def market_futures_daily(days: int = 20):
        """#254: the futures investor flows per trading day (the day's last snapshot), newest first.
        History starts when the server began taking snapshots; ``supported: false`` without KIS."""
        rows = await asyncio.to_thread(futures_history.daily, days)
        return {"supported": getattr(futures, "rest", None) is not None,
                "interval_min": interval_min(settings.futures_snapshot_min),
                "days": rows}

    @app.get("/api/market/futures/oi-history")
    async def market_futures_oi_history(days: int = 250):
        """#603: KOSPI200 futures open interest (front month and every quarterly contract), basis and the
        monthly options' put/call open interest · ATM IV per business day from KRX, newest first.
        ``reason`` when there is none yet: no_key (DESK_KRX_API_KEY unset) or no_history."""
        if not 1 <= days <= OI_MAX_DAYS:
            raise HTTPException(422, f"days 는 1 ~ {OI_MAX_DAYS}")
        items = await asyncio.to_thread(futures_oi.history, days)
        reason = None if items else ("no_key" if not settings.krx_api_key else "no_history")
        return {"items": items, "source": "krx", "last_date": items[0]["date"] if items else None,
                "reason": reason}

    @app.get("/api/market/flow/intraday")
    async def market_flow_intraday(date: str | None = None, bucket: int = 5):
        """#359: a trading day's spot (KOSPI / KOSDAQ) and futures investor flows as ``bucket``-minute
        bars, with the foreign buy/sell turns (desk.flow_intraday). Default today, KST."""
        try:
            day = (datetime.fromisoformat(date).date() if date
                   else datetime.now(UTC).astimezone(futures_kst).date())
        except ValueError:
            raise HTTPException(422, "date must be YYYY-MM-DD") from None
        points = await asyncio.to_thread(futures_history.intraday, day)
        # #743 the program turns on the alerts' band (once a day per process)
        band = (await asyncio.to_thread(program_flow.bands_for, futures_history, day))["band"]
        try:
            body = flow_intraday.view(points, bucket, band)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"supported": getattr(futures, "rest", None) is not None, "date": day.isoformat(),
                "market_open": futures_open(datetime.now(UTC)), **body}

    @app.get("/api/market/futures/intraday")
    async def market_futures_intraday(date: str | None = None):
        """#254: a trading day's snapshots (default today, KST), oldest first."""
        try:
            day = (datetime.fromisoformat(date).date() if date
                   else datetime.now(UTC).astimezone(futures_kst).date())
        except ValueError:
            raise HTTPException(422, "date must be YYYY-MM-DD") from None
        points = await asyncio.to_thread(futures_history.intraday, day)
        return {"supported": getattr(futures, "rest", None) is not None, "date": day.isoformat(),
                "interval_min": interval_min(settings.futures_snapshot_min), "market_open": futures_open(datetime.now(UTC)),
                "points": points}

    @app.get("/api/bottom-zone")
    async def bottom_zone_of(ticker: str):
        """#240: the long-term bottom zone ("인생 타점") of a stock or an index - score, stage,
        evidence, the staged buys over months and the monthly close that voids the plan.
        ``supported: false`` with too little weekly history. #310: the steps are cut at the price
        now (``price_now`` · ``price_source`` · ``price_at``; each step ``from_now_pct`` · ``status``)."""
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        price, source, at = await _price_now(ticker)
        try:
            return await asyncio.to_thread(bottoms.one, ticker, price, source, at)
        except Exception as exc:  # noqa: BLE001 - Yahoo down
            log.warning("bottom zone for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no bottom zone for {ticker}") from None

    @app.get("/api/market/weekly")
    async def market_weekly(symbol: str | None = None):
        """#202: the weekly market map - each main index's weekly trend lines, bands, ranges, this
        week's candle and the lines' record, in plain Korean. One index failing answers its
        ``error``; the others still come. ``symbol`` asks for one map: a main index or any ticker (#229)."""
        if symbol is None:
            return await asyncio.to_thread(weekly.all)
        try:
            symbol = clean_ticker(symbol)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"maps": [await asyncio.to_thread(weekly.one, symbol)]}

    @app.get("/api/market/weekly/{symbol}/history")
    async def market_weekly_history(symbol: str, weeks: int = 8):
        """#202: the map as it read at the end of each of the last ``weeks`` weeks (bars up to each)."""
        try:
            symbol = clean_ticker(symbol)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"symbol": symbol, "weeks": await asyncio.to_thread(weekly.history, symbol, min(max(weeks, 1), 52))}

    bound_book: tuple[str, Portfolio] | None = None

    def account_book(approved: OwnerResources | None) -> Portfolio:
        nonlocal bound_book
        if approved is None:
            return book
        if bound_book is None or bound_book[0] != approved.binding.generation:
            bound_book = (approved.binding.generation, Portfolio(approved.broker, prices))
        return bound_book[1]

    def approved_account(request: Request) -> OwnerResources | None:
        approved = current_resources()
        if approved is not None and approved.broker is not None and approved.binding == owner_binding(request):
            return approved
        return None

    @app.get("/api/portfolio")
    async def portfolio(request: Request, response: Response):
        """Holdings at live prices in KRW and USD (desk.portfolio). Failures land in ``error``. #775 the
        owner's only."""
        approved = approved_account(request)
        if (owners is not None and approved is None) or (owners is None and not owns(request)):
            raise HTTPException(403, "only the desk's owner sees the account", headers=_RUN_READ_CACHE)
        result = await account_book(approved).snapshot()
        if owners is not None:
            if not await session_is_current(request):
                raise HTTPException(401, "login required", headers=_RUN_READ_CACHE)
            if approved_account(request) is not approved:
                raise HTTPException(403, "only the desk's owner sees the account", headers=_RUN_READ_CACHE)
            response.headers.update(_RUN_READ_CACHE)
        return result

    @app.get("/api/portfolio/stream")
    async def portfolio_stream(request: Request):
        """The same snapshot every DESK_PORTFOLIO_STREAM_SEC seconds, as SSE. #775 the owner's only."""
        approved = approved_account(request)
        if (owners is not None and approved is None) or (owners is None and not owns(request)):
            raise HTTPException(403, "only the desk's owner sees the account", headers=_RUN_READ_CACHE)
        interval = min(max(settings.portfolio_stream_sec, 2.0), 60.0)

        async def still_allowed() -> bool:
            return await session_is_current(request) and (
                approved_account(request) is approved if owners is not None else owns(request)
            )

        async def frames():
            yield f"retry: {int(interval * 1000)}\n\n"
            while not await request.is_disconnected():
                # Re-check every round: logout, revoke or expiry ends an open stream too.
                if not await still_allowed():
                    return
                snap = await account_book(approved).snapshot()
                if not await still_allowed():
                    return
                yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
                # #762 a round up to 60 s still re-checks the session within STREAM_RECHECK_SEC
                waited = 0.0
                while (step := min(STREAM_RECHECK_SEC, interval - waited)) > 0:
                    await asyncio.sleep(step)
                    waited += step
                    if waited < interval and (await request.is_disconnected() or not await still_allowed()):
                        return

        cache = _RUN_READ_CACHE if owners is not None else {"Cache-Control": "no-cache"}
        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={**cache, "X-Accel-Buffering": "no"})

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
                        if await asyncio.to_thread(auth.resolve, token, touch=False) is None:
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
                            if await asyncio.to_thread(auth.resolve, token, touch=False) is None:
                                return
                    else:
                        await asyncio.sleep(pause)
            finally:
                live_streams[key] -= 1
                if not live_streams[key]:
                    del live_streams[key]

        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    market_streams: dict[str, int] = {}

    @app.get("/api/stream/market")
    async def market_stream(request: Request, channels: str = "", tickers: str = ""):
        """#690 quotes · flows · futures · alerts pushed as they are seen (docs/market-stream.md): the latest
        values first, then ``ready``, then each event; ``degraded`` names the channels to poll again,
        ``heartbeat`` every 15 s, ``reset`` before closing a stream that fell behind or a stopping server."""
        bus = market_bus.BUS
        if not bus.on:
            raise HTTPException(503, "market stream is off (DESK_MARKET_STREAM)")
        wanted = tuple(dict.fromkeys(c.strip() for c in channels.split(",") if c.strip())) or market_bus.DEFAULT_CHANNELS
        if any(c not in market_bus.KINDS for c in wanted):
            raise HTTPException(422, f"channels: {', '.join(market_bus.KINDS)}")
        try:
            names = frozenset(clean_ticker(t) for t in tickers.split(",") if t.strip())
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if len(names) > MARKET_STREAM_TICKERS:
            raise HTTPException(422, f"at most {MARKET_STREAM_TICKERS} tickers")
        token = request.cookies.get(COOKIE)
        key = hash_token(token or "")
        # Existing holding alerts have no verified owner generation, including Redis snapshots.
        owner = owners is None and await asyncio.to_thread(app.state.market_alerts.owner) == request.state.user.id
        if market_streams.get(key, 0) >= MARKET_STREAMS_PER_SESSION:
            raise HTTPException(429, f"at most {MARKET_STREAMS_PER_SESSION} market streams at once")
        sub = bus.subscribe(wanted, names, owner)
        market_streams[key] = market_streams.get(key, 0) + 1

        def frame(event: str, data: dict, event_id: str | None = None) -> str:
            head = f"event: {event}\n" + (f"id: {event_id}\n" if event_id else "")
            return head + "data: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + NL2

        async def frames():
            try:
                watched_quotes = names if kis_feed is not None and "quote" in wanted else ()
                for ticker in watched_quotes:
                    kis_feed.want(ticker)
                yield "retry: 3000" + NL2
                for event in bus.snapshot(sub):
                    yield frame(event["kind"], event, event["event_id"])
                degraded, _why = bus.degraded(sub)
                yield frame("ready", {"v": market_bus.VERSION, "channels": list(wanted), "bus": bus.mode,
                                      "degraded": degraded})
                loop = asyncio.get_running_loop()
                next_check = loop.time() + market_bus.HEARTBEAT_SEC
                while not await request.is_disconnected():
                    try:
                        event = await asyncio.wait_for(sub.queue.get(), max(0, next_check - loop.time()))
                    except TimeoutError:
                        event = None
                    heartbeat = loop.time() >= next_check
                    if heartbeat:
                        # Logout, revocation, or expiry takes effect before sending more market data.
                        if await asyncio.to_thread(auth.resolve, token, touch=False) is None:
                            return
                        for ticker in watched_quotes:
                            kis_feed.want(ticker)
                        next_check = loop.time() + market_bus.HEARTBEAT_SEC
                    if sub.overflowed or not bus.on:
                        yield frame("reset", {"v": market_bus.VERSION, "reason": "overflow" if bus.on else "shutdown"})
                        return
                    if event is not None:
                        yield frame(event["kind"], event, event["event_id"])
                    now_degraded, why = bus.degraded(sub)
                    if now_degraded != degraded:
                        degraded = now_degraded
                        yield frame("degraded", {"v": market_bus.VERSION, "degraded": degraded, "reason": why})
                    if heartbeat:
                        yield frame("heartbeat", {"v": market_bus.VERSION,
                                                  "t": datetime.now(UTC).isoformat(timespec="seconds"),
                                                  "degraded": degraded})
            finally:
                bus.unsubscribe(sub)
                market_streams[key] -= 1
                if not market_streams[key]:
                    del market_streams[key]

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
    async def candles(ticker: str, interval: str = "1d", count: int = 120, indicators: str = "",
                      line_alert: bool = False):
        """Bars for the chart, oldest first. 1m/1d: Toss then Yahoo; minute bars, 1w, 1M, 1y
        (and > 200 bars): Yahoo - for KRX minute bars (5m~240m) with their late tail rebuilt from
        Toss 1m (source "yahoo+toss", #379; buckets count from 09:00 KST, ``at`` = bucket start). ``indicators=rsi,stoch,bb`` (or rsi14, bb20, bb50) adds each bar's
        RSI(14), Stochastic (14, 1, 3) and Bollinger Bands (20 / 50, 2σ) - desk.indicators (#203)."""
        try:
            raw = ticker.strip().upper()
            ticker, guessed = resolve_ticker(ticker)
            allowed = sorted(line_watch_bars.ALERT_CHART_FRAMES) if line_alert else INTERVALS
            if interval not in allowed:
                raise ValueError(f"interval must be one of {', '.join(allowed)}")
            wanted = normalize_indicators(indicators.split(","))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        async def fetch(symbol: str):
            if line_alert:
                return await line_watch_bars.alert_chart(prices.candles, symbol, interval, count, wanted)
            return await prices.candles(symbol, interval, count, wanted)

        try:
            got = await fetch(ticker)
        except Exception as exc:  # noqa: BLE001 - both sources failed
            if not guessed:
                log.warning("candles for %s %s failed: %s", ticker, interval, exc)
                raise HTTPException(503, f"no candles for {ticker}") from None
            got = None
        if guessed and not (got or {}).get("bars"):
            # #477: a code the list does not know was guessed KOSPI; empty there, try KOSDAQ once
            try:
                kq = await fetch(raw + ".KQ")
            except Exception as exc:  # noqa: BLE001 - neither market
                log.warning("candles for %s (.KS / .KQ) failed: %s", raw, exc)
                kq = None
            if kq and kq.get("bars"):
                if len(learned_kr) > 4096:      # KRX lists about 2,700 codes; never grows past a bad loop
                    learned_kr.clear()
                learned_kr[raw] = ".KQ"
                return kq if line_alert else _delayed(_with_session(kq, interval))
            if got is None:
                raise HTTPException(503, f"no candles for {ticker}") from None
        return got if line_alert else _delayed(_with_session(got, interval))

    def _delayed(got: dict) -> dict:
        """#653 about how far the series runs behind (late Yahoo feeds: KRX, CME futures) - added only."""
        return {**got, "delay_sec": live_bars.series_delay(got.get("source") or "", got.get("ticker") or "")}

    @app.get("/api/candles/live")
    async def candles_live(ticker: str, interval: str = "1d"):
        """#653 the chart's last bar with the price now, for a page polling every few seconds: ``bar`` (the
        last bar carried on - ``state`` updated - or a new one - appended; ``price_only`` when nothing
        trades and there is no estimate (``live`` false), an estimate falls in a real bar's bucket, or the
        price is older than the bar), ``price`` and its ``source`` (kis · toss · yahoo · nxt · krx-overtime ·
        binance-estimate, ``estimated``), ``session``, ``delay_sec`` (the price's age, plus Yahoo's own lag)
        and the series' own source. The board's off-hours rules (#250 · #283). Cached LiveBars.TTL_SEC per
        ticker and interval (desk.live_bars)."""
        try:
            ticker, _ = resolve_ticker(ticker)
            if interval not in INTERVALS:
                raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            return await chart_live.get(ticker, interval)
        except Exception as exc:  # noqa: BLE001 - no bars or no price: the chart keeps what it has
            log.warning("live bar for %s %s failed: %s", ticker, interval, type(exc).__name__)
            raise HTTPException(503, f"no live bar for {ticker}") from None

    def _with_session(got: dict, interval: str) -> dict:
        """#559 daily bars: how much of the last bar's session has run (desk.session_progress) - the
        chart's volume tag does not hold a day still trading against whole days. None for a finished
        bar or a market without one session; only added (and only on 1d)."""
        if interval != "1d" or not got.get("bars"):
            return got
        try:
            return {**got, "session_elapsed": session_progress.elapsed(got.get("ticker") or "", got["bars"])}
        except Exception:   # the bars stand without it
            log.exception("session progress for %s failed", got.get("ticker"))
            return got

    @app.get("/api/oscillator")
    async def oscillator_view(ticker: str, interval: str = "1d"):
        """#203: is the ticker overbought ("hot") or oversold ("cold") on its RSI / Stochastic, and what
        followed the same reading over the last years (desk.oscillator). A leveraged or inverse
        product also gets its underlying's reading (``underlying``)."""
        try:
            ticker = clean_ticker(ticker)
            if interval not in oscillator.INTERVALS:
                raise ValueError(f"interval must be one of {', '.join(oscillator.INTERVALS)}")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            return await asyncio.to_thread(oscillator.view, oscillator_fetch, ticker, interval)
        except Exception as exc:  # noqa: BLE001 - no bars from Yahoo
            log.warning("oscillator for %s %s failed: %s", ticker, interval, exc)
            raise HTTPException(503, f"no oscillator for {ticker}") from None

    @app.get("/api/events/calendar")
    def economic_calendar(days: int = 14):
        """#148: upcoming FOMC decisions and major BLS releases (official calendars, refreshed in the
        background) and the release window a fresh entry should wait out, if one is open now."""
        return events_calendar.CACHE.view(days=min(max(days, 1), 60))

    @app.get("/api/backtest/reports")
    def backtest_report_list():
        """#185: the basket backtest snapshots shipped with the image, one line per horizon."""
        return backtest_reports.summaries()

    @app.get("/api/research/extreme-fear")
    def extreme_fear_research(symbol: Literal["SOXL", "KORU"]):
        """#919: archived fixed-horizon research and observed US fear gauges."""
        observations = fear_gauges.us_observations() if fear_gauges is not None else ()
        return extreme_fear_reports.get(symbol, as_of=datetime.now(UTC), observations=observations)

    @app.get("/api/backtest/reports/{horizon}")
    def backtest_report(horizon: str):
        """#185: one horizon's basket backtest report (desk.backtest.Report as JSON), as committed."""
        try:
            report = backtest_reports.get(horizon)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if report is None:
            raise HTTPException(404, f"no backtest report for {horizon}")
        return report

    @app.get("/api/calendar")
    def market_month(request: Request, month: str | None = None):
        """#243: a month of KRX / NYSE closed days, early closes, releases, expiries and earnings,
        in Korean time. ``month`` YYYY-MM (default this month in Korea), within a year either way."""
        today = datetime.now(market_calendar.KST).date()
        month = month or today.strftime("%Y-%m")
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month):
            raise HTTPException(422, "month must be YYYY-MM")
        first = date.fromisoformat(month + "-01")
        if abs((first.year - today.year) * 12 + first.month - today.month) > 12:
            raise HTTPException(422, "month must be within a year of now")
        watch = {f["ticker"] for f in favorites.list(request.state.user.id)}
        earnings.CACHE.want(watch)
        return market_calendar.month_view(month, watch)

    @app.get("/api/sector-news")
    def sector_issues(sector: str | None = None):
        """#244: per sector its surging company / key-person stories with a Korean read."""
        if sector is not None and sector not in {s.id for s in sector_news.SECTORS}:
            raise HTTPException(422, "unknown sector")
        return sector_news.CACHE.view(sector)

    @app.get("/api/earnings/preview")
    async def earnings_preview_of(ticker: str):
        """#293: what the street expects before the ticker's report - EPS / revenue consensus, past
        surprises, price targets, ratings, the implied and past moves (free sources only; the rest
        named in ``unavailable``) and the ticker's digested headlines as check points."""
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        preview = await earnings_preview.CACHE.get(ticker)
        return {**preview, "checkpoints": earnings_preview.checkpoints(ticker)}

    @app.get("/api/macro/brief")
    def macro_brief(ticker: str | None = None):
        """World news-flow themes (GDELT, refreshed in the background): article count in 24h,
        surge against the week, average tone and headlines; with ``ticker`` its themes first."""
        if ticker is not None:
            try:
                ticker = clean_ticker(ticker)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
        return news_digest.DIGEST.annotate(macro_news.CACHE.brief(ticker))

    @app.get("/api/macro/market-outlook")
    async def macro_market_outlook(market: Literal["us"] = "us"):
        """#903 attributed views and quarterly disclosures, separate from market evidence."""
        return await market_outlook_reader.read()

    # #527 the paper account: the scored plans traded with won on paper (no orders, no LLM)
    paper_desk = paper.Paper(engine, dict if settings.fake_runs else paper.yahoo_rates, listing_name,
                            public_only=protected)
    app.state.paper = paper_desk
    # #775 legacy: another account's paper account leaves out the runs that read the broker account, as
    # GET /api/runs does (protected mode trades proven public runs only, whoever asks)
    others_paper = paper_desk if protected else paper.Paper(
        engine, paper_desk.fetch_rates, listing_name, scope=not_(account_run(run_rows.c.options)))

    def paper_for(request: Request) -> paper.Paper:
        return paper_desk if others_paper is paper_desk or owns(request) else others_paper

    @app.get("/api/paper/settings")
    def paper_settings_get():
        return paper_desk.settings()

    @app.put("/api/paper/settings")
    def paper_settings_put(body: PaperSettingsIn, request: Request):
        if owners is not None:
            must_own_account(request)
        patch = body.model_dump(exclude_unset=True)
        if "start_date" in patch and patch["start_date"] == "":
            patch["start_date"] = None
        try:
            return paper_desk.save(patch)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/paper/summary")
    async def paper_summary(request: Request):
        return await asyncio.to_thread(lambda: paper_for(request).summary())

    @app.get("/api/paper/equity")
    async def paper_equity(request: Request):
        return await asyncio.to_thread(lambda: paper_for(request).equity())

    @app.get("/api/paper/trades")
    async def paper_trades(request: Request, status: Literal["closed", "open", "waiting", "skipped"] | None = None,
                           direction: Literal["long", "short"] | None = None, ticker: str | None = None,
                           limit: int = 200):
        return await asyncio.to_thread(lambda: paper_for(request).trades(status, direction, ticker, limit))

    @app.get("/api/stats/plans")
    def plan_stats(days: int = 90):
        """How the plans of the last ``days`` played out (desk.outcome): by horizon, rating,
        direction, the desk's opinion and analyst set, plus a win-probability calibration.
        Runs older than the retention are gone, so the window stops there."""
        limit = settings.retention_days if settings.retention_days > 0 else 365
        days = min(max(days, 1), limit)
        since = now() - timedelta(days=days)
        result = outcome.stats(store.scored_since(since, public_only=protected))
        return {"days": days, "from": iso(since), "to": iso(now()),
                "thresholds": {"win_probability": verdict.MIN_PROBABILITY, "agreeing": verdict.MIN_AGREEING},
                **result}

    @app.get("/api/levels")
    async def levels(ticker: str, request: Request, response: Response,
                     horizon: str | None = None, run: str | None = None):
        """Support/resistance zones (desk.sr) from the horizon's bars - daily and weekly by
        default - cached a minute or a few. #465: ``gaps`` (the unfilled daily / weekly gaps, from
        the swing frame's bars) and ``explain`` (plain sentences on the price now); with ``run``
        (an analysis of this ticker) ``basis`` - each plan line's scenario and what it sits on -
        and a sentence per line in ``explain``. The fields are added; the levels are as before."""
        found = None
        try:
            ticker = clean_ticker(ticker)
            if horizon is not None and horizon not in horizons.HORIZONS:
                raise ValueError(f"horizon must be one of {', '.join(horizons.HORIZONS)}")
            if run is not None:
                found = full_run(request, run)
                if not found:
                    raise HTTPException(404, "no such run", headers=_RUN_READ_CACHE if run_reader else None)
                if run_reader is not None:
                    response.headers.update(_RUN_READ_CACHE)
                if found["ticker"] != ticker:
                    raise ValueError("the run is of another ticker")
                # the plan's lines are matched against the levels of the run's own frame (as /api/playbooks)
                run_horizon = (found.get("plan") or {}).get("horizon") or (found.get("options") or {}).get("horizon")
                if horizon and run_horizon and horizon != run_horizon:
                    raise ValueError("the run is of another horizon; leave horizon out")
                horizon = horizon or run_horizon
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            sr = await support_resistance(prices, ticker, horizon=horizon)
        except Exception as exc:  # noqa: BLE001 - no bars or too few
            log.warning("levels for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no levels for {ticker}") from None
        found_gaps, gaps_error = gap_cache.get(ticker), None
        if found_gaps is None or time.monotonic() - found_gaps[0] >= GAPS_TTL_SEC:
            # the daily and weekly bars whatever the horizon; kept as long as the swing levels are, so
            # a board that reads many tickers' levels asks the source no more often than before
            try:
                days, weeks, _c, _p, _s = await frame_bars(prices, ticker, None, horizons.get(None))
                found_gaps = (time.monotonic(), line_basis.find_gaps(days, weeks, ticker))
                if len(gap_cache) > 512:
                    gap_cache.clear()
                gap_cache[ticker] = found_gaps
            except Exception as exc:  # noqa: BLE001 - the levels stand without the gaps
                log.info("gaps for %s failed: %s", ticker, exc)
                found_gaps, gaps_error = (0.0, []), f"{type(exc).__name__}"
        if run is not None:
            await verify_full_run(request, run)
        gap_rows = line_basis.place(found_gaps[1], sr["reference_price"])
        lines = line_basis.basis((found or {}).get("plan"), sr, gap_rows) if found else []
        # a new dict: the levels are a cached object shared with other callers
        return {**sr, "gaps": gap_rows, "gaps_error": gaps_error, "basis": lines,
                "explain": line_basis.explain(sr, gap_rows, lines)}

    lines_memo: dict[tuple[str, str], tuple[str, int, bool, dict]] = {}

    @app.get("/api/lines")
    async def chart_lines(ticker: str = "NQ=F", tf: str = "5m"):
        """#611 parallel channels and short trendlines on one timeframe (desk.channels), each line with
        its past hit rate where desk.lines_bt has run for the ticker. ``tf``: 5m · 15m · 1h · 2h · 4h · 1d
        (or the chart's 60m · 240m). Recomputed only when a new bar comes in."""
        try:
            ticker = clean_ticker(ticker)
            interval = channels.TF_ALIASES.get(tf)
            if interval is None:
                raise ValueError(f"tf must be one of {', '.join(channels.TF_ALIASES)}")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            got = await prices.candles(ticker, interval, channels.BARS[interval])
        except Exception as exc:  # noqa: BLE001 - no bars
            log.warning("lines for %s %s failed: %s", ticker, interval, type(exc).__name__)
            raise HTTPException(503, f"no bars for {ticker}") from None
        bars = got["bars"]
        key = (ticker, interval)
        memo = lines_memo.get(key)
        # the last bar still trading is not judged a break (it may close back): see desk.channels
        live = channels.is_live(bars, interval)
        stamp = (bars[-1]["at"], round(bars[-1]["close"] * 1e4), live) if bars else ("", 0, False)
        if memo is None or memo[:3] != stamp:
            found = await asyncio.to_thread(channels.lines, bars, interval, lines_bt.rates(ticker, interval), live)
            # #679 a daily chart also gets the lines of months (daily) and years (weekly): long_lines
            found["long_lines"] = []
            weekly_ok = True
            if interval == "1d":
                try:
                    weeks = (await prices.candles(ticker, "1w", long_lines.SPEC["1w"]["window"] + 4))["bars"]
                except Exception as exc:  # noqa: BLE001 - the daily long lines stand without the weekly
                    log.warning("weekly bars for %s failed: %s", ticker, type(exc).__name__)
                    weeks, weekly_ok = [], False
                try:
                    found["long_lines"] = await asyncio.to_thread(long_lines.for_chart, bars, weeks, live)
                except Exception as exc:  # noqa: BLE001 - the chart stands without them
                    log.warning("long lines for %s failed: %s", ticker, type(exc).__name__)
            if len(lines_memo) > 256:
                lines_memo.clear()
            # without the weekly bars this answer is not kept: the next call tries them again
            memo = (*stamp, found)
            if weekly_ok:
                lines_memo[key] = memo
        return {"ticker": ticker, "interval": interval, "source": got.get("source"), "live_bar": live, **memo[3],
                "backtest": lines_bt.summary(ticker)}

    chart_reads: dict[tuple[str, tuple[str, ...], tuple[date, str] | None], tuple[float, dict]] = {}

    async def _lead_peers(ticker: str) -> dict[str, list[dict] | None]:
        """#977 the daily bars of the KOSPI-lead tickers other than ``ticker`` (a failed one is None)."""
        others = [t for t in trend_follow.KR_LEAD if t != ticker]
        count = max(channels.BARS["1d"], accumulation_live.DAILY_BARS)          # the chart-read's own daily ask
        more = await asyncio.gather(*(prices.candles(t, "1d", count) for t in others), return_exceptions=True)
        return {t: r.get("bars") if isinstance(r, dict) else None for t, r in zip(others, more, strict=True)}

    async def _trend_follow(ticker: str, tfs: tuple[str, ...], got: list, frames: list[dict], now: datetime,
                            peer_fetch: asyncio.Future | None) -> dict:
        """#977 the daily trend-following read on chart-read's own daily bars and 1d frame; for KOSPI, SK hynix
        and Samsung Electronics the other two's daily bars too (``peer_fetch``, started beside the chart's own)."""
        daily = got[tfs.index("1d")]
        frame = next((f for f in frames if f.get("timeframe") == "1d" and "error" not in f), None)
        peers = await peer_fetch if peer_fetch is not None else {}     # failed fetches come back as None
        try:
            return await asyncio.to_thread(trend_follow.build, ticker, daily if isinstance(daily, dict) else None,
                                           now, frame, peers)
        except Exception as exc:                        # the chart's other reads still stand
            log.warning("chart read trend follow failed: %s", type(exc).__name__, exc_info=True)
            return trend_follow.unavailable(ticker, now, "trend_follow_error")

    @app.get("/api/chart-read")
    async def chart_read_get(ticker: str = "NQ=F", tf: str | None = None):
        """#631 the chart alone (desk.chart_read): per timeframe the trend, channel, pattern, nearest
        lines, volume and a plain sentence, where the timeframes disagree, one summary - rule-based,
        no model. ``tf``: comma separated (5m · 15m · 1h · 2h · 4h · 1d, default 5m,15m,1h,4h,1d).
        Kept until the shortest timeframe's bar ends (a read is then one cached answer)."""
        try:
            ticker = clean_ticker(ticker)
            names = [t.strip() for t in (tf or "").split(",") if t.strip()]
            tfs = tuple(dict.fromkeys(channels.TF_ALIASES.get(t, "?") for t in names)) or chart_read.TIMEFRAMES
            if "?" in tfs:
                raise ValueError(f"tf must be among {', '.join(channels.TF_ALIASES)}")
            tfs = tuple(sorted(tfs, key=channels.TIMEFRAMES.index))       # one cache entry whatever the order
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        phase = daily_resistance.cache_phase(ticker, datetime.now(UTC)) if "1d" in tfs else None
        key = (ticker, tfs, phase)
        hit = chart_reads.get(key)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        # #751 the daily once, long enough for the mid view's bands too (the frame reads its last BARS)
        wanted = [prices.candles(ticker, t, max(channels.BARS[t], accumulation_live.DAILY_BARS)
                                 if t == "1d" else channels.BARS[t])
                  for t in tfs]
        if "1d" in tfs:
            # the weekly gaps beside the daily, and (#751) the long view's weeks
            wanted.append(prices.candles(ticker, "1w", max(CHART_READ_WEEKS, accumulation_live.WEEKLY_BARS)))
        # #977 the lead comparison's other two daily series, fetched beside the chart's own
        peer_fetch = (asyncio.ensure_future(_lead_peers(ticker))
                      if "1d" in tfs and ticker in trend_follow.KR_LEAD else None)
        got = await asyncio.gather(*wanted, return_exceptions=True)
        weekly = got[-1] if "1d" in tfs and isinstance(got[-1], dict) else None
        weeks = weekly["bars"] if weekly is not None else None
        now = datetime.now(UTC)
        frames, keep = [], CHART_READ_MAX_SEC
        if "1d" in tfs and weeks is None:
            keep = CHART_READ_MIN_SEC * 4                  # the weekly gaps missing: ask again soon
        for t, res in zip(tfs, got, strict=False):
            if isinstance(res, BaseException):
                log.info("chart read %s %s failed: %s", ticker, t, type(res).__name__)
                frames.append({"timeframe": t, "error": type(res).__name__})
                keep = min(keep, CHART_READ_MIN_SEC * 4)
                continue
            bars = res["bars"][-channels.BARS[t]:]
            live = channels.is_live(bars, t, now)
            gaps, gaps_error = [], None
            if t == "1d" and bars:
                try:
                    gaps = await asyncio.to_thread(line_basis.gaps, bars, weeks[-CHART_READ_WEEKS:] if weeks else weeks,
                                                   bars[-1]["close"], ticker)
                except Exception as exc:  # noqa: BLE001 - the read stands without the gaps
                    gaps_error = type(exc).__name__
                    keep = min(keep, CHART_READ_MIN_SEC * 4)
                if weeks is None:
                    gaps_error = gaps_error or "no weekly bars"
            try:
                one = await asyncio.to_thread(chart_read.frame, bars, t, live, lines_bt.rates(ticker, t), gaps)
            except Exception as exc:  # noqa: BLE001 - one timeframe failing leaves the others
                log.warning("chart read %s %s failed: %s", ticker, t, type(exc).__name__)
                frames.append({"timeframe": t, "error": type(exc).__name__})
                keep = min(keep, CHART_READ_MIN_SEC * 4)
                continue
            if t == "1d":
                one["gaps_error"] = gaps_error
            frames.append(one)
            if live and bars and t not in channels.GROUPED:
                end = datetime.fromisoformat(bars[-1]["at"]) + timedelta(minutes=channels.MINUTES[t])
                keep = min(keep, (end - now).total_seconds() + 5)
        out = chart_read.read(ticker, frames)
        if "1d" in tfs:
            price = next((f["price"] for f in frames if f.get("price")), None)     # the shortest timeframe's: now
            out["accumulation"], out["legs"] = await accumulation_live.read(tfs, got, weeks, now, price)
            if out["accumulation"] is None or None in (out["accumulation"].get("mid"), out["accumulation"].get("long")):
                keep = min(keep, CHART_READ_MIN_SEC * 4)                        # a view missing: ask again soon
            if daily_resistance.eligible(ticker):
                key = (ticker, tfs, daily_resistance.cache_phase(ticker, now))
                daily = got[tfs.index("1d")]
                try:
                    out["daily_reaction"] = await asyncio.to_thread(
                        daily_resistance.build, ticker, daily if isinstance(daily, dict) else None, now,
                    )
                except Exception as exc:  # noqa: BLE001 - daily evidence must not break the chart
                    log.warning("chart read daily reaction failed: %s", type(exc).__name__)
                    out["daily_reaction"] = daily_resistance.unavailable(ticker, now, "reaction_error")
                if out["daily_reaction"]["status"] != "confirmed":
                    keep = min(keep, CHART_READ_MIN_SEC * 4)
            if ticker == "NQ=F":
                try:
                    reference = weekly_chart_evidence.chart_price(tfs, got, now)
                    out["weekly_drawdown"] = await asyncio.to_thread(weekly_chart_evidence.build, weekly, reference, now)
                except Exception as exc:  # noqa: BLE001 - the other chart frames still stand
                    log.warning("chart read weekly evidence failed: %s", type(exc).__name__)
                    out["weekly_drawdown"] = weekly_chart_evidence.unavailable("weekly_evidence_error", now)
                if out["weekly_drawdown"]["status"] == "unavailable":
                    keep = min(keep, CHART_READ_MIN_SEC * 4)
            out["trend_follow"] = await _trend_follow(ticker, tfs, got, frames, now, peer_fetch)
            if out["trend_follow"]["reason"] in ("missing_daily_source", "trend_follow_error"):
                keep = min(keep, CHART_READ_MIN_SEC * 4)          # a passing failure: ask again soon
        if len(chart_reads) > 256:
            chart_reads.clear()
        chart_reads[key] = (time.monotonic() + max(CHART_READ_MIN_SEC, keep), out)
        return out

    @app.get("/api/stock/profile")
    async def stock_profile(ticker: str | None = None, tickers: str | None = None):
        """#295 one instrument's card (desk.stock_profile): facts, the last ten sessions, indicators,
        the chart's support / resistance as steps, short interest. ``tickers`` (comma separated,
        up to 30) answers {"items": [...]}; a failing source leaves its part null, never the card."""
        names = [t for t in (x.strip() for x in (tickers or ticker or "").split(",")) if t]
        if not names or len(names) > PROFILE_MAX or (ticker and tickers) or (ticker and "," in ticker):
            raise HTTPException(422, f"ticker, or between 1 and {PROFILE_MAX} tickers")
        try:
            names = list(dict.fromkeys(clean_ticker(t) for t in names))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if ticker:
            return await profiles.one(names[0])
        return {"items": await profiles.many(names)}

    @app.get("/api/stock-social")
    async def stock_social_get(ticker: str = "SOXX", days: int = 7):
        """#934 observed headline sentiment and public filing evidence."""
        if not 1 <= days <= 30:
            raise HTTPException(422, "days must be between 1 and 30")
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return await stock_social.read(ticker, days, now=datetime.now(UTC), include_filings=not settings.fake_runs)

    # #299 차트 예상도: 확률 원뿔 · 레벨 경로 · 설명(모델 문장은 슬롯이 빌 때만, 숫자는 데스크 것만)
    projections = projection.Projections(projection.fake_note if settings.fake_runs else projection.llm_note)

    @app.get("/api/funds")
    async def funds_list():
        """#483 the well-known funds: each one's latest 13F (quarter, period of report, filed date,
        count, total value, the largest new buys) and the next deadline."""
        return funds.list()

    @app.get("/api/funds/changes")
    async def funds_changes(tickers: str):
        """#483 the badges: what each fund did with these tickers in its latest quarter (read ones only)."""
        try:
            names = list(dict.fromkeys(clean_ticker(t) for t in tickers.split(",") if t.strip()))
            if not names or len(names) > FUNDS_TICKERS_MAX:
                raise ValueError(f"between 1 and {FUNDS_TICKERS_MAX} tickers")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return funds.changes(names)

    @app.get("/api/funds/{fund_id}")
    async def fund_view(fund_id: str, quarter: str | None = None):
        """#483 one fund's holdings in ``quarter`` (2026Q3; the latest when absent) against the quarter
        before: shares, value, weight, new / increase / decrease / sold, and a summary. 404: no such
        fund or nothing filed for the quarter · 503: SEC (or another fetch running - come back)."""
        if fund_id not in funds13f.BY_ID:
            raise HTTPException(404, "no such fund")
        try:
            if quarter is not None:
                funds13f.period_of(quarter)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            return await asyncio.to_thread(funds.view, fund_id, quarter)
        except funds13f.NotFiled as exc:
            raise HTTPException(404, str(exc)) from None
        except funds13f.FundsUnavailable as exc:      # the desk's own words (no remote text in them)
            raise HTTPException(503, str(exc)) from None
        except Exception as exc:  # noqa: BLE001 - SEC down or a filing we cannot read
            log.warning("13F %s %s failed: %s", fund_id, quarter, type(exc).__name__)
            raise HTTPException(503, "13F 공시를 받지 못했습니다") from None

    @app.get("/api/tips")
    async def tips_view(ticker: str | None = None):
        """#330 the rate / dollar regime now and the tips with their backtest evidence; ``ticker``
        keeps only the tips that instrument is in. #460, without ``ticker`` only (the per-ticker badges
        answer as before): ``trend`` - each main index's primary trend (bull / bear / range / turning)
        with its evidence and record - and ``guide``, the beginner's guide per state. The trend has its
        own fetch and waits at most TREND_WAIT_SEC: slow, busy or failed, ``trend`` is empty and
        ``trend_error`` says why; the tips answer as before."""
        try:
            ticker = clean_ticker(ticker) if ticker else None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if ticker:
            out = await asyncio.to_thread(tip_cache.view, ticker)
            if out is None:
                raise HTTPException(503, "rate / dollar series unavailable")
            return out

        async def trend_rows():
            try:   # a timed-out fetch keeps running in its thread and fills the cache for the next ask
                return await asyncio.wait_for(asyncio.to_thread(trend_cache.view), TREND_WAIT_SEC)
            except TimeoutError:
                return None

        out, trend = await asyncio.gather(asyncio.to_thread(tip_cache.view, None), trend_rows())
        if out is None:
            raise HTTPException(503, "rate / dollar series unavailable")
        error = (None if trend else "지수 일봉을 불러오는 중입니다 — 잠시 뒤 다시 확인하세요" if trend is None
                 else "지수 일봉을 받지 못해 대세 국면을 판정하지 못했습니다")
        try:     # #605 the leveraged / inverse ETFs' units and the KTB yields from the stored KRX rows
            krx_lines = await asyncio.to_thread(krx_etp.tips_view, engine, datetime.now(krx.KST).date())
        except Exception as exc:  # noqa: BLE001 - the tips stand without them
            log.warning("krx tips failed: %s", type(exc).__name__)
            krx_lines = {"etf_flow": [], "ktb": None}
        return {**out, "trend": trend or [], "guide": primary_trend.guide(), "trend_error": error,
                "gaps": gap_lines(gap_fill.WATCH), **krx_lines}

    def gap_lines(tickers) -> list[dict]:
        """#577 the tips' gap lines: each ticker's nearest open gap, from the cache only (prewarmed)."""
        rows = []
        for t in tickers:
            rep = gap_fill.cached(t)
            if rep and rep["open"]:
                g = rep["open"][0]
                rows.append({"ticker": rep["ticker"], "name": rep.get("name"), "text": gap_fill.line_ko(rep, g),
                             "direction": g["direction"], "fill_price": g["fill_price"], "p5": g["odds"]["p5"],
                             "p20": g["odds"]["p20"], "n": g["odds"]["n"], "few": g["odds"]["few"]})
        return rows

    @app.get("/api/gaps")
    async def gaps_view(ticker: str):
        """#577 the session gaps' fill odds (desk.gap_fill): the open gaps - fill price, size, cause, how
        long open, the odds of gaps like it still open that long - and the table by direction, size,
        cause and trend (1 / 3 / 5 / 20 sessions, median, sample). KOSPI200F reads KODEX 200; a
        leveraged product its underlying, with the fill in its own price too. Past counts, not a forecast."""
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            return await asyncio.to_thread(gap_fill.read, gap_fetch, ticker, gap_events)
        except Exception as exc:  # noqa: BLE001 - no bars, too few, or a source down
            log.warning("gaps for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no gap odds for {ticker}") from None

    @app.get("/api/projection")
    async def projection_view(ticker: str, horizon: str | None = None):
        """The look ahead for the chart (#299): a calibrated cone, paths through the desk's levels and
        a note. Probability ranges and scenarios, not a forecast."""
        try:
            ticker = clean_ticker(ticker)
            if horizon is not None and horizon not in horizons.HORIZONS:
                raise ValueError(f"horizon must be one of {', '.join(horizons.HORIZONS)}")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        h = horizons.get(horizon)

        async def bars_for(levels: dict):
            # the levels fell back to the swing frame (intraday bars missing): the cone uses it too
            frame = horizons.get(None) if levels.get("fallback") else h
            return (await frame_bars(prices, ticker, None, frame))[0], frame.primary

        try:
            return await projections.get(ticker, h.id, h.sr_ttl_sec, lambda: support_resistance(prices, ticker, horizon=horizon),
                                         bars_for, slot=app.state.manager.spare_slot)
        except Exception as exc:  # noqa: BLE001 - no bars or too few
            log.warning("projection for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no projection for {ticker}") from None

    @app.get("/api/playbooks")
    async def live_playbooks(request: Request, response: Response, ticker: str | None = None,
                             horizon: str | None = None, run: str | None = None):
        """Long and short playbooks on the bars and the price now (#128). With ``run`` the
        analysis' zones, ladder, stop and targets stay and only conditions, score and state move."""
        run_pb = None
        if run is not None:
            found = full_run(request, run)
            if not found:
                raise HTTPException(404, "no such run", headers=_RUN_READ_CACHE if run_reader else None)
            if run_reader is not None:
                response.headers.update(_RUN_READ_CACHE)
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
            result = await playbook_live.current(prices, ticker, horizon, run, run_pb, risk=risk)
        except Exception as exc:  # noqa: BLE001 - no bars, too few, or a source down
            log.warning("live playbooks for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no playbooks for {ticker}") from None
        if run is not None:
            await verify_full_run(request, run)
        return result

    # one gate a kind for the whole app: two pages batching at once do not double the load on the sources
    batch_gates = {k: asyncio.Semaphore(BATCH_PARALLEL_BARS if k in ("candles", "estimate-candles") else BATCH_PARALLEL)
                   for k in ("bottom-zone", "candles", "levels", "playbooks", "projection", "gaps", "estimate-candles")}
    screen_gate = asyncio.Semaphore(SCREEN_PARALLEL)     # #858 over the kinds' gates (taken last)

    @app.get("/api/batch/{kind}")
    async def batch(kind: str, tickers: str, request: Request, response: Response,
                    horizon: str | None = None, interval: str | None = None, count: int = 120):
        """#705 one call for a page that asked the same per-ticker path for many tickers (the dashboard's
        first visit: bottom zones, the market tiles' bars, levels and futures gaps, the today strip's
        playbooks and projections, the favourites' sparks - about 60 of its ~100 requests). ``kind``:
        bottom-zone · candles · levels · playbooks · projection · gaps · estimate-candles; up to MAX_BOARD tickers, BATCH_PARALLEL at a time, each
        answered by the same handler as its own path (same caches, same validation). ``items`` is keyed by
        the ticker as asked; a ticker that fails is ``{"error": status, "detail": ...}`` and the others
        still come - like /api/estimates."""
        handlers = {
            "bottom-zone": lambda t: bottom_zone_of(t),
            "candles": lambda t: candles(t, interval=interval or "1d", count=count),
            "levels": lambda t: levels(t, request, response, horizon=horizon),
            "playbooks": lambda t: live_playbooks(request, response, ticker=t, horizon=horizon),
            "projection": lambda t: projection_view(t, horizon=horizon),
            "gaps": lambda t: gaps_view(t),
            "estimate-candles": lambda t: estimate_candles(t, interval=interval or "5m"),   # the single path's default
        }
        fn = handlers.get(kind)
        if fn is None:
            raise HTTPException(404, f"kind must be one of {', '.join(handlers)}")
        names = list(dict.fromkeys(t for t in (x.strip() for x in tickers.split(",")) if t))
        if not names or len(names) > MAX_BOARD:
            raise HTTPException(422, f"between 1 and {MAX_BOARD} tickers")
        if not 1 <= count <= BATCH_MAX_COUNT:
            raise HTTPException(422, f"count must be 1 to {BATCH_MAX_COUNT}")
        request.state.timing_note = {"kind": kind, "n": len(names)}    # #858 the slow-request line: no tickers
        gate = batch_gates[kind]

        stale = BATCH_STALE_SEC if kind == "candles" else 0.0

        async def one(ticker: str) -> tuple[str, dict]:
            async with gate, screen_gate:
                try:
                    with stale_ok(stale):
                        return ticker, await fn(ticker)
                except HTTPException as exc:
                    return ticker, {"error": exc.status_code, "detail": exc.detail}
                except Exception as exc:  # noqa: BLE001 - one ticker failing leaves the others
                    log.warning("batch %s for %r failed: %s", kind, ticker[:24], type(exc).__name__)
                    return ticker, {"error": 503, "detail": type(exc).__name__}

        return {"kind": kind, "items": dict(await asyncio.gather(*(one(t) for t in names)))}

    @app.get("/api/market/watchboard")
    async def watchboard(group: str = "semiconductors", days: int = 30):
        """#921 completed daily history and comparison for a fixed public watch group."""
        if group not in market_watchboard.GROUPS:
            raise HTTPException(422, f"group must be one of {', '.join(market_watchboard.GROUPS)}")
        if not 1 <= days <= 90:
            raise HTTPException(422, "days must be between 1 and 90")

        async def fetch(symbol: str) -> dict:
            async with batch_gates["candles"], screen_gate:
                with stale_ok(BATCH_STALE_SEC):
                    # This fixed daily count uses Yahoo through the existing PriceSource.
                    return await prices.candles(symbol, "1d", 300)

        return await market_watchboard.read(group, days, fetch, clock=lambda: datetime.now(UTC))

    @app.get("/api/entry-now")
    async def entry_now_of(ticker: str, horizon: str | None = None):
        """#312: one entry on the price now - the long or short side worth more per plan (fill rate by
        distance x expected R), its zone, stop, targets and why; ``stand_aside`` when none is worth it."""
        try:
            ticker = clean_ticker(ticker)
            if horizon is not None and horizon not in horizons.HORIZONS:
                raise ValueError(f"horizon must be one of {', '.join(horizons.HORIZONS)}")
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            got = await entry_now.entry_now(prices, ticker, horizon, risk=risk, bottoms=bottoms)
        except Exception as exc:  # noqa: BLE001 - no bars, too few, or a source down
            log.warning("entry now for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no entry for {ticker}") from None
        # #577: an entry beyond an open gap may be pulled back to it - the field is added, None without one.
        # Only from the cache: a ticker not read yet is filled in the background (eight years of bars) and
        # this answer goes out without it - the entry summary never waits for the gap odds (#536)
        side = (got.get("best") or {}).get("side") if isinstance(got, dict) else None
        warning = None
        if side and not settings.fake_runs:
            rep = gap_fill.cached(ticker)
            if rep is not None:
                warning = gap_fill.entry_warning(rep, side)
            else:
                fill_gaps_later(ticker)
        return {**got, "gap_warning": warning} if isinstance(got, dict) else got

    gap_filling: set[str] = set()

    def fill_gaps_later(ticker: str) -> None:
        """#577 one background read per ticker at a time; a failure only logs."""
        if ticker in gap_filling or len(gap_filling) >= GAP_FILL_MAX:
            return
        gap_filling.add(ticker)

        async def fill():
            try:
                await asyncio.to_thread(gap_fill.read, gap_fetch, ticker, gap_events)
            except Exception as exc:  # noqa: BLE001 - asked again on the next entry summary
                log.info("gap odds for %s failed: %s", ticker, type(exc).__name__)
            finally:
                gap_filling.discard(ticker)

        task = asyncio.create_task(fill())
        gap_tasks.add(task)
        task.add_done_callback(gap_tasks.discard)

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

    first_waits: dict[str, float] = {}     # #257: when the board last waited for a ticker's first trade

    async def _first_trades(names: list[str]) -> None:
        """#257: off hours, ask the KIS stream for the board's US tickers and - once per ticker every
        FIRST_TICK_RETRY_SEC - wait up to FIRST_TICK_WAIT_SEC in all for the new ones' first trades,
        so the board's first load is not all estimates. A ticker that never subscribes (stream down,
        refused, over the limit) is not waited for again on every poll; nothing holds the board's gate."""
        stream = getattr(prices, "stream", None)
        if stream is None or kis_stream.us_session() not in ("pre", "post", "overnight"):
            return
        now = time.monotonic()
        us = [t for t in names if kis_stream.market_of(t) == "US"]
        new = [t for t in us if not stream.subscribed(t) and now - first_waits.get(t, -1e9) > FIRST_TICK_RETRY_SEC]
        for t in us:
            stream.want(t)
        if len(first_waits) > 512:
            first_waits.clear()
        for t in new:
            first_waits[t] = now
        waited = 0.0
        while new and waited < FIRST_TICK_WAIT_SEC:
            await asyncio.sleep(FIRST_TICK_POLL_SEC)
            waited += FIRST_TICK_POLL_SEC
            new = [t for t in new if stream.latest(t, OFF_HOURS_FRESH_SEC) is None]

    def _off_hours_trade(ticker: str):
        """#257: a KIS trade of a US ticker off hours, up to OFF_HOURS_FRESH_SEC old (thin ETFs trade
        minutes apart)."""
        stream = getattr(prices, "stream", None)
        if stream is None:
            return None
        stream.want(ticker)
        return stream.latest(ticker, OFF_HOURS_FRESH_SEC)

    async def _nxt_today(ticker: str):
        """#250: a KRX stock's NXT / after-hours trade made today (KST): (price, traded at UTC), or
        None - yesterday evening's trade read before 08:00 is not a live price."""
        reader = getattr(estimator, "_nxt_trade", None)
        if reader is None:
            return None
        try:
            found = await reader(ticker[:-3])
        except Exception as exc:  # noqa: BLE001 - Naver down: the estimate stands
            log.info("NXT trade for %s failed: %s", ticker, exc)
            return None
        if not found:
            return None
        price, at = found[0], found[1]
        if at.astimezone(sessions.KST).date() != datetime.now(UTC).astimezone(sessions.KST).date():
            return None
        return price, at

    async def _krx_overtime(ticker: str):
        """#283: a KRX stock's own after-hours trade today (closing-price 15:40-16:00, single-price
        16:00-18:00) - the only real price after the close for a stock that does not trade on NXT."""
        reader = getattr(estimator, "_overtime_trade", None)
        if reader is None:
            return None
        try:
            return await reader(ticker[:-3])
        except Exception as exc:  # noqa: BLE001 - Naver down: the estimate stands
            log.info("KRX after-hours trade for %s failed: %s", ticker, exc)
            return None

    async def _chart_off_hours(ticker: str, session: str, close: float | None) -> dict | None:
        """#653 the off-hours trade the quote board takes (#250 · #283) for a chart's last bar, or None:
        KRX - NXT, then (after the close) the KRX's own after-hours auction; US - a KIS trade, then a Toss
        price that has moved off the close."""
        market = kis_stream.market_of(ticker)
        if market == "KR" and session in ("pre", "post"):
            if (nxt := await _nxt_today(ticker)) is not None:
                return {"price": nxt[0], "source": "nxt", "at": nxt[1].isoformat(timespec="seconds")}
            if session == "post" and (ot := await _krx_overtime(ticker)) is not None:
                return {"price": ot.price, "source": "krx-overtime", "at": ot.traded_at.isoformat(timespec="seconds")}
        if market == "US" and session in ("pre", "post", "overnight"):
            if (tick := _off_hours_trade(ticker)) is not None:
                return {"price": tick.price, "source": "kis", "at": tick.received_at}
            if (q := await _toss_off_hours(ticker, close)) is not None:
                return {"price": q.price, "source": "toss", "at": q.fetched_at}
        return None

    async def _toss_off_hours(ticker: str, close: float | None):
        """#250: a US ticker's broker (Toss) quote off hours, taken only when it differs from the last
        regular close - Toss gives no trade time, and a price equal to the close is the close."""
        if not close:
            return None
        try:
            q = await prices.quote(ticker)
        except Exception as exc:  # noqa: BLE001 - no broker price: the estimate stands
            log.info("off-hours broker quote for %s failed: %s", ticker, exc)
            return None
        broker_name = getattr(getattr(prices, "broker", None), "name", None)
        if q.source != broker_name or abs(q.price / close - 1) < OFF_HOURS_MOVE:
            return None
        return q

    async def _board_quote(ticker: str):
        try:
            return await prices.quote(ticker)
        except Exception as exc:  # noqa: BLE001 - the estimate stands in
            log.warning("board quote for %s failed: %s", ticker, exc)
            return None

    ahead_tasks: set[asyncio.Future] = set()

    async def _has_contract(ticker: str) -> bool:
        try:
            return await estimator.resolve(ticker) is not None
        except Exception:  # noqa: BLE001 - Binance's list unread: the estimate below says so
            return False

    async def _quote_ahead(ticker: str) -> None:
        with suppress(Exception):            # the caller that takes it logs a failure
            await prices.quote(ticker)

    async def _board_close(ticker: str):
        """#858 the board's change base while the market trades (desk.estimate.BinanceEstimator.last_close)."""
        try:
            return await estimator.last_close(ticker)
        except Exception as exc:  # noqa: BLE001 - the live price without its change
            log.warning("board close for %s failed: %s", ticker, exc)
            return None

    @app.get("/api/estimates")
    async def estimates(tickers: str, request: Request):
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
        request.state.timing_note = {"n": len(names)}                   # #858 the slow-request line: no tickers
        gate = asyncio.Semaphore(BOARD_PARALLEL)
        await _first_trades(names)

        async def one(ticker: str) -> dict:
            async with gate:
                # #250: one session answer; while anything trades the board shows a real price
                session = await sessions.session(calendar, ticker)
                est, err, quote, asked = None, None, None, False
                if session == "regular":
                    # #858 the price is the live quote; the estimator gives only the last close (the change's
                    # base), read alongside it - the contract's marks (Binance) are not asked
                    live.watch(prices, ticker)       # the board's tickers ride the KIS stream too
                    est, quote = await asyncio.gather(_board_close(ticker), _board_quote(ticker))
                    asked = True
                if quote is None:                    # off hours, or no quote: the estimate stands in
                    ahead = None
                    if (session in ("pre", "post", "overnight") and kis_stream.market_of(ticker) == "US"
                            and _off_hours_trade(ticker) is None and await _has_contract(ticker)):
                        # #858 the broker quote the row may take below (#250), read alongside the estimate:
                        # desk.prices shares the read, so the call below finds it (no contract, no estimate:
                        # that row never asked for a quote and still does not)
                        ahead = asyncio.ensure_future(_quote_ahead(ticker))
                        ahead_tasks.add(ahead)
                        ahead.add_done_callback(ahead_tasks.discard)
                    try:
                        est = await estimator.estimate(ticker)
                    except Exception as exc:  # noqa: BLE001 - Binance or Yahoo down for this one
                        est, err = None, type(exc).__name__
                        log.warning("board estimate for %s failed: %s", ticker, exc)
                    if est is None and ahead is not None:
                        ahead.cancel()               # no close to weigh it against: not read when not begun
                    if est and est.market_state == "open":
                        session = "regular"          # the estimator's own calendar (special days) agrees
                close = est.last_close if est else None
                row: dict = {"ticker": ticker, "close": close,
                             "close_date": est.last_close_date if est else None, "session": session,
                             "source": ({"kind": est.method, "contract": est.contract, "basis": est.basis,
                                         "multiple": est.multiple} if est else None)}

                def live_row(price: float, source: str, at, change=None) -> dict:
                    if change is None and close:
                        change = round((price / close - 1) * 100, 2)
                    return {**row, "state": "live", "price": price, "change_pct": change,
                            "quote_source": source, "fetched_at": at}

                if quote is not None:
                    return live_row(quote.price, quote.source, quote.fetched_at)
                if est and est.market_state == "nxt":
                    # #209: NXT trading (08:00-08:50, 15:40-20:00 KST) - its last trade is a real price
                    row["session"] = session if session in ("pre", "post") else "post"
                    return live_row(est.estimate, "nxt", est.fetched_at, est.change_pct)
                if session == "regular":
                    if not asked:
                        live.watch(prices, ticker)
                        if (q := await _board_quote(ticker)) is not None:
                            return live_row(q.price, q.source, q.fetched_at)
                elif session in ("pre", "post") and kis_stream.market_of(ticker) == "KR":
                    # #250: any KRX stock trading on NXT / after hours - not only those with a contract
                    if (nxt := await _nxt_today(ticker)) is not None:
                        return live_row(nxt[0], "nxt", nxt[1].isoformat(timespec="seconds"))
                    # #283: no NXT trade (not listed there): the KRX's own after-hours auction
                    if session == "post" and (ot := await _krx_overtime(ticker)) is not None:
                        today = datetime.now(UTC).astimezone(sessions.KST).date().isoformat()
                        if ot.close and (not close or (row["close_date"] or "") < today):
                            # today's regular close is the change's base (no estimate, or Yahoo not caught up)
                            row["close"], row["close_date"] = ot.close, today
                            close = ot.close
                        return {**live_row(ot.price, "krx-overtime", ot.traded_at.isoformat(timespec="seconds")),
                                "overtime": ot.kind, "traded_at": ot.traded_at.isoformat(timespec="seconds")}
                elif session in ("pre", "post", "overnight") and kis_stream.market_of(ticker) == "US":
                    # #238: a KIS trade beats the estimate; #250: without one, Toss's price when it has
                    # moved off the close (a quote equal to the close is the close, not an off-hours trade)
                    if (tick := _off_hours_trade(ticker)) is not None:
                        return live_row(tick.price, "kis", tick.received_at)
                    if (q := await _toss_off_hours(ticker, close)) is not None:
                        return live_row(q.price, "toss", q.fetched_at)
                if est:
                    return {**row, "state": "estimate", "price": est.estimate, "change_pct": est.change_pct,
                            "quote_source": "binance-estimate", "fetched_at": est.fetched_at}
                return {**row, "state": "unavailable", "price": None, "change_pct": None, "error": err,
                        "quote_source": None, "fetched_at": None}

        return {"items": await asyncio.gather(*(one(t) for t in names))}

    @app.get("/api/longshort")
    async def long_short(tickers: str):
        """#271: per ticker the long / short split of its Binance perpetual (all accounts, top traders'
        positions, taker volume) and the 24 h change; ``contract: null`` without one. One failing
        contract does not fail the others."""
        names = [t for t in (x.strip() for x in tickers.split(",")) if t]
        if not names or len(names) > MAX_BOARD:
            raise HTTPException(422, f"between 1 and {MAX_BOARD} tickers")
        try:
            names = list(dict.fromkeys(clean_ticker(t) for t in names))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return {"items": await asyncio.gather(*(longshort.get(t) for t in names))}

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

    # --- the desk chat (#314): per account, the model looks the desk up --------------------

    chats = chat.ChatStore(engine, policy)
    chatter = chatter or (chat.FakeChatter() if settings.fake_runs else chat.LangChainChatter(
        tier=settings.chat_model_tier, effort=settings.chat_reasoning_effort or None))
    # #414: only these reach the provider. Default: DESK_CHAT_MODEL, else the chat tier's own model
    # (#404) when offered, else the first offered.
    chat_models = chat.model_options(settings.chat_models, settings.chat_model, chat.model_of(chatter))
    if settings.chat_model and settings.chat_model != chat_models["default"]:
        log.warning("DESK_CHAT_MODEL %r is not in DESK_CHAT_MODELS; the chat defaults to %r",
                    settings.chat_model, chat_models["default"])
    # one answer at a time per account: user id -> when it started. Stale after the answer's time
    # limit, so a stream that never started (the client left first) cannot hold the account.
    chatting: dict[int, float] = {}

    def _chat_tools(*, account_runs: bool = True) -> dict[str, chat.Tool]:
        """Read-only desk lookups. No order tool; nothing reads the broker account. ``account_runs=False``
        (#775, legacy: another account's chat) leaves out the runs that read it, as GET /api/runs does."""
        def keep(d: dict | None, *keys: str) -> dict:
            return {k: (d or {}).get(k) for k in keys}

        async def macro_brief(_a: dict):
            # #428: status first (it leads the one-line summary), so an empty answer says why it is empty
            cache = macro_news.CACHE
            b = cache.brief()
            status = cache.status()
            note = {"warming": "재기동 직후 첫 수집 중(몇 분 걸림) - 아직 테마 없음",
                    "error": f"수집 실패: {b.get('error')}"}.get(status)
            if status == "ok" and cache.restored:
                note = (f"재기동 전 저장된 스냅샷(갱신 {b.get('refreshed_at')}), "
                        + (f"이후 수집 실패: {b.get('error')}" if b.get("error") else "새 수집 진행 중"))
            return {"status": status, "note": note, "error": b.get("error"),
                    "refreshed_at": b.get("refreshed_at"), "as_of": b.get("as_of"), "themes": [
                        {**keep(t, "label", "tone", "spike", "articles_24h", "quiet", "stale", "fetched_at"),
                         "headlines": [h.get("title") for h in (t.get("headlines") or [])[:2]]}
                        for t in (b.get("themes") or [])[:8]]}

        async def market_risk(a: dict):
            if risk is None:
                return {"error": "시장 투매 점수를 쓸 수 없는 환경"}
            return await asyncio.to_thread(risk, clean_ticker(a.get("ticker") or "SPY"))

        def as_of() -> str:                     # #432: when a lookup read its numbers (KST)
            return datetime.now(market_calendar.KST).isoformat(timespec="seconds")

        async def calendar_week(_a: dict):
            today = datetime.now(market_calendar.KST).date()
            end = today + timedelta(days=7)
            months = {today.strftime("%Y-%m"), end.strftime("%Y-%m")}
            days = []
            for month in sorted(months):
                view = await asyncio.to_thread(market_calendar.month_view, month)
                days += [{"date": d["date"], "kr_open": d["kr"]["open"], "us_open": d["us"]["open"],
                          "events": [keep(e, "label", "at", "importance") for e in d["events"]],
                          "earnings": [keep(r, "ticker", "name", "when") for r in d["earnings"][:8]]}
                         for d in view["days"] if today.isoformat() <= d["date"] <= end.isoformat()]
            return {"as_of": as_of(), "days": days}

        async def quote(a: dict):
            ticker = clean_ticker(a["ticker"])
            q = await prices.quote(ticker)
            return {"ticker": ticker, "price": q.price, "currency": q.currency, "source": q.source, "at": q.fetched_at}

        async def levels(a: dict):
            sr = await support_resistance(prices, clean_ticker(a["ticker"]))
            return {**keep(sr, "last_close", "atr", "as_of"), "levels": (sr or {}).get("levels", [])[:8]}

        async def playbooks(a: dict):
            pb = await playbook_live.current(prices, clean_ticker(a["ticker"]), a.get("horizon"), risk=risk)
            side_keys = ("setup", "reason", "zone", "entries", "stop", "targets", "score", "direction")
            # #432: as_of is the playbooks' own computation time; volume_note says today's volume is
            # still forming (not a whole day to hold against the 20-day average)
            at = pb.get("as_of")
            at = datetime.fromisoformat(at).astimezone(market_calendar.KST).isoformat(timespec="seconds") if at else None
            return {"as_of": at, **keep(pb, "volume_note", "summary", "direction"),
                    "long": keep(pb.get("long"), *side_keys), "short": keep(pb.get("short"), *side_keys)}

        async def bottom(a: dict):
            z = await asyncio.to_thread(bottoms.one, clean_ticker(a["ticker"]))
            return keep(z, "supported", "stage_ko", "score", "close", "as_of", "ladder", "invalidation", "error")

        async def recent_runs(a: dict):
            want = clean_ticker(a["ticker"]) if a.get("ticker") else None
            scope = None if account_runs else not_(account_run(run_rows.c.options))
            rows = [r for r in store.list_runs(40, public_only=protected, scope=scope)
                    if not want or r["ticker"] == want][:8]
            return [{**keep(r, "id", "ticker", "created_at", "status"),
                     **keep(r.get("plan"), "rating", "bias", "entry_price", "stop_loss", "price_target",
                            "levels_side", "horizon")} for r in rows]

        ticker_opt = {"type": "object", "properties": {"ticker": {"type": "string"}}}
        tools = [
            chat.Tool("macro_brief", "오늘의 거시 · 정책 뉴스 테마(톤 · 급증 · 대표 헤드라인)", chat.NO_ARGS, macro_brief),
            chat.Tool("market_risk", "시장 투매 · 위험 점수와 국면(지수 기준)", ticker_opt, market_risk),
            chat.Tool("calendar_week", "앞으로 7일 국장 · 미장 휴장 · 경제 발표 · 주요 실적", chat.NO_ARGS, calendar_week),
            chat.Tool("quote", "종목의 지금 가격과 출처 · 시각", chat.TICKER_ARG, quote),
            chat.Tool("levels", "종목의 일 · 주봉 지지 · 저항 레벨과 ATR", chat.TICKER_ARG, levels),
            chat.Tool("playbooks", "종목의 지금 롱 · 숏 플레이북(구역 · 분할 진입 · 손절 · 목표)과 요약 결론",
                      chat.TICKER_ARG, playbooks),
            chat.Tool("bottom_zone", "종목의 장기 바닥(인생 타점) 단계 · 점수 · 분할 매수 사다리", chat.TICKER_ARG, bottom),
            chat.Tool("recent_runs", "데스크의 최근 분석 결과(등급 · 방향 · 진입 · 손절 · 목표)", ticker_opt, recent_runs),
        ]
        async def entry(a: dict):                  # #312
            horizon = a.get("horizon") if a.get("horizon") in horizons.HORIZONS else None
            got = await entry_now.entry_now(prices, clean_ticker(a["ticker"]), horizon, risk=risk, bottoms=bottoms)
            return keep(got, "price_now", "price_at", "best", "other", "stand_aside", "bottom", "basis")

        tools.append(chat.Tool("entry_now", "지금가 기준 최적 타점(롱 · 숏 중 계획당 기대 R 이 큰 쪽, 관망 이유)",
                               chat.TICKER_ARG, entry))

        async def news(a: dict):                   # #403: out of the desk - the query alone, results cut
            if settings.fake_runs:                 # no network in a scripted session
                return web_search.scripted(a.get("query"))
            return await web_search.search(a.get("query"), a.get("window"))

        tools.append(chat.Tool("news_search", "최근 뉴스 기사 검색(제목 · 매체 · 게시 시각 · 링크, 본문 없음). "
                               "검색어는 짧은 주제어로, 개인 · 계좌 정보는 넣지 않는다", web_search.ARGS, news))
        return {t.name: t for t in tools}

    chat_tools = _chat_tools()
    app.state.chat_tools = chat_tools     # what the model may look up (tests check it stays read-only)
    # protected mode lists proven public runs only, whoever asks
    others_chat_tools = chat_tools if run_reader is not None else _chat_tools(account_runs=False)

    @app.get("/api/chat/models")
    def chat_models_of():
        return chat_models

    def require_chat_provenance() -> None:
        # Thread histories can contain tool output stored before scoped reads.
        if owners is not None:
            raise HTTPException(403, "chat history is unavailable", headers=_RUN_READ_CACHE)

    @app.get("/api/chat/threads")
    def chat_threads_of(request: Request):
        require_chat_provenance()
        return {"items": chats.threads(request.state.user.id)}

    @app.post("/api/chat/threads", status_code=201)
    def chat_new_thread(body: ChatThreadIn, request: Request):
        require_chat_provenance()
        try:
            return chats.create(request.state.user.id, body.title)
        except chat.TooMany as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/chat/threads/{thread_id}", status_code=204)
    def chat_delete_thread(thread_id: str, request: Request):
        require_chat_provenance()
        chats.delete(request.state.user.id, thread_id)       # another account's: nothing, the same 204

    @app.get("/api/chat/threads/{thread_id}/messages")
    def chat_messages_of(thread_id: str, request: Request):
        require_chat_provenance()
        user_id = request.state.user.id
        items = chats.messages(user_id, thread_id, chat.READ_LAST)
        if items is None:
            raise HTTPException(404, "thread not found")
        return {"thread": chats.get(user_id, thread_id), "items": items}

    @app.post("/api/chat/threads/{thread_id}/messages")
    async def chat_send(thread_id: str, body: ChatMessageIn, request: Request):
        """The answer as SSE: token · tool_call · tool_result · done · error. One answer at a time per
        account; during the runs' rate-limit cooldown (the same model plan) a 429 says when."""
        require_chat_provenance()
        user_id = request.state.user.id
        model = body.model or chat_models["default"]
        if body.model is not None and body.model not in {m["id"] for m in chat_models["items"]}:
            raise HTTPException(422, "model not offered")
        thread = chats.get(user_id, thread_id)
        if thread is None:
            raise HTTPException(404, "thread not found")
        tools = chat_tools if others_chat_tools is chat_tools or owns(request) else others_chat_tools
        manager = getattr(request.app.state, "manager", None)
        wait = manager.rate_limit_wait() if manager is not None else 0.0
        if wait > 0:
            minutes = max(1, round(wait / 60))
            return JSONResponse({"detail": f"모델 속도 제한 중 — 약 {minutes}분 뒤 다시"}, status_code=429,
                                headers={"Retry-After": str(int(wait))})
        clock = asyncio.get_running_loop().time
        started = chatting.get(user_id)
        if started is not None and clock() - started < chat.ANSWER_TIMEOUT_SEC + 30:
            raise HTTPException(409, "an answer is still being written")
        chatting[user_id] = clock()
        try:
            earlier = chats.messages(user_id, thread_id, chat.HISTORY + 1) or []
            dropped = len(earlier) > chat.HISTORY
            title = body.text.strip().splitlines()[0][:40] if not earlier and thread["title"] == "새 대화" else None
            mine = chats.add(user_id, thread_id, "user", body.text, title=title)
        except LookupError:
            chatting.pop(user_id, None)
            raise HTTPException(404, "thread not found") from None
        except chat.TooMany as exc:
            chatting.pop(user_id, None)
            raise HTTPException(409, str(exc)) from None
        except Exception:
            chatting.pop(user_id, None)
            raise
        messages = chat.build_messages(earlier[-chat.HISTORY:], body.text, body.ticker, dropped)
        token = request.cookies.get(COOKIE)
        queue: asyncio.Queue = asyncio.Queue()

        async def work() -> None:
            try:
                if not await asyncio.to_thread(policy.current):
                    return
                text, used = await asyncio.wait_for(chatter(messages, tools, queue.put_nowait, model=model),
                                                    chat.ANSWER_TIMEOUT_SEC)
                saved = chats.add(user_id, thread_id, "assistant", text or "(빈 답)", used, model=model)
                queue.put_nowait({"type": "done", "message_id": saved["id"], "user_message_id": mine["id"],
                                  "model": saved["model"]})
            except PolicyChanged:
                queue.put_nowait(None)   # let the stream close without returning saved content
            except Exception as exc:  # noqa: BLE001 - the page gets a fixed word, the log the detail
                log.warning("chat answer in %s failed: %s", thread_id, exc)
                limited = manager is not None and manager.note_rate_limit(f"chat:{thread_id}", exc)
                queue.put_nowait({"type": "error", "message": "모델 속도 제한 — 잠시 뒤 다시 물어봐 주세요" if limited
                                  else "답을 만들지 못했습니다"})

        async def frames():
            task = asyncio.create_task(work())
            checked = asyncio.get_running_loop().time()
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), 5.0)
                    except TimeoutError:
                        event = None
                    if not await asyncio.to_thread(policy.current):
                        return
                    loop_now = asyncio.get_running_loop().time()
                    if event is None or loop_now - checked >= 5.0:
                        # docs/security.md §2: an open stream re-checks the session within 5 s
                        checked = loop_now
                        if await asyncio.to_thread(auth.resolve, token, touch=False) is None or await request.is_disconnected():
                            return
                        if event is None:
                            yield ": ping\n\n"
                            continue
                    yield f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event["type"] in ("done", "error"):
                        return
            finally:
                task.cancel()
                chatting.pop(user_id, None)

        return StreamingResponse(frames(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    app.include_router(chart_memos.create_router(engine))

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
            raw = ticker.strip().upper()
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        favorites.remove(request.state.user.id, ticker)
        if raw != ticker:   # #477: a favorite saved as a bare code before the codes got their suffix
            favorites.remove(request.state.user.id, raw)

    # --- #154 short selling & options positioning ------------------------------------------

    option_details = options_detail.OptionsDetail(options_provider)
    funding = funding_history if funding_history is not None else binance_funding.FundingHistory(
        enabled=not settings.fake_runs)

    @app.get("/api/positioning/funding")
    async def ticker_funding(symbol: str, days: int = 30):
        """#912 Public, settled USDⓈ-M funding history."""
        try:
            return await funding.read(symbol, days)
        except ValueError:
            raise HTTPException(422, "funding requires a supported symbol and days between 1 and 30") from None

    @app.get("/api/positioning/options")
    async def ticker_option_details(ticker: str, expiration: str | None = None, strike_min: float | None = None,
                                    strike_max: float | None = None):
        """#887 Detailed option counts for one US expiration from a configured source."""
        try:
            ticker = clean_ticker(ticker)
            if not positioning.is_us_ticker(ticker):
                raise ValueError("options detail requires a US underlying ticker")
            if expiration is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", expiration):
                raise ValueError("expiration must be YYYY-MM-DD")
            day = date.fromisoformat(expiration) if expiration is not None else None
            return await option_details.get(ticker, day, strike_min=strike_min, strike_max=strike_max)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

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
        """US short and options positioning, or domestic KRX data under ``kr_short``."""
        try:
            ticker = clean_ticker(ticker)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if kr_short_sales.is_kr_ticker(ticker):
            domestic = await short_sales.get(ticker)
            status = domestic["status"]
            reason = status if status in {"source_unavailable", "unsupported"} else None
            body = positioning.blank(ticker, supported=status != "unsupported", reason=reason)
            if domestic["trade"]["latest"] is not None or domestic["balance"]["latest"] is not None:
                body["sources"] = ["krx"]
            return {**body, "kr_short": domestic}
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

    # --- sector tabs (#156): per account, ordered ---------------------------------------------

    def _sector_body(name: str | None, items: list[FavoriteIn] | None):
        try:
            clean = clean_name(name) if name is not None else None
            tickers = (clean_tickers([(i.ticker, i.name) for i in items], listing_name)
                       if items is not None else None)
        except ValueError as exc:   # TooMany too: the body itself is over the cap
            raise HTTPException(422, str(exc)) from None
        return clean, tickers

    def _sector_call(fn, *args):
        try:
            return fn(*args)
        except NotFound as exc:
            raise HTTPException(404, str(exc)) from None
        except (TooMany, Duplicate, Conflict) as exc:
            raise HTTPException(409, str(exc)) from None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.get("/api/sectors/movers")
    async def sector_movers_view(sector: str = "sox"):
        """#680 the sector's move since the last close and the stocks that drove it (weight x return), with
        the "주범" sentence on a big day (±2%) - approximate weights (desk/data/sector_weights.json)."""
        if sector not in sector_movers.SECTORS:
            raise HTTPException(422, f"sector must be one of {', '.join(sector_movers.SECTORS)}")
        hit = movers_cache.get(sector)
        if hit is not None:
            return hit
        got = await sector_movers.read(sector, prices, mover_causes, mover_name)
        movers_cache.put(sector, got)
        return got

    @app.get("/api/sectors")
    def list_sectors(request: Request):
        """The account's sector tabs in order; a new account gets the default sectors."""
        return _sector_call(sectors.list, request.state.user.id)

    @app.post("/api/sectors", status_code=201)
    def create_sector(body: SectorIn, request: Request):
        name, tickers = _sector_body(body.name, body.tickers)
        return _sector_call(sectors.create, request.state.user.id, name, tickers)

    @app.put("/api/sectors/order")       # before /{sector_id}: "order" is not an id
    def reorder_sectors(body: SectorOrderIn, request: Request):
        return _sector_call(sectors.reorder, request.state.user.id, body.ids)

    @app.put("/api/sectors/{sector_id}")
    def update_sector(sector_id: Annotated[int, FastPath(ge=1, le=2**31 - 1)], body: SectorPatch, request: Request):
        name, tickers = _sector_body(body.name, body.tickers)
        return _sector_call(sectors.update, request.state.user.id, sector_id, name, tickers)

    @app.delete("/api/sectors/{sector_id}", status_code=204)
    def delete_sector(sector_id: Annotated[int, FastPath(ge=1, le=2**31 - 1)], request: Request):
        _sector_call(sectors.remove, request.state.user.id, sector_id)

    @app.get("/api/regime")
    async def regime_view(ticker: str | None = None):
        """semibot's live regime when it is connected (the PC), and #409 the desk's own regime always -
        on the server semibot does not run, so ``desk`` is what the card shows. Fields are only added.
        #694 with ``ticker``: that ticker's regime instead - trend · range · turning on the daily and the
        hourly bars, with its numbers and box (desk.regime_live.view)."""
        if ticker is not None:
            try:
                ticker, _ = resolve_ticker(ticker)
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from None
            return await regime_live.view(prices, ticker)
        out = regime.snapshot(settings.semibot_db_path)
        out["source"] = "semibot" if out.get("enabled") else "desk"
        out["desk"] = await asyncio.to_thread(regime.desk_view, risk, tip_cache.regime)
        return out

    _push_routes(app, engine, settings, hide_holding_alerts=protected, send_notice=send_notice)
    _notify_routes(app, engine, settings, owners=owners, current_resources=current_resources,
                   active_session=active_session, send_notice=send_notice)
    weekly_alerts = nq_weekly_alerts.Service(
        nq_weekly_alerts.Ledger(engine, policy), prices, getattr(app.state, "notify_store", None), current_resources,
    )
    nq_weekly_alerts.register(app, weekly_alerts)
    # outermost (added last): the session check and every other middleware count in the time
    app.add_middleware(req_timing.TimingMiddleware, timings=timings, loop_max=lag.max_since)
    return app


# --- #361 web push · foreign-flow turn alerts (kept at the end: #359 edits this file too) ---

class PushKeysIn(BaseModel):
    p256dh: str = Field(min_length=1, max_length=200)
    auth: str = Field(min_length=1, max_length=100)


class PushKindsIn(BaseModel):
    flow: bool = True               # every market's turns (the master switch)
    flow_futures: bool = True
    flow_kospi: bool = True
    flow_kosdaq: bool = True
    morning_brief: bool = True      # #571 the 07:00 morning brief
    flow_shift: bool = True         # #579 the investors' flow shifts (desk.market_alerts)
    nq_alert: bool = True           # #579 the NQ=F selling alarms
    flow_warn: bool = True          # #699 the flow warnings (retail alone, heavy selling)
    day_call: bool = True           # #572 each market's first direction call of the day
    line_signal: bool = True        # #613 channel / trendline signals (the watch itself is off until set)
    holding_alert: bool = True      # #694 each holding's stop / take-profit (desk.holding_watch)
    sector_mover: bool = True       # #680 a sector's big day and the stocks that drove it (desk.sector_movers)
    program_alert: bool = True      # #743 program trading buy / sell turns (desk.program_flow)
    # #641 the flow / NQ=F alerts by tone (desk.alert_tone)
    tone_positive: bool = True
    tone_neutral: bool = True
    tone_caution: bool = True
    tone_warning: bool = True


class PushSubscriptionIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)
    keys: PushKeysIn
    device: str = Field(default="", max_length=80)
    kinds: PushKindsIn = Field(default_factory=lambda: PushKindsIn())

    @field_validator("device")
    @classmethod
    def _printable(cls, v: str) -> str:
        return "".join(ch for ch in v if ch.isprintable())      # no control characters into the logs

    @field_validator("endpoint")
    @classmethod
    def _push_service(cls, v: str) -> str:
        from .push import allowed_endpoint

        if not allowed_endpoint(v):
            raise ValueError("not a browser push service endpoint")
        return v


class PushEndpointIn(BaseModel):
    endpoint: str = Field(min_length=1, max_length=1024)


def _push_routes(app: FastAPI, engine, settings: Settings, *, hide_holding_alerts: bool = False,
                 send_notice=None) -> None:
    """Subscriptions are the caller's own only. ``app.state.pusher`` is read per request (tests swap it)."""
    from . import flow_alerts, market_alerts, push

    store = push.PushStore(engine)
    app.state.pusher = push.Pusher(store, settings.vapid_public_key, settings.vapid_private_key,
                                   settings.vapid_subject)
    app.state.flow_alerts = flow_alerts.FlowAlerts(engine, app.state.pusher, send_notice=send_notice)
    app.state.market_alerts = market_alerts.MarketAlerts(
        engine, app.state.pusher, send_notice=send_notice, hide_holdings=hide_holding_alerts,
    )

    @app.get("/api/push/key")
    def push_key():
        sender = app.state.pusher
        return {"enabled": sender.enabled, "public_key": sender.public_key if sender.enabled else None}

    @app.post("/api/push/subscriptions", status_code=201)
    def push_subscribe(body: PushSubscriptionIn, request: Request):
        if not app.state.pusher.enabled:
            raise HTTPException(503, "push is off")
        try:
            return store.subscribe(request.state.user.id, body.endpoint, body.keys.model_dump(),
                                   body.device.strip() or "기기",
                                   body.kinds.model_dump(exclude_unset=True))   # #641 left out = as stored
        except (push.TooMany, push.Taken) as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/push/subscriptions", status_code=204)
    def push_unsubscribe(body: PushEndpointIn, request: Request):
        store.unsubscribe(request.state.user.id, body.endpoint)     # another account's: nothing, the same 204

    @app.post("/api/push/test")
    def push_test(request: Request):
        sender = app.state.pusher
        if not sender.enabled:
            raise HTTPException(503, "push is off")
        return {"sent": sender.to_user(request.state.user.id,
                                       {"title": "트레이딩 데스크", "body": "시험 알림입니다.", "tag": "test"})}

    @app.get("/api/alerts/flow")
    def flow_alert_list(date: str | None = None):
        if date is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise HTTPException(422, "date: YYYY-MM-DD")
        day = date or datetime.now(futures_kst).date().isoformat()
        return {"date": day, "items": app.state.flow_alerts.list(day)}

    @app.get("/api/alerts/today")
    def alerts_today(request: Request, date: str | None = None):
        """#579 the day's alert timeline (KST, default today), oldest first: the foreign turns (#361) and
        the flow shifts / NQ=F alarms (desk.market_alerts) in one shape. #581 the holdings' stop / take-profit
        alerts (#694) only for the owner - they are pushed to the owner's devices only."""
        if date is not None and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
            raise HTTPException(422, "date: YYYY-MM-DD")
        day = date or datetime.now(futures_kst).date().isoformat()
        turns = [{"trade_date": a["trade_date"], "kind": "flow_turn", "rule": a["kind"], "market": a["market"],
                  "who": "foreign", "severity": "caution", "t": a["t"], "value": a["value"], "prev": None,
                  "text": a["text"], "pushed": True} for a in app.state.flow_alerts.list(day)]
        # #740 a sector's big day (#680) is the market's; a holding's alerts (#694) its owner's only
        hidden = set()
        if hide_holding_alerts or request.state.user.id != app.state.market_alerts.owner():
            hidden.add(holding_watch.KIND)
        items = turns + [a for a in app.state.market_alerts.list(day) if a["kind"] not in hidden]
        for a in items:     # #694 the % against the average stays in GET /api/holding-watch (docs/holding-watch.md)
            if a["kind"] == holding_watch.KIND:
                a["value"] = a["prev"] = None
        for a in items:     # #641 the direction beside the size, worked out from what each one stores
            a["tone"] = alert_tone.tone(a)
        items.sort(key=lambda a: datetime.fromisoformat(a["t"]))
        return {"date": day, "items": items}


class NotifyChannelIn(BaseModel):
    enabled: bool | None = None
    kinds: dict[str, bool] | None = None


class LineWatchRuleIn(BaseModel):
    ticker: str
    enabled: bool = False
    timeframes: list[str] = Field(default_factory=list, max_length=len(line_signals.TIMEFRAMES))
    events: dict[str, bool] = Field(default_factory=dict, max_length=len(line_signals.EVENTS))
    side: Literal["both", "support", "resistance"] = "both"


class LineWatchIn(BaseModel):
    """#613 the channel / trendline signal watch (desk.line_signals)."""
    enabled: bool = False
    tickers: list[str] = Field(default_factory=list, max_length=line_signals.MAX_TICKERS)
    timeframes: list[str] = Field(default_factory=list, max_length=len(line_signals.TIMEFRAMES))
    events: dict[str, bool] = Field(default_factory=dict, max_length=len(line_signals.EVENTS))
    rules: list[LineWatchRuleIn] | None = Field(default=None, max_length=line_signals.MAX_TICKERS)


class HoldingTickerIn(BaseModel):
    enabled: bool = True
    max_loss_pct: float | None = Field(default=None, ge=holding_watch.MAX_LOSS_RANGE[0], le=holding_watch.MAX_LOSS_RANGE[1])


class HoldingWatchIn(BaseModel):
    """#694 the holding stop / take-profit watch (desk.holding_watch)."""
    enabled: bool = True
    max_loss_pct: float = Field(default=holding_watch.MAX_LOSS_PCT, ge=holding_watch.MAX_LOSS_RANGE[0],
                                le=holding_watch.MAX_LOSS_RANGE[1])
    tickers: dict[str, HoldingTickerIn | None] = Field(default_factory=dict, max_length=holding_watch.MAX_TICKERS)


# #631 GET /api/chart-read: kept until the shortest timeframe's bar ends, within these bounds
CHART_READ_MIN_SEC = 15.0
CHART_READ_MAX_SEC = 300.0
CHART_READ_WEEKS = 120

# #986 Telegram only: a row another channel left in notify_channels is never listed, set or tested (404)
NOTIFY_LABELS = {"telegram": "텔레그램"}
NOTIFY_TEST_GAP_SEC = 5.0


def _notify_routes(app: FastAPI, engine, settings: Settings, *, owners: AccountOwnerStore | None = None,
                   current_resources, active_session, send_notice) -> None:
    """#580 Telegram beside web push (desk.notify_channels), sent through desk.notify.

    One set of channels for the desk (the alerts go to every account's devices, as web push does), so
    only the owner - the first account - changes them or tests them; others read."""
    from . import notify_channels as nc
    from . import push
    from .auth import owner_id as account_owner
    from .db import users

    nc.install_log_filter()
    if owners is None:
        store = nc.ChannelStore(engine)
        base = settings.public_url if settings.public_url.startswith("https://") else ""
        telegram = nc.Telegram(settings.telegram_bot_token, settings.telegram_chat_id, base)
        notify.install([telegram], store)
        app.state.notify_store = store
    last_test = {"at": 0.0}

    def owner_id() -> int | None:
        if owners is None:
            return account_owner(engine)
        binding = owners.binding()
        return binding.user_id if binding is not None else None

    def must_own(request: Request) -> None:
        if request.state.user.id != owner_id():
            raise HTTPException(403, "only the desk's owner changes the alert channels")

    def view(name: str) -> dict:
        # connected · needs_reconnect · refresh_expires_at stay in the answer the settings screen reads (#986)
        ch, row = notify.channel(name), store.get(name)
        return {"id": name, "label": NOTIFY_LABELS[name], "configured": ch.configured, "enabled": bool(row["enabled"]),
                "kinds": {k: bool((row["kinds"] or {}).get(k, True)) for k in push.KINDS},
                "connected": ch.configured, "needs_reconnect": False, "refresh_expires_at": None}

    def approved_destination(name: str, request: Request):
        approved = current_resources()
        if approved is None or approved.binding.user_id != request.state.user.id:
            return None
        return next((d for d in approved.destinations if d.name == name), None)

    def require_legacy_channel_settings(request: Request):
        if owners is not None:
            raise HTTPException(403, "channel settings unavailable", headers=_RUN_READ_CACHE)
        must_own(request)

    def known(name: str) -> str:
        if name not in NOTIFY_LABELS:
            raise HTTPException(404, "no such channel")
        return name

    @app.get("/api/notify/channels")
    def notify_channels_list(request: Request, response: Response):
        if owners is None:
            return {"kinds": list(push.KINDS), "can_edit": request.state.user.id == owner_id(),
                    "channels": [view(n) for n in NOTIFY_LABELS]}
        response.headers.update(_RUN_READ_CACHE)
        channels = []
        for name, label in NOTIFY_LABELS.items():
            destination = approved_destination(name, request)
            channels.append({"name": name, "label": label, "configured": destination is not None,
                             "enabled": destination is not None, "kinds": {
                                 kind: destination is not None and kind in destination.kinds for kind in push.KINDS
                             }})
        return {"kinds": list(push.KINDS), "can_edit": False, "channels": channels}

    @app.put("/api/notify/channels/{name}")
    def notify_channel_set(name: str, body: NotifyChannelIn, request: Request):
        require_legacy_channel_settings(request)
        known(name)
        unknown = sorted(set(body.kinds or {}) - set(push.KINDS))
        if unknown:
            raise HTTPException(422, f"unknown kinds: {', '.join(unknown)}")
        store.set_prefs(name, body.enabled, body.kinds)
        return view(name)

    @app.post("/api/notify/channels/{name}/test")
    async def notify_channel_test(name: str, request: Request):
        must_own(request)
        known(name)
        destination = approved_destination(name, request)
        if owners is not None and destination is None:
            raise HTTPException(403, "channel unavailable", headers=_RUN_READ_CACHE)
        moment = time.monotonic()
        if moment - last_test["at"] < NOTIFY_TEST_GAP_SEC:
            raise HTTPException(429, "one test every few seconds", headers={"Retry-After": "5"})
        last_test["at"] = moment
        payload = {"title": "트레이딩 데스크", "body": "시험 알림입니다.", "url": "/#/settings"}
        if owners is not None:
            def deliver() -> bool:
                if not active_session(request) or approved_destination(name, request) is not destination:
                    return False
                destination.deliver(payload)
                return active_session(request) and approved_destination(name, request) is destination

            try:
                sent = await asyncio.to_thread(deliver)
            except Exception as exc:  # noqa: BLE001 - destination errors may contain identifiers
                log.warning("approved destination test failed: %s", type(exc).__name__)
                return {"ok": False, "error": "delivery failed"}
            if not sent:
                raise HTTPException(403, "channel unavailable", headers=_RUN_READ_CACHE)
            return {"ok": True, "error": None}
        try:
            error = await asyncio.to_thread(notify.test, name, payload, allowed=lambda: active_session(request))
        except nc.ChannelError as exc:         # the worker busy past the wait: it may still go later
            error = str(exc)
        return {"ok": error is None, "error": error}

    holdings_watch = holding_watch.HoldingWatch(engine)
    app.state.holding_watch = holdings_watch

    def holding_view(request: Request) -> dict:
        """#762 the owner's holdings are the owner's: another account gets the default setting and no lines -
        not which tickers are held, their per-ticker limits or their returns, nor that any exist."""
        mine = owners is None and request.state.user.id == owner_id()
        return {**(holdings_watch.settings() if mine else holding_watch.clean(dict(holding_watch.DEFAULT))),
                "can_edit": mine,
                "limits": {"max_loss_pct": list(holding_watch.MAX_LOSS_RANGE), "max_tickers": holding_watch.MAX_TICKERS},
                "holdings": list(holdings_watch.last.values()) if mine else []}

    @app.get("/api/holding-watch")
    def holding_watch_get(request: Request):
        """#694 the holding watch: its setting and each holding's lines on the last pass (ticker, regime,
        stop, take profit, %) - no quantity, no amount."""
        return holding_view(request)

    @app.put("/api/holding-watch")
    def holding_watch_set(body: HoldingWatchIn, request: Request):
        must_own(request)
        if owners is not None:
            raise HTTPException(403, "historical account data is unavailable", headers=_RUN_READ_CACHE)
        try:
            given = body.model_dump(exclude_unset=True)     # what is left out keeps its value
            if "tickers" in given:
                given["tickers"] = {clean_ticker(t): v for t, v in given["tickers"].items()}
            holdings_watch.save(given)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return holding_view(request)

    def line_owner() -> line_signals.SignalOwner | None:
        if owners is None:
            with engine.connect() as conn:
                account = conn.execute(select(users.c.id, users.c.password_hash).order_by(users.c.id).limit(1)).first()
            return line_signals.SignalOwner.legacy(account.id, account.password_hash) if account is not None else None
        binding = owners.binding()
        if binding is None or binding.user_id is None:
            return None
        return line_signals.SignalOwner(binding.user_id, binding.generation)

    def send_line_notice(pusher, *kinds, payload, owner=None):
        owner = owner if owner is not None else line_owner()
        if owner is None:
            return 0
        return send_notice(pusher, *kinds, payload=payload, user_id=owner.user_id,
                           still_owner=lambda: line_owner() == owner)

    watch = line_signals.LineWatch(engine, send_notice=send_line_notice, owner_snapshot=line_owner)
    app.state.line_watch = watch

    def watch_view(request: Request, ticker: str | None = None) -> dict:
        owner = line_owner()
        can_edit = owner is not None and request.state.user.id == owner.user_id
        if can_edit:
            config, recent = watch.settings(), watch.recent(owner=owner)
            can_edit = active_session(request) and owner == line_owner()
        if not can_edit:
            config = {**line_signals.DEFAULT, "tickers": [], "timeframes": [], "rules": []}
            recent = []
        return {**config, "can_edit": can_edit,
                "allowed": {"timeframes": list(line_signals.TIMEFRAMES), "events": list(line_signals.EVENTS),
                            "max_tickers": line_signals.MAX_TICKERS,
                            "sides": ["both", "support", "resistance"],
                            "support": line_signals.allowed_support(ticker)},
                "limits": {"cooldown_min": line_signals.COOLDOWN_MIN, "cooldown_bars": line_signals.COOLDOWN_BARS,
                           "daily_max": line_signals.DAILY_MAX, "per_ticker_max": line_signals.PER_TICKER_MAX},
                "recent": recent}

    @app.get("/api/notify/line-watch")
    def line_watch_get(request: Request, response: Response, ticker: str | None = None,
                       signal_id: int | None = Query(default=None, ge=1, le=2_147_483_647)):
        """#613 the watch (off until the owner turns it on), its limits and the signals sent lately."""
        response.headers["Cache-Control"] = "private, no-store"
        if signal_id is not None:
            owner = line_owner()
            if owner is None or request.state.user.id != owner.user_id:
                return {"signal": None}
            signal = watch.chart_signal(signal_id, owner)
            return {"signal": signal if active_session(request) and owner == line_owner() else None}
        try:
            symbol = clean_ticker(ticker) if ticker is not None else None
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        return watch_view(request, symbol)

    @app.put("/api/notify/line-watch")
    def line_watch_set(body: LineWatchIn, request: Request, response: Response):
        must_own(request)
        try:
            given = body.model_dump(exclude_unset=True)     # what is left out keeps its value
            if "tickers" in given:
                given["tickers"] = [clean_ticker(t) for t in body.tickers]
            for rule in given.get("rules") or []:
                rule["ticker"] = clean_ticker(rule["ticker"])
            watch.save(given)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        response.headers["Cache-Control"] = "private, no-store"
        return watch_view(request)


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
