"""V2 collection of an existing documented special-order receivable.

Deliberately does not recognize a new sale/advance/tax or manufacture an account.
An approved, sealed V2 agreement and an exact unclassified native bank movement
are mandatory. This service is not registered by server.py or the old router.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from zoneinfo import ZoneInfo

from pydantic import Field, StrictInt

from .contracts import Contract, Evidence, Key, LedgerProof
from .domain import DomainError, add_event, balances, consume_financial_proof, digest, source_snapshot
from .evidence import EvidenceStore
from .mz2_v2_port import V2Event, V2Leg, post_in_owner_transaction
from .repository import COLLECTION, bounded
from .service import validate_key
from .v2_owner import execute_in_v2_owner

BINDINGS = "mezan_special_order_bank_bindings_v1"
EVENTS = "mezan_special_order_financial_events_v1"
NATIVE_MOVEMENTS = "mz2_daily_movements"
RIYADH = ZoneInfo("Asia/Riyadh")


class CollectReceiptV2(Contract):
    expected_revision: Annotated[StrictInt, Field(ge=1)]
    expected_epoch: Annotated[StrictInt, Field(ge=1)]
    receipt_claim_id: Key
    movement_id: Key
    confirmation: Literal["CONFIRM_SPECIAL_ORDER_BANK_ARRIVAL"]


def exact_minor(value):
    """Native decimal text is exact; malformed/fractional minor units fail closed."""
    if not isinstance(value, (str, int, Decimal)) or isinstance(value, bool):
        raise DomainError("special_v2_native_amount_invalid")
    try:
        number = Decimal(str(value))
        minor = number * 100
        if not number.is_finite() or minor != minor.to_integral_value() or not 0 < minor <= 10**12:
            raise ValueError()
        return int(minor)
    except (InvalidOperation, ValueError, OverflowError):
        raise DomainError("special_v2_native_amount_invalid") from None


def movement_facts(row):
    return {key: deepcopy(row.get(key)) for key in (
        "id", "user_id", "bank_account_id", "movement_date", "direction", "amount", "currency", "reference", "file_id")}


async def _journal(scoped, owner, group):
    from accounting_ledger_v2 import read_verified_journal_metadata_v2, query_entries_v2
    meta = await read_verified_journal_metadata_v2(scoped._db, user_id=owner,
        txn_group_id=group, require_unreversed=True, mongo_session=scoped._session)
    # query_entries_v2 accepts a SessionDatabase and inherits this exact session.
    rows = await query_entries_v2(scoped, user_id=owner, txn_group_id=group, limit=1000)
    return meta, rows


async def _agreement(scoped, doc):
    binding = doc.get("financial_agreement_v2")
    if not isinstance(binding, dict) or not binding.get("txn_group_id"):
        raise DomainError("special_v2_verified_agreement_required")
    if (doc.get("financial_agreement") or doc.get("costs")
            or binding.get("source_digest") != doc["snapshot_digest"]
            or binding.get("gross_sar_minor") != doc["customer_agreed_sar_minor"]):
        raise DomainError("special_v2_agreement_reconciliation_required")
    account = binding.get("receivable") or {}
    if set(account) != {"entity_type", "entity_id", "sub_account"} or (
            account["entity_type"], account["sub_account"]) != ("asset", "other_receivable"):
        raise DomainError("special_v2_native_receivable_required")
    # Exact existing typed identity; no default, alias, Legacy or setup mutation.
    fact = await scoped.mz2_opening_facts_v2.find_one({"user_id": doc["tenant_id"],
        "status": "active", **account})
    if not fact:
        raise DomainError("special_v2_native_receivable_required")
    meta, rows = await _journal(scoped, doc["tenant_id"], binding["txn_group_id"])
    expected = {"special_order_id": doc["order_id"], "special_event_id": binding.get("event_id"),
        "purpose": doc["purpose"], "evidence_digest": doc["snapshot_digest"],
        "policy_digest": binding.get("policy_digest")}
    if not binding.get("event_id") or meta != expected:
        raise DomainError("special_v2_agreement_binding_mismatch")
    debit = sum(exact_minor(r["amount"]) * (1 if r["side"] == "debit" else -1)
        for r in rows if all(r.get(k) == v for k, v in account.items()))
    if debit != binding["gross_sar_minor"]:
        raise DomainError("special_v2_agreement_principal_mismatch")
    return {**binding, "verified_effective_at": rows[0]["effective_at"]}


async def _event(scoped, local, doc, event_id):
    row = await local[EVENTS].find_one({"tenant_id": doc["tenant_id"], "order_id": doc["order_id"],
        "movement_id": event_id, "ledger_backend": "v2"}, {"_id": 0})
    if row is None or digest({k: v for k, v in row.items() if k != "integrity_digest"}) != row.get("integrity_digest"):
        raise DomainError("special_v2_receipt_event_integrity_failed")
    meta, entries = await _journal(scoped, doc["tenant_id"], row["txn_group_id"])
    if meta != row["journal_metadata"]:
        raise DomainError("special_v2_receipt_journal_binding_mismatch")
    expected = row.get("entries")
    if not isinstance(expected, list) or len(expected) != 2 or any(not isinstance(r, dict) for r in expected):
        raise DomainError("special_v2_receipt_event_integrity_failed")
    actual = sorted([{key: leg.get(key) for key in expected[0]} for leg in entries], key=lambda r: r["leg_key"])
    if actual != expected:
        raise DomainError("special_v2_receipt_journal_binding_mismatch")
    native = await scoped[NATIVE_MOVEMENTS].find_one({"user_id": doc["tenant_id"], "id": row["native_movement"]["id"]})
    if (not native or movement_facts(native) != row["native_movement"]
            or native.get("status") != "classified" or native.get("accounting_event_id") != event_id
            or native.get("special_order_id") != doc["order_id"]
            or native.get("special_receipt_claim_id") != row["payment"].get("receipt_claim_id")):
        raise DomainError("special_v2_native_movement_changed")
    await EvidenceStore(local).verify(doc["tenant_id"], Evidence.model_validate(row["evidence"]))
    payment = next((p for p in doc["payments"] if p["movement_id"] == event_id), None)
    if payment != row["payment"]:
        raise DomainError("special_v2_order_payment_changed")
    claims = [c for c in doc["receipt_claims"] if c.get("claim_id") == payment["receipt_claim_id"]]
    if len(claims) != 1:
        raise DomainError("special_v2_receipt_claim_changed")
    claim = claims[0]
    if (claim.get("state"), claim.get("movement_id"), claim.get("bank_account_id"),
            claim.get("amount_minor"), claim.get("evidence")) != (
            "confirmed", event_id, payment["account_id"], payment["amount_minor"], row["evidence"]):
        raise DomainError("special_v2_receipt_claim_changed")
    binding = await local[BINDINGS].find_one({"tenant_id": doc["tenant_id"],
        "order_id": doc["order_id"], "event_id": event_id, "ledger_backend": "v2"})
    reference_key = " ".join(native["reference"].split()).casefold()
    if not binding or (binding.get("account_id"), binding.get("reference_key"), binding.get("transaction_id")) != (
            payment["account_id"], reference_key, native["id"]):
        raise DomainError("special_v2_bank_binding_history_changed")
    return row


async def _order(local, owner, order_id):
    doc = await local[COLLECTION].find_one({"tenant_id": owner, "order_id": order_id, "provider": "mezan"}, {"_id": 0})
    if not doc:
        raise DomainError("order_not_found", 404)
    if digest(source_snapshot(doc)) != doc["snapshot_digest"]:
        raise DomainError("local_order_snapshot_mismatch")
    if doc.get("fx", {}).get("currency") != "SAR" or doc["fx"].get("rate_to_sar") != "1":
        raise DomainError("special_v2_bank_currency_not_yet_supported")
    ids = [p.get("movement_id") for p in doc["payments"]]
    if any(not isinstance(p, str) or not p for p in ids) or len(ids) != len(set(ids)):
        raise DomainError("special_v2_duplicate_observed_payment")
    claim_ids = [c.get("claim_id") for c in doc["receipt_claims"]]
    if len(claim_ids) != len(set(claim_ids)):
        raise DomainError("special_v2_duplicate_receipt_claim")
    # No silent reuse of former-ledger observations or unrelated posting paths.
    if any(p.get("kind") != "bank_collection" for p in doc["payments"]):
        raise DomainError("special_v2_other_financial_paths_require_migration")
    workflow = await local.order_review_workflows.find_one({"user_id": owner, "order_number": doc["order_number"]}, {"_id": 0})
    if workflow:
        if (workflow.get("order_id"), workflow.get("source_provider"), workflow.get("special_source_digest")) != (
                order_id, "mezan", doc["snapshot_digest"]):
            raise DomainError("shared_workflow_source_identity_mismatch")
        stage = workflow["stage"]
        result = await local.order_review_workflows.update_one({"user_id": owner,
            "order_number": doc["order_number"], "stage": stage, "special_source_digest": doc["snapshot_digest"]},
            {"$inc": {"special_financial_fence": 1}})
        if result.matched_count != 1:
            raise DomainError("workflow_changed_during_financial_operation")
        doc["stage"] = "processing" if stage == "in_progress" else stage
    return doc


async def collect_existing_receipt(db, *, tenant_id, session_user, order_id,
                                    request: CollectReceiptV2, idempotency_key):
    """Accountant approval; receipt attachment alone never calls this operation."""
    validate_key(idempotency_key)
    if type(request) is not CollectReceiptV2:
        raise DomainError("special_v2_bank_command_required", 422)
    command = digest("v2-bank:" + idempotency_key)
    fingerprint = digest({"receipt_claim_id": request.receipt_claim_id, "movement_id": request.movement_id,
                          "confirmation": request.confirmation})

    async def apply(local, scoped, actor):
        from accounting_financial_identity import require_financial_ledger_identity
        from accounting_mz2_balances import read_mz2_write_balances
        from accounting_periods import assert_open_journal_periods
        doc = await _order(local, tenant_id, order_id)
        agreement = await _agreement(scoped, doc)
        for payment in doc["payments"]:
            await _event(scoped, local, doc, payment["movement_id"])
        prior = doc["command_log"].get(command)
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise DomainError("idempotency_payload_conflict")
            event = await _event(scoped, local, doc, prior["event_id"])
            return {"order_id": order_id, "revision": doc["revision"], "balances": balances(doc),
                    "txn_group_id": event["txn_group_id"], "replayed": True}
        if doc["revision"] != request.expected_revision:
            raise DomainError("revision_conflict")
        if doc["stage"] in {"cancelled", "refunded"}:
            raise DomainError("order_closed")
        claim = next((r for r in doc["receipt_claims"] if r["claim_id"] == request.receipt_claim_id and r["state"] == "pending"), None)
        if not claim:
            raise DomainError("pending_receipt_claim_required")
        evidence = Evidence.model_validate(claim["evidence"])
        await EvidenceStore(local).verify(tenant_id, evidence)
        account = await require_financial_ledger_identity(scoped, tenant_id, claim["bank_account_id"],
            account_types=("bank",), currency="SAR")
        amount = claim["amount_minor"]
        if type(amount) is not int or not 0 < amount <= balances(doc)["remaining_minor"]:
            raise DomainError("payment_exceeds_balance")
        movement = await scoped[NATIVE_MOVEMENTS].find_one({"user_id": tenant_id, "id": request.movement_id})
        if not movement or movement.get("bank_account_id") != account["id"] or movement.get("direction") != "in":
            raise DomainError("bank_transaction_does_not_match")
        if (movement.get("status") != "unclassified" or any(movement.get(k) for k in (
                "receipt_id", "accounting_event_id", "explicit_provider", "confirmed_provider", "suggested_provider"))):
            raise DomainError("bank_movement_already_allocated")
        if movement.get("currency") not in (None, "SAR"):
            raise DomainError("special_v2_native_movement_currency_mismatch")
        if exact_minor(movement.get("amount")) != amount:
            raise DomainError("bank_receipt_movement_mismatch")
        raw_date = movement.get("movement_date")
        try:
            day = date.fromisoformat(raw_date)
            when = datetime.combine(day, time.min, RIYADH).astimezone(timezone.utc)
            transfer = datetime.fromisoformat(claim["transferred_at"])
            if transfer.utcoffset() is None or transfer.astimezone(RIYADH).date() != day:
                raise ValueError()
        except (ValueError, TypeError):
            raise DomainError("special_v2_bank_date_mismatch") from None
        if when > datetime.now(timezone.utc):
            raise DomainError("future_bank_movement_not_allowed")
        if when < datetime.fromisoformat(agreement["verified_effective_at"].replace("Z", "+00:00")):
            raise DomainError("special_v2_receipt_before_receivable_requires_advance")
        reference = movement.get("reference")
        if type(reference) is not str or not reference.strip():
            raise DomainError("special_v2_bank_reference_required")
        reference_key = " ".join(reference.split()).casefold()
        if await local[BINDINGS].find_one({"tenant_id": tenant_id, "account_id": account["id"], "reference_key": reference_key}):
            raise DomainError("bank_movement_already_allocated")
        if await local[EVENTS].find_one({"tenant_id": tenant_id, "evidence.sha256": evidence.sha256}):
            raise DomainError("special_v2_receipt_already_allocated")
        import re
        if await scoped[NATIVE_MOVEMENTS].find_one({"user_id": tenant_id, "id": {"$ne": movement["id"]},
                "bank_account_id": account["id"], "status": "classified",
                "reference": {"$regex": "^" + re.escape(reference.strip()) + "$", "$options": "i"}}):
            raise DomainError("bank_movement_already_allocated")
        existing_receipt = await scoped.mz2_bank_transfer_receipts.find_one({"user_id": tenant_id,
            "status": {"$in": ["pending_approval", "confirmed_waiting_delivery", "recognized"]}, "$or": [
                {"receipt_file_sha256": evidence.sha256}, {"bank_movement_id": movement["id"]},
                {"bank_reference_key": reference.strip().upper(), "bank_account_id": account["id"]}]})
        if existing_receipt:
            raise DomainError("special_v2_receipt_used_by_native_order")
        native_balances = await read_mz2_write_balances(scoped, owner=tenant_id,
            required_accounts=[("bank", account["id"], "main")])
        receivable = agreement["receivable"]
        if native_balances.net_balance(**receivable) < Decimal(amount) / 100:
            raise DomainError("special_v2_receivable_balance_insufficient")
        await assert_open_journal_periods(scoped, tenant_id, [{"metadata": {"accounting_at": when.isoformat()}}])
        event_id = digest([tenant_id, order_id, "v2-bank-collection", idempotency_key])
        evidence_digest = digest({"movement": movement_facts(movement), "receipt": claim,
                                  "agreement": agreement, "command": fingerprint})
        plan = V2Event(owner=tenant_id, order_id=order_id, event_id=event_id, purpose=doc["purpose"],
            effective_at=when.isoformat(), evidence_digest=evidence_digest, policy_digest=agreement["policy_digest"],
            legs=(V2Leg("bank", "bank", account["id"], "debit", amount, "payment", "main"),
                  V2Leg("receivable", receivable["entity_type"], receivable["entity_id"], "credit", amount,
                        "payment", receivable["sub_account"])))
        journal = await post_in_owner_transaction(scoped, actor_id=actor.actor_id,
            required_permission="accounting.receivables.post", event=plan)
        payment = LedgerProof(tenant_id=tenant_id, order_id=order_id, movement_id=event_id,
            kind="bank_collection", currency="SAR", amount_minor=amount, amount_sar_minor=amount,
            account_id=account["id"], evidence_id=evidence.object_id, receipt_claim_id=claim["claim_id"])
        consume_financial_proof(doc, payment)
        changed = await scoped[NATIVE_MOVEMENTS].update_one({"_id": movement["_id"], "user_id": tenant_id,
            "status": "unclassified", "receipt_id": {"$in": [None, ""]}, "accounting_event_id": {"$in": [None, ""]}},
            {"$set": {"status": "classified", "accounting_event_id": event_id,
                      "special_order_id": order_id, "special_receipt_claim_id": claim["claim_id"]}})
        if changed.matched_count != 1:
            raise DomainError("bank_movement_already_allocated")
        await local[BINDINGS].insert_one({"tenant_id": tenant_id, "account_id": account["id"],
            "reference_key": reference_key, "transaction_id": movement["id"], "order_id": order_id,
            "event_id": event_id, "ledger_backend": "v2"})
        event = {"tenant_id": tenant_id, "order_id": order_id, "movement_id": event_id,
            "ledger_backend": "v2", "operation": "bank_collection", "txn_group_id": journal["group"]["txn_group_id"],
            "journal_metadata": journal["group"]["metadata"], "entries": sorted(plan.payload()["entries"], key=lambda r: r["leg_key"]),
            "native_movement": movement_facts(movement), "evidence": evidence.model_dump(mode="json"),
            "payment": payment.model_dump(mode="json")}
        event["integrity_digest"] = digest(event)
        await local[EVENTS].insert_one(event)
        doc["financial_backend"] = "v2"
        doc["revision"] += 1
        doc["command_log"][command] = {"fingerprint": fingerprint, "event_id": event_id, "revision": doc["revision"]}
        add_event(doc, "special_order.v2_bank_collection", actor.actor_id)
        bounded(doc)
        saved = await local[COLLECTION].replace_one({"tenant_id": tenant_id, "order_id": order_id,
            "revision": request.expected_revision, "snapshot_digest": doc["snapshot_digest"]}, doc)
        if saved.matched_count != 1:
            raise DomainError("revision_conflict")
        await _event(scoped, local, doc, event_id)
        return {"order_id": order_id, "revision": doc["revision"], "balances": balances(doc),
                "txn_group_id": journal["group"]["txn_group_id"], "replayed": False}

    return await execute_in_v2_owner(db, tenant_id=tenant_id, session_user=session_user,
        callback=apply, expected_epoch=request.expected_epoch)
