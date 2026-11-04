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

from .broker.base import Broker
from .events import StateDiffer
from .levels import fill_levels
from .plan import parse_plan
from .prices import PriceSource
from .profile import desk_portfolio
from .sr import prompt_note, support_resistance
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
            ([], {"final_trade_decision": (
                "**Rating**: Overweight\n\n**Executive Summary**: 182.5 부근 분할 진입, 171 손절, 목표 205.\n\n"
                "**Investment Thesis**: 수주 모멘텀과 이익 성장, 밸류 부담은 비중으로 관리.\n\n"
                "**Price Target**: 205\n\n**Time Horizon**: 2-4주"),
                  "final_rating": "Overweight"}),
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


class RunManager:
    def __init__(self, store: Store, hub: Hub, runner: Runner, broker: Broker, max_concurrent: int,
                 levels=fill_levels, prices: PriceSource | None = None) -> None:
        self.levels = levels
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

    async def _live_quote(self, request: RunRequest) -> dict | None:
        """The current quote for a same-day analysis; None for a past date or no price."""
        if self.prices is None or request.trade_date != dt.date.today().isoformat():
            return None
        try:
            quote = await self.prices.quote(request.ticker)
        except Exception as exc:  # noqa: BLE001 - levels then start from the last close
            log.warning("no live quote for %s: %s", request.ticker, exc)
            return None
        return {"price": quote.price, "source": quote.source, "fetched_at": quote.fetched_at}

    async def _support_resistance(self, request: RunRequest) -> dict | None:
        """Daily/weekly support and resistance up to the analysis date; None without prices."""
        if self.prices is None:
            return None
        try:
            return await asyncio.wait_for(
                support_resistance(self.prices, request.ticker, request.trade_date), SR_TIMEOUT_SEC)
        except Exception as exc:  # noqa: BLE001 - incl. timeout: the analysis runs without the reference
            log.warning("no support/resistance for %s: %s", request.ticker, exc)
            return None

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
                sr = await self._support_resistance(request)
                portfolio = desk_portfolio(request.profile, book, prompt_note(sr))
                result = await asyncio.to_thread(self.runner, request, emit, portfolio)
                # The model may still leave a level out; fill it from ATR and mark it as computed.
                live = await self._live_quote(request)
                plan = await asyncio.to_thread(self.levels, parse_plan(result.final_state),
                                               request.ticker, request.trade_date, live=live)
                if sr:
                    plan["support_resistance"] = sr
                self.store.mark_done(run_id, plan, result.report_path)
                self._publish(run_id, {"type": "plan", "agent": "portfolio_manager", "data": plan})
                self._publish(run_id, {"type": "done", "agent": "system", "content": "done"})
            except Exception as exc:   # surface every failure to the page, not just the log
                log.exception("run %s failed", run_id)
                message = f"{type(exc).__name__}: {exc}"
                self.store.mark_failed(run_id, message)
                self._publish(run_id, {"type": "error", "agent": "system", "content": message})
                self._publish(run_id, {"type": "done", "agent": "system", "content": "failed"})
