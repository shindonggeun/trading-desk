"""Shared mode fence for privacy-aware processes, without a deployment switch."""
from collections.abc import Callable
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.sql.elements import ColumnElement

from .account_owner import AccountOwnerStore, OwnerBinding
from .broker.base import Broker
from .db import privacy_policy

LEGACY = "legacy"
ISOLATED = "isolated"
PROTECTED = "protected"


@dataclass(frozen=True)
class PolicyEpoch:
    mode: str
    epoch: int

    @property
    def valid(self) -> bool:
        if type(self.epoch) is not int:
            return False
        if self.mode == LEGACY:
            return self.epoch == 0
        return self.epoch > 0 and (
            (self.mode == ISOLATED and self.epoch % 2 == 1)
            or (self.mode == PROTECTED and self.epoch % 2 == 0)
        )


class PolicyChanged(RuntimeError):
    """The process no longer has authority to serve or write."""


def read_policy_epoch(conn: Connection) -> PolicyEpoch | None:
    rows = conn.execute(select(privacy_policy.c.id, privacy_policy.c.mode, privacy_policy.c.epoch).limit(2)).all()
    if len(rows) != 1 or rows[0].id != 1:
        return None
    current = PolicyEpoch(rows[0].mode, rows[0].epoch)
    return current if current.valid else None


class PrivacyPolicyStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def current(self) -> PolicyEpoch | None:
        with self.engine.connect() as conn:
            return read_policy_epoch(conn)

    def transition(self, expected: PolicyEpoch, target: str) -> PolicyEpoch | None:
        """An internal CAS used by a trusted coordinator with the old processes stopped.

        Isolation never reopens legacy reads. A retained database may only resume
        in protected mode after isolation. Factories do not call this method.
        """
        if not expected.valid or (expected.mode, target) not in {
            (LEGACY, ISOLATED), (ISOLATED, PROTECTED), (PROTECTED, ISOLATED),
        }:
            return None
        result = PolicyEpoch(target, expected.epoch + 1)
        with self.engine.begin() as conn:
            row = conn.execute(update(privacy_policy).where(policy_epoch_matches(expected))
                               .values(mode=result.mode, epoch=result.epoch).returning(privacy_policy.c.epoch))
            return result if row.scalar_one_or_none() is not None else None


def policy_epoch_matches(expected: PolicyEpoch) -> ColumnElement[bool]:
    return (privacy_policy.c.id == 1) & (privacy_policy.c.mode == expected.mode) & (privacy_policy.c.epoch == expected.epoch)


@dataclass(frozen=True)
class ProcessPolicy:
    store: PrivacyPolicyStore
    expected: PolicyEpoch

    @classmethod
    def open(cls, engine: Engine, protected: bool | None) -> "ProcessPolicy":
        store = PrivacyPolicyStore(engine)
        current = store.current()
        if current is None or current.mode == ISOLATED or (
            protected is not None and current.mode != (PROTECTED if protected else LEGACY)
        ):
            raise PolicyChanged("process privacy mode does not match the database")
        return cls(store, current)

    @property
    def protected(self) -> bool:
        return self.expected.mode == PROTECTED

    def current(self) -> bool:
        return self.store.current() == self.expected

    def lock(self, conn: Connection, *, exclusive: bool = False) -> None:
        """Fence a DB write against transitions. Admin writes also serialize with one another."""
        if not self.expected.valid:
            raise PolicyChanged("process privacy mode changed")
        match = policy_epoch_matches(self.expected)
        if conn.dialect.name == "sqlite":
            # A SELECT does not take a write lock under sqlite3's legacy transaction mode.
            query = update(privacy_policy).where(match).values(
                epoch=privacy_policy.c.epoch,
            ).returning(privacy_policy.c.epoch)
        else:
            query = select(privacy_policy.c.epoch).where(match).with_for_update(read=not exclusive)
        if conn.scalar(query) is None:
            raise PolicyChanged("process privacy mode changed")


@dataclass(frozen=True)
class AttestedDestination:
    name: str
    deliver: Callable[[dict], None] = field(repr=False)
    kinds: frozenset[str] = frozenset()


@dataclass(frozen=True)
class OwnerResources:
    """Server-only inputs already verified against this exact desk binding.

    The caller must independently verify the broker account and destinations.
    Never copy existing configuration or derive attestation from the current owner.
    """

    binding: OwnerBinding = field(repr=False)
    broker: Broker | None = field(default=None, repr=False)
    destinations: tuple[AttestedDestination, ...] = field(default=(), repr=False)

    def __post_init__(self) -> None:
        from .broker.toss import TossBroker

        if type(self.binding.user_id) is not int or self.binding.user_id < 1:
            raise ValueError("account resources require an occupied binding")
        if isinstance(self.broker, TossBroker) and not self.broker.account_pinned:
            raise ValueError("account resources require an explicitly configured account")
        if len({d.name for d in self.destinations}) != len(self.destinations):
            raise ValueError("duplicate attested destination")

    def current(self, owners: AccountOwnerStore) -> bool:
        return owners.is_current(user_id=self.binding.user_id, generation=self.binding.generation)
