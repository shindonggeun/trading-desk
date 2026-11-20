"""The owner's mandate, handed to the agents that decide the trade.

TradingAgents puts ``PortfolioContext.render()`` into the trader's, the three risk
debaters' and the portfolio manager's prompts. ``DeskPortfolio`` extends it with a
mandate, so it reaches exactly the agents that set levels and sizing — without
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
    "them out because the call is bearish or uncertain; state the best level you can justify. "
    "The portfolio manager's levels are the ones the owner trades: end the Executive Summary with "
    "the final levels written exactly as 'Entry Price: <number> / Stop Loss: <number> / Price "
    "Target: <number>' (keep these English labels in any output language), and when the risk "
    "debate revised the trader's entry or stop, give the revised numbers there."
)

# #696: one mandate - the owner no longer picks a style (aggressive / balanced). The risk of a plan is
# the instrument's (desk.all_horizons.class_note: leverage, inverse, bond, defensive), told beside this.
MANDATE = (
    "Investor mandate (the account owner): a short-term trader who acts in both directions - a bearish view "
    "is a short or inverse entry, not merely trimming - and may trade the underlying through 2x-3x leveraged "
    "or inverse ETFs. " + _LEVELS + " "
    "Base the stop on volatility (ATR) and price structure and aim for reward:risk of at least 2. In Position "
    "Sizing, state the loss at the stop as a percentage of capital (keep it within 2-3%); a leveraged product "
    "is already leveraged, so size it smaller. If you know a liquid leveraged or inverse product on this "
    "underlying, name it; if unsure, say so instead of inventing one."
)


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


def desk_portfolio(book: PortfolioContext | None, levels_note: str | None = None, *,
                   horizon_mandate: str | None = None) -> DeskPortfolio:
    """``levels_note`` is the desk's support/resistance reference (desk.sr.prompt_note);
    ``horizon_mandate`` the run's trading horizon (desk.horizons) and its four lines (desk.all_horizons),
    placed after the mandate."""
    mandate = "\n\n".join(part for part in (MANDATE, horizon_mandate, levels_note) if part)
    data = book.model_dump() if book is not None else {}
    return DeskPortfolio(**data, mandate=mandate, book_known=book is not None)
