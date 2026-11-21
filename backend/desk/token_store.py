"""#688 broker tokens shared by the desk's processes (table ``broker_tokens``), issued by one of them.

Toss keeps one valid token per client: a token issued anywhere else on the same key revokes ours at once.
A new KIS token or approval key unsettles the one in use too, and KIS notifies the owner on each issue.
So with the backend split into processes (desk.roles) exactly one of them issues:

- a *minter* (DESK_ROLE ``all`` | ``collector``) issues under a lease on the token's row: it takes the lease
  (its own nonce), reads the row again, checks the gap again, stamps ``asked_at`` *before* asking the
  provider (a crash or a cancel mid-issue still counts), issues and saves - each write only while the lease
  is still its own. The gap between issues (and the backoff after refusals) counts from ``asked_at``, so it
  holds across processes and restarts. The collector's ``tokens`` job (desk.api) renews ahead of expiry and
  answers the readers' requests;
- a *reader* (``api`` | ``analyst`` | the CLI) never issues. When its token is refused (HTTP 401) or missing
  it leaves a request on the row - the refused token's hash, so any number of asks for the same token are
  one - and reads the row again for a few seconds while the minter issues. A token near its end but still
  valid is used meanwhile (the request goes in, nothing waits). A 403 (an IP not registered with Toss) is
  not a refusal of the token: no request, since a new token would not help.

A token turned away within ``FRESH_SEC`` of its issue is a refusal of the issue itself (a key without the
rights, an endpoint that turns every token away): the row's ``refusals`` goes up at once - once per token, the
request marked ``c:<hash>`` - and with it the backoff the provider's ``gap`` gives. The issue that answers it
keeps the count; any other issue (an expiry, a token turned away hours after its issue) starts it again.

A token still valid in the row is reused: a restart (a deploy) issues nothing.

Values are Fernet-sealed with a key derived (HKDF) from that provider's own secret, so the database alone
gives no usable token. A row the secret no longer opens (the key was rotated) reads as no token and is
issued again. Never log a token, its hash or the sealed value - only the token's name and the role (the
engine hides SQL parameters in its errors, desk.db.make_engine).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from sqlalchemy import and_, insert, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError

from .db import aware, broker_tokens

log = logging.getLogger(__name__)

NAMES = ("toss", "kis_rest", "kis_approval")
MINTERS = ("all", "collector")
LEASE_SEC = 30.0          # an issue in flight holds the row this long at most (a crashed minter frees it)
WAIT_SEC = 10.0           # a reader with no valid token waits this long for the minter's new one
POLL_SEC = 0.5
FAIL_FAST_SEC = 15.0      # after a wait in vain, a reader does not wait again for this long (it still reads)
FRESH_SEC = 600.0         # a token refused this soon after its issue counts as a refused issue (backoff)
MISSING = "missing"       # a reader's request when it holds no token at all
COUNTED = "c:"            # a request whose refusal is already in ``refusals`` (COUNTED + the hash)

Gap = Callable[[int], float]   # refusals in a row -> seconds between issues


class TokenUnavailable(RuntimeError):
    """No usable token now (a reader's wait ran out, or the minter must wait before issuing again)."""


@dataclass(frozen=True)
class Issued:
    value: str = field(repr=False)
    life_sec: float        # seconds from now the provider keeps it (the caller trims its own margin)


def token_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:32]


def _fernet(name: str, secret: str) -> Fernet:
    key = HKDF(algorithm=hashes.SHA256(), length=32, salt=None,
               info=f"trading-desk broker token {name} v1".encode()).derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(key))


def _gap_60(_refusals: int) -> float:
    return 60.0


class TokenStore:
    """The shared rows. ``minter``: this process may issue (DESK_ROLE all | collector)."""

    def __init__(self, engine: Engine, *, role: str, clock: Callable[[], datetime] = lambda: datetime.now(UTC),
                 wait_sec: float = WAIT_SEC, poll_sec: float = POLL_SEC,
                 can_issue: Callable[[], bool] | None = None) -> None:
        self._engine = engine
        self.role = role
        self.minter = role in MINTERS
        self._can_issue = can_issue
        self._clock = clock
        self._wait_sec, self._poll_sec = wait_sec, poll_sec

    def __repr__(self) -> str:
        return f"TokenStore(role={self.role})"

    def slot(self, name: str, secret: str) -> TokenSlot:
        if name not in NAMES:
            raise ValueError(f"unknown token {name!r}")
        return TokenSlot(self, name, _fernet(name, secret))

    # --- rows (sync: called through asyncio.to_thread) ------------------------------------------------

    def _row(self, name: str) -> dict:
        query = select(broker_tokens).where(broker_tokens.c.name == name)
        with self._engine.connect() as conn:
            row = conn.execute(query).mappings().first()
        if row is not None:
            return dict(row)
        try:
            with self._engine.begin() as conn:
                conn.execute(insert(broker_tokens).values(name=name, refusals=0))
        except IntegrityError:
            pass                                           # another process made it first
        with self._engine.connect() as conn:
            return dict(conn.execute(query).mappings().one())

    def _take_lease(self, name: str, owner: str, now: datetime) -> bool:
        with self._engine.begin() as conn:
            res = conn.execute(update(broker_tokens).where(and_(
                broker_tokens.c.name == name,
                or_(broker_tokens.c.lease_until.is_(None), broker_tokens.c.lease_until < now),
            )).values(lease_until=now + timedelta(seconds=LEASE_SEC), lease_owner=owner))
            return res.rowcount == 1

    def _write_leased(self, name: str, owner: str, **values) -> bool:
        """Only while the lease is still ``owner``'s: a minter that outran its lease writes nothing."""
        with self._engine.begin() as conn:
            res = conn.execute(update(broker_tokens).where(and_(
                broker_tokens.c.name == name, broker_tokens.c.lease_owner == owner)).values(**values))
            return res.rowcount == 1

    def _request(self, name: str, ask: str) -> None:
        # one request per refused token, however many readers ask (a counted one stays counted); "missing"
        # never hides a real refusal
        col = broker_tokens.c.refresh_for
        cond = col.is_(None) if ask == MISSING else or_(col.is_(None), and_(col != ask, col != COUNTED + ask))
        with self._engine.begin() as conn:
            conn.execute(update(broker_tokens).where(and_(broker_tokens.c.name == name, cond)).values(refresh_for=ask))

    def _count_refusal(self, name: str, mark: str) -> None:
        """One more refusal in a row, once per refused token (``mark`` = COUNTED + its hash)."""
        col = broker_tokens.c.refresh_for
        with self._engine.begin() as conn:
            conn.execute(update(broker_tokens).where(and_(
                broker_tokens.c.name == name, or_(col.is_(None), col != mark),
            )).values(refusals=broker_tokens.c.refusals + 1, refresh_for=mark))


class TokenSlot:
    """One token's row, as seen by one owner object (TossBroker, KisRest, KisStream)."""

    def __init__(self, store: TokenStore, name: str, box: Fernet) -> None:
        self._store, self.name, self._box = store, name, box
        self._lock = asyncio.Lock()          # one issuer at a time in this process (the lease covers the rest)
        self._fail_until = 0.0
        self._unreadable_warned = False

    def __repr__(self) -> str:
        return f"TokenSlot({self.name})"

    @property
    def minter(self) -> bool:
        return self._store.minter

    def _open(self, row: dict) -> tuple[str, datetime] | None:
        if not row.get("sealed") or row.get("expires_at") is None:
            return None
        try:
            value = self._box.decrypt(row["sealed"].encode()).decode()
        except InvalidToken:
            if not self._unreadable_warned:   # the provider's secret changed (never the value)
                log.warning("%s token in the store does not open with the current secret: read as none",
                            self.name)
                self._unreadable_warned = True
            return None
        return value, aware(row["expires_at"])

    async def _read(self) -> tuple[dict, tuple[str, datetime] | None]:
        row = await asyncio.to_thread(self._store._row, self.name)
        return row, self._open(row)

    @staticmethod
    def _usable(cur, refused: str | None, ahead: float, now: datetime) -> bool:
        return cur is not None and cur[0] != refused and cur[1] - timedelta(seconds=ahead) > now

    @staticmethod
    def _asked_for(row: dict, cur) -> bool:
        """A live request (a reader's, or a refusal already counted): for the token in the row, or for any
        token when there is none."""
        ask = row.get("refresh_for")
        if not ask:
            return False
        if cur is None:
            return ask == MISSING
        return ask in (token_hash(cur[0]), COUNTED + token_hash(cur[0]))

    @staticmethod
    def _counted(row: dict, cur) -> bool:
        return cur is not None and row.get("refresh_for") == COUNTED + token_hash(cur[0])

    @staticmethod
    def _refusals(row: dict, cur, now: datetime) -> int:
        """The row's refusals in a row - none once the token the last ask gave has served ``FRESH_SEC`` unrefused
        (a refusal followed by a token that works ends the run)."""
        count = row.get("refusals") or 0
        if count and cur is not None and not TokenSlot._counted(row, cur):
            issued, last = aware(row.get("issued_at")), aware(row.get("asked_at"))
            if issued is not None and last is not None and issued >= last \
                    and (now - issued).total_seconds() >= FRESH_SEC:
                return 0
        return count

    @staticmethod
    def _fresh_refusal(row: dict, cur, refused: str | None, asked: bool, now: datetime) -> bool:
        """The token in the row turned away soon after its issue, and not counted yet."""
        issued = aware(row.get("issued_at"))
        turned_away = asked or (refused is not None and cur is not None and cur[0] == refused)
        return (turned_away and cur is not None and issued is not None
                and (now - issued).total_seconds() < FRESH_SEC and not TokenSlot._counted(row, cur))

    async def get(self, issue: Callable[[], Awaitable[Issued]], *, refused: str | None = None, ahead: float = 0.0,
                  gap: Gap = _gap_60) -> tuple[str, datetime]:
        """A token other than ``refused`` (one the provider just turned away with a 401), valid for more than
        ``ahead`` seconds, and its expiry. A minter issues when it must; a reader asks and waits."""
        if self.minter:
            got, _issued = await self._mint(issue, refused=refused, ahead=ahead, gap=gap, on_demand=False)
            return got
        return await self._wait(refused=refused, ahead=ahead)

    async def keep(self, issue: Callable[[], Awaitable[Issued]], *, ahead: float, gap: Gap = _gap_60,
                   on_demand: bool = False) -> tuple[str, datetime] | None:
        """The minter's job: renew a token expiring within ``ahead`` seconds, or one a reader asked for.
        ``on_demand``: only what a reader asked for, never ahead of time (the KIS approval key - each issue
        notifies the owner, and a streaming process renews its own key when it reconnects). The token and
        its expiry when one was issued here, else None."""
        if not self.minter:
            return None
        try:
            got, issued = await self._mint(issue, refused=None, ahead=ahead, gap=gap, on_demand=on_demand)
        except TokenUnavailable:
            return None
        return got if issued else None

    async def _decide(self, refused, ahead: float, gap: Gap):
        """(row, token in the row, use it?) after counting a fresh refusal; raises while the gap holds and the
        row has nothing usable."""
        store = self._store
        row, cur = await self._read()
        now = store._clock()
        asked = self._asked_for(row, cur)
        if self._usable(cur, refused, ahead, now) and not asked:
            return row, cur, True
        if self._fresh_refusal(row, cur, refused, asked, now):
            await asyncio.to_thread(store._count_refusal, self.name, COUNTED + token_hash(cur[0]))
            row, cur = await self._read()
        last = aware(row.get("asked_at"))
        if last is not None and (now - last).total_seconds() < gap(self._refusals(row, cur, now)):
            if self._usable(cur, refused, 0.0, now):
                return row, cur, True                     # a renewal ahead of time can wait for the gap
            raise TokenUnavailable(f"{self.name}: waiting before asking for another token")
        return row, cur, False

    async def _mint(self, issue, *, refused, ahead, gap: Gap, on_demand: bool):
        """(token and expiry, issued here) - the token is None only for ``on_demand`` with nothing asked."""
        store = self._store
        async with self._lock:
            row, cur = await self._read()
            now = store._clock()
            if on_demand and not self._asked_for(row, cur):
                return (cur if self._usable(cur, None, 0.0, now) else None), False
            lease = aware(row.get("lease_until"))
            if lease is not None and lease > now:
                return await self._wait(refused=refused, ahead=0.0), False   # another minter is issuing
            row, cur, use = await self._decide(refused, ahead, gap)
            if use:
                return cur, False
            owner = secrets.token_hex(8)
            if not await asyncio.to_thread(store._take_lease, self.name, owner, now):
                return await self._wait(refused=refused, ahead=0.0), False
            try:
                # under the lease, everything again: another minter may have just saved, or just failed
                row, cur, use = await self._decide(refused, ahead, gap)
                if use:
                    return cur, False
                if store._can_issue is not None and not await asyncio.to_thread(store._can_issue):
                    raise TokenUnavailable("broker issuer lock not held")
                counted = self._counted(row, cur)
                at = store._clock()
                refusals = self._refusals(row, cur, at)
                # the ask counts before it is sent: a crash, a cancel or a lost save still keeps the gap
                if not await asyncio.to_thread(store._write_leased, self.name, owner, asked_at=at):
                    raise TokenUnavailable(f"{self.name}: lease lost")
                try:
                    got = await issue()
                except Exception:
                    await asyncio.to_thread(store._write_leased, self.name, owner, refusals=refusals + 1)
                    raise
                expires = store._clock() + timedelta(seconds=got.life_sec)
                saved = await asyncio.to_thread(
                    store._write_leased, self.name, owner, sealed=self._box.encrypt(got.value.encode()).decode(),
                    expires_at=expires, issued_at=at, issued_by=store.role, refresh_for=None,
                    # an issue that answers a counted refusal keeps the count for the next one; any other resets it
                    refusals=refusals if counted else 0)
                if not saved:                             # outran the lease: another minter's token stands
                    raise TokenUnavailable(f"{self.name}: lease lost while issuing")
                log.info("%s token issued (%s)", self.name, store.role)
                return (got.value, expires), True
            finally:
                await asyncio.to_thread(store._write_leased, self.name, owner, lease_until=None, lease_owner=None)

    async def _wait(self, *, refused, ahead):
        store = self._store
        _row, cur = await self._read()
        now = store._clock()
        if self._usable(cur, refused, ahead, now):
            return cur
        held = refused if refused is not None else (cur[0] if cur else None)
        if not store.minter:
            await asyncio.to_thread(store._request, self.name, token_hash(held) if held else MISSING)
        if self._usable(cur, refused, 0.0, now):
            return cur                                    # near its end but valid: use it while the minter renews
        if time.monotonic() < self._fail_until:
            raise TokenUnavailable(f"{self.name}: no fresh token yet")
        deadline = time.monotonic() + store._wait_sec
        while time.monotonic() < deadline:
            await asyncio.sleep(store._poll_sec)
            _row, cur = await self._read()
            if self._usable(cur, refused, 0.0, store._clock()):
                return cur
        self._fail_until = time.monotonic() + FAIL_FAST_SEC
        raise TokenUnavailable(f"{self.name}: no fresh token from the issuing process")
