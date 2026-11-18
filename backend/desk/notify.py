"""Where every alert leaves the server (#579): one ``send`` for each channel.

Today the only channel is web push (desk.push, per-kind switches on each subscription). Another
channel (#580: Telegram · KakaoTalk) is added here, so the alert modules (desk.flow_alerts ·
desk.market_alerts) never learn about it. A channel's failure is logged and never stops the alert
from being recorded, nor the other channels.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def send(pusher, *kinds: str, payload: dict) -> int:
    """Deliver ``payload`` ({title, body, tag, url, ...}) to the subscriptions that have all of
    ``kinds`` on; the number delivered."""
    sent = 0
    if pusher is not None:
        try:
            sent += pusher.broadcast(*kinds, payload=payload)
        except Exception as exc:  # noqa: BLE001 - a push never stops recording
            log.warning("push of %s failed: %s", "/".join(kinds), exc)
    return sent
