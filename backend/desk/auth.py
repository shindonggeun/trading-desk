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
from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine

from .db import aware, now, sessions, users

COOKIE = "desk_session"
MIN_PASSWORD = 12
# Refresh last_seen_at at most this often: one write per minute, not per request.
TOUCH_EVERY = timedelta(minutes=1)

_hasher = PasswordHasher()   # argon2id, library defaults (RFC 9106 low-memory profile)
# Verified against when the username is unknown, so a miss costs the same time as a wrong password.
_DUMMY_HASH = _hasher.hash(secrets.token_hex(16))


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


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
        try:
            _hasher.verify(stored, password)
        except (VerificationError, InvalidHashError):
            return None
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


def _check_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise ValueError(f"password must be at least {MIN_PASSWORD} characters")


class LoginThrottle:
    """Failed logins per client IP and per username inside a sliding window.

    In memory on purpose: one process, and a restart forgetting the count is fine —
    fail2ban on the ingress keeps the longer memory (nginx log, 401 on the login route).
    """

    def __init__(self, per_ip: int = 5, per_user: int = 10, window_sec: float = 900) -> None:
        self.per_ip = per_ip
        self.per_user = per_user
        self.window = window_sec
        self._fails: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def retry_after(self, ip: str, username: str) -> int:
        """Seconds until this client may try again; 0 = go ahead."""
        with self._lock:
            waits = [self._wait(f"ip:{ip}", self.per_ip), self._wait(f"user:{username.lower()}", self.per_user)]
        return max(waits)

    def failed(self, ip: str, username: str) -> None:
        stamp = time.monotonic()
        with self._lock:
            self._fails[f"ip:{ip}"].append(stamp)
            self._fails[f"user:{username.lower()}"].append(stamp)

    def succeeded(self, ip: str, username: str) -> None:
        with self._lock:
            self._fails.pop(f"ip:{ip}", None)
            self._fails.pop(f"user:{username.lower()}", None)

    def _wait(self, key: str, limit: int) -> int:
        fails = self._fails.get(key)
        if not fails:
            return 0
        cutoff = time.monotonic() - self.window
        while fails and fails[0] < cutoff:
            fails.popleft()
        if not fails:
            del self._fails[key]
            return 0
        if len(fails) < limit:
            return 0
        return max(1, int(fails[0] + self.window - time.monotonic()) + 1)
