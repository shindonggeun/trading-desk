"""Telegram as an alert channel (#580), next to web push in desk.notify.

**Telegram**: a BotFather bot's ``sendMessage`` to one chat. The token and the chat id come from the
server env (``DESK_TELEGRAM_BOT_TOKEN`` · ``DESK_TELEGRAM_CHAT_ID``); either empty = the channel is off.
The token sits in the request path, so httpx's request log line is redacted (``_RedactBotToken``).

It sends plain text: the alert's title, body and a link back to the desk (``DESK_PUBLIC_URL``).
Errors carry a short code only (HTTP status, Telegram error code, exception type) - never the token,
the chat id or a URL holding them. A 429 or a 5xx is retried (Telegram's ``retry_after`` honoured,
capped); the caller (desk.notify) runs the channel off the alert's thread.

#986 retired the other channel #580 had. The row it left in ``notify_channels`` stays in the table and
is never read: the app installs and lists Telegram only.
"""

from __future__ import annotations

import logging
import math
import re
import time
from collections.abc import Callable

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine

from . import net
from .db import notify_channels, now

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
CHANNELS = ("telegram",)
TIMEOUT_SEC = 10.0
TRIES = 3
RETRY_CAP_SEC = 30.0
TEXT_MAX = 900                           # an alert is a few lines; Telegram takes up to 4096


class ChannelError(Exception):
    """A send that did not go; ``str()`` is a short code safe to log and to show."""


class ChannelRateLimited(ChannelError):
    def __init__(self, retry_after: float) -> None:
        super().__init__("channel rate limited")
        self.retry_after = retry_after


class ChannelUnknown(ChannelError):
    """Whether the remote channel accepted the post is unknown."""


class ChannelStopped(ChannelError):
    """Policy or recipient changed before the message was posted."""


class _RedactBotToken(logging.Filter):
    """httpx logs every request URL at INFO; a Telegram URL holds the bot token."""

    _bot = re.compile(r"/bot[^/\s\"']+")

    def filter(self, record: logging.LogRecord) -> bool:
        if record.args:
            record.args = tuple(self._bot.sub("/bot***", a) if isinstance(a, str) else
                                (self._bot.sub("/bot***", str(a)) if isinstance(a, httpx.URL) else a)
                                for a in record.args)
        if isinstance(record.msg, str):
            record.msg = self._bot.sub("/bot***", record.msg)
        return True


def install_log_filter() -> None:
    """httpx's request lines off (WARNING, as desk.broker.kis_stream does for websockets) and, should a
    root logger ever lower that, the bot token redacted from them."""
    target = logging.getLogger("httpx")
    target.setLevel(logging.WARNING)
    if not any(isinstance(f, _RedactBotToken) for f in target.filters):
        target.addFilter(_RedactBotToken())


def message(payload: dict, base_url: str) -> tuple[str, str | None]:
    """(text, link) of an alert payload ({title, body, url})."""
    title, body = (payload.get("title") or "").strip(), (payload.get("body") or "").strip()
    text = "\n".join(p for p in (title, body) if p)[:TEXT_MAX] or "트레이딩 데스크 알림"
    path = payload.get("url") or "/"
    link = base_url.rstrip("/") + path if base_url and path.startswith("/") else None
    return text, link


def _retry_wait(resp: httpx.Response, attempt: int) -> float:
    try:
        after = float((resp.json().get("parameters") or {}).get("retry_after") or 0)
    except (ValueError, AttributeError):
        after = 0.0
    if not after:
        after = float(resp.headers.get("Retry-After") or 0) or 2.0 ** attempt
    return min(after, RETRY_CAP_SEC)


def _post(client: httpx.Client, url: str, sleep: Callable[[float], None], **kw) -> httpx.Response:
    """POST with retries on 429 / 5xx / a network error; the last response, or ChannelError."""
    for attempt in range(TRIES):
        try:
            resp = client.post(url, timeout=TIMEOUT_SEC, **kw)
        except httpx.HTTPError as exc:
            if attempt == TRIES - 1:
                raise ChannelError(type(exc).__name__) from None
            sleep(2.0 ** attempt)
            continue
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == TRIES - 1:
                return resp
            sleep(_retry_wait(resp, attempt))
            continue
        return resp
    raise ChannelError("no response")       # not reached


def _post_once(client: httpx.Client, url: str, allowed, **kw) -> httpx.Response:
    if allowed is not None and not allowed():
        raise ChannelStopped("channel authorization changed")
    try:
        response = client.post(url, timeout=TIMEOUT_SEC, **kw)
    except httpx.HTTPError:
        raise ChannelUnknown("channel result unknown") from None
    if response.status_code == 429:
        params = _json(response).get("parameters")
        body_after = params.get("retry_after") if isinstance(params, dict) else None
        try:
            after = float(body_after or response.headers.get("Retry-After") or 60)
        except (ValueError, TypeError):
            after = 86400
        raise ChannelRateLimited(max(60, min(after, 86400)) if math.isfinite(after) else 86400)
    if response.status_code >= 500:
        raise ChannelUnknown("channel result unknown")
    return response


class Telegram:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, base_url: str = "", client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self._token, self._chat = token.strip(), chat_id.strip()
        self.base_url = base_url
        self._client = client
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"Telegram(configured={self.configured})"

    @property
    def configured(self) -> bool:
        return bool(self._token and self._chat)

    def send(self, payload: dict) -> None:
        self._send(payload)

    def send_once(self, payload: dict, *, allowed=None) -> None:
        self._send(payload, once=True, allowed=allowed)

    def _send(self, payload: dict, *, once: bool = False, allowed=None) -> None:
        if not self.configured:
            raise ChannelError("not configured")
        text, link = message(payload, self.base_url)
        body = {"chat_id": self._chat, "text": text + (f"\n{link}" if link else ""),
                "disable_web_page_preview": True}
        client = self._client or net.client()
        try:
            url = f"{TELEGRAM_API}/bot{self._token}/sendMessage"
            resp = _post_once(client, url, allowed, json=body) if once else _post(client, url, self._sleep, json=body)
        finally:
            if self._client is None:
                client.close()
        if resp.status_code != 200:
            if once:
                raise ChannelError("telegram rejected")
            code = _json(resp).get("error_code") or resp.status_code
            raise ChannelError(f"telegram {code}")
        if once and _json(resp).get("ok") is not True:
            raise ChannelUnknown("channel result unknown")


def _json(resp: httpx.Response) -> dict:
    try:
        got = resp.json()
        return got if isinstance(got, dict) else {}
    except ValueError:
        return {}


class ChannelStore:
    """The ``notify_channels`` rows: per channel on/off and per-kind switches. The token columns
    (``secret`` · ``*_expires_at`` · ``needs_reconnect``) belonged to the channel #986 retired: nothing
    writes them now but the NOT NULL default on insert."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def get(self, channel: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(notify_channels).where(notify_channels.c.channel == channel)).mappings().first()
        if row is None:
            return {"channel": channel, "enabled": False, "kinds": {}, "secret": None, "access_expires_at": None,
                    "refresh_expires_at": None, "needs_reconnect": False}
        return dict(row)

    def _upsert(self, channel: str, values: dict) -> None:
        """Insert the row, or (another thread made it first) write the same values over it."""
        values = {"enabled": False, "kinds": {}, "needs_reconnect": False, **values, "updated_at": now()}
        dialect = self.engine.dialect.name
        stmt = (postgresql.insert if dialect == "postgresql" else sqlite.insert)(notify_channels).values(
            channel=channel, **values)
        with self.engine.begin() as conn:
            conn.execute(stmt.on_conflict_do_update(index_elements=["channel"],
                                                    set_={k: stmt.excluded[k] for k in values}))

    def set_prefs(self, channel: str, enabled: bool | None = None, kinds: dict | None = None) -> dict:
        cur = self.get(channel)
        merged = {**(cur["kinds"] or {}), **{k: bool(v) for k, v in (kinds or {}).items()}}
        with self.engine.begin() as conn:
            hit = conn.execute(update(notify_channels).where(notify_channels.c.channel == channel).values(
                enabled=cur["enabled"] if enabled is None else bool(enabled), kinds=merged, updated_at=now()))
        if not hit.rowcount:
            self._upsert(channel, {"enabled": bool(enabled), "kinds": merged})
        return self.get(channel)

    def wants(self, channel: str, kinds: tuple[str, ...]) -> bool:
        row = self.get(channel)
        return bool(row["enabled"]) and all((row["kinds"] or {}).get(k, True) for k in kinds)
