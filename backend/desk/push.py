"""Web push (#361): subscriptions per account and sending with VAPID.

The keys come from env (DESK_VAPID_PUBLIC_KEY · DESK_VAPID_PRIVATE_KEY · DESK_VAPID_SUBJECT); without
all three push is off. A subscription the push service answers 404/410 for is gone and deleted.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from sqlalchemy import delete, insert, select, update
from sqlalchemy.engine import Engine

from .db import now, push_subscriptions

log = logging.getLogger(__name__)

# "flow" switches every foreign-flow turn; flow_<market> one market of it (#362: a toggle per market).
KINDS = ("flow", "flow_futures", "flow_kospi", "flow_kosdaq")
MAX_PER_USER = 10
# The server only ever POSTs to the browsers' push services - never a host a caller names (SSRF).
PUSH_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "web.push.apple.com")
PUSH_HOST_SUFFIXES = (".push.services.mozilla.com", ".notify.windows.com", ".push.apple.com")
_HOST = re.compile(r"[a-z0-9-]+(\.[a-z0-9-]+)+")      # plain DNS labels: no "\\", "%", trailing dot


class TooMany(Exception):
    pass


class Taken(Exception):
    """The endpoint is another account's subscription."""


def allowed_endpoint(url: str) -> bool:
    """https on 443 to a known push service; no IP literal, no userinfo."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or parts.username or parts.password or port not in (None, 443) or not host:
        return False
    # the allow-list closes on its own, not on how the HTTP client later reads an odd host
    if not _HOST.fullmatch(host) or parts.netloc.lower() not in (host, f"{host}:443"):
        return False
    return host in PUSH_HOSTS or host.endswith(PUSH_HOST_SUFFIXES)


class PushStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def subscribe(self, user_id: int, endpoint: str, keys: dict, device: str, kinds: dict) -> dict:
        """Add or refresh; an endpoint another account registered moves to this one (same browser)."""
        row = {"user_id": user_id, "keys": {"p256dh": keys["p256dh"], "auth": keys["auth"]},
               "device": device, "kinds": {k: bool(kinds.get(k, True)) for k in KINDS}}
        with self.engine.begin() as conn:
            got = conn.execute(select(push_subscriptions.c.id, push_subscriptions.c.user_id)
                               .where(push_subscriptions.c.endpoint == endpoint)).first()
            if got and got.user_id != user_id:
                raise Taken("이미 다른 계정의 구독")
            if got:
                conn.execute(update(push_subscriptions).where(push_subscriptions.c.id == got.id).values(**row))
            else:
                n = len(conn.execute(select(push_subscriptions.c.id)
                                     .where(push_subscriptions.c.user_id == user_id)).all())
                if n >= MAX_PER_USER:
                    raise TooMany(f"구독은 계정당 {MAX_PER_USER}개까지")
                conn.execute(insert(push_subscriptions).values(endpoint=endpoint, created_at=now(), **row))
        return {"endpoint": endpoint, "device": device, "kinds": row["kinds"]}

    def unsubscribe(self, user_id: int, endpoint: str) -> bool:
        """Only the caller's own subscription."""
        with self.engine.begin() as conn:
            return conn.execute(delete(push_subscriptions).where(
                push_subscriptions.c.user_id == user_id, push_subscriptions.c.endpoint == endpoint)).rowcount > 0

    def of_user(self, user_id: int) -> list[dict]:
        with self.engine.connect() as conn:
            rows = conn.execute(select(push_subscriptions).where(push_subscriptions.c.user_id == user_id)).mappings()
            return [dict(r) for r in rows]

    def for_kind(self, *kinds: str) -> list[dict]:
        """Subscriptions with every one of ``kinds`` on; a kind a row never stored counts as on."""
        with self.engine.connect() as conn:
            rows = conn.execute(select(push_subscriptions).order_by(push_subscriptions.c.id)).mappings().all()
        return [dict(r) for r in rows if all((r["kinds"] or {}).get(k, True) for k in kinds)]

    def remove(self, endpoint: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(delete(push_subscriptions).where(push_subscriptions.c.endpoint == endpoint))


def _webpush(sub: dict, data: str, private_key: str, subject: str) -> int:
    """pywebpush; the push service's status (2xx sent, 404/410 gone)."""
    from pywebpush import WebPushException, webpush

    try:
        resp = webpush({"endpoint": sub["endpoint"], "keys": sub["keys"]}, data,
                       vapid_private_key=private_key, vapid_claims={"sub": subject}, ttl=600, timeout=10)
        return getattr(resp, "status_code", 201)
    except WebPushException as exc:
        return getattr(exc.response, "status_code", 0) or 0


class Pusher:
    def __init__(self, store: PushStore, public_key: str = "", private_key: str = "", subject: str = "",
                 send: Callable[[dict, str], int] | None = None) -> None:
        self.store = store
        self.public_key = public_key
        self._private = private_key
        self._subject = subject
        self._send = send or (lambda sub, data: _webpush(sub, data, self._private, self._subject))

    @property
    def enabled(self) -> bool:
        return bool(self.public_key and self._private and self._subject)

    def _deliver(self, subs: list[dict], payload: dict) -> int:
        if not self.enabled:
            return 0
        data = json.dumps(payload, ensure_ascii=False)
        sent = 0
        for sub in subs:
            if not allowed_endpoint(sub["endpoint"]):
                log.warning("push skipped: %r is not a push service", sub.get("device"))
                continue
            try:
                status = self._send(sub, data)
            except Exception as exc:  # noqa: BLE001 - one bad endpoint never stops the rest
                log.warning("push to %r failed: %s", sub.get("device"), exc)
                continue
            if status in (404, 410):
                self.store.remove(sub["endpoint"])
            elif 200 <= status < 300:
                sent += 1
        return sent

    def broadcast(self, *kinds: str, payload: dict) -> int:
        """To every subscription that has all of ``kinds`` on; the number delivered."""
        return self._deliver(self.store.for_kind(*kinds), payload)

    def to_user(self, user_id: int, payload: dict) -> int:
        return self._deliver(self.store.of_user(user_id), payload)
