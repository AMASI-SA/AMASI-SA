"""Driver-attested cash and explicit handover matching, without financial writes.

Expected COD remains the existing responsibility. Actual cash is a separate,
immutable observation; neither a variance nor its matching posts a journal.
"""
from decimal import Decimal
from typing import Literal

from fastapi import HTTPException
from pydantic import Field, model_validator

from accounting_shipping_native_contract import Input, digest, now, instant, money, amount, fail, EVENTS, SOURCE
from accounting_module_contract import accounting_owner_id, OPERATION_ID
from accounting_write_control import fresh_actor
from accounting_ledger_v2 import read_verified_journal_metadata_v2, get_journal_v2, GROUPS_COLLECTION
from operational_atomic import operational_owner

COLLECTIONS = "store_delivery_collections"
LINKS = "mz2_driver_cash_reconciliations_v1"
SCHEMA = "mz2.driver.physical_cash.v1"
MAX_ROWS = 10000


def validate_cash_confirmation(requirements, physical_cash_amount, physical_cash_confirmed):
    required = requirements.get("payment_method") == "cash" and money(requirements.get("amount")) > 0
    if not required:
        if physical_cash_amount is not None or physical_cash_confirmed:
            fail("driver_physical_cash_not_applicable", 422)
        return None
    if physical_cash_confirmed is not True or not isinstance(physical_cash_amount, str) or not physical_cash_amount.strip():
        fail("driver_physical_cash_confirmation_required", 422)
    try:
        value = money(physical_cash_amount)
    except HTTPException:
        fail("driver_physical_cash_amount_invalid", 422)
    if value > Decimal("1000000"):
        fail("driver_physical_cash_amount_invalid", 422)
    return amount(value)


def build_cash_evidence(*, owner, driver, actor, assignment, collection, actual_amount, confirmed_at):
    actual = money(actual_amount)
    expected = money(collection.get("amount"), positive=True)
    if (collection.get("payment_method") != "cash" or not all((owner, driver.get("id"), driver.get("name"),
            actor.get("id"), assignment.get("id"), assignment.get("order_id"),
            assignment.get("order_number"), collection.get("id"), collection.get("delivery_proof_reference")))
            or collection.get("user_id") != owner or collection.get("driver_id") != driver["id"]
            or collection.get("assignment_id") != assignment["id"]
            or str(collection.get("order_id")) != str(assignment["order_id"])
            or collection.get("order_number") != assignment["order_number"]
            or driver.get("account_user_id") != actor["id"] or driver.get("user_id") != owner
            or actual > Decimal("1000000")):
        fail("driver_physical_cash_identity_required")
    instant(confirmed_at)
    row = {"schema": SCHEMA, "id": collection["id"], "user_id": owner,
        "source": "driver_confirmation_at_delivered", "collection_id": collection["id"],
        "driver_id": driver["id"], "driver_name": driver["name"],
        "assignment_id": assignment["id"], "order_id": str(assignment["order_id"]),
        "order_number": assignment["order_number"], "delivery_status": "delivered",
        "cod_amount": amount(expected), "physical_cash_amount": amount(actual),
        "variance": amount(actual - expected), "currency": "SAR", "confirmed_at": confirmed_at,
        "confirmation_actor": actor["id"], "delivery_proof_reference": collection["delivery_proof_reference"],
        "financial_effect": "none"}
    row["seal"] = digest(row)
    return row


def validate_evidence(collection, owner, driver_id):
    row = collection.get("physical_cash_evidence")
    if not row:
        return None
    if (not isinstance(row, dict) or row.get("seal") != digest({k: v for k, v in row.items() if k != "seal"})
            or row.get("schema") != SCHEMA or row.get("user_id") != owner or row.get("driver_id") != driver_id
            or row.get("id") != collection.get("id") or row.get("collection_id") != collection.get("id")
            or row.get("assignment_id") != collection.get("assignment_id")
            or row.get("order_id") != str(collection.get("order_id"))
            or row.get("order_number") != collection.get("order_number")
            or row.get("delivery_proof_reference") != collection.get("delivery_proof_reference")
            or row.get("cod_amount") != amount(money(collection.get("amount"), positive=True))
            or collection.get("payment_method") != "cash" or row.get("delivery_status") != "delivered"
            or row.get("source") != "driver_confirmation_at_delivered" or row.get("financial_effect") != "none"
            or row.get("currency") != "SAR" or not row.get("confirmation_actor") or not row.get("driver_name")
            or row.get("variance") != amount(money(row.get("physical_cash_amount")) - money(row["cod_amount"]))):
        fail("driver_physical_cash_integrity_failure")
    instant(row["confirmed_at"])
    return row


async def bounded(db, name, query):
    rows = await db[name].find(query).limit(MAX_ROWS + 1).to_list(MAX_ROWS + 1)
    if len(rows) > MAX_ROWS:
        fail("driver_physical_cash_scope_too_large")
    return rows


async def eligibility(db, owner, row):
    assignment = await db.store_delivery_assignments.find_one({"user_id": owner,
        "id": row["assignment_id"], "driver_id": row["driver_id"]})
    if (not assignment or assignment.get("status") != "delivered" or assignment.get("active") is not True
            or str(assignment.get("order_id")) != row["order_id"] or assignment.get("order_number") != row["order_number"]):
        return False, (assignment or {}).get("status"), "driver_delivered_source_changed"
    # Reuse the existing pure driver-source contract. This reads the exact
    # snapshot but never calls driver_facts(), which pins and mutates sources.
    from order_engine.repository import MongoOrderRepository
    from accounting_shipping_native_evidence import canonical_facts
    snapshot = await MongoOrderRepository(db).financial_delivery_snapshot(user_id=owner, order_number=row["order_number"])
    watermark = (snapshot or {}).get("g47_salla_snapshot") or {}
    if not snapshot or watermark.get("requires_authoritative_refresh") or watermark.get("cancelled"):
        return False, assignment["status"], "shipping_source_conflict"
    try:
        facts = canonical_facts(snapshot["raw_by_source"]["salla_direct"], order_number=row["order_number"],
            source_revision=assignment.get("delivered_at"), require_cod=False, require_carrier=False,
            driver_amount=row["cod_amount"], driver_delivered_at=assignment.get("delivered_at"))
        if facts["order_id"] != row["order_id"]:
            fail("shipping_order_identity_conflict")
    except HTTPException as exc:
        return False, assignment["status"], exc.detail.get("code") if isinstance(exc.detail, dict) else "shipping_source_conflict"
    return True, assignment["status"], None


async def handover_source(db, owner, driver_id, source_type, source_id, *, allow_reversed=False):
    if source_type == "operational_cod_remittance":
        rows = await db.store_delivery_driver_settlements.find({"user_id": owner, "driver_id": driver_id,
            "id": source_id}).limit(2).to_list(2)
        row = rows[0] if len(rows) == 1 else None
        if (not row or row.get("settlement_type") != "cod_remittance" or row.get("status") != "posted"
                or row.get("posting_scope") != "operational_balance" or row.get("accounting_status") != "operational_only"
                or row.get("ledger_txn_group_id") or row.get("accounting_operation_id")
                or not isinstance(row.get("account_id"), str) or not row["account_id"].strip() or not row.get("created_by")
                or money(row.get("earning_offset", 0)) != 0 or money(row.get("delivery_fee_settled_amount", 0)) != 0
                or money(row.get("cod_settled_amount")) != money(row.get("amount"), positive=True)):
            fail("driver_cash_handover_source_invalid")
        value = {k: row.get(k) for k in ("id", "user_id", "driver_id", "amount", "settlement_type", "status",
            "cod_settled_amount", "delivery_fee_settled_amount", "earning_offset", "reference", "created_at", "created_by",
            "account_id", "account_name_snapshot",
            "posting_scope", "accounting_status", "ledger_txn_group_id", "accounting_operation_id")}
        instant(row.get("created_at"))
        return {"source_type": source_type, "source_id": source_id, "amount": amount(money(row["amount"])),
                "occurred_at": row["created_at"],
                "fingerprint": digest(value), "financial_proof": "operational_only_no_native_settlement_proof",
                "txn_group_id": None, "journal_reversed": False}
    row = await db[EVENTS].find_one({"user_id": owner, "_id": source_id,
        "party_type": "store_driver", "party_id": driver_id, "kind": "settlement", "action": "receive_cod"})
    if not row or not row.get("txn_group_id") or row.get("settlement_origin") == "driver_payment_review":
        fail("driver_cash_handover_source_invalid")
    metadata = await read_verified_journal_metadata_v2(db, user_id=owner, txn_group_id=row["txn_group_id"],
        require_unreversed=not allow_reversed)
    journal = await get_journal_v2(db, user_id=owner, txn_group_id=row["txn_group_id"])
    group = journal["group"]
    if (group.get("source") != SOURCE or group.get("txn_type") != "shipping_receive_cod"
            or group.get("idempotency_key") != source_id or group.get("reversal_of_txn_group_id")
            or any(metadata.get(k) != row.get(k) for k in ("party_type", "party_id", "action", "movement_id", "binding", "reason"))):
        fail("driver_cash_handover_source_invalid")
    legs = journal["entries"]
    cash = [leg for leg in legs if leg["side"] == "credit" and leg["entity_type"] == "store_driver"
            and leg["entity_id"] == driver_id and leg.get("sub_account") == "cod_receivable"]
    banks = [leg for leg in legs if leg["side"] == "debit" and leg["entity_type"] == "bank"]
    if len(legs) != 2 or len(cash) != 1 or len(banks) != 1 or money(cash[0]["amount"]) != money(banks[0]["amount"]):
        fail("driver_cash_handover_source_invalid")
    reversals = await db[GROUPS_COLLECTION].find({"user_id": owner, "operation_id": OPERATION_ID,
        "reversal_of_txn_group_id": row["txn_group_id"]}).limit(2).to_list(2)
    if len(reversals) > 1:
        fail("driver_cash_handover_source_invalid")
    for reversal in reversals:
        await read_verified_journal_metadata_v2(db, user_id=owner, txn_group_id=reversal["txn_group_id"])
    return {"source_type": source_type, "source_id": source_id, "amount": amount(money(cash[0]["amount"], positive=True)),
        "occurred_at": group["effective_at"],
        "fingerprint": digest([row, group["content_hash"]]), "financial_proof": "verified_native_cash_settlement",
        "txn_group_id": row["txn_group_id"], "journal_reversed": bool(reversals)}


def validate_link(row, owner, driver_id):
    if (row.get("seal") != digest({k: v for k, v in row.items() if k not in {"_id", "seal"}})
            or row.get("user_id") != owner or row.get("driver_id") != driver_id
            or row.get("_id") != digest([owner, "driver-cash-reconciliation", row.get("request_id")])
            or row.get("financial_effect") != "none"
            or (row.get("source") or {}).get("source_type") not in {"native_cash_settlement", "operational_cod_remittance"}
            or not isinstance(row.get("allocations"), list) or not row["allocations"]
            or len({a.get("collection_id") for a in row["allocations"]}) != len(row["allocations"])
            or sum((money(a.get("amount"), positive=True) for a in row["allocations"]), Decimal(0)) != money(row["source"].get("amount"), positive=True)):
        fail("driver_cash_reconciliation_integrity_failure")


async def read_custody(db, owner, driver_id):
    collections = await bounded(db, COLLECTIONS, {"user_id": owner, "driver_id": driver_id, "payment_method": "cash"})
    links = await bounded(db, LINKS, {"user_id": owner, "driver_id": driver_id})
    allocated, allocation_seals, targets, reconciliations = {}, {}, set(), []
    for link in links:
        validate_link(link, owner, driver_id)
        source = await handover_source(db, owner, driver_id, link["source"]["source_type"], link["source"]["source_id"], allow_reversed=True)
        if source["fingerprint"] != link["source"]["fingerprint"] or (source["source_type"], source["source_id"]) in targets:
            fail("driver_cash_reconciliation_source_changed")
        targets.add((source["source_type"], source["source_id"]))
        for allocation in link["allocations"]:
            key = allocation["collection_id"]
            if key in allocation_seals and allocation_seals[key] != allocation.get("evidence_seal"):
                fail("driver_cash_reconciliation_integrity_failure")
            allocation_seals[key] = allocation.get("evidence_seal")
            allocated[key] = allocated.get(key, Decimal(0)) + money(allocation["amount"], positive=True)
        reconciliations.append({k: v for k, v in link.items() if k not in {"_id", "user_id"}} | {"source": source})
    items, missing = [], []
    totals = {key: Decimal(0) for key in ("confirmed_cash", "eligible_confirmed_cash", "expected_cod", "variance", "matched_handover", "confirmed_cash_remaining")}
    for collection in collections:
        evidence = validate_evidence(collection, owner, driver_id)
        if evidence is None:
            missing.append(str(collection.get("id") or collection["_id"]))
            continue
        actual, expected = money(evidence["physical_cash_amount"]), money(evidence["cod_amount"])
        if collection["id"] in allocation_seals and allocation_seals[collection["id"]] != evidence["seal"]:
            fail("driver_cash_reconciliation_integrity_failure")
        paid = allocated.pop(collection["id"], Decimal(0))
        if paid > actual:
            fail("driver_cash_reconciliation_exceeds_confirmation")
        eligible, status, reason = await eligibility(db, owner, evidence)
        items.append({**evidence, "eligible_for_reconciliation": eligible, "current_status": status,
            "reconciliation_reason": reason, "allocated_amount": amount(paid), "remaining_amount": amount(actual - paid)})
        for key, value in (("confirmed_cash", actual), ("expected_cod", expected), ("variance", actual - expected),
                ("matched_handover", paid), ("confirmed_cash_remaining", actual - paid)):
            totals[key] += value
        if eligible:
            totals["eligible_confirmed_cash"] += actual
    if allocated:
        fail("driver_cash_reconciliation_confirmation_missing")
    candidates = []
    native = await bounded(db, EVENTS, {"user_id": owner, "kind": "settlement", "party_type": "store_driver",
        "party_id": driver_id, "action": "receive_cod"})
    operational = await bounded(db, "store_delivery_driver_settlements", {"user_id": owner, "driver_id": driver_id,
        "settlement_type": "cod_remittance", "status": "posted"})
    for source_type, rows in (("native_cash_settlement", native), ("operational_cod_remittance", operational)):
        for row in rows:
            source_id = row["_id"] if source_type == "native_cash_settlement" else row["id"]
            if (source_type, source_id) not in targets:
                candidates.append(await handover_source(db, owner, driver_id, source_type, source_id, allow_reversed=True))
    changed_proofs = [row["source"]["source_id"] for row in reconciliations if row["source"]["journal_reversed"]]
    complete = not missing and not candidates and not changed_proofs and all(item["eligible_for_reconciliation"] for item in items)
    return {"schema": SCHEMA, "scope": "captured_delivered_cash_only", "driver_id": driver_id,
        "financial_effect": "none", "read_only": True, "items": sorted(items, key=lambda r: (r["confirmed_at"], r["id"]), reverse=True),
        "totals": {key: amount(value) for key, value in totals.items()},
        "coverage": {"complete": complete, "missing_confirmation_collection_ids": sorted(missing),
            "unmatched_handover_source_ids": [{"source_type": row["source_type"], "source_id": row["source_id"]} for row in candidates],
            "changed_financial_proof_source_ids": changed_proofs,
            "opening_physical_cash": None, "historical_cash_inferred": False},
        "handover_candidates": candidates, "reconciliations": reconciliations}


class CashAllocation(Input):
    collection_id: str = Field(min_length=1, max_length=160)
    amount: str = Field(min_length=1, max_length=32)


class CashReconciliationInput(Input):
    request_id: str = Field(min_length=8, max_length=120)
    source_type: Literal["native_cash_settlement", "operational_cod_remittance"]
    source_id: str = Field(min_length=1, max_length=160)
    allocations: list[CashAllocation] = Field(min_length=1, max_length=250)
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def exact_allocations(self):
        if len({a.collection_id for a in self.allocations}) != len(self.allocations):
            raise ValueError("driver_cash_duplicate_confirmation")
        for allocation in self.allocations:
            money(allocation.amount, positive=True)
        return self


async def save_reconciliation(db, owner, actor_id, driver_id, payload):
    await db[LINKS].create_index([("user_id", 1), ("driver_id", 1)], name="ix_driver_cash_reconciliation_owner")
    async def commit(scoped):
        from store_delivery_settlement_routes import _require_accountant
        actor = await fresh_actor(scoped, {"id": actor_id})
        if accounting_owner_id(actor) != owner:
            fail("shipping_owner_scope_mismatch", 403)
        # fresh_actor deliberately projects accounting fields only. The
        # delivered operational permission also has explicit grants/denials.
        grants = await scoped.users.find_one({"id": actor_id}, {"extra_permissions": 1, "denied_permissions": 1}) or {}
        _require_accountant({**actor, "extra_permissions": grants.get("extra_permissions", []),
                             "denied_permissions": grants.get("denied_permissions", [])})
        key = digest([owner, "driver-cash-reconciliation", payload.request_id])
        fingerprint = digest([driver_id, payload.model_dump(mode="json")])
        prior = await scoped[LINKS].find_one({"_id": key, "user_id": owner})
        if prior:
            validate_link(prior, owner, driver_id)
            if prior.get("request_hash") != fingerprint:
                fail("driver_cash_reconciliation_idempotency_conflict")
            return {"state": "already_recorded", "id": key, "financial_effect": "none"}
        view = await read_custody(scoped, owner, driver_id)
        source = await handover_source(scoped, owner, driver_id, payload.source_type, payload.source_id)
        if any(row["source"]["source_type"] == payload.source_type and row["source"]["source_id"] == payload.source_id for row in view["reconciliations"]):
            fail("driver_cash_handover_already_matched")
        by_id = {row["id"]: row for row in view["items"]}
        allocations = []
        for allocation in payload.allocations:
            evidence = by_id.get(allocation.collection_id)
            if not evidence or not evidence["eligible_for_reconciliation"]:
                fail("driver_cash_eligible_confirmation_required")
            if instant(evidence["confirmed_at"]) > instant(source["occurred_at"]):
                fail("driver_cash_handover_before_confirmation")
            if money(allocation.amount, positive=True) > money(evidence["remaining_amount"]):
                fail("driver_cash_reconciliation_exceeds_confirmation")
            allocations.append({"collection_id": allocation.collection_id, "amount": amount(money(allocation.amount)), "evidence_seal": evidence["seal"]})
        if sum((money(a["amount"]) for a in allocations), Decimal(0)) != money(source["amount"]):
            fail("driver_cash_handover_amount_mismatch")
        row = {"_id": key, "id": key, "user_id": owner, "driver_id": driver_id,
            "request_id": payload.request_id, "request_hash": fingerprint, "source": source,
            "allocations": allocations, "reason": payload.reason, "recorded_by": actor_id,
            "recorded_at": now(), "financial_effect": "none"}
        row["seal"] = digest({k: v for k, v in row.items() if k != "_id"})
        await scoped[LINKS].insert_one(row)
        return {"state": "recorded", "id": key, "financial_effect": "none"}
    return await operational_owner(db, owner, commit, profile="driver_cash_reconciliation")
