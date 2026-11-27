"""TTL lifecycle for bank receipt appearance evidence and replay hashes.

Never store raw beneficiary IBAN, transfer reference, original attachment
bytes or customer messages in these collections. Temporary staff triage
evidence expires after seven days. HMAC replay hashes remain for up to thirty
days to reject reuse of old bank screenshots; they cannot be reversed without
the separately controlled HMAC secret.

Mongo doesn't delete by merely storing a purge_after date. Production bank
receipt screening fails closed unless BOTH proper TTL indexes already exist.
Indexes are installed only in a separately owner-approved migration.
"""

from __future__ import annotations

from datetime import datetime,timedelta,timezone
from typing import Any

EVIDENCE_TTL_NAME="salla_assisted_bank_receipt_evidence_ttl_v1"
REF_TTL_NAME="salla_assisted_bank_receipt_refs_ttl_v1"
RECEIPT_EVIDENCE_AGE=timedelta(days=7)
RECEIPT_REF_AGE=timedelta(days=30)


class BankReceiptRetentionDenied(ValueError):
    pass


def _is_purge_after_index(data: Any) -> bool:
    return (isinstance(data,dict)
            and data.get("expireAfterSeconds")==0
            and isinstance(data.get("key"),(tuple,list))
            and list(data["key"])==[("purge_after",1)])


async def require_bank_receipt_retention(db: Any) -> None:
    try:
        evidence_indexes=await db.salla_ai_bank_receipt_evidence.index_information()
        ref_indexes=await db.salla_ai_bank_receipt_refs.index_information()
    except Exception as exc:
        raise BankReceiptRetentionDenied("bank_receipt_ttl_read_unavailable") from exc
    if not _is_purge_after_index(evidence_indexes.get(EVIDENCE_TTL_NAME)):
        raise BankReceiptRetentionDenied("receipt_evidence_pii_ttl_missing")
    if not _is_purge_after_index(ref_indexes.get(REF_TTL_NAME)):
        raise BankReceiptRetentionDenied("receipt_reference_fence_ttl_missing")


async def install_bank_receipt_retention(db: Any) -> dict:
    """One-time approved migration, NEVER invoked from customer request."""
    await db.salla_ai_bank_receipt_evidence.create_index(
        [("purge_after",1)],expireAfterSeconds=0,name=EVIDENCE_TTL_NAME,
    )
    await db.salla_ai_bank_receipt_refs.create_index(
        [("purge_after",1)],expireAfterSeconds=0,name=REF_TTL_NAME,
    )
    await require_bank_receipt_retention(db)
    return {"evidence_index":EVIDENCE_TTL_NAME,
            "reference_index":REF_TTL_NAME}
