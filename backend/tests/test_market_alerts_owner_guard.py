import pytest

from desk import market_alerts
from desk.db import make_engine, migrate


@pytest.mark.parametrize("owner_id", [None, 7])
def test_owner_only_delivery_requires_an_owner_and_public_delivery_stays_public(db_url, monkeypatch, owner_id):
    migrate(db_url)
    engine = make_engine(db_url)
    dispatcher = market_alerts.MarketAlerts(engine)
    deliveries = []
    owner_checks = []

    def resolve_owner():
        owner_checks.append(True)
        return owner_id

    def send(_pusher, kind, _tone, *, user_id, payload):
        deliveries.append((kind, user_id, payload["kind"]))

    monkeypatch.setattr(dispatcher, "owner", resolve_owner)
    monkeypatch.setattr(market_alerts.notify, "send", send)
    monkeypatch.setattr(market_alerts, "publish_market_event", lambda *_args: None)
    alerts = [
        {
            "kind": "holding_alert", "rule": "hold_stop", "market": "TEST", "who": "all",
            "severity": "strong", "t": "2026-11-21T10:30:00+09:00",
        },
        {
            "kind": "flow_shift", "rule": "buy_grow", "market": "kospi", "who": "foreign",
            "severity": "caution", "t": "2026-11-21T10:31:00+09:00", "value": 100, "prev": 50,
        },
    ]
    try:
        recorded = dispatcher.dispatch(alerts)
        expected = [] if owner_id is None else [("holding_alert", owner_id, "holding_alert")]
        assert deliveries == [*expected, ("flow_shift", None, "flow_shift")]
        assert len(owner_checks) == 1
        assert [row["pushed"] for row in recorded] == [owner_id is not None, True]
        assert {row["kind"]: row["pushed"] for row in dispatcher.list("2026-11-21")} == {
            "holding_alert": owner_id is not None,
            "flow_shift": True,
        }
    finally:
        engine.dispose()
