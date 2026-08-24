"""Public transparency checkpoints over the audit log.

A checkpoint commits to every audit event up to a sequence number with a Merkle root
over the events' chain hashes. Publishing the root and a signature lets an outside
party check that a record it was shown is in the log, and that the log it is shown
later still contains the same history, without database access and without seeing any
company, payment or serial data: the leaves are hashes.

Anchoring the root to an external ledger is a separate, optional step. When no anchor
service is configured the checkpoint is still created and its anchoring state says
plainly that it is unanchored, because a signed local root is not a blockchain record.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from taxstamp.audit import AuditRecord, record_audit_event
from taxstamp.db import LockKey, advisory_xact_lock
from taxstamp.enums import Role
from taxstamp.errors import Conflict, NotFound
from taxstamp.jsontypes import JsonObject
from taxstamp.merkle import ProofStep, inclusion_proof, merkle_root, verify_inclusion_proof
from taxstamp.models import AuditEvent, TransparencyCheckpoint
from taxstamp.security import document_signature_matches, sign_document
from taxstamp.services.context import Actor

CHECKPOINT_PURPOSE = "transparency-checkpoint"


@dataclass(frozen=True, slots=True)
class InclusionProof:
    checkpoint_ref: str
    tree_size: int
    root_hash: str
    leaf_index: int
    leaf_hash: str
    path: tuple[ProofStep, ...]

    def document(self) -> JsonObject:
        return {
            "checkpoint_ref": self.checkpoint_ref,
            "tree_size": self.tree_size,
            "root_hash": self.root_hash,
            "leaf_index": self.leaf_index,
            "leaf_hash": self.leaf_hash,
            "path": [{"position": step.position, "hash": step.hash_hex} for step in self.path],
        }


def _leaves(session: Session, *, up_to_seq: int) -> list[str]:
    return list(
        session.execute(select(AuditEvent.hash).where(AuditEvent.seq <= up_to_seq).order_by(AuditEvent.seq))
        .scalars()
        .all()
    )


def checkpoint_document(checkpoint: TransparencyCheckpoint) -> JsonObject:
    return {
        "checkpoint_ref": checkpoint.checkpoint_ref,
        "tree_size": checkpoint.tree_size,
        "covers_to_seq": checkpoint.covers_to_seq,
        "root_hash": checkpoint.root_hash,
        "prev_root_hash": checkpoint.prev_root_hash,
    }


def publish_checkpoint(
    session: Session,
    *,
    actor: Actor,
    now: dt.datetime,
    checkpoint_secret: str,
    audit_secret: str,
    revision: str,
) -> TransparencyCheckpoint:
    """Commit to the audit log as it stands, or refuse when nothing has been added."""
    actor.require_role(Role.SUPERVISOR, Role.ADMIN)
    # The chain lock is what the audit writer takes, so a checkpoint never commits to a
    # tree that grows underneath it.
    advisory_xact_lock(session, LockKey.AUDIT_CHAIN)
    latest_seq = session.execute(select(func.max(AuditEvent.seq))).scalar_one_or_none()
    if latest_seq is None:
        raise Conflict("there is nothing to checkpoint")
    previous = session.execute(
        select(TransparencyCheckpoint).order_by(TransparencyCheckpoint.tree_size.desc()).limit(1)
    ).scalar_one_or_none()
    leaves = _leaves(session, up_to_seq=int(latest_seq))
    if previous is not None and len(leaves) == previous.tree_size:
        raise Conflict(
            "no audit events were added since the last checkpoint",
            detail={"tree_size": str(previous.tree_size)},
        )

    root = merkle_root(leaves)
    checkpoint = TransparencyCheckpoint(
        checkpoint_ref=f"cp-{len(leaves)}-{root[:12]}",
        tree_size=len(leaves),
        covers_to_seq=int(latest_seq),
        root_hash=root,
        prev_root_hash=None if previous is None else previous.root_hash,
        signature="",
        published_by=actor.principal_id,
        created_at=now,
    )
    checkpoint.signature = sign_document(
        checkpoint_document(checkpoint), secret=checkpoint_secret, purpose=CHECKPOINT_PURPOSE
    )
    session.add(checkpoint)
    session.flush()
    record_audit_event(
        session,
        actor=actor.audit_actor(),
        record=AuditRecord(
            action="transparency.publish_checkpoint",
            target_type="transparency_checkpoint",
            target_id=str(checkpoint.id),
            outcome="success",
            after_state=checkpoint_document(checkpoint),
            request_id=actor.request_id,
        ),
        occurred_at=now,
        secret=audit_secret,
        revision=revision,
    )
    return checkpoint


def latest_checkpoint(session: Session) -> TransparencyCheckpoint:
    checkpoint = session.execute(
        select(TransparencyCheckpoint).order_by(TransparencyCheckpoint.tree_size.desc()).limit(1)
    ).scalar_one_or_none()
    if checkpoint is None:
        raise NotFound("no checkpoint has been published")
    return checkpoint


def prove_inclusion(session: Session, *, checkpoint_ref: str, audit_seq: int) -> InclusionProof:
    """A proof that one audit event is committed to by a published checkpoint."""
    checkpoint = session.execute(
        select(TransparencyCheckpoint).where(TransparencyCheckpoint.checkpoint_ref == checkpoint_ref)
    ).scalar_one_or_none()
    if checkpoint is None:
        raise NotFound("checkpoint not found", detail={"checkpoint_ref": checkpoint_ref})
    if audit_seq < 1 or audit_seq > checkpoint.covers_to_seq:
        raise NotFound(
            "this checkpoint does not cover that audit event",
            detail={"covers_to_seq": str(checkpoint.covers_to_seq)},
        )
    leaves = _leaves(session, up_to_seq=checkpoint.covers_to_seq)
    index = audit_seq - 1
    path = inclusion_proof(leaves, index)
    return InclusionProof(
        checkpoint_ref=checkpoint.checkpoint_ref,
        tree_size=checkpoint.tree_size,
        root_hash=checkpoint.root_hash,
        leaf_index=index,
        leaf_hash=leaves[index],
        path=path,
    )


def verify_proof(proof: InclusionProof) -> bool:
    return verify_inclusion_proof(leaf=proof.leaf_hash, proof=proof.path, root=proof.root_hash)


def checkpoint_signature_is_valid(checkpoint: TransparencyCheckpoint, *, checkpoint_secret: str) -> bool:
    expected = sign_document(
        checkpoint_document(checkpoint), secret=checkpoint_secret, purpose=CHECKPOINT_PURPOSE
    )
    return document_signature_matches(checkpoint.signature, expected)


def checkpoints_with_broken_root(session: Session, *, checkpoint_secret: str) -> list[str]:
    """Checkpoints whose stored root no longer matches the audit log they commit to."""
    findings: list[str] = []
    checkpoints = list(
        session.execute(select(TransparencyCheckpoint).order_by(TransparencyCheckpoint.tree_size))
        .scalars()
        .all()
    )
    for checkpoint in checkpoints:
        leaves = _leaves(session, up_to_seq=checkpoint.covers_to_seq)
        if len(leaves) != checkpoint.tree_size or merkle_root(leaves) != checkpoint.root_hash:
            findings.append(checkpoint.checkpoint_ref)
            continue
        if not checkpoint_signature_is_valid(checkpoint, checkpoint_secret=checkpoint_secret):
            findings.append(checkpoint.checkpoint_ref)
    return findings
