"""Settings read once from the environment (and .env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _list(name: str) -> list[str]:
    return [part.strip() for part in os.getenv(name, "").split(",") if part.strip()]


@dataclass(frozen=True)
class Settings:
    # sqlite:///data/desk.db locally; postgresql+psycopg://user:pw@host:5432/db?options=-csearch_path%3Ddesk
    database_url: str = field(default_factory=lambda: os.getenv("DESK_DATABASE_URL", "sqlite:///data/desk.db"))
    # Fake runs replay a scripted debate: build the UI without spending the plan's quota.
    fake_runs: bool = field(default_factory=lambda: _bool("DESK_FAKE_RUNS"))
    fake_delay: float = field(default_factory=lambda: float(os.getenv("DESK_FAKE_DELAY", "0.6")))
    # One run at a time by default: every run draws on the same ChatGPT plan.
    max_concurrent_runs: int = field(default_factory=lambda: int(os.getenv("DESK_MAX_CONCURRENT_RUNS", "1")))
    # Login sessions: hard limit from sign-in, and sign-out after this long without a request.
    session_max_hours: float = field(default_factory=lambda: float(os.getenv("DESK_SESSION_MAX_HOURS", "72")))
    session_idle_hours: float = field(default_factory=lambda: float(os.getenv("DESK_SESSION_IDLE_HOURS", "12")))
    # Secure cookies need HTTPS; browsers treat http://localhost as secure, so this stays on in dev too.
    cookie_secure: bool = field(default_factory=lambda: _bool("DESK_COOKIE_SECURE", True))
    # FastAPI's /docs and /openapi.json. Even when on, they sit behind the login.
    enable_docs: bool = field(default_factory=lambda: _bool("DESK_ENABLE_DOCS"))
    # Finished runs (and their events) older than this are deleted at start and once a day. 0 keeps all.
    retention_days: int = field(default_factory=lambda: int(os.getenv("DESK_RETENTION_DAYS", "90")))
    cors_origins: list[str] = field(default_factory=lambda: _list("DESK_CORS_ORIGINS"))

    # Default investing style for new runs (desk.profile.PROFILES).
    investor_profile: str = field(
        default_factory=lambda: os.getenv("DESK_INVESTOR_PROFILE", "aggressive_leverage"))
    broker: str = field(default_factory=lambda: os.getenv("DESK_BROKER", "mock").lower())
    toss_client_id: str = field(default_factory=lambda: os.getenv("TOSS_CLIENT_ID", ""))
    toss_client_secret: str = field(default_factory=lambda: os.getenv("TOSS_CLIENT_SECRET", ""))
    toss_account_seq: str = field(default_factory=lambda: os.getenv("TOSS_ACCOUNT_SEQ", ""))
    # Toss gives a bare 6-digit code for KR listings; Yahoo needs .KS (KOSPI) or .KQ (KOSDAQ).
    kosdaq_symbols: list[str] = field(default_factory=lambda: _list("DESK_KOSDAQ_SYMBOLS"))

    # semibot's SQLite (regime/trigger log), read-only. Empty = panel hidden.
    semibot_db_path: str = field(default_factory=lambda: os.getenv("SEMIBOT_DB_PATH", ""))


def load() -> Settings:
    return Settings()
