"""Running an analysis: the TradingAgents pipeline (or a scripted fake) in a worker
thread, with every new chat event written to the store as it happens."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field, replace
from typing import Any

from . import horizons, macro_news, playbook, timing, trade_map, verdict
from . import underlying as under
from .broker.base import Broker
from .consensus import CONSENSUS_TIMEOUT_SEC, ConsensusContext, llm_summariser
from .consensus import parse as parse_consensus
from .consensus import render as render_consensus
from .events import StateDiffer
from .levels import LEVELS, entry_vs_live, fill_levels, triggers_vs_live
from .leverage_analyst import (
    LEVER_TIMEOUT_SEC,
    LeverContext,
    llm_analyst,
    parse_holder_exit,
    parse_pair,
    strip_extras,
)
from .plan import BIAS, parse_plan
from .prices import CRYPTO_SUFFIXES, PriceSource, is_market_symbol
from .profile import desk_portfolio
from .recent import prompt_note as recent_note
from .related import collect as collect_related
from .related import inverse_pair
from .related import prompt_note as related_note
from .sr import frame_bars, live_note, prompt_note, support_resistance
from .store import Store

log = logging.getLogger(__name__)

ANALYSTS = ("market", "social", "news", "fundamentals")

Emit = Callable[[dict], None]


@dataclass
class RunRequest:
    ticker: str
    trade_date: str
    name: str | None = None
    # desk.profile.PROFILES key: the owner's style, handed to the deciding agents.
    profile: str | None = None
    # desk.horizons.HORIZONS key: how long the trade is meant to live. None (older runs) = swing.
    horizon: str | None = None
    analysts: list[str] = field(default_factory=lambda: list(ANALYSTS))
    use_portfolio: bool = False
    output_language: str | None = None
    max_debate_rounds: int | None = None
    max_risk_rounds: int | None = None

    def asset_type(self) -> str:
        return "crypto" if self.ticker.upper().endswith(CRYPTO_SUFFIXES) else "stock"

    def selected_analysts(self) -> list[str]:
        chosen = [a for a in ANALYSTS if a in self.analysts] or list(ANALYSTS)
        # Crypto, indices (^KS11), futures (NQ=F) and FX (KRW=X) have no company statements to read.
        # (desk.prices.is_market_symbol)
        no_statements = self.asset_type() == "crypto" or is_market_symbol(self.ticker)
        return [a for a in chosen if not (no_statements and a == "fundamentals")]



@dataclass
class RunResult:
    final_state: dict[str, Any]
    report_path: str | None = None


Runner = Callable[[RunRequest, Emit, Any], RunResult]


# --- the real pipeline ---------------------------------------------------------------

def run_tradingagents(request: RunRequest, emit: Emit, portfolio: Any) -> RunResult:
    """One TradingAgents run, the same steps the CLI takes, streamed through StateDiffer.

    Provider, models and keys come from the TRADINGAGENTS_* environment (see .env),
    so switching from the ChatGPT plan to an API key or Ollama is a config change.
    """
    from tradingagents.dataflows.config import run_config
    from tradingagents.default_config import build_default_config
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    config = build_default_config()
    if request.output_language:
        config["output_language"] = request.output_language
    if request.max_debate_rounds:
        config["max_debate_rounds"] = request.max_debate_rounds
    if request.max_risk_rounds:
        config["max_risk_discuss_rounds"] = request.max_risk_rounds

    graph = TradingAgentsGraph(selected_analysts=request.selected_analysts(), config=config)
    ticker, date, asset_type = request.ticker, request.trade_date, request.asset_type()
    differ = StateDiffer()
    final_state: dict[str, Any] = {}

    with run_config(graph.config):
        init_state = graph.create_run_state(ticker, date, asset_type, portfolio)
        args = graph.propagator.get_graph_args()
        thread_id = graph.begin_checkpoint(ticker, date, asset_type, portfolio)
        if thread_id is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = thread_id
        try:
            for messages, chunk in graph.stream_run(graph.checkpoint_input(init_state), **args):
                for event in differ.feed(messages, chunk):
                    emit(event)
                if chunk is not None:
                    final_state.update(chunk)
        finally:
            graph.end_checkpoint()
        graph.record_decision(ticker, date, final_state)
        graph.clear_checkpoint_on_success(ticker, date, asset_type, portfolio)
        report_path = graph.save_reports(final_state, ticker)
    return RunResult(final_state=final_state, report_path=str(report_path))


# --- a scripted run, for building the UI without spending quota ----------------------

# A made-up ATR per horizon so a scripted intraday plan has intraday-sized levels.
_FAKE_ATR = {"scalp": 0.4, "intraday": 0.9, "short_swing": 3.0}


def _fake_decision(request: RunRequest) -> dict:
    h = horizons.get(request.horizon)
    if h.id not in _FAKE_ATR:   # swing: the original script, unchanged
        text = ("**Rating**: Overweight\n\n**Executive Summary**: 182.5 부근 분할 진입, 171 손절, 목표 205.\n"
                f"{verdict.FAKE_SUMMARY_LINE}\n{playbook.FAKE_LINES}\n\n"
                f"**Investment Thesis**: 수주 모멘텀과 이익 성장, 밸류 부담은 비중으로 관리.\n{verdict.FAKE_THESIS_LINE}\n\n"
                "**Price Target**: 205\n\n**Time Horizon**: 2-4주")
        return {"final_trade_decision": text, "final_rating": "Overweight"}
    atr, entry = _FAKE_ATR[h.id], 182.5
    stop, target = round(entry - h.stop_atr * atr, 2), round(entry + h.target_atr * atr, 2)
    text = (f"**Rating**: Overweight\n\n**Executive Summary**: {entry} 눌림 지정가 진입. "
            f"Entry Price: {entry} / Stop Loss: {stop} / Price Target: {target}\n"
            f"Entry: limit / Invalidation: {stop} 아래 마감 / Exit by: {h.holding} 안에\n\n"
            f"{verdict.FAKE_SUMMARY_LINE}\n{playbook.FAKE_LINES}\n\n"
            f"**Investment Thesis**: {h.label} 기준 구조상 지지 위.\n{verdict.FAKE_THESIS_LINE}\n\n"
            f"**Price Target**: {target}\n\n**Time Horizon**: {h.holding}")
    return {"final_trade_decision": text, "final_rating": "Overweight"}


def make_fake_runner(delay: float) -> Runner:
    def run_fake(request: RunRequest, emit: Emit, portfolio: Any) -> RunResult:
        t = request.ticker
        known = portfolio is not None and getattr(portfolio, "book_known", True)
        held = f"\n\n보유 현황 반영: {portfolio.render(t)}" if known else ""
        steps: list[tuple[list, dict | None]] = []
        reports = {
            "market": ("market_report", f"## {t} 기술적 분석\n\n- 20일선 위, 50일선 지지 확인\n- RSI 61, 과열 아님\n- MACD 골든크로스 3일차"),
            "social": ("sentiment_report", f"## {t} 시장 심리\n\nStockTwits 강세 62% / 약세 21%. Reddit 언급량 전주 대비 +35%."),
            "news": ("news_report", f"## {t} 뉴스\n\n- 신규 데이터센터 수주 보도\n- 거시: 금리 동결 기대 유지"),
            "fundamentals": ("fundamentals_report", f"## {t} 재무\n\n| 항목 | 값 |\n|---|---|\n| 매출 성장(YoY) | +18% |\n| 영업이익률 | 31% |\n| PER | 34 |"),
        }
        for analyst in request.selected_analysts():
            key, text = reports[analyst]
            steps.append(([_FakeToolCall(f"{analyst}-tool", _TOOLS[analyst], {"symbol": t})], None))
            steps.append(([], {key: text}))

        bull = f"Bull Analyst: {t} 는 수주 모멘텀과 실적 가시성이 모두 살아 있습니다. 눌림에서 분할 매수가 유리합니다."
        bear = "Bear Analyst: PER 34배는 이미 기대를 반영합니다. 수주 지연 시 하방이 큽니다."
        bull2 = bull + "\nBull Analyst: 밸류에이션 부담은 이익 성장률로 상당 부분 상쇄됩니다."
        steps += [
            ([], {"investment_debate_state": {"bull_history": bull, "bear_history": ""}}),
            ([], {"investment_debate_state": {"bull_history": bull, "bear_history": bear}}),
            ([], {"investment_debate_state": {"bull_history": bull2, "bear_history": bear}}),
            ([], {"investment_plan": "**Recommendation**: Overweight\n\n**Rationale**: 성장 근거가 우세하나 밸류 부담으로 비중 확대에 그친다."}),
            ([], {"trader_investment_plan": (
                "**Action**: Buy\n\n**Reasoning**: 20일선 근처 분할 진입.\n\n"
                "**Entry Price**: 182.5\n\n**Stop Loss**: 171.0\n\n**Position Sizing**: 포트폴리오의 5%"
                + held + "\n\nFINAL TRANSACTION PROPOSAL: **BUY**")}),
            ([], {"risk_debate_state": {"aggressive_history": "Aggressive Analyst: 모멘텀 구간이라 8%까지 늘려도 됩니다.",
                                        "conservative_history": "", "neutral_history": ""}}),
            ([], {"risk_debate_state": {"aggressive_history": "Aggressive Analyst: 모멘텀 구간이라 8%까지 늘려도 됩니다.",
                                        "conservative_history": "Conservative Analyst: 실적 발표 전이므로 3%로 시작해야 합니다.",
                                        "neutral_history": ""}}),
            ([], {"risk_debate_state": {"aggressive_history": "Aggressive Analyst: 모멘텀 구간이라 8%까지 늘려도 됩니다.",
                                        "conservative_history": "Conservative Analyst: 실적 발표 전이므로 3%로 시작해야 합니다.",
                                        "neutral_history": "Neutral Analyst: 5%로 시작해 171 이탈 시 정리하는 안이 균형적입니다."}}),
            ([], _fake_decision(request)),
        ]

        differ = StateDiffer()
        final_state: dict[str, Any] = {}
        for messages, chunk in steps:
            time.sleep(delay)
            for event in differ.feed(messages, chunk):
                emit(event)
            if chunk:
                final_state.update(chunk)
        return RunResult(final_state=final_state)

    return run_fake


_TOOLS = {"market": "get_stock_data", "social": "get_stocktwits_posts", "news": "get_news",
          "fundamentals": "get_fundamentals"}


class _FakeToolCall:
    def __init__(self, msg_id: str, name: str, args: dict) -> None:
        self.id = msg_id
        self.content = ""
        self.tool_calls = [{"name": name, "args": args}]


# --- orchestration -------------------------------------------------------------------

async def _none() -> None:
    return None


def _background_note(read: Callable[[], str | None] | None, name: str) -> str | None:
    """A note read from a background cache; a failure leaves it out and never fails a run."""
    if read is None:
        return None
    try:
        return read()
    except Exception:
        log.exception("%s note failed", name)
        return None


class Hub:
    """Wakes SSE readers when a run gets a new event. Take ``listen()`` before reading
    the store, so an event written in between still wakes the reader."""

    def __init__(self) -> None:
        self._events: dict[str, asyncio.Event] = {}

    def listen(self, run_id: str) -> asyncio.Event:
        return self._events.setdefault(run_id, asyncio.Event())

    def notify(self, run_id: str) -> None:
        event = self._events.pop(run_id, None)
        if event is not None:
            event.set()


# The levels are a reference, not a prerequisite: a slow Toss (429 retries) or Yahoo must not
# hold the run — and the queue behind it — at "running" with nothing on screen.
SR_TIMEOUT_SEC = 20.0
# A leverage-analyst level further than this from the product's price is not a product price.
IMPLAUSIBLE = 0.5
# The same for the quote, now fetched before the run as well as after it.
QUOTE_TIMEOUT_SEC = 10.0
# And for the related instruments (desk.related): one Yahoo download plus a few news lists.
RELATED_TIMEOUT_SEC = 30.0


class RunManager:
    def __init__(self, store: Store, hub: Hub, runner: Runner, broker: Broker, max_concurrent: int,
                 levels=fill_levels, prices: PriceSource | None = None, related=collect_related,
                 lever_analyst=llm_analyst, summariser=llm_summariser,
                 macro: Callable[[str], str | None] | None = None,
                 filings: Callable[[str], Awaitable[str | None]] | None = None,
                 positioning: Callable[[str], Awaitable[str | None]] | None = None,
                 calendar: Callable[[], str | None] | None = None,
                 credit: Callable[[], str | None] | None = None) -> None:
        self.levels = levels
        # #148: recent SEC filings of a US company (desk.filings.note_for) and the economic calendar
        # (desk.events_calendar.prompt_note). Both read "now": same-day runs only. None leaves them out.
        self.filings = filings
        # #154: short volume and option positioning of a US ticker (desk.positioning.note_for), same day
        self.positioning = positioning
        self.calendar = calendar
        # #155: Korean margin debt vs investor deposits (desk.credit.prompt_note) - Korean tickers, same day
        self.credit = credit
        # #115: the world news-flow note (desk.macro_news.prompt_note) - None leaves it out
        self.macro = macro
        self.summariser = summariser
        self.lever_analyst = lever_analyst
        self.related = related
        self.prices = prices
        self.store = store
        self.hub = hub
        self.runner = runner
        self.broker = broker
        self._slots = asyncio.Semaphore(max(1, max_concurrent))
        self._tasks: set[asyncio.Task] = set()

    async def _playbooks(self, request: RunRequest, sr: dict | None, live: dict | None, h: horizons.Horizon,
                         related: dict | None, ticker: str) -> dict | None:
        """desk.playbook from the frame's bars (a cache hit right after the levels). None without
        prices or levels; a failure is logged and the run goes on without playbooks."""
        if self.prices is None or not sr:
            return None
        try:
            primary, secondary, _complete, _p, _s = await asyncio.wait_for(
                frame_bars(self.prices, ticker, request.trade_date, h), SR_TIMEOUT_SEC)
            daily = primary if h.primary == "1d" else secondary
            today = None
            if h.primary != "1d" and primary:
                day = primary[-1]["at"][:10]
                first = next(i for i, b in enumerate(primary) if b["at"][:10] == day)
                session = primary[first:]
                # only when the window reaches back past the session's start; else its "open" is
                # just the first bar fetched and a gap would be invented
                today = None if first == 0 else {"at": session[0]["at"], "open": session[0]["open"], "close": session[-1]["close"],
                         "high": max(b["high"] for b in session), "low": min(b["low"] for b in session),
                         "volume": sum(b.get("volume") or 0 for b in session)}
            vix = next((row.get("price") for row in (related or {}).get("instruments") or []
                        if row.get("ticker") == "^VIX"), None)
            macro = macro_news.CACHE.brief(ticker) if self.macro is not None else None
            return await asyncio.to_thread(playbook.build, sr, daily, primary, h, live=live, vix=vix,
                                           macro=macro, today=today)
        except Exception as exc:  # noqa: BLE001 - incl. timeout
            log.warning("playbooks for %s failed: %s", ticker, exc)
            return None

    async def _product_playbooks(self, request: RunRequest, lever, pb_main: dict | None, sr: dict | None,
                                 frame: horizons.Horizon, related: dict | None, live: dict | None,
                                 final_state: dict, starts: tuple, srs: tuple) -> dict | None:
        """The playbooks on the traded instrument's prices: the main ones for an ordinary run, the
        product's own bars for a leveraged one (the model's lines, in the underlying's prices, are
        converted through the same anchors as its levels, or set aside)."""
        parsed = playbook.parse((final_state or {}).get("final_trade_decision") or "")
        if not lever:
            return playbook.merge(playbook.refresh(pb_main, live), parsed)
        # the product's own frame: its levels may have fallen back to daily bars when the
        # underlying's did not (or the other way round)
        own = horizons.get(None) if sr and sr.get("fallback") else horizons.get(request.horizon)
        pb = await self._playbooks(request, sr, live, own, related, request.ticker)
        if not pb:
            return None
        anchor = under.anchors(*starts, *srs)
        leverage = lever[1]
        if leverage < 0:   # an inverse product: the underlying's long is the product's short
            bias = parsed.get("bias")
            flip = {"long": "short", "short": "long"}
            parsed = {**parsed, "long": parsed.get("short"), "short": parsed.get("long"),
                      "bias": {**bias, "side": flip.get(bias["side"], bias["side"]), "source": "converted"}
                      if bias else None}
        convert = (lambda p: under.to_product(p, anchor, leverage)) if anchor else (lambda _p: None)
        return playbook.merge(playbook.refresh(pb, live), parsed, convert=convert)

    def _calendar_note(self) -> str | None:
        return _background_note(self.calendar, "economic calendar")

    def _credit_note(self, ticker: str) -> str | None:
        if not ticker.upper().endswith((".KS", ".KQ")):
            return None
        return _background_note(self.credit, "KOFIA credit")

    def _macro_note(self, ticker: str) -> str | None:
        if self.macro is None:
            return None
        try:
            return self.macro(ticker)
        except Exception:  # background context; never fails a run
            log.exception("macro note for %s failed", ticker)
            return None

    def submit(self, request: RunRequest) -> str:
        run_id = self.store.create_run(request.ticker, request.trade_date, asdict(request))
        self._publish(run_id, {"type": "status", "agent": "system", "content": "queued"})
        task = asyncio.create_task(self._execute(run_id, request))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return run_id

    async def _live_quote(self, request: RunRequest, ticker: str | None = None) -> dict | None:
        """The current quote of ``ticker`` (default: the run's) for a same-day analysis; None for a
        past date or no price."""
        ticker = ticker or request.ticker
        if self.prices is None or request.trade_date != dt.date.today().isoformat():
            return None
        try:
            quote = await asyncio.wait_for(self.prices.quote(ticker), QUOTE_TIMEOUT_SEC)
        except Exception as exc:  # noqa: BLE001 - incl. timeout: levels then start from the last close
            log.warning("no live quote for %s: %s", ticker, exc)
            return None
        return {"price": quote.price, "source": quote.source, "fetched_at": quote.fetched_at}

    async def _pair_price(self, request: RunRequest, ticker: str) -> dict | None:
        """The pair's price for the leverage analyst and the check of its levels: live for a same-day
        analysis, else (or when the quote fails) the last daily close up to the analysis date."""
        live = await self._live_quote(request, ticker)
        if live or self.prices is None:
            return live
        try:
            bars = (await asyncio.wait_for(self.prices.candles(ticker, "1d", 30), QUOTE_TIMEOUT_SEC))["bars"]
        except Exception as exc:  # noqa: BLE001 - incl. timeout: no pair trade then
            log.warning("no close for pair %s: %s", ticker, exc)
            return None
        bars = [b for b in bars if str(b["at"])[:10] <= request.trade_date]
        return {"price": bars[-1]["close"], "source": "close", "fetched_at": bars[-1]["at"]} if bars else None

    async def _support_resistance(self, request: RunRequest, live: dict | None = None,
                                  ticker: str | None = None) -> dict | None:
        """Daily/weekly support and resistance of ``ticker`` (default: the run's) up to the analysis
        date, sided around the live price when there is one; None without prices."""
        ticker = ticker or request.ticker
        if self.prices is None:
            return None
        try:
            return await asyncio.wait_for(
                support_resistance(self.prices, ticker, request.trade_date,
                                   price=live["price"] if live else None, horizon=request.horizon),
                SR_TIMEOUT_SEC)
        except Exception as exc:  # noqa: BLE001 - incl. timeout: the analysis runs without the reference
            log.warning("no support/resistance for %s: %s", ticker, exc)
            return None

    async def _related(self, request: RunRequest) -> dict | None:
        """Underlying, peers, drivers and their headlines; None when Yahoo has nothing or is slow."""
        if self.related is None:
            return None
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(self.related, request.ticker, request.trade_date), RELATED_TIMEOUT_SEC)
        except Exception as exc:  # noqa: BLE001 - incl. timeout: the analysis runs without it
            log.warning("no related instruments for %s: %s", request.ticker, exc)
            return None

    async def _underlying_plan(self, sub: dict, request: RunRequest, sr_u: dict | None,
                               live_u: dict | None) -> dict:
        """The underlying side, ATR-filled from its own bars and checked against its own price. Without
        its support/resistance the model's levels still stand; missing ones come from Yahoo bars."""
        # The same horizon as the product, unless its levels fell back to daily bars: then its
        # last close and ATR are daily ones and only the swing multiples fit them.
        kwargs = {"live": live_u, "horizon": None if sr_u and sr_u.get("fallback") else request.horizon}
        if sr_u:
            kwargs["prices"] = lambda *_args: (sr_u["last_close"], sr_u["atr"])
        sub = await asyncio.to_thread(self.levels, sub, sub["ticker"], request.trade_date, **kwargs)
        sub = entry_vs_live(sub, sr_u and sr_u.get("atr"), sr_u and sr_u.get("last_close"))
        if sr_u:
            sub["support_resistance"] = sr_u
        return sub

    async def _leveraged_plan(self, run_id: str, request: RunRequest, lever: tuple, result: RunResult,
                              horizon, starts: tuple, lives: tuple, srs: tuple, related: dict | None) -> dict:
        """The product's plan from the underlying's run: the underlying side as analysed, then the
        leverage analyst's call on the product, its gaps filled by conversion and ATR. A failing
        analyst leaves the underlying's direction (flipped for inverse) and converted levels."""
        base, leverage, proxy = lever
        (start, start_u), (live, live_u), (sr, sr_u) = starts, lives, srs
        u_plan = parse_plan(result.final_state)
        u_plan.pop("underlying_levels", None)
        underlying = await self._underlying_plan({**u_plan, "ticker": base, "leverage": leverage, "proxy": proxy},
                                                 request, sr_u, live_u)
        daily = sr_u and (sr_u.get("fallback") or horizon.primary == "1d")
        pair = inverse_pair(request.ticker)
        live_pair = await self._pair_price(request, pair[0]) if pair else None
        if not live_pair:   # no price to check its levels against: no pair trade offered
            pair = None
        ctx = LeverContext(
            product=request.ticker, base=base, leverage=leverage, proxy=proxy, horizon=horizon.id,
            holding=horizon.holding_en, u_plan=u_plan,
            u_decision=result.final_state.get("final_trade_decision") or "", live=live, live_u=live_u,
            sr=sr, sr_u=sr_u, beta=(related or {}).get("tracking"),
            language=request.output_language, daily_atr_u=sr_u["atr"] if daily else None,
            intraday=horizon.flat_by_close,
            pair=pair and pair[0], pair_leverage=pair and pair[1], live_pair=live_pair)
        text, error = None, None
        expected = under.flip_rating(u_plan["rating"]) if leverage < 0 else u_plan["rating"]
        if self.lever_analyst is not None:
            # the rail shows who is working while the call runs
            self._publish(run_id, {"type": "status", "agent": "leverage_analyst", "content": "thinking"})
            try:   # the HTTP call has its own timeout; this is the backstop for the thread
                text = await asyncio.wait_for(asyncio.to_thread(self.lever_analyst, ctx), LEVER_TIMEOUT_SEC + 30)
            except Exception as exc:  # noqa: BLE001 - incl. timeout: the run ends on the underlying's call
                log.warning("leverage analyst failed for %s: %s", request.ticker, exc)
                error = f"{type(exc).__name__}: {exc}"
        if text and text.strip():
            self._publish(run_id, {"type": "message", "agent": "leverage_analyst", "content": text.strip(),
                                   "data": None})
            clean = strip_extras(text)   # the pair's and a holder's numbers are not the product's entry
            plan = parse_plan({"final_trade_decision": clean, "trader_investment_plan": clean})
            plan.pop("underlying_levels", None)
            if plan["rating"] == "REVIEW":   # '**Rating**: 매수 (Buy)', '**Buy**' ...
                loose = under.loose_rating(text)
                rating = loose or expected
                plan["rating"], plan["bias"] = rating, BIAS.get(rating, "review")
                if not loose:
                    plan["leverage_analyst_warning"] = "rating not readable; the underlying's call is used"
            # A level far from the product's price is the underlying's number in the wrong slot
            # ('Entry Price: 450' for a $60 product): set aside, conversion fills it.
            ref = (live or {}).get("price") or (sr or {}).get("last_close")
            for key in LEVELS:
                if ref and plan.get(key) is not None and abs(plan[key] - ref) > IMPLAUSIBLE * ref:
                    plan.setdefault("implausible_levels", {})[key] = plan[key]
                    plan[key] = None
        else:
            rating = expected
            plan = {"rating": rating, "bias": BIAS.get(rating, "review"), "trader_action": None,
                    "entry_price": None, "stop_loss": None, "price_target": None, "reward_risk": None,
                    "time_horizon": u_plan.get("time_horizon"), "position_sizing": None,
                    "executive_summary": None, "levels_origin": {},
                    "leverage_analyst_error": error or "no answer"}
        # Gaps in the analyst's levels: converted from the underlying's (anchor = the pre-run pair) —
        # only when the analyst kept the underlying's direction; a Sell SOXL must not get a long setup.
        kept = plan["bias"] == BIAS.get(expected) and plan["bias"] in ("long", "short")
        if kept:
            plan["underlying_levels"] = {k: u_plan[k] for k in LEVELS if u_plan.get(k) is not None}
        # #64's filter needs a checklist and a win probability: the research team scored the
        # underlying, so its checklist carries over (sides swapped for inverse) and, when the
        # analyst kept the direction and gave none, so does its probability.
        if plan.get("checklist") is None and u_plan.get("checklist"):
            plan["checklist"] = under.flip_checklist(u_plan["checklist"]) if leverage < 0 else u_plan["checklist"]
            score = u_plan.get("score") or {}
            plan["score"] = {"long": score.get("short"), "short": score.get("long")} if leverage < 0 else score
        if plan.get("win_probability") is None and (kept or not text):
            plan["win_probability"] = u_plan.get("win_probability")
        plan, _ = under.split(plan, base, leverage, proxy, under.anchors(start, start_u, sr, sr_u))
        plan = await asyncio.to_thread(self.levels, plan, request.ticker, request.trade_date, live=live,
                                       horizon=horizon.id)
        plan = entry_vs_live(plan, sr and sr.get("atr"), sr and sr.get("last_close"))
        plan["underlying"] = underlying
        plan["analysis_subject"] = base
        # #109: the bet against the product, bought in its pair; a holder's exit in the product's price
        if paired := parse_pair(text or "", ctx, plan.get("bias")):
            plan["pair"] = paired
        if (holder := parse_holder_exit(text or "", ctx)) is not None:
            plan["holder_exit"] = holder
        return plan

    async def _consensus(self, run_id: str, request: RunRequest, subject: str, final_state: dict,
                         plan: dict, price: float | None = None, book: Any = None) -> None:
        """#94: the closing summary into ``plan["consensus"]``; a failure only leaves
        ``consensus_error`` — the run still ends with its plan."""
        if self.summariser is None:
            return
        self._publish(run_id, {"type": "status", "agent": "consensus", "content": "thinking"})
        held = book.position_in(request.ticker) if book is not None else None
        position = {"quantity": held.quantity, "average_price": held.average_price} if held else None
        ctx = ConsensusContext(ticker=request.ticker, subject=subject, final_state=final_state, plan=plan,
                               language=request.output_language, price=price, position=position)
        try:   # the HTTP call has its own timeout; this is the backstop for the thread
            text = await asyncio.wait_for(asyncio.to_thread(self.summariser, ctx), CONSENSUS_TIMEOUT_SEC + 30)
            summary = parse_consensus(text, final_state, subject, plan, price, position)
        except Exception as exc:  # noqa: BLE001 - incl. timeout and unreadable JSON
            log.warning("consensus failed for %s: %s", request.ticker, exc)
            plan["consensus_error"] = f"{type(exc).__name__}: {exc}"[:300]
            # the rail and chat stop showing the summariser as working
            self._publish(run_id, {"type": "status", "agent": "consensus", "content": "skipped"})
            return
        plan["consensus"] = summary
        self._publish(run_id, {"type": "message", "agent": "consensus", "content": render_consensus(summary),
                               "data": None})

    def _publish(self, run_id: str, event: dict) -> None:
        self.store.append_event(run_id, event)
        self.hub.notify(run_id)

    async def _execute(self, run_id: str, request: RunRequest) -> None:
        async with self._slots:
            self.store.mark_running(run_id)
            # A leveraged/inverse product is analysed through its underlying (desk.underlying, #82):
            # TradingAgents reads SOXX, then the leverage analyst turns the call into one on SOXL.
            lever = under.leverage_of(request.ticker)
            base = lever[0] if lever else None
            subject = replace(request, ticker=base) if base else request
            self._publish(run_id, {"type": "status", "agent": "system", "content": "running",
                                   "data": {"analysts": subject.selected_analysts()}})
            loop = asyncio.get_running_loop()
            watch = timing.Stopwatch()       # #171: where the minutes go

            def emit(event: dict) -> None:   # called from the worker thread
                watch.message(event)
                loop.call_soon_threadsafe(self._publish, run_id, event)

            try:
                book = await self.broker.portfolio() if request.use_portfolio else None
                # The agents set levels against the price now, not only a close days old.
                start, start_u = await asyncio.gather(
                    self._live_quote(request), self._live_quote(request, base) if base else _none())
                same_day = request.trade_date == dt.date.today().isoformat()
                sr, sr_u, related, filings_note, positioning_note = await asyncio.gather(
                    self._support_resistance(request, start),
                    self._support_resistance(request, start_u, base) if base else _none(),
                    self._related(request),
                    self.filings(base or request.ticker) if self.filings and same_day else _none(),
                    self.positioning(base or request.ticker) if self.positioning and same_day else _none())
                # The run's own instrument: the underlying for a leveraged product.
                main_live, main_sr = (start_u, sr_u) if lever else (start, sr)
                lever_note = under.prompt_note(request.ticker, *lever) if lever else None
                macro_note = self._macro_note(base or request.ticker)
                calendar_note = self._calendar_note() if same_day else None
                credit_note = self._credit_note(base or request.ticker) if same_day else None
                horizon = horizons.get(request.horizon)
                frame = horizons.get(None) if main_sr and main_sr.get("fallback") else horizon
                # #116: both playbooks from the bars the levels came from - the run's own instrument
                # for the agents (the underlying of a leveraged product)
                pb_main = await self._playbooks(request, main_sr, main_live, frame, related, base or request.ticker)
                pb_note = playbook.prompt_note(pb_main)
                note = "\n\n".join(part for part in (live_note(main_live, main_sr and main_sr.get("atr")),
                                                     prompt_note(main_sr), recent_note((main_sr or {}).get("recent")),
                                                     pb_note, lever_note,
                                                     related_note(related), macro_note, calendar_note, credit_note,
                                                     filings_note, positioning_note)
                                   if part) or None
                mandate = horizons.horizon_mandate(horizon, main_sr and main_sr.get("atr"), frame=frame,
                                                   levels_below=bool(main_sr))
                # #64: the symmetric checklist and high-probability filter, swing included
                mandate = "\n\n".join(part for part in (mandate, verdict.mandate(horizon),
                                                       playbook.mandate() if pb_note else None) if part)
                portfolio = desk_portfolio(request.profile, book, note, horizon_mandate=mandate)
                watch.mark("desk_pre")
                result = await asyncio.to_thread(self.runner, subject, emit, portfolio)
                watch.mark("graph")
                # The model may still leave a level out; fill it from ATR and mark it as computed.
                fresh, fresh_u = await asyncio.gather(
                    self._live_quote(request), self._live_quote(request, base) if base else _none())
                live, live_u = fresh or start, fresh_u or start_u
                if lever:
                    plan = await self._leveraged_plan(run_id, request, lever, result, horizon,
                                                      (start, start_u), (live, live_u), (sr, sr_u), related)
                else:
                    plan = parse_plan(result.final_state)
                    plan.pop("underlying_levels", None)   # a model that wrote the line anyway
                    plan = await asyncio.to_thread(self.levels, plan, request.ticker, request.trade_date,
                                                   live=live, horizon=horizon.id)
                    plan = entry_vs_live(plan, sr and sr.get("atr"), sr and sr.get("last_close"))
                # The entry is valid from the price the agents saw, so the minutes spent analysing count.
                plan["horizon"] = horizon.id
                plan["valid_until"] = horizons.valid_until(horizon, start["fetched_at"] if start else None)
                # #151: a trigger beyond the horizon's reach is a watch level (the playbooks' cap)
                plan = triggers_vs_live(plan, sr and sr.get("atr"), playbook.MAX_ZONE_PCT[horizon.id])
                plan = verdict.apply(plan, horizon)   # opinion + filter; rating, bias and levels stay
                if sr:
                    plan["support_resistance"] = sr
                # #116: the product's playbooks (its own bars), the model's three lines merged when sane
                try:
                    plan_pb = await self._product_playbooks(request, lever, pb_main, sr, frame, related, live,
                                                            result.final_state, (start, start_u), (sr, sr_u))
                    if plan_pb:
                        plan["playbooks"] = plan_pb
                except Exception as exc:  # noqa: BLE001 - the plan stands without its playbooks
                    log.warning("no playbooks for %s: %s", request.ticker, exc)
                # #106: entry → targets → guards → stop from the plan, the zones and the volume profile
                try:
                    plan["trade_map"] = trade_map.build(plan, sr)
                except Exception as exc:  # noqa: BLE001 - the plan stands without its map
                    log.warning("no trade map for %s: %s", request.ticker, exc)
                if related:
                    plan["related"] = related
                price = (live or {}).get("price") or (sr or {}).get("last_close")
                watch.mark("desk_post")
                await self._consensus(run_id, request, subject.ticker, result.final_state, plan, price, book)
                watch.mark("consensus")
                plan["timing"] = watch.result()
                log.info("run %s %s timing: %s", run_id, request.ticker, watch.line())
                self.store.mark_done(run_id, plan, result.report_path)
                self._publish(run_id, {"type": "plan", "agent": "portfolio_manager", "data": plan})
                self._publish(run_id, {"type": "done", "agent": "system", "content": "done"})
            except Exception as exc:   # surface every failure to the page, not just the log
                log.exception("run %s failed", run_id)
                message = f"{type(exc).__name__}: {exc}"
                self.store.mark_failed(run_id, message)
                self._publish(run_id, {"type": "error", "agent": "system", "content": message})
                self._publish(run_id, {"type": "done", "agent": "system", "content": "failed"})
