"""Running an analysis: the TradingAgents pipeline (or a scripted fake) in a worker
thread, with every new chat event written to the store as it happens."""

from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from . import horizons, verdict
from . import underlying as under
from .broker.base import Broker
from .events import StateDiffer
from .levels import entry_vs_live, fill_levels
from .plan import parse_plan
from .prices import PriceSource
from .profile import desk_portfolio
from .related import collect as collect_related
from .related import prompt_note as related_note
from .sr import live_note, prompt_note, support_resistance
from .store import Store

log = logging.getLogger(__name__)

ANALYSTS = ("market", "social", "news", "fundamentals")
CRYPTO_SUFFIXES = ("-USD", "-USDT", "-USDC", "-BTC", "-ETH")

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
        # Crypto has no company statements to read.
        return [a for a in chosen if not (self.asset_type() == "crypto" and a == "fundamentals")]


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
                f"{verdict.FAKE_SUMMARY_LINE}\n\n"
                f"**Investment Thesis**: 수주 모멘텀과 이익 성장, 밸류 부담은 비중으로 관리.\n{verdict.FAKE_THESIS_LINE}\n\n"
                "**Price Target**: 205\n\n**Time Horizon**: 2-4주")
        return {"final_trade_decision": text, "final_rating": "Overweight"}
    atr, entry = _FAKE_ATR[h.id], 182.5
    stop, target = round(entry - h.stop_atr * atr, 2), round(entry + h.target_atr * atr, 2)
    text = (f"**Rating**: Overweight\n\n**Executive Summary**: {entry} 눌림 지정가 진입. "
            f"Entry Price: {entry} / Stop Loss: {stop} / Price Target: {target}\n"
            f"Entry: limit / Invalidation: {stop} 아래 마감 / Exit by: {h.holding} 안에\n\n"
            f"{verdict.FAKE_SUMMARY_LINE}\n\n"
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
# The same for the quote, now fetched before the run as well as after it.
QUOTE_TIMEOUT_SEC = 10.0
# And for the related instruments (desk.related): one Yahoo download plus a few news lists.
RELATED_TIMEOUT_SEC = 30.0


class RunManager:
    def __init__(self, store: Store, hub: Hub, runner: Runner, broker: Broker, max_concurrent: int,
                 levels=fill_levels, prices: PriceSource | None = None, related=collect_related) -> None:
        self.levels = levels
        self.related = related
        self.prices = prices
        self.store = store
        self.hub = hub
        self.runner = runner
        self.broker = broker
        self._slots = asyncio.Semaphore(max(1, max_concurrent))
        self._tasks: set[asyncio.Task] = set()

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
        sub = entry_vs_live(sub, sr_u and sr_u.get("atr"))
        if sr_u:
            sub["support_resistance"] = sr_u
        return sub

    def _publish(self, run_id: str, event: dict) -> None:
        self.store.append_event(run_id, event)
        self.hub.notify(run_id)

    async def _execute(self, run_id: str, request: RunRequest) -> None:
        async with self._slots:
            self.store.mark_running(run_id)
            self._publish(run_id, {"type": "status", "agent": "system", "content": "running",
                                   "data": {"analysts": request.selected_analysts()}})
            loop = asyncio.get_running_loop()

            def emit(event: dict) -> None:   # called from the worker thread
                loop.call_soon_threadsafe(self._publish, run_id, event)

            try:
                book = await self.broker.portfolio() if request.use_portfolio else None
                # The agents set levels against the price now, not only a close days old.
                # A leveraged/inverse product is also read on its underlying (desk.underlying).
                lever = under.leverage_of(request.ticker)
                base = lever[0] if lever else None
                start, start_u = await asyncio.gather(
                    self._live_quote(request), self._live_quote(request, base) if base else _none())
                sr, sr_u, related = await asyncio.gather(
                    self._support_resistance(request, start),
                    self._support_resistance(request, start_u, base) if base else _none(),
                    self._related(request))
                lever_note = under.prompt_note(request.ticker, *lever, sr_u, start_u) if lever else None
                note = "\n\n".join(part for part in (live_note(start, sr and sr.get("atr")), prompt_note(sr),
                                                     lever_note, related_note(related)) if part) or None
                horizon = horizons.get(request.horizon)
                frame = horizons.get(None) if sr and sr.get("fallback") else horizon
                mandate = horizons.horizon_mandate(horizon, sr and sr.get("atr"), frame=frame, levels_below=bool(sr))
                # #64: the symmetric checklist and high-probability filter, swing included
                mandate = "\n\n".join(part for part in (mandate, verdict.mandate(horizon)) if part)
                portfolio = desk_portfolio(request.profile, book, note, horizon_mandate=mandate)
                result = await asyncio.to_thread(self.runner, request, emit, portfolio)
                # The model may still leave a level out; fill it from ATR and mark it as computed.
                fresh, fresh_u = await asyncio.gather(
                    self._live_quote(request), self._live_quote(request, base) if base else _none())
                live, live_u = fresh or start, fresh_u or start_u
                plan, sub = parse_plan(result.final_state), None
                if lever:   # the anchor stays the pre-run pair: what the agents saw
                    plan, sub = under.split(plan, *lever, under.anchors(start, start_u, sr, sr_u))
                plan = await asyncio.to_thread(self.levels, plan, request.ticker, request.trade_date, live=live,
                                               horizon=horizon.id)
                plan = entry_vs_live(plan, sr and sr.get("atr"))
                if sub is not None:
                    plan["underlying"] = await self._underlying_plan(sub, request, sr_u, live_u)
                # only a non-leveraged run gets here with them (its model wrote the line anyway)
                plan.pop("underlying_levels", None)
                # The entry is valid from the price the agents saw, so the minutes spent analysing count.
                plan["horizon"] = horizon.id
                plan["valid_until"] = horizons.valid_until(horizon, start["fetched_at"] if start else None)
                plan = verdict.apply(plan, horizon)   # opinion + filter; rating, bias and levels stay
                if sr:
                    plan["support_resistance"] = sr
                if related:
                    plan["related"] = related
                self.store.mark_done(run_id, plan, result.report_path)
                self._publish(run_id, {"type": "plan", "agent": "portfolio_manager", "data": plan})
                self._publish(run_id, {"type": "done", "agent": "system", "content": "done"})
            except Exception as exc:   # surface every failure to the page, not just the log
                log.exception("run %s failed", run_id)
                message = f"{type(exc).__name__}: {exc}"
                self.store.mark_failed(run_id, message)
                self._publish(run_id, {"type": "error", "agent": "system", "content": message})
                self._publish(run_id, {"type": "done", "agent": "system", "content": "failed"})
