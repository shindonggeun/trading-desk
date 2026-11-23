"""Record independently verified Toss credentials against an isolated owner binding."""

import hashlib
import hmac
import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.engine import Engine

from .account_owner import OwnerBinding
from .db import account_owner_binding, owner_toss_attestations, privacy_policy
from .privacy import (
    ISOLATED,
    PROTECTED,
    PolicyChanged,
    PolicyEpoch,
    PrivacyPolicyStore,
    ProcessPolicy,
    policy_epoch_matches,
)

_VERSION = 1


@dataclass(frozen=True)
class TossSource:
    client_id: str = field(repr=False)
    client_secret: str = field(repr=False)
    account_seq: str = field(repr=False)


@dataclass(frozen=True)
class VerifiedTossAttestation:
    seal_material: str = field(repr=False)


def _encode(value) -> bytes:
    # JSON framing preserves boundaries, leading zeros and the exact spelling of each field.
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "surrogatepass")


def _digest(key: bytes, purpose: str, *fields: str | int) -> bytes:
    return hmac.digest(key, _encode(["trading-desk", "toss", purpose, _VERSION, *fields]), "sha256")


def _source_evidence(binding: OwnerBinding, source: TossSource) -> tuple[bytes, bytes] | None:
    if type(binding.user_id) is not int or binding.user_id < 1:
        return None
    if not isinstance(binding.generation, str) or not binding.generation:
        return None
    if not all(isinstance(value, str) and value.strip() for value in (
        source.client_id, source.client_secret, source.account_seq,
    )):
        return None
    # Hash the exact secret before HMAC's padding, which otherwise equates some distinct short keys.
    key = hashlib.sha256(_encode(source.client_secret)).digest()
    fingerprint = _digest(key, "source", binding.user_id, binding.generation, source.client_id, source.account_seq)
    return key, fingerprint


def _same_digest(stored: object, expected: bytes) -> bool:
    return isinstance(stored, bytes) and hmac.compare_digest(stored, expected)


def _binding_query(binding: OwnerBinding):
    return select(account_owner_binding.c.id).where(
        account_owner_binding.c.id == 1,
        account_owner_binding.c.user_id == binding.user_id,
        account_owner_binding.c.generation == binding.generation,
    )


def _proof_query(binding: OwnerBinding):
    return select(owner_toss_attestations).where(owner_toss_attestations.c.generation == binding.generation)


class TossAttestationStore:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.policy = PrivacyPolicyStore(engine)

    def approve(self, *, expected: PolicyEpoch, binding: OwnerBinding, source: TossSource) -> bool:
        """Persist an operator's independent verification of this exact source in the expected isolation."""
        if expected.mode != ISOLATED or not expected.valid:
            return False
        evidence = _source_evidence(binding, source)
        if evidence is None:
            return False
        key, fingerprint = evidence
        approval = _digest(key, "approval", fingerprint.hex(), expected.epoch)
        with self.engine.begin() as conn:
            try:
                ProcessPolicy(self.policy, expected).lock(conn, exclusive=True)
            except PolicyChanged:
                return False
            # Account binding updates and user deletion can commit without taking the policy lock.
            if conn.scalar(_binding_query(binding).with_for_update()) is None:
                return False
            prior = conn.execute(_proof_query(binding)).one_or_none()
            pending = {"approved_isolation_epoch": expected.epoch, "approval_fingerprint": approval}
            if prior is None:
                conn.execute(owner_toss_attestations.insert().values(
                    generation=binding.generation, version=_VERSION, fingerprint=fingerprint, **pending,
                ))
                return True
            if prior.version != _VERSION or not _same_digest(prior.fingerprint, fingerprint):
                return False
            approved_epoch = PolicyEpoch(ISOLATED, prior.approved_isolation_epoch)
            if not approved_epoch.valid or approved_epoch.epoch > expected.epoch:
                return False
            if approved_epoch == expected:
                return _same_digest(prior.approval_fingerprint, approval)
            conn.execute(owner_toss_attestations.update().where(
                owner_toss_attestations.c.generation == binding.generation,
            ).values(**pending))
            return True

    def verify(self, *, expected: PolicyEpoch, binding: OwnerBinding,
               source: TossSource) -> VerifiedTossAttestation | None:
        """Verify the live policy and owner, and derive seal material for this protected epoch."""
        if expected.mode != PROTECTED or not expected.valid:
            return None
        evidence = _source_evidence(binding, source)
        if evidence is None:
            return None
        key, fingerprint = evidence
        isolation = expected.epoch - 1
        # One statement gives the policy, binding and approval the same database snapshot.
        active_policy = select(privacy_policy.c.id).where(policy_epoch_matches(expected)).exists()
        with self.engine.connect() as conn:
            proof = conn.execute(_proof_query(binding).where(
                active_policy, _binding_query(binding).exists(),
                owner_toss_attestations.c.approved_isolation_epoch == isolation,
            )).one_or_none()
        if proof is None or proof.version != _VERSION or not _same_digest(proof.fingerprint, fingerprint):
            return None
        if not _same_digest(proof.approval_fingerprint, _digest(key, "approval", fingerprint.hex(), isolation)):
            return None
        seal = _digest(key, "protected-token-seal", fingerprint.hex(), expected.epoch)
        return VerifiedTossAttestation(seal.hex())
