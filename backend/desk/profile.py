"""The owner's investing style, handed to the agents that decide the trade.

TradingAgents puts ``PortfolioContext.render()`` into the trader's, the three risk
debaters' and the portfolio manager's prompts. ``DeskPortfolio`` extends it with a
mandate, so the style reaches exactly the agents that set levels and sizing — without
patching the fork. The mandate is written in English like the rest of the prompts;
the output language setting still makes the agents answer in Korean.
"""

from __future__ import annotations

from tradingagents.portfolio import PortfolioContext

_LEVELS = (
    "Every decision must carry numeric levels in the instrument's quote currency: the trader "
    "always fills **Entry Price** and **Stop Loss**, and the portfolio manager always fills "
    "**Price Target** — for Buy/Overweight they describe the long entry, for Sell/Underweight "
    "the short or inverse entry, for Hold the level at which the owner should act. Never leave "
    "them out because the call is bearish or uncertain; state the best level you can justify."
)

PROFILES: dict[str, dict[str, str]] = {
    "aggressive_leverage": {
        "label": "공격형 · 레버리지",
        "mandate": (
            "Investor mandate (the account owner): an aggressive, leverage-focused short-term trader. "
            "Trades the underlying with 2x-3x leveraged or inverse ETFs/ETNs or margin; holding period "
            "from a few days to about three weeks. Acts in both directions: a bearish view is a short or "
            "inverse entry, not merely trimming. "
            + _LEVELS + " "
            "Base the stop on volatility (ATR) and price structure and aim for reward:risk of at least 2. "
            "In Position Sizing, state the suggested leverage (e.g. 2x) and the loss at the stop as a "
            "percentage of capital (keep it within 2-3%). If you know a liquid leveraged or inverse "
            "product on this underlying, name it; if unsure, say so instead of inventing one."
        ),
    },
    "balanced": {
        "label": "균형형",
        "mandate": (
            "Investor mandate (the account owner): a balanced swing investor without leverage, holding "
            "weeks to a few months; reduces rather than shorts on bearish views. "
            + _LEVELS + " "
            "Aim for reward:risk of at least 2 and keep the loss at the stop within 1-2% of capital."
        ),
    },
}
DEFAULT_PROFILE = "aggressive_leverage"


class DeskPortfolio(PortfolioContext):
    """PortfolioContext plus the owner's mandate. ``book_known`` keeps "no holdings were
    given" distinct from "holds nothing", as TradingAgents insists."""

    mandate: str | None = None
    book_known: bool = True

    def render(self, ticker: str) -> str:
        if self.book_known:
            block = super().render(ticker)
        else:
            block = "Portfolio: not provided for this run; judge the trade setup on its own."
        return f"{block}\n\n{self.mandate}" if self.mandate else block


def desk_portfolio(profile: str | None, book: PortfolioContext | None) -> DeskPortfolio | None:
    mandate = PROFILES.get(profile or "", {}).get("mandate")
    if book is None and mandate is None:
        return None
    data = book.model_dump() if book is not None else {}
    return DeskPortfolio(**data, mandate=mandate, book_known=book is not None)
