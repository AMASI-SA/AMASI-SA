"""Read-only bank identity for courier collection in the current financial phase.

Operational bank keys retain their namespace. Native consumers continue to use
the strict canonical contract; this adapter never creates or activates accounts.
"""
from fastapi import HTTPException

from accounting_financial_identity import list_financial_accounts
from operational_balance_store import STATES, digest
from operational_bank_catalog import bank_identities

SNAPSHOT_FIELDS = ("bank_identity_source", "bank_identity_version", "bank_financial_source",
                   "bank_operational_started_at")


async def collection_bank_accounts(db, owner, *, actor_id=None):
    from operational_driver_payment_review import financial_source

    phase = await financial_source(db, owner)
    canonical = await list_financial_accounts(db, owner, account_types=("bank",), currency="SAR")
    started_at = None
    if phase == "operational":
        state = await db[STATES].find_one({"_id": owner, "owner_id": owner}) or {}
        started_at = state.get("started_at")
        if not isinstance(started_at, str) or not started_at:
            raise HTTPException(409, detail={"code": "operational_driver_bank_context_invalid"})
        try:
            rows = [row for row in await bank_identities(db, owner) if row.get("currency") == "SAR"]
        except ValueError as error:
            raise HTTPException(409, detail={"code": str(error)}) from None
        if len(rows) > 1000:
            raise HTTPException(409, detail={"code": "operational_driver_bank_scope_too_large"})
    else:
        # Existing canonical callers may predate phase activation. Their bank
        # identity remains readable; native write gates still own authorization.
        rows = canonical
    canonical_ids = {row["id"] for row in canonical}
    items = []
    for row in rows:
        identity = row["id"]
        if identity in canonical_ids:
            source = "mz2_financial_accounts"
        elif phase == "operational" and identity.startswith("account:"):
            source = "accounts"
        elif phase == "operational" and identity.startswith("operational-bank:"):
            source = "mz2_operational_bank_accounts"
        else:
            raise HTTPException(409, detail={"code": "operational_driver_bank_identity_invalid"})
        items.append({
            "id": identity, "name": row.get("name") or row.get("display_name") or identity,
            "account_type": "bank", "currency": "SAR", "status": "active",
            "bank_identity_source": source, "bank_identity_version": 2 if phase == "operational" else 1,
            "bank_financial_source": phase, "bank_operational_started_at": started_at,
        })
    return {"items": sorted(items, key=lambda row: row["id"]), "total": len(items),
            "source": "operational_bank_catalog" if phase == "operational" else "mz2_financial_accounts",
            "financial_source": phase, "started_at": started_at,
            "session_scope": digest([owner, actor_id]) if actor_id else None}


async def find_collection_bank(db, owner, bank_id):
    if not isinstance(bank_id, str) or not bank_id:
        return None
    choices = await collection_bank_accounts(db, owner)
    return next((row for row in choices["items"] if row["id"] == bank_id), None)


async def public_collection_bank_accounts(db, owner, *, actor_id):
    """Keep the existing canonical picker DTO; freeze richer identity internally."""
    result = await collection_bank_accounts(db, owner, actor_id=actor_id)
    if result["financial_source"] != "operational":
        result["items"] = [{key: row[key] for key in ("id", "name", "account_type", "currency", "status")}
                           for row in result["items"]]
    return result


def collection_bank_snapshot(bank):
    return {"bank_account_id": bank["id"], "bank_name_snapshot": bank["name"],
            **{field: bank.get(field) for field in SNAPSHOT_FIELDS}}


async def require_collection_bank_snapshot(db, owner, bank):
    """Call in the claim transaction, BEFORE the first external delivery request.

    Once Salla confirms delivery, persist delivery/fee/pending review with the
    claim's frozen snapshot, even if bank/phase changed during the request. The
    accountant's approval then requires reconciliation. Completed retries also
    use their frozen result. Display-name changes do not change bank identity.
    """
    current = await find_collection_bank(db, owner, bank.get("id"))
    if current is None or any(current.get(field) != bank.get(field) for field in SNAPSHOT_FIELDS):
        raise HTTPException(409, detail={"code": "driver_payment_bank_context_changed"})
    return current


def require_saved_bank_identity(bank, *records):
    """Preserve existing canonical records; new operational FKs are explicit."""
    for record in records:
        if record.get("bank_account_id") != bank["id"]:
            raise HTTPException(409, detail={"code": "driver_payment_destination_proof_invalid"})
        present = any(field in record for field in SNAPSHOT_FIELDS)
        if not present and bank["bank_identity_source"] == "mz2_financial_accounts":
            continue
        if not present or any(record.get(field) != bank.get(field) for field in SNAPSHOT_FIELDS):
            raise HTTPException(409, detail={"code": "driver_payment_bank_context_changed"})
