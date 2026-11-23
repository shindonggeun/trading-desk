"""#859 TLS contexts for outbound httpx clients, built once per thread instead of once per client.

``httpx.AsyncClient()`` builds a fresh ``ssl.SSLContext`` each time - it reads the whole CA bundle - and
the many clients made per call (a news feed, a calendar day, a filing) did that on the event loop: 13-44 ms
each in production, hundreds of ms while worker threads hold the GIL, during which every request waited
(``/api/health`` 1.6 s). Now each thread keeps one context: the loop's is built off the loop by ``warm()``
at startup, a worker thread's on its first client. Per thread, not one for all: httpcore sets the ALPN list
on the context for every new connection, which must not race a handshake in another thread. It is httpx's
own default (certifi, or ``SSL_CERT_FILE`` / ``SSL_CERT_DIR`` when set), so verification is unchanged.
"""

from __future__ import annotations

import asyncio
import logging
import ssl
import threading

import httpx

log = logging.getLogger(__name__)

_lock = threading.Lock()
_by_thread: dict[int, ssl.SSLContext] = {}


def ssl_context() -> ssl.SSLContext:
    """This thread's context, built on first use (a thread id reused after its thread ended reuses it)."""
    tid = threading.get_ident()
    ctx = _by_thread.get(tid)
    if ctx is None:
        ctx = httpx.create_ssl_context()
        with _lock:
            ctx = _by_thread.setdefault(tid, ctx)
    return ctx


def async_client(**kwargs) -> httpx.AsyncClient:
    """``httpx.AsyncClient(**kwargs)`` on this thread's context."""
    return httpx.AsyncClient(verify=ssl_context(), **kwargs)


def client(**kwargs) -> httpx.Client:
    """``httpx.Client(**kwargs)`` on this thread's context."""
    return httpx.Client(verify=ssl_context(), **kwargs)


async def warm() -> None:
    """Build the calling (loop) thread's context in a worker thread, so its first client finds it ready.
    A failure (a bad ``SSL_CERT_FILE``) is logged and left to the first client, as before."""
    tid = threading.get_ident()
    try:
        ctx = await asyncio.to_thread(httpx.create_ssl_context)
    except Exception as exc:  # noqa: BLE001 - the outbound calls fail on their own, the app still starts
        log.warning("TLS context not prepared: %s", exc)
        return
    with _lock:
        _by_thread.setdefault(tid, ctx)
