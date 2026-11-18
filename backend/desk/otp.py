"""TOTP second factor (RFC 6238, the codes Google Authenticator and friends show).

- Secrets are stored Fernet-sealed with ``DESK_OTP_KEY``; without the key the server
  cannot enrol anyone and refuses OTP logins rather than skipping the check.
- A code is accepted for its 30-second step and one step either side (clock drift),
  and only once: the last accepted step is stored and anything at or before it fails.
- Recovery codes are random, shown once, stored as SHA-256, each usable once.
- Logging in with OTP on is two calls to the same login route: the password returns a
  short-lived challenge, the code exchanges it for the session. See docs/security.md §6.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass, field

import pyotp
from cryptography.fernet import Fernet, InvalidToken

ISSUER = "Trading Desk"
STEP_SEC = 30
DRIFT_STEPS = 1
RECOVERY_COUNT = 10
CHALLENGE_SEC = 300
CHALLENGE_TRIES = 5
PENDING_TTL_SEC = 600            # an unfinished enrolment is dropped after 10 minutes
USER_FAILS = 10                  # OTP failures per user per hour before new challenges are refused
USER_FAIL_WINDOW_SEC = 3600
_RECOVERY_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"   # no 0/o, 1/l/i to misread


class OtpUnavailable(RuntimeError):
    """DESK_OTP_KEY is missing or does not open the stored secret."""


class SecretBox:
    """Fernet sealing under one env key (``name``): DESK_OTP_KEY for TOTP secrets, DESK_NOTIFY_KEY for
    the alert channels' tokens (#580) - separate keys, so rotating one never breaks the other."""

    def __init__(self, key: str | None, name: str = "DESK_OTP_KEY") -> None:
        self.error: str | None = None
        self.name = name
        self._fernet = None
        if key:
            try:
                self._fernet = Fernet(key.encode())
            except ValueError:
                # A malformed key disables what it seals only (fail closed); the app still starts.
                self.error = f"{name} is not a valid Fernet key (python -m desk.cli otp-key)"

    def __repr__(self) -> str:
        return f"SecretBox({self.name}, available={self.available})"

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def seal(self, secret: str) -> str:
        if self._fernet is None:
            raise OtpUnavailable(f"{self.name} is not set on the server")
        return self._fernet.encrypt(secret.encode()).decode()

    def open(self, sealed: str) -> str:
        if self._fernet is None:
            raise OtpUnavailable(f"{self.name} is not set on the server")
        try:
            return self._fernet.decrypt(sealed.encode()).decode()
        except InvalidToken as exc:
            raise OtpUnavailable(f"{self.name} does not match the stored secret") from exc


def new_key() -> str:
    """A fresh DESK_OTP_KEY value."""
    return Fernet.generate_key().decode()


def new_secret() -> str:
    return pyotp.random_base32()


def provisioning_uri(secret: str, account: str) -> str:
    """The otpauth:// address an authenticator app reads from a QR code."""
    return pyotp.TOTP(secret, interval=STEP_SEC).provisioning_uri(name=account, issuer_name=ISSUER)


def is_totp_code(code: str) -> bool:
    return len(code) == 6 and code.isdigit()


def matching_step(secret: str, code: str, last_step: int | None, at: float | None = None) -> int | None:
    """The 30 s step this code belongs to, or None. Steps at or before ``last_step`` never match."""
    if not is_totp_code(code):
        return None
    totp = pyotp.TOTP(secret, interval=STEP_SEC)
    current = int((time.time() if at is None else at) // STEP_SEC)
    for step in range(current - DRIFT_STEPS, current + DRIFT_STEPS + 1):
        if last_step is not None and step <= last_step:
            continue
        if hmac.compare_digest(totp.at(step * STEP_SEC), code):
            return step
    return None


def normalize_recovery(code: str) -> str:
    return "".join(code.split()).replace("-", "").lower()


def new_recovery_codes(count: int = RECOVERY_COUNT) -> list[str]:
    """``xxxxx-xxxxx`` codes, 10 letters from a 31-letter alphabet (~50 bits each)."""
    codes = []
    for _ in range(count):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(10))
        codes.append(f"{raw[:5]}-{raw[5:]}")
    return codes


def hash_recovery(code: str) -> str:
    return hashlib.sha256(normalize_recovery(code).encode()).hexdigest()


@dataclass
class _Challenge:
    user_id: int
    expires: float
    tries: int = 0


@dataclass
class Challenges:
    """Password-passed, code-pending logins. In memory: a restart just asks for the password again."""

    ttl: float = CHALLENGE_SEC
    tries: int = CHALLENGE_TRIES
    _open: dict[str, _Challenge] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def issue(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        stamp = time.monotonic()
        with self._lock:
            self._open = {k: c for k, c in self._open.items() if c.expires > stamp}
            self._open[hashlib.sha256(token.encode()).hexdigest()] = _Challenge(user_id, stamp + self.ttl)
        return token

    def user_for(self, token: str) -> int | None:
        """The user a live challenge belongs to; counts the try, and burns the challenge after the last."""
        key = hashlib.sha256(token.encode()).hexdigest()
        with self._lock:
            challenge = self._open.get(key)
            if challenge is None or challenge.expires <= time.monotonic():
                self._open.pop(key, None)
                return None
            challenge.tries += 1
            if challenge.tries >= self.tries:
                self._open.pop(key, None)       # this is the last try it gets
            return challenge.user_id

    def close(self, token: str) -> None:
        with self._lock:
            self._open.pop(hashlib.sha256(token.encode()).hexdigest(), None)
