"""P02 evidence port and retained-source linkage; NOT an approved-evidence service.

The reviewed HEAD has immutable accounting_source_files bytes, but no approved
contract/signature lifecycle adapter. The production resolver below deliberately
fails closed. No environment variable, payload flag or owner override enables it.
Positive adapter-contract tests use an explicitly test-only authority with real
Mongo and real original bytes; those tests do NOT prove production integration.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Literal, Protocol

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from accounting_atomic import SessionDatabase
from accounting_write_control import AccountingDatabase

SCHEMA = "mz2.shipping.evidence.snapshot.v1"
LINKS = "mz2_shipping_evidence_links"


def _hash(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _error(code, status=409):
    raise HTTPException(status, detail={"code": code})


class EvidenceIdentity(BaseModel):
    """Internal adapter output, never a public request/approval model.

    These are required port semantics, not a claim that a matching production
    collection or status enum already exists at the reviewed HEAD.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)
    evidence_id: str = Field(min_length=1)
    user_id: str = Field(min_length=1)
    courier_id: str = Field(min_length=1)
    file_id: str = Field(min_length=1)
    record_type: str = Field(min_length=1)
    state: Literal["approved", "signed"]
    deleted: StrictBool
    revision: int = Field(ge=1, strict=True)
    approved_by: str = Field(min_length=1)
    approved_at: datetime
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    purpose: Literal["contract", "shipping_tax", "commission_tax", "cod_handover"]

    @field_validator("evidence_id", "user_id", "courier_id", "file_id", "record_type", "approved_by")
    @classmethod
    def text(cls, value):
        if value != value.strip() or not value:
            raise ValueError("evidence_identity_invalid")
        return value

    @field_validator("approved_at")
    @classmethod
    def date(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("evidence_approval_timezone_required")
        return value.astimezone(timezone.utc)


class ApprovedEvidenceAuthority(Protocol):
    async def resolve_approved(self, db, *, owner, courier_id, evidence_id, purpose) -> dict: ...
    async def lock_current(self, db, *, snapshot: dict) -> None: ...


def _approved_service() -> ApprovedEvidenceAuthority:
    """Replace only in a separately reviewed integration with the real service.

    That integration MUST bind approved/signed state, owner, revision, soft and
    hard deletion, revocation and source replacement to the same Mongo boundary.
    Do not implement this by returning a client record or accepting a text ref.
    """
    _error("shipping_evidence_service_not_ready", 503)


def readiness():
    try:
        _approved_service()
    except HTTPException as exc:
        return {"ready": False, "code": exc.detail["code"],
                "approval_blocked": True, "native_posting_blocked": True}
    return {"ready": True, "code": None, "approval_blocked": False,
            "native_posting_blocked": False}


def _transaction(db, owner):
    if isinstance(db, AccountingDatabase):
        db = db.current()
    if not isinstance(db, SessionDatabase) or db._owner != owner or not db._session.in_transaction:
        _error("shipping_evidence_requires_owner_transaction")
    return db


async def snapshot_one(db, *, owner, courier_id, evidence_id, purpose):
    if not isinstance(evidence_id, str) or not evidence_id.strip():
        _error("shipping_evidence_id_required")
    authority = _approved_service()  # Unavailable means no arbitrary collection fallback.
    raw = await authority.resolve_approved(db, owner=owner, courier_id=courier_id,
                                            evidence_id=evidence_id, purpose=purpose)
    try:
        proof = EvidenceIdentity.model_validate(raw)
    except ValueError as exc:
        raise HTTPException(409, detail={"code": "shipping_evidence_not_approved"}) from exc
    if (proof.user_id != owner or proof.courier_id != courier_id
            or proof.evidence_id != evidence_id or proof.purpose != purpose):
        _error("shipping_evidence_scope_mismatch")
    if proof.deleted:
        _error("shipping_evidence_deleted")
    if proof.approved_at > datetime.now(timezone.utc):
        _error("shipping_evidence_approval_in_future")
    blob = await db.accounting_source_files.find_one({"user_id": owner, "file_id": proof.file_id})
    if not blob or blob.get("deleted") is True or blob.get("is_deleted") is True:
        _error("shipping_evidence_original_unavailable")
    try:
        content = bytes(blob["content"])
    except (KeyError, ValueError, TypeError):
        _error("shipping_evidence_original_unavailable")
    digest = hashlib.sha256(content).hexdigest()
    if (not content or digest != proof.source_sha256 or digest != blob.get("sha256")
            or len(content) != blob.get("size")):
        _error("shipping_evidence_original_hash_mismatch")
    result = proof.model_dump(mode="json")
    result["size"] = len(content)
    result["snapshot_sha256"] = _hash(result)
    return result


def validate_bundle(bundle, *, owner, courier_id):
    if not isinstance(bundle, dict) or bundle.get("schema") != SCHEMA:
        _error("shipping_evidence_snapshot_required")
    items = bundle.get("items")
    if not isinstance(items, list) or not items or _hash(items) != bundle.get("sha256"):
        _error("shipping_evidence_snapshot_hash_mismatch")
    purposes = set()
    for item in items:
        if not isinstance(item, dict):
            _error("shipping_evidence_snapshot_invalid")
        data = {key: value for key, value in item.items() if key != "snapshot_sha256"}
        if _hash(data) != item.get("snapshot_sha256"):
            _error("shipping_evidence_snapshot_hash_mismatch")
        try:
            proof = EvidenceIdentity.model_validate({key: data[key] for key in EvidenceIdentity.model_fields})
        except (KeyError, ValueError) as exc:
            raise HTTPException(409, detail={"code": "shipping_evidence_snapshot_invalid"}) from exc
        if proof.deleted or proof.user_id != owner or proof.courier_id != courier_id or proof.purpose in purposes:
            _error("shipping_evidence_snapshot_scope_mismatch")
        purposes.add(proof.purpose)
    return items


def bundle_of(items):
    ordered = sorted(items, key=lambda row: (row["purpose"], row["evidence_id"]))
    return {"schema": SCHEMA, "items": ordered, "sha256": _hash(ordered)}


async def approval_snapshot(db, *, owner, version, payload):
    purposes = [("contract", payload.contract_evidence_id)]
    if version.shipping_cost > 0 and version.shipping_vat_percent > 0:
        purposes.append(("shipping_tax", payload.shipping_tax_evidence_id))
    if (version.commission_vat_percent > 0
            and any(t.commission_percent > 0 or t.fixed_fee > 0 for t in version.cod_fee_tiers)):
        purposes.append(("commission_tax", payload.commission_tax_evidence_id))
    return bundle_of([await snapshot_one(db, owner=owner, courier_id=version.courier_id,
                                        evidence_id=identity, purpose=purpose)
                      for purpose, identity in purposes])


async def verify_bundle(db, *, owner, courier_id, bundle):
    for item in validate_bundle(bundle, owner=owner, courier_id=courier_id):
        fresh = await snapshot_one(db, owner=owner, courier_id=courier_id,
                                   evidence_id=item["evidence_id"], purpose=item["purpose"])
        if fresh != item:
            _error("shipping_evidence_changed_since_approval")
    return bundle


async def pin_bundle(db, *, owner, courier_id, bundle, link_kind, link_id):
    scoped = _transaction(db, owner)
    if link_kind not in {"contract", "journal"} or not link_id:
        _error("shipping_evidence_link_invalid")
    authority = _approved_service()
    for item in validate_bundle(bundle, owner=owner, courier_id=courier_id):
        # The future adapter must lock its actual signed/approved source record;
        # locking only the bytes would not protect approval revocation races.
        await authority.lock_current(scoped, snapshot=item)
        current = await snapshot_one(scoped, owner=owner, courier_id=courier_id,
                                     evidence_id=item["evidence_id"], purpose=item["purpose"])
        if current != item:
            _error("shipping_evidence_changed_since_approval")
        locked = await scoped.accounting_source_files.update_one(
            {"user_id": owner, "file_id": item["file_id"], "sha256": item["source_sha256"],
             "deleted": {"$ne": True}, "is_deleted": {"$ne": True}},
            {"$inc": {"mz2_shipping_link_revision": 1}},
        )
        if locked.matched_count != 1:
            _error("shipping_evidence_original_changed")
        key = _hash([owner, item["file_id"], link_kind, link_id, item["purpose"]])
        await scoped[LINKS].update_one({"_id": key}, {"$setOnInsert": {
            "user_id": owner, "file_id": item["file_id"], "evidence_id": item["evidence_id"],
            "link_kind": link_kind, "link_id": link_id, "purpose": item["purpose"],
            "source_sha256": item["source_sha256"], "snapshot_sha256": item["snapshot_sha256"],
        }}, upsert=True)


async def assert_file_unlinked(db, owner, file_id):
    """P01 deletion integration; never rely on a missing auxiliary link alone."""
    linked = await db[LINKS].find_one({"user_id": owner, "file_id": file_id})
    contract = await db.mz2_shipping_rate_policies.find_one({"user_id": owner, "versions": {"$elemMatch": {
        "verification_status": "approved", "evidence_snapshot.items.file_id": file_id,
    }}})
    journal = await db.general_ledger.find_one({"user_id": owner,
        "metadata.evidence_snapshot.items.file_id": file_id})
    if linked or contract or journal:
        _error("shipping_evidence_linked_delete_forbidden")


async def guarded_source_delete(db, owner, file_id, callback):
    """Serialize retained source deletion with approvals and journal links.

    Additional source-model deletion/revocation APIs remain an integration gate.
    This one hook does not claim to cover an unimplemented evidence lifecycle.
    """
    from accounting_atomic import atomic_owner
    await assert_file_unlinked(db, owner, file_id)
    async def commit(scoped):
        await scoped.accounting_source_files.update_one(
            {"user_id": owner, "file_id": file_id}, {"$inc": {"mz2_shipping_link_revision": 1}})
        await assert_file_unlinked(scoped, owner, file_id)
        return await callback(scoped, owner, file_id)
    return await atomic_owner(db, owner, commit)
