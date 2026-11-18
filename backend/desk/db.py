"""Database schema and engine. SQLAlchemy Core, so the same code runs on SQLite
(local dev, tests) and PostgreSQL (the homelab). Alembic owns schema changes:
``migrate()`` brings any database to head and runs on every start."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    create_engine,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, make_url

MIGRATIONS = Path(__file__).parent / "migrations"

# Explicit constraint names keep Alembic diffs stable across SQLite and PostgreSQL.
metadata = MetaData(naming_convention={
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
})
# none_as_null: a Python None is SQL NULL, not the JSON literal null.
Json = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")
Timestamp = DateTime(timezone=True)

runs = Table(
    "runs", metadata,
    Column("id", String(32), primary_key=True),
    Column("ticker", String(20), nullable=False),
    Column("trade_date", String(10), nullable=False),
    Column("status", String(16), nullable=False),        # queued | running | done | failed
    Column("options", Json, nullable=False),
    Column("plan", Json),
    Column("error", Text),
    Column("report_path", Text),
    Column("created_at", Timestamp, nullable=False),
    Column("started_at", Timestamp),
    Column("finished_at", Timestamp),
    # How the plan played out on later bars (desk.outcome): target | stop | both | expired | unfilled |
    # open | waiting | invalid | unscorable | error. NULL = not scored yet.
    Column("outcome", Json),
    Column("outcome_status", String(16)),
    Column("scored_at", Timestamp),
    Index("ix_runs_created_at", "created_at"),
    Index("ix_runs_outcome_status", "outcome_status"),
)

events = Table(
    "events", metadata,
    Column("run_id", String(32), ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True),
    Column("seq", Integer, primary_key=True, autoincrement=False),
    Column("ts", Timestamp, nullable=False),
    Column("type", String(16), nullable=False),          # status | message | tool | plan | error | done
    Column("agent", String(64)),
    Column("content", Text),
    Column("data", Json),
)

# Name search over the broker's listing (desk.universe). Rebuilt whole once a day.
instruments = Table(
    "instruments", metadata,
    Column("symbol", String(20), primary_key=True),      # broker symbol: 005930, NVDA, BRK.B
    Column("yahoo", String(24), nullable=False),          # what TradingAgents reads: 005930.KS, BRK-B
    Column("name", String(200), nullable=False),          # Korean name from Toss
    Column("name_key", String(200), nullable=False),      # lower-cased, spaces removed: "kodex레버리지"
    Column("market", String(16), nullable=False),
    Column("security_type", String(24), nullable=False),
    Column("updated_at", Timestamp, nullable=False),
)

# One row in practice: accounts are made with `python -m desk.cli create-user`, never over HTTP.
users = Table(
    "users", metadata,
    Column("id", Integer, primary_key=True),
    Column("username", String(64), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("created_at", Timestamp, nullable=False),
    Column("password_changed_at", Timestamp, nullable=False),
    # TOTP (desk.otp). Secrets are Fernet-sealed with DESK_OTP_KEY: a copy of the table mints no codes.
    Column("totp_secret", Text),                         # active secret; NULL = OTP off
    Column("totp_pending", Text),                        # secret being enrolled, until a code confirms it
    Column("totp_pending_at", Timestamp),                # enrolment expires PENDING_TTL after this
    Column("totp_enabled_at", Timestamp),
    Column("totp_last_step", Integer),                   # last accepted 30 s step: a code works once
)

# One-time codes for when the phone is gone. Only SHA-256 is stored; used_at marks a spent code.
recovery_codes = Table(
    "recovery_codes", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("code_hash", String(64), nullable=False),
    Column("used_at", Timestamp),
)

# The cookie holds a random token; only its SHA-256 is stored, so a leaked table logs no one in.
sessions = Table(
    "sessions", metadata,
    Column("token_hash", String(64), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("created_at", Timestamp, nullable=False),
    Column("last_seen_at", Timestamp, nullable=False),
    Column("expires_at", Timestamp, nullable=False),
    Column("ip", String(64)),
    Column("user_agent", String(256)),
)

# Starred tickers per account (#134), in the order the owner set. The dashboard and the quote board read them.
favorites = Table(
    "favorites", metadata,
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("ticker", String(20), primary_key=True),      # clean_ticker form: NVDA, 005930.KS
    Column("name", String(200)),                          # display name; NULL when the listing has none
    Column("position", Integer, nullable=False),
    Column("created_at", Timestamp, nullable=False),
)

# Sector tabs per account (#156): a named, ordered group of tickers ([{ticker, name}] in JSON).
# KOSPI200 front-month futures, a snapshot every few minutes of the session (#254): investor net
# contracts / value (million won), price, open interest, basis. The day's last one is its daily row.
futures_snapshots = Table(
    "futures_snapshots", metadata,
    Column("id", Integer, primary_key=True),
    Column("at", Timestamp, nullable=False),
    Column("trade_date", String(10), nullable=False),           # KST YYYY-MM-DD
    Column("code", String(12), nullable=False),
    Column("price", Float),
    Column("open_interest", Float),
    Column("market_basis", Float),
    Column("foreign_contracts", Float),
    Column("foreign_value_mn", Float),
    Column("individual_contracts", Float),
    Column("individual_value_mn", Float),
    Column("institution_contracts", Float),
    Column("institution_value_mn", Float),
    # #359 spot investor net at the same moment (running total for the day, 100 million KRW)
    *[Column(f"{m}_{c}_100m", Float) for m in ("kospi", "kosdaq") for c in ("foreign", "institution", "individual")],
    Index("ix_futures_snapshots_trade_date_at", "trade_date", "at"),
)

sector_groups = Table(
    "sector_groups", metadata,
    Column("id", Integer, primary_key=True),
    # the (user_id, name) unique index also serves lookups by account
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("name", String(40), nullable=False),
    Column("position", Integer, nullable=False),
    Column("tickers", Json, nullable=False),
    Column("created_at", Timestamp, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
    UniqueConstraint("user_id", "name", name="uq_sector_groups_user_id_name"),
)

# The desk chat (#314): a conversation per thread, scoped to its account. ``tools`` keeps what the
# model looked up for an answer (name, arguments, a short summary) - never the raw tool output.
chat_threads = Table(
    "chat_threads", metadata,
    Column("id", String(32), primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("title", String(80), nullable=False),
    Column("created_at", Timestamp, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
    Index("ix_chat_threads_user_id_updated_at", "user_id", "updated_at"),
)

chat_messages = Table(
    "chat_messages", metadata,
    Column("id", Integer, primary_key=True),
    Column("thread_id", String(32), ForeignKey("chat_threads.id", ondelete="CASCADE"), nullable=False),
    Column("role", String(16), nullable=False),                  # user | assistant
    Column("content", Text, nullable=False),
    Column("tools", Json),
    Column("model", String(64)),                                 # #404 the answer's model id
    Column("created_at", Timestamp, nullable=False),
    Index("ix_chat_messages_thread_id_id", "thread_id", "id"),
)

# #361: web push subscriptions per account (endpoint + the browser's keys) and the foreign-flow
# turns already pushed (one row per turn - a re-judged snapshot does not push it twice).
push_subscriptions = Table(
    "push_subscriptions", metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
    Column("endpoint", String(1024), nullable=False, unique=True),
    Column("keys", Json, nullable=False),                         # {p256dh, auth}
    Column("device", String(80), nullable=False),
    Column("kinds", Json, nullable=False),                        # {"flow": true}
    Column("created_at", Timestamp, nullable=False),
)

flow_alerts_sent = Table(
    "flow_alerts_sent", metadata,
    Column("id", Integer, primary_key=True),
    Column("trade_date", String(10), nullable=False),
    Column("market", String(16), nullable=False),                 # futures | kospi | kosdaq
    Column("kind", String(16), nullable=False),                   # buy_turn | sell_turn
    Column("turned_at", String(32), nullable=False),              # the turn's ISO time
    Column("value", Float),
    Column("text", String(300), nullable=False),
    Column("created_at", Timestamp, nullable=False),
    UniqueConstraint("trade_date", "market", "kind", "turned_at", name="uq_flow_alerts_sent_turn"),
    Index("ix_flow_alerts_sent_trade_date", "trade_date"),
)

# #579 investor flow shifts and NQ=F alarms (desk.market_alerts): the day's timeline and its push record
market_alerts = Table(
    "market_alerts", metadata,
    Column("id", Integer, primary_key=True),
    Column("trade_date", String(10), nullable=False),             # KST date of the alert
    Column("kind", String(16), nullable=False),                   # flow_shift | nq_alert
    Column("rule", String(24), nullable=False),                   # sell_grow | turn ... | nq_drop ...
    Column("market", String(16), nullable=False),                 # kospi | kosdaq | futures | nq
    Column("who", String(16), nullable=False),                    # foreign | institution | individual | all
    Column("severity", String(8), nullable=False),                # info | caution | strong
    Column("at", String(32), nullable=False),                     # the moment judged (ISO, KST)
    Column("value", Float),
    Column("prev", Float),
    Column("text", String(300), nullable=False),
    Column("pushed", Boolean, nullable=False),
    Column("created_at", Timestamp, nullable=False),
    UniqueConstraint("trade_date", "kind", "rule", "market", "who", "at", name="uq_market_alerts_moment"),
    Index("ix_market_alerts_trade_date", "trade_date"),
)

# #580 the alert channels beside web push (desk.notify_channels): one row per channel, the desk's own
notify_channels = Table(
    "notify_channels", metadata,
    Column("channel", String(16), primary_key=True),              # telegram | kakao
    Column("enabled", Boolean, nullable=False),
    Column("kinds", Json, nullable=False),                        # kind -> on; a kind not stored is on
    Column("secret", Text),                                       # Kakao tokens, Fernet-sealed (DESK_NOTIFY_KEY)
    Column("access_expires_at", Timestamp),
    Column("refresh_expires_at", Timestamp),
    Column("needs_reconnect", Boolean, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
)

reports = Table(
    "reports", metadata,
    Column("id", String(32), primary_key=True),
    Column("created_at", Timestamp, nullable=False),
    Column("symbol", String(32), nullable=False),                 # the subject (an index or a favorite)
    Column("title", String(200), nullable=False),
    Column("source", String(8), nullable=False),                  # ai | rules
    Column("body", Json, nullable=False),                         # the whole report (#495 contract)
    Index("ix_reports_created_at", "created_at"),
)

# #571 the morning market briefs: one or more a Korean day (the automatic one at 07:00, manual reruns)
morning_briefs = Table(
    "morning_briefs", metadata,
    Column("id", String(32), primary_key=True),
    Column("day", Date, nullable=False),                          # the Korean date it is for
    Column("created_at", Timestamp, nullable=False),
    Column("source", String(8), nullable=False),                  # ai | rules
    Column("model", String(64)),                                  # the model that reworded it (None: the desk's)
    Column("trigger", String(8), nullable=False),                 # auto | manual
    Column("body", Json, nullable=False),                         # the brief (#571 contract)
    Column("inputs", Json, nullable=False),                       # the numbers it was written from
    Index("ix_morning_briefs_day", "day"),
    Index("ix_morning_briefs_created_at", "created_at"),
)

# #527 the paper account's settings: one row (id 1) for the desk; version + 1 on every save
paper_settings = Table(
    "paper_settings", metadata,
    Column("id", Integer, primary_key=True, autoincrement=False),
    Column("body", Json, nullable=False),
    Column("version", Integer, nullable=False),
    Column("updated_at", Timestamp, nullable=False),
)


def now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def aware(value: datetime | None) -> datetime | None:
    """SQLite hands timestamps back without a zone; everything stored is UTC."""
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def make_engine(url: str) -> Engine:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite":
        if parsed.database and parsed.database != ":memory:":
            Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
        engine = create_engine(url, connect_args={"check_same_thread": False, "timeout": 30})

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        return engine
    # pool_pre_ping: the homelab DB restarts now and then; a dead pooled connection should not fail a request.
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5)


def alembic_config(url: str):
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS))
    # ConfigParser interpolation would eat a '%' in a password.
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


def migrate(url: str) -> None:
    from alembic import command

    command.upgrade(alembic_config(url), "head")
