"""Telegram and KakaoTalk "send to me" as alert channels (#580), next to web push in desk.notify.

- **Telegram**: a BotFather bot's ``sendMessage`` to one chat. The token and the chat id come from the
  server env (``DESK_TELEGRAM_BOT_TOKEN`` · ``DESK_TELEGRAM_CHAT_ID``); either empty = the channel is off.
  The token sits in the request path, so httpx's request log line is redacted (``_RedactBotToken``).
- **KakaoTalk** "나에게 보내기" (free; sending to friends and AlimTalk are paid / reviewed): the owner
  connects once with Kakao OAuth (``talk_message`` consent). The tokens are Fernet-sealed with
  ``DESK_NOTIFY_KEY`` in ``notify_channels.secret``; the access token (hours) is refreshed with the
  refresh token (about two months) shortly before it expires, or once after a 401. A refused refresh
  (``invalid_grant``) marks the channel to reconnect - the settings screen asks for it.

Both send plain text: the alert's title, body and a link back to the desk (``DESK_PUBLIC_URL``).
Errors carry a short code only (HTTP status, Kakao / Telegram error code, exception type) - never a
token, the chat id or a URL holding them. A 429 or a 5xx is retried (Telegram's ``retry_after``
honoured, capped); the caller (desk.notify) runs channels off the alert's thread.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode, urlsplit

import httpx
from sqlalchemy import select, update
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Engine

from .db import notify_channels, now

log = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org"
KAKAO_AUTH = "https://kauth.kakao.com"
KAKAO_API = "https://kapi.kakao.com"
CHANNELS = ("telegram", "kakao")
TIMEOUT_SEC = 10.0
TRIES = 3
RETRY_CAP_SEC = 30.0
REFRESH_AHEAD_SEC = 60
RECONNECT_AHEAD = timedelta(days=7)      # the refresh token this close to its end: ask to reconnect
TEXT_MAX = 900                           # Kakao's text template takes 200 shown / 1000 sent; Telegram 4096


class ChannelError(Exception):
    """A send that did not go; ``str()`` is a short code safe to log and to show."""


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
        if not self.configured:
            raise ChannelError("not configured")
        text, link = message(payload, self.base_url)
        body = {"chat_id": self._chat, "text": text + (f"\n{link}" if link else ""),
                "disable_web_page_preview": True}
        client = self._client or httpx.Client()
        try:
            resp = _post(client, f"{TELEGRAM_API}/bot{self._token}/sendMessage", self._sleep, json=body)
        finally:
            if self._client is None:
                client.close()
        if resp.status_code != 200:
            code = _json(resp).get("error_code") or resp.status_code
            raise ChannelError(f"telegram {code}")


def _json(resp: httpx.Response) -> dict:
    try:
        got = resp.json()
        return got if isinstance(got, dict) else {}
    except ValueError:
        return {}


class ChannelStore:
    """The ``notify_channels`` rows: per channel on/off, per-kind switches and (Kakao) the sealed tokens."""

    def __init__(self, engine: Engine, box) -> None:
        self.engine = engine
        self.box = box                      # desk.otp.SecretBox over DESK_NOTIFY_KEY

    def get(self, channel: str) -> dict:
        with self.engine.connect() as conn:
            row = conn.execute(select(notify_channels).where(notify_channels.c.channel == channel)).mappings().first()
        if row is None:
            return {"channel": channel, "enabled": False, "kinds": {}, "secret": None, "access_expires_at": None,
                    "refresh_expires_at": None, "needs_reconnect": False}
        return dict(row)

    def _upsert(self, channel: str, values: dict) -> None:
        values = {**values, "updated_at": now()}
        dialect = self.engine.dialect.name
        ins = (postgresql.insert if dialect == "postgresql" else sqlite.insert)(notify_channels)
        stmt = ins.values(channel=channel, enabled=values.pop("enabled", False), kinds=values.pop("kinds", {}),
                          needs_reconnect=values.pop("needs_reconnect", False), **values)
        keep = {k: stmt.excluded[k] for k in ("updated_at", *values)}
        with self.engine.begin() as conn:
            conn.execute(stmt.on_conflict_do_update(index_elements=["channel"], set_=keep))

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

    def tokens(self, channel: str) -> dict | None:
        sealed = self.get(channel)["secret"]
        return json.loads(self.box.open(sealed)) if sealed else None

    def save_tokens(self, channel: str, tokens: dict, access_expires_at: datetime,
                    refresh_expires_at: datetime | None) -> None:
        cur = self.get(channel)
        values = {"secret": self.box.seal(json.dumps(tokens)), "access_expires_at": access_expires_at,
                  "refresh_expires_at": refresh_expires_at or cur["refresh_expires_at"], "needs_reconnect": False}
        with self.engine.begin() as conn:
            hit = conn.execute(update(notify_channels).where(notify_channels.c.channel == channel)
                               .values(updated_at=now(), **values))
        if not hit.rowcount:
            self._upsert(channel, {**values, "enabled": True, "kinds": {}})

    def mark_reconnect(self, channel: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(notify_channels).where(notify_channels.c.channel == channel)
                         .values(needs_reconnect=True, updated_at=now()))

    def forget(self, channel: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(update(notify_channels).where(notify_channels.c.channel == channel).values(
                secret=None, access_expires_at=None, refresh_expires_at=None, needs_reconnect=False,
                enabled=False, updated_at=now()))


def _aware(at: datetime | None) -> datetime | None:
    return None if at is None else (at if at.tzinfo else at.replace(tzinfo=UTC))


class Kakao:
    name = "kakao"

    def __init__(self, rest_key: str, client_secret: str, redirect_uri: str, store: ChannelStore,
                 base_url: str = "", client: httpx.Client | None = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], datetime] | None = None) -> None:
        self._key, self._secret = rest_key.strip(), client_secret.strip()
        self.redirect_uri = redirect_uri
        self.store = store
        self.base_url = base_url
        self._client = client
        self._sleep = sleep
        self._clock = clock or (lambda: datetime.now(UTC))

    def __repr__(self) -> str:
        return f"Kakao(configured={self.configured})"

    @property
    def configured(self) -> bool:
        # the client secret too: an OAuth code left in an access log is then useless on its own
        return bool(self._key and self._secret and self.redirect_uri.startswith("https://")
                    and self.store.box.available)

    def authorize_url(self, state: str) -> str:
        return f"{KAKAO_AUTH}/oauth/authorize?" + urlencode({
            "client_id": self._key, "redirect_uri": self.redirect_uri, "response_type": "code",
            "scope": "talk_message", "state": state})

    def _token_call(self, form: dict) -> dict:
        form = {"client_id": self._key, "client_secret": self._secret, **form}
        client = self._client or httpx.Client()
        try:
            resp = _post(client, f"{KAKAO_AUTH}/oauth/token", self._sleep, data=form)
        finally:
            if self._client is None:
                client.close()
        got = _json(resp)
        if resp.status_code != 200 or "access_token" not in got:
            raise ChannelError(f"kakao token {got.get('error') or resp.status_code}")
        return got

    def _keep(self, got: dict, refresh_token: str | None = None) -> dict:
        at = self._clock()
        tokens = {"access_token": got["access_token"], "refresh_token": got.get("refresh_token") or refresh_token}
        refresh_end = (at + timedelta(seconds=int(got["refresh_token_expires_in"]))
                       if got.get("refresh_token_expires_in") else None)
        self.store.save_tokens(self.name, tokens, at + timedelta(seconds=int(got.get("expires_in") or 0)), refresh_end)
        return tokens

    def connect(self, code: str) -> None:
        """The OAuth callback's code -> sealed tokens; the channel turned on."""
        self._keep(self._token_call({"grant_type": "authorization_code", "redirect_uri": self.redirect_uri,
                                     "code": code}))

    def _refresh(self, tokens: dict) -> dict:
        try:
            got = self._token_call({"grant_type": "refresh_token", "refresh_token": tokens.get("refresh_token") or ""})
        except ChannelError as exc:
            if "invalid_grant" in str(exc) or str(exc).endswith(" 401"):    # the refresh token is done
                self.store.mark_reconnect(self.name)
            raise
        return self._keep(got, tokens.get("refresh_token"))

    def status(self) -> dict:
        row = self.store.get(self.name)
        refresh_end = _aware(row["refresh_expires_at"])
        soon = refresh_end is not None and refresh_end - self._clock() < RECONNECT_AHEAD
        return {"connected": bool(row["secret"]), "needs_reconnect": bool(row["needs_reconnect"]) or soon,
                "refresh_expires_at": refresh_end.isoformat(timespec="seconds") if refresh_end else None}

    def send(self, payload: dict) -> None:
        if not self.configured:
            raise ChannelError("not configured")
        tokens = self.store.tokens(self.name)
        if not tokens:
            raise ChannelError("not connected")
        expires = _aware(self.store.get(self.name)["access_expires_at"])
        if expires is None or expires - self._clock() < timedelta(seconds=REFRESH_AHEAD_SEC):
            tokens = self._refresh(tokens)
        text, link = message(payload, self.base_url)
        template = {"object_type": "text", "text": text, "button_title": "열기",
                    "link": {"web_url": link, "mobile_web_url": link} if link else {}}
        resp = self._memo(tokens, template)
        if resp.status_code == 401:
            resp = self._memo(self._refresh(tokens), template)
        if resp.status_code != 200:
            raise ChannelError(f"kakao {_json(resp).get('code') or resp.status_code}")

    def _memo(self, tokens: dict, template: dict) -> httpx.Response:
        client = self._client or httpx.Client()
        try:
            return _post(client, f"{KAKAO_API}/v2/api/talk/memo/default/send", self._sleep,
                         headers={"Authorization": f"Bearer {tokens['access_token']}"},
                         data={"template_object": json.dumps(template, ensure_ascii=False)})
        finally:
            if self._client is None:
                client.close()


def origin_of(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else ""
