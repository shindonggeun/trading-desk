from tradingagents.portfolio import PortfolioContext

from desk import profile
from desk.profile import desk_portfolio


def test_mandate_rides_on_the_portfolio_block_without_inventing_a_flat_book():
    unknown = desk_portfolio(None)
    text = unknown.render("NVDA")
    assert "not provided" in text and "No current position" not in text
    assert "leveraged" in text and "**Entry Price**" in text and "**Price Target**" in text

    book = PortfolioContext.model_validate({"positions": [{"ticker": "NVDA", "quantity": 12, "average_price": 168.4}]})
    held = desk_portfolio(book).render("NVDA")
    assert "Current position in NVDA: 12 units" in held and "Investor mandate" in held


def test_there_is_one_mandate_and_no_style_to_choose():
    # #696: the owner no longer picks aggressive / balanced - the instrument's class says the risk
    assert not hasattr(profile, "PROFILES")
    assert desk_portfolio(None).mandate == profile.MANDATE
    assert desk_portfolio(None).fingerprint() == desk_portfolio(None).fingerprint()
