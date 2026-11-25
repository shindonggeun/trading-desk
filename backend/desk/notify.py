"""Where every alert leaves the server (#579): one ``send`` for each channel.

Web push (desk.push, per-kind switches on each subscription) is sent here and now, as before. #580's
Telegram (desk.notify_channels) is installed by the app (``install``) and sent on one worker thread: a
slow or rate-limited channel (retries, Telegram's ``retry_after``) never holds up the alert's own thread
(the snapshot loop). A channel's failure is logged as a short code and never stops the alert from being
recorded, nor the other channels. Each channel has its own on/off and per-kind switches
(``ChannelStore.wants``).

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
# a test send waits behind the queued sends on the one worker, then for its own worst case: TRIES x 10 s
# with the back-off between (desk.notify_channels)
TEST_TIMEOUT_SEC = 150.0

_lock = threading.Lock()
_channels: dict = {}                 # name -> channel (desk.notify_channels.Telegram)
_store = None                        # desk.notify_channels.ChannelStore
_pool: ThreadPoolExecutor | None = None
_closed = False                      # after shutdown: nothing new is queued (until the next install)
_slots = threading.BoundedSemaphore(QUEUE_MAX)


def install(channels: list, store) -> None:
    """The app's channels (configured or not: ``test`` reports which) and their settings store."""
    global _channels, _store, _closed
    shutdown()
    with _lock:
        _channels, _store, _closed = {c.name: c for c in channels}, store, False


def channel(name: str):
    return _channels.get(name)


def _executor() -> ThreadPoolExecutor:
    global _pool
    with _lock:
        if _closed:
            raise RuntimeError("notify is shut down")
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="notify")
        return _pool


def shutdown() -> None:
    """Stop the worker; sends still queued are dropped, and none is taken after (the app's lifespan end:
    a judging thread finishing meanwhile must not start a new worker the interpreter would wait for)."""
    global _pool, _closed
    with _lock:
        pool, _pool, _closed = _pool, None, True
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


def _submit(ch, payload: dict, what: str, allowed=None) -> Future | None:
    def deliver():
        return _deliver(ch, payload, what) if allowed is None or allowed() else None

    if not _slots.acquire(blocking=False):
        log.warning("%s send of %s dropped: %d already queued", ch.name, what, QUEUE_MAX)
        return None
    try:
        future = _executor().submit(deliver)
    except RuntimeError:                  # shut down meanwhile
        _slots.release()
        return None
    future.add_done_callback(lambda _f: _slots.release())
    return future


def send(pusher, *kinds: str, payload: dict, user_id: int | None = None,
         use_channels: bool = True, allowed=None) -> int:
    """Deliver ``payload`` ({title, body, tag, url, ...}) to the web push subscriptions and the channels
    that have all of ``kinds`` on; the number of web pushes delivered (the channels go on afterwards).
    #694 ``user_id``: web push to that user's devices only (the channels are the owner's own)."""
    sent = 0
    if allowed is not None and not allowed():
        return sent
    if pusher is not None:
        try:
            sent += (pusher.broadcast(*kinds, payload=payload) if user_id is None
                     else pusher.broadcast(*kinds, payload=payload, user_id=user_id))
        except Exception as exc:  # noqa: BLE001 - a push never stops recording
            log.warning("push of %s failed: %s", "/".join(kinds), type(exc).__name__)
    if not use_channels:
        return sent
    store, channels = _store, list(_channels.values())
    for ch in channels:
        try:
            if not ch.configured or not store.wants(ch.name, kinds):
                continue
        except Exception as exc:  # noqa: BLE001 - a settings read never stops the other channels
            log.warning("%s settings unreadable: %s", ch.name, type(exc).__name__)
            continue
        _submit(ch, payload, "/".join(kinds), allowed)
    return sent


def run(fn, timeout: float | None = None):
    """``fn()`` on the channels' worker (a test send); its result."""
    try:
        future = _executor().submit(fn)
    except RuntimeError:
        raise ChannelError("shut down") from None
    try:
        return future.result(timeout=TEST_TIMEOUT_SEC if timeout is None else timeout)
    except FutureTimeout:
        future.cancel()
        raise ChannelError("timeout") from None


def test(name: str, payload: dict, *, allowed=None) -> str | None:
    """A test message on one channel, whatever its switches; None when it went, else the short code."""
    ch = _channels.get(name)
    if ch is None:
        return "unknown channel"
    if not ch.configured:
        return "not configured"
    return run(lambda: _deliver(ch, payload, "test") if allowed is None or allowed() else "authorization changed")


def drain(timeout: float = 10.0) -> None:
    """Wait for the queued sends (tests)."""
    pool = _pool
    if pool is not None:
        pool.submit(lambda: None).result(timeout=timeout)
