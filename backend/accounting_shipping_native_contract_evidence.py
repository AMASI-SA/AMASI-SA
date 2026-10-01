"""Native accountant-reviewed shipping sources; no financial writer.

The caller authorizes an actual review and commits its result through the
existing owner setup CAS. The authority resolves only those persisted records,
then locks that same setup document during a financial transaction. Neither
client approval flags nor an uploaded file alone authorize a contract.
"""
from datetime import datetime, timezone
import hashlib

from fastapi import HTTPException

from accounting_shipping_evidence import EvidenceIdentity, _error, _transaction
from accounting_shipping_native_contract import SETUP


RECORD_TYPE = "accountant_reviewed_shipping_source"
PURPOSES = frozenset({"contract", "shipping_tax", "commission_tax"})


def _text(value):
    if not isinstance(value, str) or not value or value != value.strip():
        _error("shipping_evidence_scope_mismatch")
    return value


def _identity(row):
    if not isinstance(row, dict):
        _error("shipping_evidence_not_approved")
    if (row.get("revoked") is True or row.get("revoked_at")
            or row.get("status") == "revoked" or row.get("is_deleted") is True):
        _error("shipping_evidence_not_approved")
    try:
        proof = EvidenceIdentity.model_validate({
            key: row[key] for key in EvidenceIdentity.model_fields
        })
    except (KeyError, ValueError) as exc:
        raise HTTPException(409, detail={"code": "shipping_evidence_not_approved"}) from exc
    if (proof.record_type != RECORD_TYPE or proof.state != "approved"
            or proof.deleted or proof.purpose not in PURPOSES):
        _error("shipping_evidence_not_approved")
    if proof.approved_at > datetime.now(timezone.utc):
        _error("shipping_evidence_approval_in_future")
    return proof.model_dump(mode="json")


async def build_review(db, *, owner, actor_id, courier_id, file_id, purpose,
                       evidence_id, approved_at):
    """Validate actual retained bytes for the caller's authenticated review.

    This is not an approval endpoint. Fresh explicit review permission, exact
    canonical courier identity, and CAS persistence are the caller's boundary.
    It returns server-authored identity fields and performs no database write.
    """
    for value in (owner, actor_id, courier_id, file_id, evidence_id):
        _text(value)
    if not isinstance(purpose, str) or purpose not in PURPOSES:
        _error("shipping_evidence_purpose_invalid")
    originals = await db.accounting_source_files.find(
        {"user_id": owner, "file_id": file_id}
    ).limit(2).to_list(2)
    if len(originals) != 1:
        _error("shipping_evidence_original_unavailable")
    blob = originals[0]
    if blob.get("deleted") is True or blob.get("is_deleted") is True:
        _error("shipping_evidence_original_unavailable")
    content = blob.get("content")
    if not isinstance(content, (bytes, bytearray, memoryview)) or not content:
        _error("shipping_evidence_original_unavailable")
    content = bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    if digest != blob.get("sha256") or len(content) != blob.get("size"):
        _error("shipping_evidence_original_hash_mismatch")
    return _identity({
        "evidence_id": evidence_id, "user_id": owner, "courier_id": courier_id,
        "file_id": file_id, "record_type": RECORD_TYPE, "state": "approved",
        "deleted": False, "revision": 1, "approved_by": actor_id,
        "approved_at": approved_at, "source_sha256": digest, "purpose": purpose,
    })


class NativeEvidenceAuthority:
    """Server-only resolver for the Native setup's evidence review records."""

    async def resolve_approved(self, db, *, owner, courier_id, evidence_id, purpose):
        for value in (owner, courier_id, evidence_id):
            _text(value)
        if not isinstance(purpose, str) or purpose not in PURPOSES:
            _error("shipping_evidence_purpose_invalid")
        setup = await db[SETUP].find_one(
            {"_id": owner, "user_id": owner}, {"contract_evidence": 1}
        )
        records = (setup or {}).get("contract_evidence", [])
        if not isinstance(records, list) or any(not isinstance(row, dict) for row in records):
            _error("shipping_evidence_not_approved")
        matches = [row for row in records if row.get("evidence_id") == evidence_id]
        if len(matches) != 1:
            _error("shipping_evidence_not_approved")
        proof = _identity(matches[0])
        if (proof["user_id"] != owner or proof["courier_id"] != courier_id
                or proof["purpose"] != purpose):
            _error("shipping_evidence_scope_mismatch")
        return proof

    async def lock_current(self, db, *, snapshot):
        proof = _identity(snapshot)
        scoped = _transaction(db, proof["user_id"])
        # Compare every evidence identity field, not just an ID or revision.
        # This writes the same document replaced by review/revocation CAS, so
        # Mongo serializes approval changes against the consuming journal.
        match = {**proof, "revoked": {"$ne": True},
                 "revoked_at": {"$in": [None, ""]},
                 "status": {"$ne": "revoked"}, "is_deleted": {"$ne": True}}
        result = await scoped[SETUP].update_one(
            {"_id": proof["user_id"], "user_id": proof["user_id"],
             "contract_evidence": {"$elemMatch": match}},
            {"$inc": {"usage_revision": 1}},
        )
        if result.matched_count != 1:
            _error("shipping_evidence_changed_since_approval")
