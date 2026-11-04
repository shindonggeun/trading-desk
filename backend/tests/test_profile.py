from tradingagents.portfolio import PortfolioContext

from desk.profile import PROFILES, desk_portfolio


def test_mandate_rides_on_the_portfolio_block_without_inventing_a_flat_book():
    unknown = desk_portfolio("aggressive_leverage", None)
    text = unknown.render("NVDA")
    assert "not provided" in text and "No current position" not in text
    assert "leverage" in text and "**Entry Price**" in text and "**Price Target**" in text

    book = PortfolioContext.model_validate({"positions": [{"ticker": "NVDA", "quantity": 12, "average_price": 168.4}]})
    held = desk_portfolio("aggressive_leverage", book).render("NVDA")
    assert "Current position in NVDA: 12 units" in held and "Investor mandate" in held


def test_profile_changes_the_fingerprint_and_unknown_profile_means_no_mandate():
    a = desk_portfolio("aggressive_leverage", None)
    b = desk_portfolio("balanced", None)
    assert a.fingerprint() != b.fingerprint()                  # a resumed run cannot mix styles
    assert desk_portfolio(None, None) is None
    assert set(PROFILES) == {"aggressive_leverage", "balanced"}
