"""Leveraged and inverse products read on their underlying (issue #67).

A 3x ETF's chart is its underlying's chart, stretched and decayed: SOXL's support and
resistance come from SOXX. So for a product in desk.related.LEVERAGED the agents also get
the underlying's live price and levels, and the plan carries a second set of levels on the
underlying (``plan["underlying"]``, the same keys as the plan itself). When the model writes
only one side, the other is converted through the daily multiple L:

    product ~ product_ref x (1 + L x (underlying / underlying_ref - 1))

anchored on a pair of prices from the same instant — both live quotes, or both closes.
Daily rebalancing makes a multi-day hold drift from this line, so converted levels say so.

The rating stays about the product the owner typed: Buy SOXS is a short on SOXX, so the
underlying side's bias is flipped for an inverse product.
"""

from __future__ import annotations

from .levels import LEVELS, LIVE_MAX_GAP
from .related import LEVERAGED, PROXY

# Both sides given but this far apart once converted: the model's two sets disagree.
MISMATCH = 0.10

DRIFT_WARNING = ("converted with the daily multiple from one anchor pair; daily rebalancing makes "
                 "multi-day holds drift from it, more so in volatile, sideways markets")


def leverage_of(ticker: str) -> tuple[str, float, bool] | None:
    """(underlying, daily multiple, proxy) for a leveraged/inverse product, else None."""
    found = LEVERAGED.get(ticker.upper())
    if not found:
        return None
    underlying, leverage = found
    return underlying, float(leverage), ticker.upper() in PROXY


def prompt_note(ticker: str, underlying: str, leverage: float, proxy: bool,
                sr_u: dict | None, live_u: dict | None) -> str:
    """Tells the deciding agents to set levels on the underlying first, and how to write them."""
    inverse = leverage < 0
    lines = [
        f"{ticker.upper()} is a {leverage:+g}x daily product on {underlying}"
        + (" (it tracks its own index; the underlying is a close proxy)" if proxy else "") + ". "
        f"Read the chart structure on {underlying}: set entry, stop and target on {underlying}'s "
        f"support/resistance first, then translate them to {ticker.upper()} prices.",
    ]
    if inverse:
        lines.append(f"It is an inverse product: a long {ticker.upper()} is a short on {underlying}, so the "
                     f"{underlying} stop sits above its entry and the {underlying} target below it.")
    if live_u:
        lines.append(f"{underlying} live price: {live_u['price']} ({live_u['source']} at {live_u['fetched_at']} UTC).")
    if sr_u and sr_u.get("levels"):
        zones = ", ".join(f"{'R' if lv['kind'] == 'resistance' else 'S'} {lv['price']} ({lv['timeframe']})"
                          for lv in sr_u["levels"])
        lines.append(f"{underlying} support (S) / resistance (R), reference {sr_u['reference_price']}, "
                     f"ATR14 {sr_u['atr']}: {zones}.")
    lines.append(
        "The portfolio manager ends the Executive Summary with one line holding both sets, English labels "
        f"in any output language: 'Entry Price: <{ticker.upper()}> / Stop Loss: <{ticker.upper()}> / "
        f"Price Target: <{ticker.upper()}> / Underlying Entry: <{underlying}> / Underlying Stop: <{underlying}> / "
        f"Underlying Target: <{underlying}>'.")
    return "\n".join(lines)


def anchors(start: dict | None, start_u: dict | None, sr: dict | None, sr_u: dict | None) -> dict | None:
    """A pair of prices from the same instant: both live quotes when both look sane against their
    own close, else both closes. None when either side is unknown."""
    close, close_u = (sr or {}).get("last_close"), (sr_u or {}).get("last_close")
    # one feed for both (a Toss overnight print against a Yahoo close is not the same instant)
    if start and start_u and close and close_u and start["source"] == start_u["source"] \
            and abs(start["price"] - close) <= LIVE_MAX_GAP * close \
            and abs(start_u["price"] - close_u) <= LIVE_MAX_GAP * close_u:
        return {"source": "live", "product": start["price"], "underlying": start_u["price"]}
    # closes only from the same session date; a 24/7 underlying (BTC) has no close at the product's
    same_day = (sr or {}).get("as_of", "")[:10] == (sr_u or {}).get("as_of", "")[:10]
    crypto = str((sr_u or {}).get("ticker") or "").endswith("-USD")
    if close and close_u and same_day and not crypto:
        return {"source": "close", "product": close, "underlying": close_u}
    return None


def _positive(value: float) -> float | None:
    # a 3x product cannot go below zero, whatever the line says
    return _round(value) if value > 0 else None


def _round(price: float) -> float:
    return float(round(price)) if price >= 1000 else round(price, 2)


def to_product(price_u: float, anchor: dict, leverage: float) -> float | None:
    return _positive(anchor["product"] * (1 + leverage * (price_u / anchor["underlying"] - 1)))


def to_underlying(price: float, anchor: dict, leverage: float) -> float | None:
    return _positive(anchor["underlying"] * (1 + (price / anchor["product"] - 1) / leverage))


def split(plan: dict, underlying: str, leverage: float, proxy: bool,
          anchor: dict | None) -> tuple[dict, dict]:
    """(product plan, underlying sub-plan) with each side's missing model levels converted from the
    other side. Only model levels are converted; ATR filling comes after, per side."""
    plan = dict(plan)
    given = plan.pop("underlying_levels", None) or {}
    origin = plan.get("levels_origin") or {}
    bias = plan.get("bias")
    if leverage < 0:
        bias = {"long": "short", "short": "long"}.get(bias, bias)
    sub = {"ticker": underlying, "leverage": leverage, "proxy": proxy, "bias": bias,
           **{key: given.get(key) for key in LEVELS}}
    source = {key: "model" for key in LEVELS if plan.get(key) is not None}
    source_u = {key: "model" for key in LEVELS if sub.get(key) is not None}
    converted, mismatch = False, {}
    if anchor:
        for key in LEVELS:
            if plan.get(key) is not None and sub.get(key) is not None:
                implied = to_product(sub[key], anchor, leverage)
                if origin.get(key) == "trader" and implied is not None:
                    # the trader's pre-debate draft yields to the PM's final level on the underlying
                    plan["trader_levels"] = {**plan.get("trader_levels", {}), key: plan[key]}
                    plan[key], source[key], converted = implied, "converted", True
                    continue
                if implied is None or abs(implied - plan[key]) > MISMATCH * plan[key]:
                    mismatch[key] = {"product": plan[key], "underlying": sub[key], "implied_product": implied}
            elif plan.get(key) is None and sub.get(key) is not None:
                plan[key] = to_product(sub[key], anchor, leverage)
                if plan[key] is not None:
                    source[key], converted = "converted", True
            elif sub.get(key) is None and plan.get(key) is not None:
                sub[key] = to_underlying(plan[key], anchor, leverage)
                if sub[key] is not None:
                    source_u[key], converted = "converted", True
    plan["levels_source"], sub["levels_source"] = source, source_u
    if converted:
        plan["conversion"] = {"leverage": leverage, "anchor": anchor, "warning": DRIFT_WARNING}
    if mismatch:
        # both kept as the model wrote them; the page shows that they do not agree
        plan["levels_mismatch"] = mismatch
    return plan, sub
