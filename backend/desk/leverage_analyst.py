"""The desk's leverage analyst: one LLM call that turns the underlying's decision into a call on
the leveraged or inverse product the owner typed (issue #82).

For SOXL the TradingAgents run analyses SOXX — its chart, news and fundamentals are what the
debate is about. This step then reads that decision with what only the product has: its own
price and levels, the daily multiple and how faithfully it was delivered (realized beta), the
volatility decay a multi-day hold pays, and the holding horizon. It answers with the same fixed
English labels the portfolio manager uses, so desk.plan reads it unchanged.

The analyst is injected into RunManager: the default calls the TradingAgents provider and model
(``TRADINGAGENTS_*``), a scripted one answers for DESK_FAKE_RUNS and the tests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace

LEVER_TIMEOUT_SEC = 180.0
DECISION_CHARS = 6000
# A day's true range averages ~1.6 sigma for a random walk (a little more with gaps); rough is enough.
ATR_PER_SIGMA = 1.6


@dataclass
class LeverContext:
    product: str
    base: str
    leverage: float
    proxy: bool
    horizon: str                       # horizons.Horizon.id
    holding: str                       # the horizon's holding period, English
    u_plan: dict                       # parse_plan of the underlying run
    u_decision: str                    # the portfolio manager's text on the underlying
    live: dict | None = None           # product quote
    live_u: dict | None = None
    sr: dict | None = None             # product support/resistance (desk.sr)
    sr_u: dict | None = None
    beta: dict | None = None           # related.tracking: realized multiple
    language: str | None = None          # None: the TradingAgents output_language setting
    intraday: bool = False                # the horizon closes the position the same session
    daily_atr_u: float | None = field(default=None)   # only from daily bars: decay needs a daily sigma

    @property
    def inverse(self) -> bool:
        return self.leverage < 0


Analyst = Callable[[LeverContext], str]


def decay_per_day(ctx: LeverContext) -> float | None:
    """Rough daily drag of a daily-rebalanced L-x product, in %: (L^2 - L) / 2 x sigma^2."""
    price = (ctx.live_u or {}).get("price") or (ctx.sr_u or {}).get("last_close")
    if not ctx.daily_atr_u or not price:
        return None
    sigma = ctx.daily_atr_u / price / ATR_PER_SIGMA
    return round((ctx.leverage ** 2 - ctx.leverage) / 2 * sigma ** 2 * 100, 3)


def _price(live: dict | None, sr: dict | None) -> str:
    """The live quote, else the last close the levels were computed from (a past-date analysis)."""
    if live and live.get("price"):
        return f"{live['price']} (live)"
    if sr and sr.get("last_close"):
        return f"{sr['last_close']} (last close)"
    return "n/a"


def _levels(sr: dict | None) -> str:
    if not sr or not sr.get("levels"):
        return "n/a"
    return ", ".join(f"{'R' if lv['kind'] == 'resistance' else 'S'} {lv['price']}" for lv in sr["levels"])


def build_prompt(ctx: LeverContext) -> str:
    p, b, u = ctx.product.upper(), ctx.base, ctx.u_plan
    decay = decay_per_day(ctx)
    if ctx.intraday:
        decay_line = "Volatility decay is negligible within an intraday hold; close the position the same session."
    elif decay is not None:
        decay_line = (f"Estimated volatility decay of a {ctx.leverage:+g}x daily product at the current "
                      f"volatility: about {decay}% per day of holding (rough; larger in choppy markets).")
    else:
        decay_line = ("Volatility decay: not measured for this run, but it is real for a multi-day hold of a "
                      f"{ctx.leverage:+g}x daily product — keep the hold short and the stop honest.")
    beta = ctx.beta or {}
    lines = [
        f"You are the desk's leverage analyst. The owner trades {p}, a {ctx.leverage:+g}x daily "
        f"{'inverse ' if ctx.inverse else ''}product on {b}"
        + (f" (it tracks its own index; {b} is a close proxy)" if ctx.proxy else "") + ". "
        f"The research team analysed {b}; their final decision is below. Turn it into a decision on {p}.",
        "",
        (f"## {b} decision (rating {u.get('rating')}, bias {u.get('bias')}; entry {u.get('entry_price')}, "
        f"stop {u.get('stop_loss')}, target {u.get('price_target')})"),
        ctx.u_decision[:DECISION_CHARS],
        "",
        "## Facts",
        f"- {b} price {_price(ctx.live_u, ctx.sr_u)}; support/resistance: {_levels(ctx.sr_u)}.",
        (f"- {p} price {_price(ctx.live, ctx.sr)}; support/resistance: {_levels(ctx.sr)}; "
        f"ATR {(ctx.sr or {}).get('atr', 'n/a')}."),
        f"- Realized multiple of {p} to {b}: 20d {beta.get('beta_20d', 'n/a')}, 60d {beta.get('beta_60d', 'n/a')}.",
        f"- {decay_line}",
        f"- Holding horizon: {ctx.holding}.",
        "",
        "## How to decide",
        (f"- {p} is inverse: a bearish {b} call is a reason to BUY {p}, a bullish one to sell or avoid it. "
         f"Your rating is about {p}, not {b}." if ctx.inverse else
         f"- Your rating is about {p}. A bullish {b} call is a reason to buy {p}, a bearish one to sell or avoid it."),
        (f"- Translate the {b} entry, stop and target to {p} prices with the multiple from the prices above, then "
        f"adjust to {p}'s own support/resistance. Keep reward:risk at least 2 and the loss at the stop within "
        "2-3% of capital — size the position for the multiple."),
        ("- If decay or the holding period makes the product a poor vehicle for the call, say so: Hold "
        "with the condition under which you would enter, or a shorter holding period."),
        f"- If the {b} decision is Hold, give Hold with the long and short trigger levels on {p}.",
        "",
        f"## Output (answer in {ctx.language or 'English'}; keep these English labels exactly, numbers in {p} prices)",
        "**Rating**: one of Buy / Overweight / Hold / Underweight / Sell",
        "**Entry Price**: <number>",
        "**Stop Loss**: <number>",
        "**Price Target**: <number>",
        "**Time Horizon**: <maximum holding period>",
        "**Position Sizing**: <share of capital, and the loss at the stop>",
        f"**Executive Summary**: <the call on {p}, how to act, and where it differs from the {b} call and why>",
        (f"End with one line: 'Entry: now|limit|stop / Invalidation: <{p} price or event> / "
         "Exit by: <time or condition>'."),
        (f"And one more: 'Win Probability: <% that the {p} target is reached before the stop> / "
         f"Long Trigger: <{p} price or none> / Short Trigger: <{p} price or none>'."),
    ]
    return "\n".join(lines)


def llm_analyst(ctx: LeverContext) -> str:
    """The TradingAgents provider and deep model. Blocking: run it in a thread."""
    from tradingagents.default_config import build_default_config
    from tradingagents.llm_clients import create_tier_client

    config = build_default_config()
    if not ctx.language:   # the same language the research team answered in
        ctx = replace(ctx, language=config.get("output_language"))
    # one attempt: the SDK's retries would multiply the timeout past the runner's backstop
    llm = create_tier_client(config, "deep", timeout=LEVER_TIMEOUT_SEC, max_retries=0).get_llm()
    return llm.invoke(build_prompt(ctx)).content


def fake_analyst(ctx: LeverContext) -> str:
    """A scripted answer for DESK_FAKE_RUNS: the underlying's direction, flipped for inverse."""
    from .underlying import flip_rating

    rating = flip_rating(ctx.u_plan.get("rating", "Hold")) if ctx.inverse else ctx.u_plan.get("rating", "Hold")
    price = (ctx.live or {}).get("price") or (ctx.sr or {}).get("last_close") or 50.0
    long = rating in ("Buy", "Overweight")
    stop, target = (price * 0.94, price * 1.12) if long else (price * 1.06, price * 0.88)
    return (f"**Rating**: {rating}\n\n**Entry Price**: {price:.2f}\n\n**Stop Loss**: {stop:.2f}\n\n"
            f"**Price Target**: {target:.2f}\n\n**Time Horizon**: 3-5일\n\n"
            f"**Position Sizing**: 자본의 15%, 손절 시 손실 약 1%\n\n"
            f"**Executive Summary**: {ctx.base} 판단({ctx.u_plan.get('rating')})을 {ctx.product} "
            f"{ctx.leverage:+g}배로 옮겼습니다. 변동성 감쇠 때문에 보유는 짧게.")
