"""Where every alert leaves the server (#579): one ``send`` for each channel.

Web push (desk.push, per-kind switches on each subscription) is sent here and now, as before. #580's
Telegram and KakaoTalk (desk.notify_channels) are installed by the app (``install``) and sent on one
worker thread: a slow or rate-limited channel (retries, Telegram's ``retry_after``) never holds up the
alert's own thread (the snapshot loop), and one thread also keeps Kakao's token refresh from racing
itself. A channel's failure is logged as a short code and never stops the alert from being recorded,
nor the other channels. Each channel has its own on/off and per-kind switches (``ChannelStore.wants``).

The queue holds ``QUEUE_MAX`` sends; past it a send is dropped (logged). Sends still queued when the
server stops are lost - the alert itself stays in the database and on the web push.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

from .notify_channels import ChannelError

log = logging.getLogger(__name__)

QUEUE_MAX = 50
TEST_TIMEOUT_SEC = 60.0

_lock = threading.Lock()
_channels: dict = {}                 # name -> channel (desk.notify_channels.Telegram / Kakao)
_store = None                        # desk.notify_channels.ChannelStore
_pool: ThreadPoolExecutor | None = None
_slots = threading.BoundedSemaphore(QUEUE_MAX)


def install(channels: list, store) -> None:
    """The app's channels (configured or not: ``test`` reports which) and their settings store."""
    global _channels, _store
    shutdown()
    with _lock:
        _channels, _store = {c.name: c for c in channels}, store


def channel(name: str):
    return _channels.get(name)


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notify")
        return _pool


def shutdown() -> None:
    """Stop the worker; sends still queued are dropped (the app's lifespan end)."""
    global _pool
    with _lock:
        pool, _pool = _pool, None
    if pool is not None:
        pool.shutdown(wait=False, cancel_futures=True)


def _deliver(ch, payload: dict, what: str) -> str | None:
    """Send on the worker; None when it went, else the short code."""
    try:
        ch.send(payload)
        return None
    except ChannelError as exc:
        log.warning("%s send of %s failed: %s", ch.name, what, exc)
        return str(exc)
    except Exception as exc:  # noqa: BLE001 - never the message: it may hold a URL with a token
        log.warning("%s send of %s failed: %s", ch.name, what, type(exc).__name__)
        return type(exc).__name__


def _submit(ch, payload: dict, what: str) -> Future | None:
    if not _slots.acquire(blocking=False):
        log.warning("%s send of %s dropped: %d already queued", ch.name, what, QUEUE_MAX)
        return None
    try:
        future = _executor().submit(_deliver, ch, payload, what)
    except RuntimeError:                  # shut down meanwhile
        _slots.release()
        return None
    future.add_done_callback(lambda _f: _slots.release())
    return future


def send(pusher, *kinds: str, payload: dict) -> int:
    """Deliver ``payload`` ({title, body, tag, url, ...}) to the web push subscriptions and the channels
    that have all of ``kinds`` on; the number of web pushes delivered (the channels go on afterwards)."""
    sent = 0
    if pusher is not None:
        try:
            sent += pusher.broadcast(*kinds, payload=payload)
        except Exception as exc:  # noqa: BLE001 - a push never stops recording
            log.warning("push of %s failed: %s", "/".join(kinds), type(exc).__name__)
    store, channels = _store, list(_channels.values())
    for ch in channels:
        try:
            if not ch.configured or not store.wants(ch.name, kinds):
                continue
        except Exception as exc:  # noqa: BLE001 - a settings read never stops the other channels
            log.warning("%s settings unreadable: %s", ch.name, type(exc).__name__)
            continue
        _submit(ch, payload, "/".join(kinds))
    return sent


def run(fn, timeout: float = TEST_TIMEOUT_SEC):
    """``fn()`` on the channels' worker (a test send, Kakao's connect: never beside a refresh); its result."""
    future = _executor().submit(fn)
    try:
        return future.result(timeout=timeout)
    except FutureTimeout:
        future.cancel()
        raise ChannelError("timeout") from None


def test(name: str, payload: dict) -> str | None:
    """A test message on one channel, whatever its switches; None when it went, else the short code."""
    ch = _channels.get(name)
    if ch is None:
        return "unknown channel"
    if not ch.configured:
        return "not configured"
    return run(lambda: _deliver(ch, payload, "test"))


def drain(timeout: float = 10.0) -> None:
    """Wait for the queued sends (tests)."""
    pool = _pool
    if pool is not None:
        pool.submit(lambda: None).result(timeout=timeout)
