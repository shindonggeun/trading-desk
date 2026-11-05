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

import re
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
    pair: str | None = None               # the product the other way on the same underlying (#109)
    pair_leverage: float | None = None
    live_pair: dict | None = None

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


def _pair_price(live: dict | None) -> str:
    if not live or not live.get("price"):
        return "n/a"
    return f"{live['price']} ({'last close' if live.get('source') == 'close' else 'live'})"


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
        *([(f"- {ctx.pair} is the {ctx.pair_leverage:+g}x {'inverse ' if (ctx.pair_leverage or 0) < 0 else ''}"
           f"product on {b}: price {_pair_price(ctx.live_pair)}.")] if ctx.pair else []),
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
        *_pair_rules(ctx, p, b),
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
        *([f"Then 'Holder Exit: <{p} price or none>' for someone already holding {p}."] if ctx.pair else []),
        *([(f"And a last one: 'Inverse Pair: {ctx.pair} / Pair Entry: <{ctx.pair} price> / Pair Stop: "
           f"<{ctx.pair} price> / Pair Target: <{ctx.pair} price>' when buying {ctx.pair} is the trade "
           f"for a {b} move against {p}, else 'Inverse Pair: none'.")] if ctx.pair else []),
    ]
    return "\n".join(lines)


def _pair_rules(ctx: LeverContext, p: str, b: str) -> list[str]:
    """How a move against the product is traded: not by shorting it but by buying its pair, and a
    holder's exit price on its own line — the Entry/Stop/Target fields keep their usual meaning."""
    if not ctx.pair:
        return []
    against = "bullish" if ctx.inverse else "bearish"
    trigger = "long" if ctx.inverse else "short"
    return [
        (f"- A {against} {b} call (your {p} rating Underweight/Sell, or Hold with a {trigger} trigger on {b}) "
         f"is not a short of {p}. Entry Price, Stop Loss and Price Target keep their usual meaning for your {p} "
         f"rating. For someone already holding {p}, add 'Holder Exit: <{p} price>' — where a holder reduces or "
         f"exits. A new trade on that move is a BUY of {ctx.pair}: give its entry, stop and target in "
         f"{ctx.pair} prices on the Inverse Pair line (stop below entry, target above)."),
        f"- Never write a {b} or {ctx.pair} price in a {p} field; each number belongs to the ticker named next to it.",
        "- Put the Holder Exit and Inverse Pair lines on lines of their own, after everything else.",
    ]


# '**Pair Entry**: **$20.4** (SOXS)', 'Pair Entry: 1,950원' — the number after the label, whatever wraps it
_NUMV = r"[\s*$₩]*([0-9][0-9,]*(?:\.[0-9]+)?)"
_PAIR_HEAD = re.compile(r"Inverse\s+Pair\**\s*:\s*\**\s*([A-Za-z0-9.^=-]+)", re.IGNORECASE)
_PAIR_FIELD = {k: re.compile(rf"Pair\s+{k}\**\s*:{_NUMV}", re.IGNORECASE) for k in ("Entry", "Stop", "Target")}
_HOLDER = re.compile(rf"Holder\s+Exit\**\s*:{_NUMV}", re.IGNORECASE)
PAIR_SPAN = 400   # the pair's three fields follow its name within this many characters (one line or a few)
_PAIR_BLOCK = re.compile(r"[*\t ]*Inverse\s+Pair\**\s*:[^\n]*(?:\n[^\n]*Pair\s+(?:Entry|Stop|Target)[^\n]*){0,3}",
                         re.IGNORECASE)
_HOLDER_LINE = re.compile(r"[*\t ]*Holder\s+Exit\**\s*:[^\n]*", re.IGNORECASE)
# a level this far from its ticker's price is another ticker's number
PAIR_IMPLAUSIBLE = 0.5


def strip_extras(text: str) -> str:
    """The answer without the Inverse Pair and Holder Exit lines, for desk.plan: their numbers are
    the pair's or a holder's, never the product plan's entry, and they do not belong in its summary."""
    return _HOLDER_LINE.sub("", _PAIR_BLOCK.sub("", text)).rstrip()


def _num(raw: str) -> float:
    return float(raw.replace(",", ""))


def parse_pair(text: str, ctx: LeverContext, bias: str | None = None) -> dict | None:
    """The 'Inverse Pair' line as a buy setup on the pair, or None: no pair or no price for it,
    'none', another ticker, a missing field, levels that are not a buy (stop < entry < target), a
    level far from the pair's price, or a product call that is itself long (the pair would fight it)."""
    ref = (ctx.live_pair or {}).get("price")
    if not ctx.pair or not text or not ref or bias == "long":
        return None
    heads = list(_PAIR_HEAD.finditer(text))
    if not heads or heads[-1].group(1).upper() != ctx.pair.upper():
        return None
    segment = text[heads[-1].end():heads[-1].end() + PAIR_SPAN]
    values = []
    for key in ("Entry", "Stop", "Target"):
        hit = _PAIR_FIELD[key].search(segment)
        if not hit:
            return None
        values.append(_num(hit.group(1)))
    entry, stop, target = values
    if not stop < entry < target or any(abs(v - ref) > PAIR_IMPLAUSIBLE * ref for v in values):
        return None
    return {"ticker": ctx.pair, "leverage": ctx.pair_leverage, "entry_price": entry, "stop_loss": stop,
            "price_target": target, "reward_risk": round((target - entry) / (entry - stop), 2),
            "price": ref, "price_source": (ctx.live_pair or {}).get("source")}


def parse_holder_exit(text: str, ctx: LeverContext) -> float | None:
    """'Holder Exit: <product price>' — where someone holding the product reduces or exits — when it
    is near the product's price (not the underlying's or the pair's number)."""
    ref = (ctx.live or {}).get("price") or (ctx.sr or {}).get("last_close")
    hits = _HOLDER.findall(text or "")
    if not hits or not ref:
        return None
    value = _num(hits[-1])
    return value if abs(value - ref) <= PAIR_IMPLAUSIBLE * ref else None


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
    text = (f"**Rating**: {rating}\n\n**Entry Price**: {price:.2f}\n\n**Stop Loss**: {stop:.2f}\n\n"
            f"**Price Target**: {target:.2f}\n\n**Time Horizon**: 3-5일\n\n"
            f"**Position Sizing**: 자본의 15%, 손절 시 손실 약 1%\n\n"
            f"**Executive Summary**: {ctx.base} 판단({ctx.u_plan.get('rating')})을 {ctx.product} "
            f"{ctx.leverage:+g}배로 옮겼습니다. 변동성 감쇠 때문에 보유는 짧게.")
    if ctx.pair:
        if long:
            text += "\n\nInverse Pair: none"
        else:   # against the product: its pair is the trade
            pp = (ctx.live_pair or {}).get("price") or 30.0
            text += (f"\n\nHolder Exit: {price * 0.97:.2f}"
                     f"\n\nInverse Pair: {ctx.pair} / Pair Entry: {pp:.2f} / Pair Stop: {pp * 0.94:.2f} / "
                     f"Pair Target: {pp * 1.12:.2f}")
    return text
