from .base import MARKETS, Bar, Broker, Holding, Listing
from .mock import MockBroker
from .toss import TossBroker


def make_broker(settings, tokens) -> Broker:
    """``tokens``: the desk's shared token store (desk.token_store, #688) - required, so a new caller decides;
    None keeps the token in this object and issues on its own (tests only: in production it revokes the desk's)."""
    if settings.broker == "toss":
        return TossBroker(
            client_id=settings.toss_client_id,
            client_secret=settings.toss_client_secret,
            account_seq=settings.toss_account_seq,
            kosdaq_symbols=settings.kosdaq_symbols,
            tokens=tokens,
        )
    return MockBroker()


__all__ = ["MARKETS", "Bar", "Broker", "Holding", "Listing", "MockBroker", "TossBroker", "make_broker"]
