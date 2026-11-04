from .base import MARKETS, Bar, Broker, Holding, Listing
from .mock import MockBroker
from .toss import TossBroker


def make_broker(settings) -> Broker:
    if settings.broker == "toss":
        return TossBroker(
            client_id=settings.toss_client_id,
            client_secret=settings.toss_client_secret,
            account_seq=settings.toss_account_seq,
            kosdaq_symbols=settings.kosdaq_symbols,
        )
    return MockBroker()


__all__ = ["MARKETS", "Bar", "Broker", "Holding", "Listing", "MockBroker", "TossBroker", "make_broker"]
