"""The app's own log lines (logger ``desk``, #676).

uvicorn configures only its own loggers, so the ``desk.*`` lines had no handler: Python's last-resort
handler printed WARNING and up, and every INFO line (the ``toss flow`` / ``kis flow`` cadence lines,
desk.market_flow; job notes) was dropped in the container log. Here ``desk`` gets one stderr handler at
``DESK_LOG_LEVEL`` (default INFO). Records still propagate (the root logger has no handler under uvicorn,
so nothing prints twice; tests' log capture keeps working). A desk module too chatty at INFO goes into
QUIET at WARNING.

#712: log lines leave the box (files, a log collector), so they carry no login e-mail and no full client
address: a user is ``#<id>``, an address loses its last IPv4 octet (``mask_ip``). Uvicorn's access line
masks the address and omits the request target (``AccessMask``). Ingress fail2ban reads the separate
ingress nginx access log (Infra ``nginx-trading-login``).
"""

from __future__ import annotations

import ipaddress
import logging
import sys

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
QUIET: dict[str, int] = {}          # desk module -> its own level (none needed so far)
_ACCESS_FORMAT = '%s - "%s %s HTTP/%s" %d'
_HTTP_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"})
_HTTP_VERSIONS = frozenset({"1.0", "1.1", "2", "3"})


def mask_ip(ip: str | None) -> str:
    """``ip`` fit for a log line: IPv4 ``a.b.c.x``, IPv6 its /48 + ``::x``, anything else ``?``.

    Never raises: it sits on the login path, where an exception would turn a 401 into a 500."""
    try:
        addr = ipaddress.ip_address((ip or "").strip().split("%", 1)[0])    # no IPv6 zone (fe80::1%eth0)
        if addr.version == 6:          # an IPv4 inside (::ffff:a.b.c.d, 6to4 2002:abcd:...) shows as one
            addr = addr.ipv4_mapped or addr.sixtofour or addr
        if addr.version == 4:
            return str(addr).rsplit(".", 1)[0] + ".x"
        return ":".join(h.lstrip("0") or "0" for h in addr.exploded.split(":")[:3]) + "::x"
    except Exception:  # noqa: BLE001 - a log line is no reason to fail the request
        return "?"


class AccessMask(logging.Filter):
    """Keep only safe values in the five arguments expected by Uvicorn's AccessFormatter."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if record.msg != _ACCESS_FORMAT or not isinstance(args, tuple) or len(args) != 5:
            return False
        address, method, _, version, status = args
        if not isinstance(status, int) or not 100 <= status <= 999:
            return False
        record.args = (
            mask_ip(address.rpartition(":")[0]) if isinstance(address, str) else "?",
            method if isinstance(method, str) and method in _HTTP_METHODS else "?",
            "-",
            version if isinstance(version, str) and version in _HTTP_VERSIONS else "?",
            status,
        )
        return True


def configure(level: str | None) -> int:
    """Set the ``desk`` logger to ``level`` (an unknown name: INFO) with one stderr handler; the level set."""
    got = logging.getLevelName((level or "INFO").strip().upper())
    lvl = got if isinstance(got, int) else logging.INFO
    root = logging.getLogger("desk")
    root.setLevel(lvl)
    if not any(getattr(h, "desk_handler", False) for h in root.handlers):
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(FORMAT))
        handler.desk_handler = True
        root.addHandler(handler)
    access = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, AccessMask) for f in access.filters):
        access.addFilter(AccessMask())
    for name, quiet in QUIET.items():
        logging.getLogger(name).setLevel(quiet)
    return lvl
