import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from alembic import command
from sqlalchemy import inspect, select

from desk.account_owner import AccountOwnerStore, OwnerBinding
from desk.db import (
    account_owner_binding,
    alembic_config,
    broker_tokens,
    make_engine,
    migrate,
    now,
    owner_toss_attestations,
    privacy_policy,
    users,
)
from desk.owner_attestation import TossAttestationStore, TossSource
from desk.privacy import (
    ISOLATED,
    LEGACY,
    PROTECTED,
    PolicyChanged,
    PolicyEpoch,
    PrivacyPolicyStore,
    ProcessPolicy,
)
from desk.privacy_admin import main
from desk.token_store import Issued, TokenStore, TokenUnavailable


@pytest.fixture
def engine(db_url):
    migrate(db_url)
    engine = make_engine(db_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def owner(engine):
    with engine.begin() as conn:
        conn.execute(users.insert().values(
            id=1, username="synthetic-owner", password_hash="unused", created_at=now(), password_changed_at=now(),
        ))
    owners = AccountOwnerStore(engine)
    vacancy = owners.binding()
    assert vacancy is not None
    bound = owners.bind_verified(verified_user_id=1, expected_vacancy=vacancy)
    assert bound is not None
    return bound


@pytest.fixture
def attestation(engine):
    return TossAttestationStore(engine), TossSource("synthetic-id", "synthetic-secret", "0001234567890")


def _advance(engine, expected, mode):
    next_epoch = PrivacyPolicyStore(engine).transition(expected, mode)
    assert next_epoch is not None
    return next_epoch


def _proof_rows(engine):
    with engine.connect() as conn:
        return conn.execute(select(owner_toss_attestations)).mappings().all()


def test_process_opens_from_the_database_or_requires_the_explicit_mode(engine):
    legacy = PolicyEpoch(LEGACY, 0)
    assert ProcessPolicy.open(engine, None).expected == ProcessPolicy.open(engine, False).expected == legacy
    with pytest.raises(PolicyChanged):
        ProcessPolicy.open(engine, True)

    isolated = _advance(engine, legacy, ISOLATED)
    for mode in (None, False, True):
        with pytest.raises(PolicyChanged):
            ProcessPolicy.open(engine, mode)

    protected = _advance(engine, isolated, PROTECTED)
    assert ProcessPolicy.open(engine, None).expected == ProcessPolicy.open(engine, True).expected == protected
    with pytest.raises(PolicyChanged):
        ProcessPolicy.open(engine, False)

    with engine.begin() as conn:
        conn.execute(privacy_policy.update().values(epoch=0))
    with pytest.raises(PolicyChanged):
        ProcessPolicy.open(engine, None)
    with engine.begin() as conn:
        conn.execute(privacy_policy.delete())
    with pytest.raises(PolicyChanged):
        ProcessPolicy.open(engine, None)


def test_invalid_policy_epoch_is_rejected_by_status_and_rollback(engine, db_url, monkeypatch, capsys):
    with engine.begin() as conn:
        conn.execute(privacy_policy.update().values(mode=PROTECTED, epoch=1))
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)

    assert main(["status"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "policy_mismatch"}
    assert main(["rollback", "--epoch", "1", "--confirm"]) == 1
    assert json.loads(capsys.readouterr().out) == {"error": "policy_mismatch"}
    with engine.connect() as conn:
        assert conn.execute(select(privacy_policy.c.mode, privacy_policy.c.epoch)).one() == (PROTECTED, 1)


def test_attestation_requires_the_current_isolation_and_exact_occupied_binding(engine, owner, attestation):
    store, source = attestation
    legacy = PolicyEpoch(LEGACY, 0)
    isolated = _advance(engine, legacy, ISOLATED)
    assert not store.approve(expected=legacy, binding=owner, source=source)
    assert not store.approve(expected=PolicyEpoch(ISOLATED, 3), binding=owner, source=source)
    assert not store.approve(expected=isolated, binding=replace(owner, user_id=2), source=source)
    assert not store.approve(expected=isolated, binding=replace(owner, user_id=None), source=source)
    assert not store.approve(expected=isolated, binding=replace(owner, generation="0" * 32), source=source)
    for field in ("client_id", "client_secret", "account_seq"):
        assert not store.approve(expected=isolated, binding=owner, source=replace(source, **{field: ""}))
    assert _proof_rows(engine) == []

    assert store.approve(expected=isolated, binding=owner, source=source)
    assert store.verify(expected=isolated, binding=owner, source=source) is None
    protected = _advance(engine, isolated, PROTECTED)
    assert not store.approve(expected=isolated, binding=owner, source=source)
    assert not store.approve(expected=protected, binding=owner, source=source)
    assert store.verify(expected=protected, binding=owner, source=source) is not None
    with engine.begin() as conn:
        conn.execute(users.delete().where(users.c.id == owner.user_id))
    assert store.verify(expected=protected, binding=owner, source=source) is None

    isolated = _advance(engine, protected, ISOLATED)
    with engine.begin() as conn:
        conn.execute(account_owner_binding.delete())
    assert not store.approve(expected=isolated, binding=owner, source=source)


def test_source_is_immutable_per_generation_and_each_isolation_requires_approval(engine, owner, attestation):
    store, source = attestation
    isolated = _advance(engine, PolicyEpoch(LEGACY, 0), ISOLATED)
    assert store.approve(expected=isolated, binding=owner, source=source)
    first = _proof_rows(engine)
    assert store.approve(expected=isolated, binding=owner, source=source)
    different = (
        replace(source, client_id="different-id"),
        replace(source, client_secret=source.client_secret + "\0"),
        replace(source, account_seq=source.account_seq.lstrip("0")),
    )
    for wrong in different:
        assert not store.approve(expected=isolated, binding=owner, source=wrong)
    assert _proof_rows(engine) == first

    protected = _advance(engine, isolated, PROTECTED)
    valid = store.verify(expected=protected, binding=owner, source=source)
    assert valid is not None
    for wrong in different:
        assert store.verify(expected=protected, binding=owner, source=wrong) is None
    assert store.verify(expected=protected, binding=OwnerBinding(2, owner.generation), source=source) is None
    assert store.verify(expected=protected, binding=OwnerBinding(owner.user_id, "0" * 32), source=source) is None

    isolated = _advance(engine, protected, ISOLATED)
    assert store.verify(expected=protected, binding=owner, source=source) is None
    for wrong in different:
        assert not store.approve(expected=isolated, binding=owner, source=wrong)
    assert _proof_rows(engine) == first

    protected = _advance(engine, isolated, PROTECTED)
    assert store.verify(expected=protected, binding=owner, source=source) is None
    isolated = _advance(engine, protected, ISOLATED)
    assert store.approve(expected=isolated, binding=owner, source=source)
    latest, = _proof_rows(engine)
    assert latest["fingerprint"] == first[0]["fingerprint"]
    assert latest["approved_isolation_epoch"] == isolated.epoch

    next_protected = _advance(engine, isolated, PROTECTED)
    verified = store.verify(expected=next_protected, binding=owner, source=source)
    assert verified is not None and verified.seal_material != valid.seal_material
    assert store.verify(expected=protected, binding=owner, source=source) is None
    evidence_and_repr = repr((first, latest, source, verified))
    for private in (source.client_id, source.client_secret, source.account_seq, verified.seal_material):
        assert private not in evidence_and_repr


def test_an_edited_approval_epoch_does_not_replay_the_last_approval(engine, owner, attestation):
    store, source = attestation
    isolated = _advance(engine, PolicyEpoch(LEGACY, 0), ISOLATED)
    assert store.approve(expected=isolated, binding=owner, source=source)
    protected = _advance(engine, isolated, PROTECTED)
    isolated = _advance(engine, protected, ISOLATED)
    with engine.begin() as conn:
        conn.execute(owner_toss_attestations.update().values(approved_isolation_epoch=isolated.epoch))
    protected = _advance(engine, isolated, PROTECTED)
    assert store.verify(expected=protected, binding=owner, source=source) is None


def test_concurrent_source_approvals_preserve_the_first_source(engine, owner, attestation):
    store, source = attestation
    isolated = _advance(engine, PolicyEpoch(LEGACY, 0), ISOLATED)
    both_ready = Barrier(2, timeout=5)
    sources = (source, replace(source, account_seq="synthetic-other-account"))

    def approve(candidate):
        both_ready.wait()
        return store.approve(expected=isolated, binding=owner, source=candidate)

    with ThreadPoolExecutor(max_workers=2) as pool:
        accepted = list(pool.map(approve, sources))
    assert accepted.count(True) == 1
    assert len(_proof_rows(engine)) == 1
    protected = _advance(engine, isolated, PROTECTED)
    assert [store.verify(expected=protected, binding=owner, source=candidate) is not None
            for candidate in sources] == accepted


def test_protected_tokens_share_the_toss_row_and_backoff_without_sharing_seals(engine, owner, attestation):
    store, source = attestation
    clock = datetime(2026, 11, 20, 1, tzinfo=UTC)
    issued = []

    async def issue():
        token = f"synthetic-token-{len(issued)}"
        issued.append(token)
        return Issued(token, 3600)

    def slot(role, seal_material):
        return TokenStore(engine, role=role, clock=lambda: clock, wait_sec=0).slot("toss", seal_material)

    legacy = slot("all", source.client_secret)
    assert asyncio.run(legacy.get(issue))[0] == "synthetic-token-0"
    isolated = _advance(engine, PolicyEpoch(LEGACY, 0), ISOLATED)
    assert store.approve(expected=isolated, binding=owner, source=source)
    protected = _advance(engine, isolated, PROTECTED)
    first = store.verify(expected=protected, binding=owner, source=source)
    assert first is not None
    collector = slot("collector", first.seal_material)
    with pytest.raises(TokenUnavailable, match="waiting before"):
        asyncio.run(collector.get(issue))
    assert len(issued) == 1

    clock += timedelta(seconds=61)
    assert asyncio.run(collector.get(issue))[0] == "synthetic-token-1"
    for role in ("api", "analyst"):
        verified = store.verify(expected=protected, binding=owner, source=source)
        assert verified is not None
        assert asyncio.run(slot(role, verified.seal_material).get(issue))[0] == "synthetic-token-1"
    with pytest.raises(TokenUnavailable):
        asyncio.run(slot("api", source.client_secret).get(issue))

    isolated = _advance(engine, protected, ISOLATED)
    assert store.approve(expected=isolated, binding=owner, source=source)
    protected = _advance(engine, isolated, PROTECTED)
    second = store.verify(expected=protected, binding=owner, source=source)
    assert second is not None
    with pytest.raises(TokenUnavailable, match="waiting before"):
        asyncio.run(slot("collector", second.seal_material).get(issue))
    clock += timedelta(seconds=61)
    assert asyncio.run(slot("collector", second.seal_material).get(issue))[0] == "synthetic-token-2"
    with pytest.raises(TokenUnavailable):
        asyncio.run(slot("api", first.seal_material).get(issue))

    isolated = _advance(engine, protected, ISOLATED)
    with engine.begin() as conn:
        conn.execute(account_owner_binding.update().values(user_id=None))
    owners = AccountOwnerStore(engine)
    vacancy = owners.binding()
    assert vacancy is not None
    next_owner = owners.bind_verified(verified_user_id=1, expected_vacancy=vacancy)
    assert next_owner is not None
    next_source = replace(source, client_id="new-client-same-secret")
    assert store.approve(expected=isolated, binding=next_owner, source=next_source)
    protected = _advance(engine, isolated, PROTECTED)
    assert store.verify(expected=protected, binding=next_owner, source=source) is None
    next_seal = store.verify(expected=protected, binding=next_owner, source=next_source)
    assert next_seal is not None
    with pytest.raises(TokenUnavailable):
        asyncio.run(slot("api", next_seal.seal_material).get(issue))
    assert len(issued) == 3
    with engine.connect() as conn:
        assert conn.scalars(select(broker_tokens.c.name)).all() == ["toss"]


@pytest.mark.parametrize("proof", ["old-schema", "missing", "invalid"])
def test_emergency_rollback_is_independent_of_toss_proof(engine, db_url, owner, attestation, monkeypatch, capsys, proof):
    store, source = attestation
    if proof == "old-schema":
        command.downgrade(alembic_config(db_url), "0030")
    isolated = _advance(engine, PolicyEpoch(LEGACY, 0), ISOLATED)
    if proof == "invalid":
        assert store.approve(expected=isolated, binding=owner, source=source)
        with engine.begin() as conn:
            conn.execute(owner_toss_attestations.update().values(approval_fingerprint=b"0" * 32))
    protected = _advance(engine, isolated, PROTECTED)
    if proof != "old-schema":
        assert store.verify(expected=protected, binding=owner, source=source) is None
    monkeypatch.setenv("DESK_DATABASE_URL", db_url)
    assert main(["rollback", "--epoch", str(protected.epoch), "--confirm"]) == 0
    assert json.loads(capsys.readouterr().out) == {"mode": ISOLATED, "epoch": protected.epoch + 1}


def test_migration_keeps_the_source_history_even_after_the_binding_changes(engine, db_url, owner, attestation):
    config = alembic_config(db_url)
    command.downgrade(config, "0030")
    assert not inspect(engine).has_table("owner_toss_attestations")
    command.upgrade(config, "0031")

    store, source = attestation
    isolated = _advance(engine, PolicyEpoch(LEGACY, 0), ISOLATED)
    assert store.approve(expected=isolated, binding=owner, source=source)
    with engine.begin() as conn:
        conn.execute(users.delete().where(users.c.id == owner.user_id))
    with pytest.raises(RuntimeError, match="Toss attestation history must be retained"):
        command.downgrade(config, "0030")
    assert _proof_rows(engine)[0]["generation"] == owner.generation
