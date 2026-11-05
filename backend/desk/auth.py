"""Single-owner login: argon2id passwords, server-side sessions, a login throttle.

The browser holds a random token in an HttpOnly cookie; the database holds only
its SHA-256, so a copy of the table cannot be replayed. Accounts are created from
the command line (``python -m desk.cli create-user``) — there is no sign-up route.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.engine import Engine

from . import otp
from .db import aware, now, recovery_codes, sessions, users

# __Host-: Secure, Path=/, no Domain — a sibling *.8llow8llowme.com app cannot plant or shadow it.
COOKIE = "__Host-desk_session"
MIN_PASSWORD = 12
# Refresh last_seen_at at most this often: one write per minute, not per request.
TOUCH_EVERY = timedelta(minutes=1)

# argon2 takes ~64 MiB per verify: cap concurrent verifies so a burst of logins cannot exhaust the host.
_VERIFY_SLOTS = threading.BoundedSemaphore(2)
_hasher = PasswordHasher()   # argon2id, library defaults (RFC 9106 low-memory profile)
# Verified against when the username is unknown, so a miss costs the same time as a wrong password.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class Busy(Exception):
    """Too many password checks already running."""


@dataclass(frozen=True)
class User:
    id: int
    username: str


class Auth:
    def __init__(self, engine: Engine, max_age: timedelta, idle: timedelta) -> None:
        self.engine = engine
        self.max_age = max_age
        self.idle = idle

    # --- accounts (CLI) --------------------------------------------------------------

    def create_user(self, username: str, password: str) -> None:
        _check_password(password)
        stamp = now()
        with self.engine.begin() as conn:
            conn.execute(users.insert().values(username=username, password_hash=_hasher.hash(password),
                                               created_at=stamp, password_changed_at=stamp))

    def set_password(self, username: str, password: str) -> bool:
        """Also signs every browser out: a new password means the old sessions are suspect."""
        _check_password(password)
        with self.engine.begin() as conn:
            user_id = conn.execute(select(users.c.id).where(users.c.username == username)).scalar()
            if user_id is None:
                return False
            conn.execute(update(users).where(users.c.id == user_id)
                         .values(password_hash=_hasher.hash(password), password_changed_at=now()))
            conn.execute(delete(sessions).where(sessions.c.user_id == user_id))
        return True

    def revoke_all(self, username: str) -> int:
        with self.engine.begin() as conn:
            user_id = conn.execute(select(users.c.id).where(users.c.username == username)).scalar()
            if user_id is None:
                return 0
            return conn.execute(delete(sessions).where(sessions.c.user_id == user_id)).rowcount

    # --- login -----------------------------------------------------------------------

    def authenticate(self, username: str, password: str) -> User | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(users.c.id, users.c.username, users.c.password_hash)
                               .where(users.c.username == username)).first()
        stored = row.password_hash if row else _DUMMY_HASH
        if not _VERIFY_SLOTS.acquire(timeout=5):
            raise Busy
        try:
            _hasher.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return None
        finally:
            _VERIFY_SLOTS.release()
        if row is None:
            return None
        if _hasher.check_needs_rehash(stored):
            with self.engine.begin() as conn:
                conn.execute(update(users).where(users.c.id == row.id)
                             .values(password_hash=_hasher.hash(password)))
        return User(row.id, row.username)

    def open_session(self, user: User, ip: str | None, user_agent: str | None) -> str:
        token = secrets.token_urlsafe(32)
        stamp = now()
        with self.engine.begin() as conn:
            conn.execute(delete(sessions).where(sessions.c.expires_at < stamp))
            conn.execute(sessions.insert().values(
                token_hash=hash_token(token), user_id=user.id, created_at=stamp, last_seen_at=stamp,
                expires_at=stamp + self.max_age, ip=(ip or "")[:64], user_agent=(user_agent or "")[:256],
            ))
        return token

    def resolve(self, token: str | None) -> User | None:
        if not token:
            return None
        key = hash_token(token)
        with self.engine.connect() as conn:
            row = conn.execute(
                select(sessions.c.user_id, sessions.c.expires_at, sessions.c.last_seen_at, users.c.username)
                .join(users, users.c.id == sessions.c.user_id)
                .where(sessions.c.token_hash == key)
            ).first()
        if row is None:
            return None
        stamp = now()
        last_seen: datetime = aware(row.last_seen_at)
        if aware(row.expires_at) <= stamp or last_seen + self.idle <= stamp:
            self.close_session(token)
            return None
        if stamp - last_seen >= TOUCH_EVERY:
            with self.engine.begin() as conn:
                conn.execute(update(sessions).where(sessions.c.token_hash == key).values(last_seen_at=stamp))
        return User(row.user_id, row.username)

    def close_session(self, token: str | None) -> None:
        if token:
            with self.engine.begin() as conn:
                conn.execute(delete(sessions).where(sessions.c.token_hash == hash_token(token)))

    # --- second factor (desk.otp) ------------------------------------------------------

    def otp_enabled(self, user_id: int) -> bool:
        with self.engine.connect() as conn:
            return conn.execute(select(users.c.totp_secret).where(users.c.id == user_id)).scalar() is not None

    def otp_begin(self, user_id: int, box: otp.SecretBox, account: str) -> dict:
        """Start (or restart) enrolment: a new pending secret, live only after otp_enable."""
        secret = otp.new_secret()
        with self.engine.begin() as conn:
            conn.execute(update(users).where(users.c.id == user_id)
                         .values(totp_pending=box.seal(secret), totp_pending_at=now()))
        return {"secret": secret, "uri": otp.provisioning_uri(secret, account)}

    def otp_enable(self, user_id: int, code: str, box: otp.SecretBox, keep_token: str | None) -> list[str] | None:
        """Confirm the pending secret with a code. Returns the recovery codes (shown once), or None.
        Every other session is signed out: they were opened without the second factor."""
        with self.engine.connect() as conn:
            row = conn.execute(select(users.c.totp_pending, users.c.totp_pending_at)
                               .where(users.c.id == user_id)).first()
        pending, started = (row.totp_pending, aware(row.totp_pending_at)) if row else (None, None)
        if pending is None or started is None or now() - started > timedelta(seconds=otp.PENDING_TTL_SEC):
            return None
        secret = box.open(pending)
        step = otp.matching_step(secret, code, None)
        if step is None:
            return None
        codes = otp.new_recovery_codes()
        keep = hash_token(keep_token) if keep_token else ""
        with self.engine.begin() as conn:
            conn.execute(update(users).where(users.c.id == user_id).values(
                totp_secret=pending, totp_pending=None, totp_pending_at=None, totp_enabled_at=now(),
                totp_last_step=step))
            conn.execute(delete(recovery_codes).where(recovery_codes.c.user_id == user_id))
            conn.execute(recovery_codes.insert(), [{"user_id": user_id, "code_hash": otp.hash_recovery(c)}
                                                   for c in codes])
            conn.execute(delete(sessions).where(sessions.c.user_id == user_id, sessions.c.token_hash != keep))
        return codes

    def otp_verify(self, user_id: int, code: str, box: otp.SecretBox) -> bool:
        """A 6-digit code (once per step) or an unused recovery code (once, ever)."""
        code = code.strip()
        if otp.is_totp_code(code):
            with self.engine.connect() as conn:
                row = conn.execute(select(users.c.totp_secret, users.c.totp_last_step)
                                   .where(users.c.id == user_id)).first()
            if row is None or row.totp_secret is None:
                return False
            step = otp.matching_step(box.open(row.totp_secret), code, row.totp_last_step)
            if step is None:
                return False
            # Compare-and-set: two requests racing with the same code cannot both win.
            last = users.c.totp_last_step
            with self.engine.begin() as conn:
                won = conn.execute(update(users).where(users.c.id == user_id, or_(last.is_(None), last < step))
                                   .values(totp_last_step=step)).rowcount
            return won == 1
        with self.engine.begin() as conn:
            spent = conn.execute(update(recovery_codes).where(
                recovery_codes.c.user_id == user_id, recovery_codes.c.code_hash == otp.hash_recovery(code),
                recovery_codes.c.used_at.is_(None)).values(used_at=now())).rowcount
        return spent == 1

    def otp_disable(self, user_id: int, keep_token: str | None = None) -> None:
        """Turning the factor off is often a response to a compromise: other sessions go too."""
        keep = hash_token(keep_token) if keep_token else ""
        with self.engine.begin() as conn:
            conn.execute(update(users).where(users.c.id == user_id).values(
                totp_secret=None, totp_pending=None, totp_pending_at=None, totp_enabled_at=None,
                totp_last_step=None))
            conn.execute(delete(recovery_codes).where(recovery_codes.c.user_id == user_id))
            conn.execute(delete(sessions).where(sessions.c.user_id == user_id, sessions.c.token_hash != keep))

    def otp_disable_by_name(self, username: str) -> bool:
        """CLI recovery when the phone is lost. Signs every browser out too."""
        with self.engine.begin() as conn:
            user_id = conn.execute(select(users.c.id).where(users.c.username == username)).scalar()
        if user_id is None:
            return False
        self.otp_disable(user_id)            # no session kept: the CLI has none
        return True

    def recovery_left(self, user_id: int) -> int:
        with self.engine.connect() as conn:
            return conn.execute(select(func.count()).select_from(recovery_codes).where(
                recovery_codes.c.user_id == user_id, recovery_codes.c.used_at.is_(None))).scalar_one()

    def user_by_id(self, user_id: int) -> User | None:
        with self.engine.connect() as conn:
            row = conn.execute(select(users.c.id, users.c.username).where(users.c.id == user_id)).first()
        return User(row.id, row.username) if row else None


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")


class LoginThrottle:
    """Login attempts per client IP inside a sliding window.

    An attempt counts *before* the password is checked, so parallel requests cannot
    all slip past the limit while argon2 runs; a success clears the IP's count.
    There is deliberately no per-username lock: with a single known account it would
    let anyone keep the owner out. Distributed guessing is left to fail2ban on the
    ingress and to the password itself (12+ chars, argon2id) — and OTP next (#6).

    In memory on purpose: one process, and a restart forgetting the count is fine.
    """

    def __init__(self, per_ip: int = 5, window_sec: float = 900) -> None:
        self.per_ip = per_ip
        self.window = window_sec
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def attempt(self, ip: str) -> int:
        """Record an attempt. Returns 0 to go ahead, or seconds to wait (nothing recorded)."""
        stamp = time.monotonic()
        with self._lock:
            attempts = self._attempts[ip]
            while attempts and attempts[0] < stamp - self.window:
                attempts.popleft()
            if len(attempts) >= self.per_ip:
                return max(1, int(attempts[0] + self.window - stamp) + 1)
            attempts.append(stamp)
            return 0

    def succeeded(self, ip: str) -> None:
        with self._lock:
            self._attempts.pop(ip, None)

    def blocked_for(self, key: str) -> int:
        """Like attempt() but records nothing: seconds until ``key`` is under the limit again."""
        stamp = time.monotonic()
        with self._lock:
            attempts = self._attempts.get(key)
            while attempts and attempts[0] < stamp - self.window:
                attempts.popleft()
            if not attempts or len(attempts) < self.per_ip:
                return 0
            return max(1, int(attempts[0] + self.window - stamp) + 1)
