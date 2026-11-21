"""Store an explicit owner binding. Product ownership checks are not wired here."""

from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.engine import Engine

from .db import account_owner_binding, users

_SINGLETON = 1


@dataclass(frozen=True)
class OwnerBinding:
    user_id: int | None
    generation: str


class AccountOwnerStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine

    def binding(self) -> OwnerBinding | None:
        with self.engine.connect() as conn:
            row = conn.execute(
                select(account_owner_binding.c.user_id, account_owner_binding.c.generation)
                .where(account_owner_binding.c.id == _SINGLETON),
            ).one_or_none()
        return OwnerBinding(row.user_id, row.generation) if row is not None else None

    def bind_verified(self, *, verified_user_id: int, expected_vacancy: OwnerBinding) -> OwnerBinding | None:
        """Bind a vacant slot after a trusted caller verifies account and operator authority.

        The caller obtains the vacancy snapshot before seeking approval.
        An absent row, occupied snapshot, missing user or stale generation cannot be bound.
        """
        if type(verified_user_id) is not int or verified_user_id < 1 or expected_vacancy.user_id is not None:
            return None
        user_exists = select(users.c.id).where(users.c.id == verified_user_id).exists()
        generation = uuid4().hex
        statement = (
            update(account_owner_binding)
            .where(
                account_owner_binding.c.id == _SINGLETON,
                account_owner_binding.c.user_id.is_(None),
                account_owner_binding.c.generation == expected_vacancy.generation,
                user_exists,
            )
            .values(user_id=verified_user_id, generation=generation)
            .returning(account_owner_binding.c.user_id, account_owner_binding.c.generation)
        )
        with self.engine.begin() as conn:
            row = conn.execute(statement).one_or_none()
        return OwnerBinding(row.user_id, row.generation) if row is not None else None

    def is_current(self, *, user_id: int, generation: str) -> bool:
        """Check an authenticated user and a previously resolved binding generation."""
        if type(user_id) is not int or user_id < 1:
            return False
        with self.engine.connect() as conn:
            return conn.scalar(select(account_owner_binding.c.id).where(
                account_owner_binding.c.id == _SINGLETON,
                account_owner_binding.c.user_id == user_id,
                account_owner_binding.c.generation == generation,
            )) is not None
