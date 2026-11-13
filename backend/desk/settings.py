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
    # One run at a time by default: every run draws on the same ChatGPT plan. With 2 (#171), two
    # different instruments run side by side; the same one always waits for the run before it.
    max_concurrent_runs: int = field(default_factory=lambda: int(os.getenv("DESK_MAX_CONCURRENT_RUNS", "1")))
    # After the provider's rate limit (HTTP 429), one run at a time for this many minutes.
    rate_limit_cooldown_min: float = field(
        default_factory=lambda: float(os.getenv("DESK_RATE_LIMIT_COOLDOWN_MIN") or "30"))
    # #171: a run of the same ticker, day, horizon and language within this many minutes reuses the
    # news and fundamentals reports of the earlier one (price and technical work is always fresh). 0 = off.
    reuse_reports_min: float = field(default_factory=lambda: float(os.getenv("DESK_REUSE_REPORTS_MIN") or "0"))
    # Login sessions: hard limit from sign-in, and sign-out after this long without a request.
    session_max_hours: float = field(default_factory=lambda: float(os.getenv("DESK_SESSION_MAX_HOURS", "72")))
    session_idle_hours: float = field(default_factory=lambda: float(os.getenv("DESK_SESSION_IDLE_HOURS", "12")))
    # Secure cookies need HTTPS; browsers treat http://localhost as secure, so this stays on in dev too.
    cookie_secure: bool = field(default_factory=lambda: _bool("DESK_COOKIE_SECURE", True))
    # FastAPI's /docs and /openapi.json. Even when on, they sit behind the login.
    enable_docs: bool = field(default_factory=lambda: _bool("DESK_ENABLE_DOCS"))
    # Fernet key that seals TOTP secrets (python -m desk.cli otp-key). Empty = OTP cannot be enabled.
    otp_key: str = field(default_factory=lambda: os.getenv("DESK_OTP_KEY", ""))
    # Finished runs (and their events) older than this are deleted at start and once a day. 0 keeps all.
    retention_days: int = field(default_factory=lambda: int(os.getenv("DESK_RETENTION_DAYS", "90")))
    # Seconds between passes that score finished plans against later bars (desk.outcome). 0 = off.
    score_interval_sec: int = field(default_factory=lambda: int(os.getenv("DESK_SCORE_INTERVAL_SEC", "3600")))
    # Seconds between refreshes of the world news-flow themes from GDELT (desk.macro_news). 0 = off.
    macro_interval_sec: int = field(default_factory=lambda: int(os.getenv("DESK_MACRO_INTERVAL_SEC", "1800")))
    # Minutes between KOSPI200 futures snapshots in the session (desk.futures_history, #254). 0 = off.
    futures_snapshot_min: float = field(default_factory=lambda: float(os.getenv("DESK_FUTURES_SNAPSHOT_MIN", "15")))
    cors_origins: list[str] = field(default_factory=lambda: _list("DESK_CORS_ORIGINS"))

    # Default investing style for new runs (desk.profile.PROFILES).
    investor_profile: str = field(
        default_factory=lambda: os.getenv("DESK_INVESTOR_PROFILE", "aggressive_leverage"))
    broker: str = field(default_factory=lambda: os.getenv("DESK_BROKER", "mock").lower())
    toss_client_id: str = field(default_factory=lambda: os.getenv("TOSS_CLIENT_ID", ""))
    toss_client_secret: str = field(default_factory=lambda: os.getenv("TOSS_CLIENT_SECRET", ""), repr=False)
    toss_account_seq: str = field(default_factory=lambda: os.getenv("TOSS_ACCOUNT_SEQ", ""))
    # KIS Open API (desk.broker.kis_stream): real-time trades over its websocket. Empty = off.
    kis_app_key: str = field(default_factory=lambda: os.getenv("DESK_KIS_APP_KEY", ""), repr=False)
    kis_app_secret: str = field(default_factory=lambda: os.getenv("DESK_KIS_APP_SECRET", ""), repr=False)
    # Empty = the real-trading endpoints; mock-trading keys need the mock ones (.env.example).
    kis_ws_url: str = field(default_factory=lambda: os.getenv("DESK_KIS_WS_URL", ""))
    kis_rest_url: str = field(default_factory=lambda: os.getenv("DESK_KIS_REST_URL", ""))
    # Toss gives a bare 6-digit code for KR listings; Yahoo needs .KS (KOSPI) or .KQ (KOSDAQ).
    kosdaq_symbols: list[str] = field(default_factory=lambda: _list("DESK_KOSDAQ_SYMBOLS"))

    # Seconds between snapshots on GET /api/portfolio/stream (clamped to 2..60).
    portfolio_stream_sec: float = field(
        default_factory=lambda: float(os.getenv("DESK_PORTFOLIO_STREAM_SEC", "5")))

    # semibot's SQLite (regime/trigger log), read-only. Empty = panel hidden.
    semibot_db_path: str = field(default_factory=lambda: os.getenv("SEMIBOT_DB_PATH", ""))

    # #361 web push (VAPID). Any of the three empty = push off.
    vapid_public_key: str = field(default_factory=lambda: os.getenv("DESK_VAPID_PUBLIC_KEY", ""))
    vapid_private_key: str = field(default_factory=lambda: os.getenv("DESK_VAPID_PRIVATE_KEY", ""), repr=False)
    vapid_subject: str = field(default_factory=lambda: os.getenv("DESK_VAPID_SUBJECT", ""))


def load() -> Settings:
    return Settings()
