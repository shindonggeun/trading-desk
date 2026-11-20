"""The app's own log lines (logger ``desk``, #676).

uvicorn configures only its own loggers, so the ``desk.*`` lines had no handler: Python's last-resort
handler printed WARNING and up, and every INFO line (the ``toss flow`` / ``kis flow`` cadence lines,
desk.market_flow; job notes) was dropped in the container log. Here ``desk`` gets one stderr handler at
``DESK_LOG_LEVEL`` (default INFO). Records still propagate (the root logger has no handler under uvicorn,
so nothing prints twice; tests' log capture keeps working). A desk module too chatty at INFO goes into
QUIET at WARNING.
"""

from __future__ import annotations

import logging
import sys

FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
QUIET: dict[str, int] = {}          # desk module -> its own level (none needed so far)


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
    for name, quiet in QUIET.items():
        logging.getLogger(name).setLevel(quiet)
    return lvl
