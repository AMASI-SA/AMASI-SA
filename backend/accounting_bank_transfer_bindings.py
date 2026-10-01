"""Explicit owner-confirmed upstream bank evidence to canonical MZ2 FK.

Save only inside the caller's atomic_owner transaction. Resolution is readonly.
The source identifies the parsed bank value from Salla order payment evidence;
values are exact, with no resolver-side normalization or implicit ID fallback.
"""
from datetime import datetime, timezone
import hashlib
import json

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from typing import Literal
from accounting_financial_identity import find_financial_account

COLLECTION = "mz2_bank_transfer_bindings"
UPSTREAM_SOURCE = "salla.payment_method_bank"


class BankTransferBindingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upstream_source: Literal["salla.payment_method_bank"]
    upstream_value: str = Field(min_length=1, max_length=500)
    financial_account_id: str = Field(min_length=1, max_length=200)
    confirmation: Literal["CONFIRM_MZ2_BANK_TRANSFER_BINDING"]
    evidence_ref: str = Field(min_length=1, max_length=500)


def _key(owner, source, value):
    if not owner or source != UPSTREAM_SOURCE or not isinstance(value, str) or not value.strip():
        raise HTTPException(409, detail={"code": "MZ2_LINK_REQUIRED"})
    return hashlib.sha256(json.dumps([owner, source, value], ensure_ascii=False).encode()).hexdigest()


async def resolve_bank_transfer_binding(db, owner, source, value):
    binding = await db[COLLECTION].find_one({
        "_id": _key(owner, source, value), "user_id": owner,
        "upstream_source": source, "upstream_value": value,
        "status": "active", "confirmed": True, "identity_contract_version": 1,
        "bank_account_source": "mz2_financial_accounts",
    })
    bank = await find_financial_account(
        db, owner, binding.get("financial_account_id"), account_types=("bank",), currency="SAR",
    ) if binding else None
    return bank


async def save_bank_transfer_binding(db, owner, actor, payload: BankTransferBindingIn):
    """Setup metadata only; route supplies fresh permissions and atomic boundary."""
    key = _key(owner, payload.upstream_source, payload.upstream_value)
    if not actor.get("id") or not payload.evidence_ref.strip():
        raise HTTPException(409, detail={"code": "binding_confirmation_evidence_required"})
    bank = await find_financial_account(
        db, owner, payload.financial_account_id, account_types=("bank",), currency="SAR")
    if not bank:
        raise HTTPException(409, detail={"code": "MZ2_LINK_REQUIRED"})
    previous = await db[COLLECTION].find_one({"_id": key, "user_id": owner})
    event = {"actor_id": actor["id"], "at": datetime.now(timezone.utc).isoformat(),
             "previous_financial_account_id": (previous or {}).get("financial_account_id"),
             "financial_account_id": bank["id"], "evidence_ref": payload.evidence_ref,
             "confirmation": payload.confirmation}
    fields = {"user_id": owner, "upstream_source": payload.upstream_source,
              "upstream_value": payload.upstream_value, "financial_account_id": bank["id"],
              "bank_account_source": "mz2_financial_accounts", "currency": "SAR",
              "identity_contract_version": 1, "status": "active", "confirmed": True}
    await db[COLLECTION].update_one({"_id": key, "user_id": owner},
        {"$set": fields, "$inc": {"revision": 1}, "$push": {"audit": event}}, upsert=True)
    return {**fields, "revision": int((previous or {}).get("revision", 0)) + 1}
