"""HTTP API. Run with: uvicorn desk.api:create_app --factory --host 0.0.0.0 --port 8000

Every route needs a login session except the two in OPEN_PATHS. See docs/security.md."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import Callable
from contextlib import asynccontextmanager, suppress
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Annotated
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as FastPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, Field, field_validator, model_validator

from . import (
    backtest_reports,
    bottom_zone,
    calibration,
    chat,
    credit,
    earnings,
    earnings_preview,
    entry_now,
    events_calendar,
    explain,
    fear,
    filings,
    flow_intraday,
    funds13f,
    futures_quote,
    horizons,
    line_basis,
    live,
    macro_news,
    market_calendar,
    news_digest,
    oscillator,
    otp,
    outcome,
    outlook,
    playbook_live,
    positioning,
    primary_trend,
    projection,
    regime,
    reports,
    review,
    risk_regime,
    sector_news,
    sessions,
    tips,
    verdict,
    web_search,
    weekly_map,
)
from .auth import COOKIE, Auth, Busy, LoginThrottle, User, hash_token
from .broker import kis_stream, make_broker
from .consensus import fake_summariser, llm_summariser
from .db import make_engine, migrate, now
from .estimate import CANDLE_INTERVALS, BinanceEstimator, naver_overtime
from .favorites import Conflict, Favorites, TooMany
from .futures_flow import KST as futures_kst
from .futures_flow import FuturesFlow, futures_open
from .futures_flow import make as make_kis_rest
from .futures_flow import off_reason as futures_off
from .futures_flow import prompt_note as futures_note
from .futures_history import FuturesHistory, closing_window, interval_min, seconds_to_next
from .indicators import normalize as normalize_indicators
from .leverage_analyst import fake_analyst, llm_analyst
from .longshort import LongShort
from .market_flow import MarketFlow
from .portfolio import Portfolio
from .prices import INTERVALS, PriceSource
from .profile import PROFILES
from .related import collect as collect_related
from .runner import ANALYSTS, Hub, RunManager, RunRequest, make_fake_runner, run_tradingagents
from .sectors import Duplicate, NotFound, Sectors, clean_name, clean_tickers
from .settings import Settings, load
from .sr import frame_bars, support_resistance
from .stock_profile import MAX_TICKERS as PROFILE_MAX
from .stock_profile import Profiles
from .store import TERMINAL, Store, iso
from .universe import KR_SUFFIX, Universe

MAX_BOARD = 30          # tickers per /api/estimates call
CALENDAR_INTERVAL_SEC = 6 * 3600   # economic calendar re-read (#148)
POSITIONING_PARALLEL = 3   # Cboe chains are ~2 MB each (#154)
POSITIONING_QUOTE_SEC = 5.0
CREDIT_INTERVAL_SEC = 6 * 3600   # KOFIA posts the day's numbers the next business day (#155)
CREDIT_RETRY_SEC = 15 * 60
BOARD_PARALLEL = 6      # concurrent Binance / quote lookups per call
FUNDS_TICKERS_MAX = 30   # #483 tickers per /api/funds/changes call (the favorites' badges)
# #240: the bottom-zone line of the weekly note waits this long (the scalp budget is 5 s; the line is
# skipped, not the weekly map, when a cold cache is slower)
BOTTOM_NOTE_SEC = 4.0
BOTTOM_QUOTE_SEC = 2.0   # #310: the price the bottom-zone ladder is cut at, per lookup
TREND_WAIT_SEC = 8.0     # #460: /api/tips waits this long for the indices' trend, then answers without it
GAPS_TTL_SEC = 300.0     # #465: a ticker's unfilled gaps are kept as long as its swing levels (desk.horizons sr_ttl_sec)
# #257: US pre / after / overnight trades on the board - a last trade this fresh still counts as live
# (thin ETFs trade minutes apart off hours), and a newly watched ticker waits this long for its first
OFF_HOURS_FRESH_SEC = 600.0
# #250: a broker quote off hours counts as a trade only this far (as a fraction) off the last close
OFF_HOURS_MOVE = 0.0001
FIRST_TICK_WAIT_SEC = 2.0
FIRST_TICK_POLL_SEC = 0.25
FIRST_TICK_RETRY_SEC = 600.0     # a ticker the board waited for is not waited for again before this
KST = timezone(timedelta(hours=9))   # Toss dates (order history from/to) are KST
REVIEW_CACHE_SEC = 60
REVIEW_FAIL_CACHE_SEC = 30
log = logging.getLogger(__name__)
NL2 = "\n\n"   # ends an SSE frame
HEARTBEAT_SEC = 15
OPEN_PATHS = frozenset({"/api/health", "/api/auth/login"})
UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


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


SPOT_TIMEOUT_SEC = 10.0      # #359 the spot flows fetched with each futures snapshot


def create_app(settings: Settings | None = None, runner=None, prices: PriceSource | None = None,
               estimator: BinanceEstimator | None = None, book: Portfolio | None = None,
               flow: MarketFlow | None = None, futures: FuturesFlow | None = None,
               weekly: weekly_map.Maps | None = None, bottoms: bottom_zone.Zones | None = None,
               longshort: LongShort | None = None, chatter: chat.Chatter | None = None) -> FastAPI:
    settings = settings or load()
    migrate(settings.database_url)
    engine = make_engine(settings.database_url)
    store = Store(engine)
    futures_history = FuturesHistory(engine)    # #254
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
    # #495 the beginner reports (made-up prices with scripted runs: no network)
    # (scripted runs: no automatic reports, so no "next due" either)
    report_desk = reports.Reports(engine, reports.fake_fetch if settings.fake_runs else reports.yahoo_fetch,
                                  0 if settings.fake_runs else settings.report_every_days)
    report_tasks: set[asyncio.Task] = set()

    async def make_report(auto: bool, taken: bool = False) -> str:
        """One report (#495): ``made`` · ``none`` (no subject had data) · ``busy`` (one being written, or -
        automatic only - no free ChatGPT slot: try later; a manual one is written in the desk's words)."""
        if not taken:                    # ``taken``: the caller already holds the one-at-a-time flag
            if report_desk.generating:
                return "busy"
            report_desk.generating = True
        try:
            if settings.fake_runs:
                out = await asyncio.to_thread(report_desk.make, None)
            else:
                if auto:                 # a run going: no price fetches either, try later
                    async with app.state.manager.spare_slot() as free:
                        if not free:
                            return "busy"
                got = await asyncio.to_thread(report_desk.choose)     # the fetches, without holding the slot
                if got is None:
                    return "none"
                async with app.state.manager.spare_slot() as free:  # the slot only around the one model call
                    if not free and auto:
                        return "busy"
                    out = await asyncio.to_thread(report_desk.finish, got, projection.llm_note if free else None)
        finally:
            report_desk.generating = False
        if out is None:
            return "none"
        log.info("report %s on %s (%s)", out["id"], out["subject"]["symbol"], out["source"])
        try:                             # a push problem never loses the report
            owner = await asyncio.to_thread(report_desk.owner)
            if owner is not None:        # the owner only: the subject may be one of the owner's favorites
                await asyncio.to_thread(app.state.pusher.to_user, owner, {
                    "title": out["title"], "body": out.get("summary") or "", "tag": "report",
                    "url": f"/#/reports/{out['id']}"})
        except Exception as exc:  # noqa: BLE001
            log.warning("report push failed: %s", exc)
        return "made"
    sectors = Sectors(engine)
    hub = Hub()
    broker = make_broker(settings)
    kis_feed = kis_stream.make(settings) if prices is None else None   # tests bring their own prices
    prices = prices or PriceSource(broker, stream=kis_feed)
    # #462: VIX · CNN / crypto Fear & Greed · VKOSPI, cached per source (fakes stay offline in tests)
    fear_gauges = (fear.Fear(candles=lambda *a: prices._yahoo_candles(*a),   # read when asked, as risk does
                             kis=lambda: getattr(futures, "rest", None))         # #486 VKOSPI via the futures KIS client
                   if not settings.fake_runs else None)
    # #146: the market's selling score from the same Yahoo source as the prices (cached ~15 min)
    risk = None if settings.fake_runs else risk_regime.Cache(
        fetcher=lambda symbols: risk_regime.fetch(symbols, candles=prices._yahoo_candles)).current
    # #330: rate / dollar regime and its tips (^IRX ^TNX DX-Y.NYB QQQ, cached 3 h)
    tip_cache = tips.Cache(fetcher=lambda symbols: risk_regime.fetch(symbols, candles=prices._yahoo_candles,
                                                                     count=tips.HISTORY_BARS))
    funds = funds13f.Funds()   # #483 the well-known funds' 13F holdings (SEC EDGAR), refreshed by refresh_funds
    # #460: the indices' primary trend (^KS11 ^NDX ^GSPC ^SOX, about ten years of dailies, cached 3 h)
    trend_cache = primary_trend.Cache(fetcher=lambda symbols: risk_regime.fetch(
        symbols, candles=prices._yahoo_candles, count=primary_trend.HISTORY_BARS))
    gap_cache: dict[str, tuple[float, list[dict]]] = {}   # #465: ticker -> (when, desk.line_basis.find_gaps)
    # #203: years of Yahoo bars for the RSI / Stochastic extremes and what followed them
    # (read when called: a test's price source may have no Yahoo at all)
    oscillator_fetch = oscillator.yahoo_fetch(lambda *args: prices._yahoo_candles(*args))
    estimator = estimator or BinanceEstimator(overtime=naver_overtime)   # #283: KRX after-hours, live only
    # #271: Binance futures traders' long / short split, on the estimate's own contract mapping
    longshort = longshort or LongShort(lambda ticker: estimator.resolve(ticker))   # read when asked (test fakes)
    book = book or Portfolio(broker, prices)
    profiles = Profiles(prices, universe.lookup)   # #295 the stock card
    flow = flow or MarketFlow(broker)
    # KOSPI200 futures from KIS REST (#160); without the KIS keys it answers supported: false
    futures = futures or FuturesFlow(make_kis_rest(settings) if not settings.fake_runs else None, flow,
                                     off=futures_off(settings))
    # #443 day / night session price and minute bars on the same KIS client (one token)
    futures_quotes = futures_quote.FuturesQuote(getattr(futures, "rest", None), off=futures_off(settings))

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
    if runner is None:
        runner = make_fake_runner(settings.fake_delay) if settings.fake_runs else run_tradingagents

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

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        stale = store.fail_unfinished(RESTART_REASON)
        if stale:
            log.warning("marked %d unfinished run(s) as failed", stale)
        # Scripted runs keep levels on the script's numbers instead of fetching a live quote.
        app.state.manager = RunManager(store, hub, runner, broker, settings.max_concurrent_runs,
                                       prices=None if settings.fake_runs else prices,
                                       # a scripted run stays offline, like its support/resistance
                                       related=None if settings.fake_runs else collect_related,
                                       lever_analyst=fake_analyst if settings.fake_runs else llm_analyst,
                                       summariser=fake_summariser if settings.fake_runs else llm_summariser,
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
                                       # #202: the index map behind a leveraged / sector ticker (same day)
                                       weekly=None if settings.fake_runs else _weekly_note,
                                       risk=risk,
                                       # #171: recent news/fundamentals reports taken over (0 = off)
                                       reuse_minutes=settings.reuse_reports_min,
                                       rate_limit_cooldown_min=settings.rate_limit_cooldown_min)

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
            fresh = True                                  # the live record is read once at boot
            while True:
                try:
                    scored = await outcome.sweep(store, prices)
                    if scored:
                        log.info("scored %d plan(s)", scored)
                    if scored or fresh:
                        # #147: the playbooks' live record on top of the backtest prior
                        since = now() - timedelta(days=calibration.LIVE_DAYS)
                        runs_scored = await asyncio.to_thread(store.scored_since, since)
                        calibration.TABLE.set_live(runs_scored)
                        fresh = False
                except Exception:  # a failed pass must not stop the next one
                    log.exception("plan scoring failed")
                await asyncio.sleep(settings.score_interval_sec)

        async def refresh_macro():
            # GDELT allows one request every five seconds: a full pass takes a couple of minutes,
            # so it runs here in the background and runs read the snapshot (desk.macro_news).
            # #428: the pass saved before a restart answers until the first one here finishes
            if await asyncio.to_thread(macro_news.CACHE.load):
                log.info("macro news: last saved snapshot loaded")
            while True:
                try:
                    ok = await macro_news.CACHE.refresh()
                    log.info("macro news: %d/%d theme(s) refreshed", ok, len(macro_news.THEMES))
                    # #242: Korean titles and a market read, only while no analysis holds the plan
                    read = await news_digest.DIGEST.refresh(macro_news.CACHE.themes, slot=app.state.manager.spare_slot)
                    if read:
                        log.info("news digest: %d headline(s) read", read)
                except Exception:  # a failed pass must not stop the next one
                    log.exception("macro news refresh failed")
                try:                # #244: sector / key-person surges, read the same way
                    ok = await sector_news.CACHE.refresh()
                    read = await news_digest.DIGEST.refresh(sector_news.CACHE.as_themes(),
                                                            slot=app.state.manager.spare_slot)
                    log.info("sector news: %d topic(s) refreshed, %d headline(s) read", ok, read)
                except Exception:  # a failed pass must not stop the next one
                    log.exception("sector news refresh failed")
                await asyncio.sleep(settings.macro_interval_sec)

        async def refresh_calendar():
            # #148: FOMC + BLS release dates change rarely; a few reads a day keep them current
            # #308: the earnings saved before a restart show until the first pass answers
            if await asyncio.to_thread(earnings.CACHE.load):
                log.info("earnings calendar: last saved rows loaded")
            while True:
                try:
                    ok = await events_calendar.CACHE.refresh()
                    log.info("economic calendar: %d/2 source(s) refreshed", ok)
                except Exception:  # a failed pass must not stop the next one
                    log.exception("economic calendar refresh failed")
                try:                # #243: company earnings for this month and the next
                    ok = await earnings.CACHE.refresh()
                    log.info("earnings calendar: %d day(s)/ticker(s) refreshed", ok)
                except Exception:
                    log.exception("earnings calendar refresh failed")
                # #308: a failed earnings pass comes back sooner (30 min, 1 h, 2 h, 4 h)
                await asyncio.sleep(earnings.CACHE.next_delay(CALENDAR_INTERVAL_SEC))

        async def refresh_credit():
            # #155: margin loans and investor deposits (KOFIA) - a few reads a day catch the new day
            while True:
                ok = False
                try:
                    ok = await credit.CACHE.refresh()
                    log.info("KOFIA credit: %s", "refreshed" if ok else f"failed ({credit.CACHE.error})")
                except Exception:  # a failed pass must not stop the next one
                    log.exception("KOFIA credit refresh failed")
                # a failure (KOFIA slow at boot) is retried soon: the cache lives in memory only
                await asyncio.sleep(CREDIT_INTERVAL_SEC if ok else CREDIT_RETRY_SEC)

        async def snapshot_futures():
            # #254: the futures investor flows through the session, every DESK_FUTURES_SNAPSHOT_MIN
            # minutes (desk.futures_history); outside the session it only checks the clock
            # on the wall clock's grid from 08:45, plus one closing snapshot just after 15:45 (the
            # day's final totals: its daily row); each one fetches fresh, never a cached pre-open value
            pruned_on, closed_on = None, None
            every = interval_min(settings.futures_snapshot_min)
            while True:
                try:
                    moment = datetime.now(UTC)
                    today_kst = moment.astimezone(futures_kst).date()
                    if futures_open(moment) or (closing_window(moment) and closed_on != today_kst):
                        view = await futures.get(fresh=True)
                        # #359 the spot flows at the same moment (a broker failure is an ``error`` body)
                        # (bounded: a hung broker must not cost the futures snapshot)
                        try:
                            got = await asyncio.wait_for(asyncio.gather(
                                flow.get("KOSPI", "1d", 1), flow.get("KOSDAQ", "1d", 1)), SPOT_TIMEOUT_SEC)
                            spot = {"kospi": got[0], "kosdaq": got[1]}
                        except TimeoutError:
                            log.warning("spot flow for the futures snapshot timed out")
                            spot = None
                        await asyncio.to_thread(futures_history.record, view, None, spot)
                        try:                     # #361: a push problem never stops the snapshots
                            await asyncio.to_thread(app.state.flow_alerts.after_snapshot, futures_history.intraday)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("flow turn alerts failed: %s", exc)
                        if closing_window(moment):
                            closed_on = today_kst
                    today = datetime.now(UTC).date()
                    if pruned_on != today:
                        dropped = await asyncio.to_thread(futures_history.prune)
                        if dropped:
                            log.info("futures history: thinned %d old snapshot(s)", dropped)
                        pruned_on = today
                except Exception:  # a failed pass must not stop the next one
                    log.exception("futures snapshot failed")
                await asyncio.sleep(seconds_to_next(datetime.now(UTC), every))

        refresher = asyncio.create_task(keep_listing_fresh())
        futures_task = (asyncio.create_task(snapshot_futures())
                        if interval_min(settings.futures_snapshot_min) > 0
                        and getattr(futures, "rest", None) is not None else None)
        # the same switch as the news flow: off in tests and scripted runs
        credit_task = (asyncio.create_task(refresh_credit())
                       if settings.macro_interval_sec > 0 and not settings.fake_runs else None)
        macro = (asyncio.create_task(refresh_macro())
                 if settings.macro_interval_sec > 0 and not settings.fake_runs else None)
        # the same switch as the news flow: off in tests and scripted runs
        calendar_task = (asyncio.create_task(refresh_calendar())
                         if settings.macro_interval_sec > 0 and not settings.fake_runs else None)

        async def refresh_funds():
            # #483: the funds' filings every few hours around the 45-day deadline, else daily
            while True:
                try:
                    ok = await asyncio.to_thread(funds.refresh)
                    log.info("13F: %d/%d fund(s) refreshed", ok, len(funds13f.FUNDS))
                except Exception:  # a failed pass must not stop the next one
                    log.exception("13F refresh failed")
                await asyncio.sleep(funds.next_delay())

        async def write_reports():
            # #495: one report every DESK_REPORT_EVERY_DAYS days after the Korean close; while a run holds
            # the ChatGPT slot it waits and tries again; a failure (no prices, the model, the DB) tries again
            # later and later (30 min doubling to 8 h) so a lasting fault never burns the quota or Yahoo
            failures = 0
            while True:
                wait = (report_desk.next_due() - datetime.now(UTC)).total_seconds()
                if wait > 0:
                    await asyncio.sleep(min(wait, 3600))
                    continue
                try:
                    got = await make_report(auto=True)
                except Exception:  # a failed pass must not stop the next one
                    log.exception("report failed")
                    got = "failed"
                if got == "made":
                    failures = 0
                elif got == "busy":
                    await asyncio.sleep(reports.RETRY_SEC)
                else:
                    failures += 1
                    await asyncio.sleep(reports.RETRY_SEC * 2 ** min(failures - 1, 4))

        report_task = (asyncio.create_task(write_reports())
                       if settings.report_every_days > 0 and not settings.fake_runs else None)
        funds_task = (asyncio.create_task(refresh_funds())
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
        if futures_task:
            futures_task.cancel()
        if macro:
            macro.cancel()
        if calendar_task:
            calendar_task.cancel()
        if funds_task:
            funds_task.cancel()
        if report_task:
            report_task.cancel()
        for task in list(report_tasks):  # a manual report still being written
            task.cancel()
        if credit_task:
            credit_task.cancel()
        if scorer:
            scorer.cancel()
        if purger:
            purger.cancel()
        await estimator.aclose()
        await longshort.aclose()
        await futures.aclose()
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
        # #437: the look ahead frozen when the run ended (an ``outlook`` event), None before / without one
        return {**run, "outlook": store.last_event_data(run_id, "outlook")}

    explain_locks: dict[str, asyncio.Lock] = {}

    @app.get("/api/runs/{run_id}/explain")
    async def run_explain(run_id: str, refresh: bool = False):
        """#493: the analysis for a beginner (desk.explain) - written once, kept as the run's ``explain``
        event and reused; ``refresh=1`` writes it again. The model words it only while a run slot is
        free; otherwise (or when it fails) the desk's own wording comes back, not kept, so a later call
        tries again."""
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(404, "run not found")
        if run.get("status") != "done" or not run.get("plan"):
            raise HTTPException(409, "the analysis has no plan yet")
        lock = explain_locks.setdefault(run_id, asyncio.Lock())
        async with lock:
            kept = None if refresh else store.last_event_data(run_id, "explain")
            if kept:
                return kept
            fear_items = None
            if fear_gauges is not None:
                try:
                    fear_items = (await asyncio.wait_for(fear_gauges.get(), 10))["items"]
                except Exception:  # noqa: BLE001 - the explanation goes without the market mood
                    fear_items = None
            if settings.fake_runs:
                out = await asyncio.to_thread(explain.build, run, fear_items, None)
            else:
                async def work():
                    async with app.state.manager.spare_slot() as free:
                        return free, await asyncio.to_thread(explain.build, run, fear_items,
                                                             projection.llm_note if free else None)
                # shielded: a client that goes away mid-call must not free the slot while the model call runs
                free, out = await asyncio.shield(asyncio.create_task(work()))
                if not free:
                    return {**out, "pending": True}
            if out.get("fallback"):
                return out
            await asyncio.to_thread(store.append_event, run_id, {"type": "explain", "agent": "explain", "content": None,
                                                                  "data": out})
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

    @app.get("/api/market/credit")
    def market_credit(days: int = 90):
        """#155: Korean margin loans (신용융자), investor deposits (투자자예탁금), their ratio (빚투 비율)
        with its 1-year percentile, and forced sales (KOFIA, refreshed in the background)."""
        return credit.CACHE.view(min(max(days, 7), 400))

    @app.get("/api/market/fear")
    async def market_fear():
        """#462: the fear gauges (desk.fear); a failed source is that item's ``error``, never a 5xx."""
        if fear_gauges is None:
            return {"items": [], "error": "fake runs: no outside requests"}
        return await fear_gauges.get()

    @app.get("/api/market/flow")
    async def market_flow(market: str = "KOSPI", interval: str = "1d", count: int = 20):
        """KOSPI / KOSDAQ trading value by investor class, newest first (desk.market_flow). Read-only;
        a broker failure comes back as ``error`` with no records, not as a 5xx."""
        try:
            return await flow.get(market.upper(), interval, count)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

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
        against the previous settlement (day) or the day's close (night)."""
        if session is not None and session not in futures_quote.SESSIONS:
            raise HTTPException(422, "session must be day or night")
        try:
            return await futures_quotes.quote(session)
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
        try:
            body = flow_intraday.view(points, bucket)
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
        (and > 200 bars): Yahoo - for KRX minute bars (5m~240m) with their late tail rebuilt from
        Toss 1m (source "yahoo+toss", #379; buckets count from 09:00 KST, ``at`` = bucket start). ``indicators=rsi,stoch,bb`` (or rsi14, bb20, bb50) adds each bar's
        RSI(14), Stochastic (14, 1, 3) and Bollinger Bands (20 / 50, 2σ) - desk.indicators (#203)."""
        try:
            raw = ticker.strip().upper()
            ticker, guessed = resolve_ticker(ticker)
            if interval not in INTERVALS:
                raise ValueError(f"interval must be one of {', '.join(INTERVALS)}")
            wanted = normalize_indicators(indicators.split(","))
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        try:
            got = await prices.candles(ticker, interval, count, wanted)
        except Exception as exc:  # noqa: BLE001 - both sources failed
            if not guessed:
                log.warning("candles for %s %s failed: %s", ticker, interval, exc)
                raise HTTPException(503, f"no candles for {ticker}") from None
            got = None
        if guessed and not (got or {}).get("bars"):
            # #477: a code the list does not know was guessed KOSPI; empty there, try KOSDAQ once
            try:
                kq = await prices.candles(raw + ".KQ", interval, count, wanted)
            except Exception as exc:  # noqa: BLE001 - neither market
                log.warning("candles for %s (.KS / .KQ) failed: %s", raw, exc)
                kq = None
            if kq and kq.get("bars"):
                if len(learned_kr) > 4096:      # KRX lists about 2,700 codes; never grows past a bad loop
                    learned_kr.clear()
                learned_kr[raw] = ".KQ"
                return kq
            if got is None:
                raise HTTPException(503, f"no candles for {ticker}") from None
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
    async def levels(ticker: str, horizon: str | None = None, run: str | None = None):
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
                found = store.get_run(run)
                if not found:
                    raise HTTPException(404, "no such run")
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
        gap_rows = line_basis.place(found_gaps[1], sr["reference_price"])
        lines = line_basis.basis((found or {}).get("plan"), sr, gap_rows) if found else []
        # a new dict: the levels are a cached object shared with other callers
        return {**sr, "gaps": gap_rows, "gaps_error": gaps_error, "basis": lines,
                "explain": line_basis.explain(sr, gap_rows, lines)}

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
        return {**out, "trend": trend or [], "guide": primary_trend.guide(), "trend_error": error}

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
            return await playbook_live.current(prices, ticker, horizon, run, run_pb,
                                               risk=risk)
        except Exception as exc:  # noqa: BLE001 - no bars, too few, or a source down
            log.warning("live playbooks for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no playbooks for {ticker}") from None

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
            return await entry_now.entry_now(prices, ticker, horizon, risk=risk, bottoms=bottoms)
        except Exception as exc:  # noqa: BLE001 - no bars, too few, or a source down
            log.warning("entry now for %s failed: %s", ticker, exc)
            raise HTTPException(503, f"no entry for {ticker}") from None

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
        await _first_trades(names)

        async def one(ticker: str) -> dict:
            async with gate:
                est, err = None, None
                try:
                    est = await estimator.estimate(ticker)
                except Exception as exc:  # noqa: BLE001 - Binance or Yahoo down for this one
                    err = type(exc).__name__
                    log.warning("board estimate for %s failed: %s", ticker, exc)
                # #250: one session answer; while anything trades the board shows a real price
                session = await sessions.session(calendar, ticker)
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

                if est and est.market_state == "nxt":
                    # #209: NXT trading (08:00-08:50, 15:40-20:00 KST) - its last trade is a real price
                    row["session"] = session if session in ("pre", "post") else "post"
                    return live_row(est.estimate, "nxt", est.fetched_at, est.change_pct)
                if session == "regular":
                    live.watch(prices, ticker)       # the board's tickers ride the KIS stream too
                    try:
                        q = await prices.quote(ticker)
                        return live_row(q.price, q.source, q.fetched_at)
                    except Exception as exc:  # noqa: BLE001 - fall back to the estimate below
                        log.warning("board quote for %s failed: %s", ticker, exc)
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

    chats = chat.ChatStore(engine)
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

    def _chat_tools() -> dict[str, chat.Tool]:
        """Read-only desk lookups. No order tool; nothing reads the broker account."""
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
            rows = [r for r in store.list_runs(40) if not want or r["ticker"] == want][:8]
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

    @app.get("/api/chat/models")
    def chat_models_of():
        return chat_models

    @app.get("/api/chat/threads")
    def chat_threads_of(request: Request):
        return {"items": chats.threads(request.state.user.id)}

    @app.post("/api/chat/threads", status_code=201)
    def chat_new_thread(body: ChatThreadIn, request: Request):
        try:
            return chats.create(request.state.user.id, body.title)
        except chat.TooMany as exc:
            raise HTTPException(409, str(exc)) from None

    @app.delete("/api/chat/threads/{thread_id}", status_code=204)
    def chat_delete_thread(thread_id: str, request: Request):
        chats.delete(request.state.user.id, thread_id)       # another account's: nothing, the same 204

    @app.get("/api/chat/threads/{thread_id}/messages")
    def chat_messages_of(thread_id: str, request: Request):
        user_id = request.state.user.id
        items = chats.messages(user_id, thread_id, chat.READ_LAST)
        if items is None:
            raise HTTPException(404, "thread not found")
        return {"thread": chats.get(user_id, thread_id), "items": items}

    @app.post("/api/chat/threads/{thread_id}/messages")
    async def chat_send(thread_id: str, body: ChatMessageIn, request: Request):
        """The answer as SSE: token · tool_call · tool_result · done · error. One answer at a time per
        account; during the runs' rate-limit cooldown (the same model plan) a 429 says when."""
        user_id = request.state.user.id
        model = body.model or chat_models["default"]
        if body.model is not None and body.model not in {m["id"] for m in chat_models["items"]}:
            raise HTTPException(422, "model not offered")
        thread = chats.get(user_id, thread_id)
        if thread is None:
            raise HTTPException(404, "thread not found")
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
                text, used = await asyncio.wait_for(chatter(messages, chat_tools, queue.put_nowait, model=model),
                                                    chat.ANSWER_TIMEOUT_SEC)
                saved = chats.add(user_id, thread_id, "assistant", text or "(빈 답)", used, model=model)
                queue.put_nowait({"type": "done", "message_id": saved["id"], "user_message_id": mine["id"],
                                  "model": saved["model"]})
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
                    loop_now = asyncio.get_running_loop().time()
                    if event is None or loop_now - checked >= 5.0:
                        # docs/security.md §2: an open stream re-checks the session within 5 s
                        checked = loop_now
                        if await asyncio.to_thread(auth.resolve, token) is None or await request.is_disconnected():
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
    async def regime_view():
        """semibot's live regime when it is connected (the PC), and #409 the desk's own regime always -
        on the server semibot does not run, so ``desk`` is what the card shows. Fields are only added."""
        out = regime.snapshot(settings.semibot_db_path)
        out["source"] = "semibot" if out.get("enabled") else "desk"
        out["desk"] = await asyncio.to_thread(regime.desk_view, risk, tip_cache.regime)
        return out

    _push_routes(app, engine, settings)
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


def _push_routes(app: FastAPI, engine, settings: Settings) -> None:
    """Subscriptions are the caller's own only. ``app.state.pusher`` is read per request (tests swap it)."""
    from . import flow_alerts, push

    store = push.PushStore(engine)
    app.state.pusher = push.Pusher(store, settings.vapid_public_key, settings.vapid_private_key,
                                   settings.vapid_subject)
    app.state.flow_alerts = flow_alerts.FlowAlerts(engine, app.state.pusher)

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
                                   body.device.strip() or "기기", body.kinds.model_dump())
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
