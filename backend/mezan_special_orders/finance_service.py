"""Special-order financial commands executed by the existing MZ2 ledger owner.

No posting is done during receipt upload, webhook ingestion, ordinary reads or
order creation. Each explicit financial command commits the source facts, journal,
audit and replay result in one real Mongo transaction. All activation defaults OFF.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import NAMESPACE_URL, uuid5

from .bank_adapter import bank_movement, verify_fx
from .binding import require_bound, transaction
from .contracts import CostProof, FxSnapshot, LedgerProof
from .domain import (DomainError, active_costs, add_event, balances, consume_financial_proof,
                     cost_report, digest)
from .evidence import EvidenceStore
from .finance_contracts import FINANCIAL_MODELS
from .ledger_adapter import (EVENTS, POLICIES, acquire_ledger_fence, balance_fx, counterparty_key,
    ensure_indexes, leg, minor, post_group, recovery_legs, require_cutover, save_event, verify_event)
from .repository import COLLECTION, bounded
from .service import public_view, require, validate_key
from .source_hooks import current_document


def financial_id(document, key):
    return str(uuid5(NAMESPACE_URL, f"special-finance:{document['tenant_id']}:{document['order_id']}:{key}"))


async def persist(db, document, expected):
    bounded(document)
    result = await db[COLLECTION].replace_one({"tenant_id": document["tenant_id"],
        "order_id": document["order_id"], "revision": expected}, document)
    if result.matched_count != 1:
        raise DomainError("revision_conflict")


async def resolve_party(db, tenant_id, kind, identity):
    if kind == "courier":
        from accounting_courier_bank_routes import external_courier_catalog_from_rows
        settings = await db.settings.find_one({"user_id": tenant_id}, {"_id": 0, "shipping_companies": 1})
        rows = external_courier_catalog_from_rows((settings or {}).get("shipping_companies"))
        row = next((r for r in rows if r["courier_key"] == identity and r["active"]), None)
        if not row:
            raise DomainError("configured_external_courier_required", 404)
        return row
    collections = {"supplier": "mezan_suppliers_v2", "store_driver": "store_drivers"}
    if kind not in collections:
        raise DomainError("financial_counterparty_kind_invalid")
    row = await db[collections[kind]].find_one({"user_id": tenant_id, "id": identity}, {"_id": 0})
    if not row or row.get("archived") is True or row.get("deleted") is True or row.get("is_active") is False or row.get("status") in {"inactive", "disabled", "hidden"}:
        raise DomainError("financial_counterparty_not_found", 404)
    return row


async def verify_document_finance(db, document):
    identifiers = {u["key"][4:] for u in document["financial_uses"] if u["key"].startswith("mz2:")}
    agreement = document.get("financial_agreement")
    if agreement:
        identifiers.add(agreement["movement_id"])
        identifiers.update(n["movement_id"] for n in document.get("financial_credit_notes", []))
    for identity in sorted(identifiers):
        await verify_event(db, document["tenant_id"], document["order_id"], identity)


async def ensure_agreement(db, document, actor_id):
    if document.get("financial_agreement") or not document["customer_agreed_minor"]:
        return
    policy_row = await db[POLICIES].find_one({"tenant_id": document["tenant_id"],
        "effective_at": {"$lte": document["created_at"]}}, {"_id": 0}, sort=[("effective_at", -1)])
    if not policy_row:
        raise DomainError("approved_contribution_accounting_policy_required")
    from .finance_contracts import AccountingPolicy
    policy = AccountingPolicy.model_validate(policy_row["policy"])
    await EvidenceStore(db).verify(document["tenant_id"], policy.evidence)
    gross = document["customer_agreed_sar_minor"]
    if gross <= 0:
        raise DomainError("agreed_contribution_rounds_to_zero")
    event_id = financial_id(document, "agreement-v1")
    credit = recovery_legs(policy.model_dump(mode="json"), document["policy"]["expense_bucket"], gross)
    entries = await post_group(db, document["tenant_id"], actor_id, order_id=document["order_id"], event_id=event_id,
        entries=[leg("special_order_receivable", document["order_id"], "debit", gross, "receivable"), *credit],
        metadata={"purpose": document["purpose"], "expense_bucket": document["policy"]["expense_bucket"],
            "contribution_policy_snapshot": policy_row, "original_fx": document["fx"],
            "recognition_kind": "agreement", "counts_as_sales_order": False})
    await save_event(db, document, event_id, operation="agreement", entries=entries)
    document["financial_agreement"] = {"movement_id": event_id, "policy": policy.model_dump(mode="json"),
        "gross_sar_minor": gross, "tax_sar_minor": sum(r["amount_minor"] for r in credit if r["entity_type"] == "tax")}


async def credit_agreement(db, document, actor_id, event_id, new_agreed_minor):
    agreement = document.get("financial_agreement")
    if not agreement:
        return None
    fx = FxSnapshot.model_validate(document["fx"])
    old_rights = document.get("financial_effective_agreed_minor", document["customer_agreed_minor"])
    if not 0 <= new_agreed_minor <= old_rights:
        raise DomainError("invalid_customer_credit")
    old_gross, new_gross = fx.to_sar_minor(old_rights), fx.to_sar_minor(new_agreed_minor)
    gross = old_gross - new_gross
    if not gross:
        document["financial_effective_agreed_minor"] = new_agreed_minor
        return None
    total_gross, total_tax = agreement["gross_sar_minor"], agreement["tax_sar_minor"]
    def tax_at(value):
        return int((Decimal(total_tax) * value / total_gross).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    tax = tax_at(old_gross) - tax_at(new_gross)
    net = gross - tax
    policy = agreement["policy"]
    entries = [leg("special_order_receivable", document["order_id"], "credit", gross, "receivable")]
    if net:
        entries.append(leg("expense" if policy["classification"] == "expense_recovery" else "other_income",
            counterparty_key(document["policy"]["expense_bucket"]), "debit", net))
    if tax:
        entries.append(leg("tax", "output_vat", "debit", tax, "payable"))
    identity = event_id + ":credit"
    facts = await post_group(db, document["tenant_id"], actor_id, order_id=document["order_id"],
        event_id=identity, entries=entries, metadata={"recognition_kind": "customer_credit", "purpose": document["purpose"]})
    await save_event(db, document, identity, operation="customer_credit", entries=facts)
    document.setdefault("financial_credit_notes", []).append({"movement_id": identity,
        "amount_minor": old_rights - new_agreed_minor, "amount_sar_minor": gross})
    document["financial_effective_agreed_minor"] = new_agreed_minor
    return identity


class FinancialService:
    def __init__(self, db):
        self.db = db

    async def execute(self, actor, order_id, expected_revision, key, operation, payload):
        require(actor, "special_orders.finance")
        require_bound(self.db, write=True, finance=True)
        validate_key(key)
        if type(expected_revision) is not int or expected_revision < 1:
            raise DomainError("invalid_expected_revision", 422)
        if operation not in FINANCIAL_MODELS:
            raise DomainError("unsupported_financial_command", 422)
        data = FINANCIAL_MODELS[operation].model_validate(payload)
        fingerprint = digest([operation, data.model_dump(mode="json")])
        await ensure_indexes(self.db)
        async def run(db):
            stored = await db[COLLECTION].find_one({"tenant_id": actor.tenant_id, "order_id": order_id}, {"_id": 0, "order_number": 1})
            if not stored:
                raise DomainError("order_not_found", 404)
            document, workflow = await current_document(db, actor.tenant_id, stored["order_number"], write=True, lock=True)
            command_id = digest("finance:" + key)
            prior = document["command_log"].get(command_id)
            if prior:
                if prior["fingerprint"] != fingerprint:
                    raise DomainError("idempotency_payload_conflict")
                await verify_document_finance(db, document)
                from .integration import integrated_view
                return integrated_view(document, actor)
            if document["revision"] != expected_revision:
                raise DomainError("revision_conflict")
            await require_cutover(db, actor.tenant_id)
            await acquire_ledger_fence(db, actor.tenant_id)
            await verify_document_finance(db, document)
            if operation not in {"cancel", "reverse_cost", "finalize_costs", "expense", "supplier_invoice", "remittance"} and document["stage"] in {"cancelled", "refunded"} and operation != "refund":
                raise DomainError("order_closed")
            event_id = financial_id(document, key)
            if operation in {"recognize_agreement", "bank_collection", "cod_collection", "refund"}:
                await ensure_agreement(db, document, actor.actor_id)
            if operation == "bank_collection":
                await self._bank_collection(db, document, actor.actor_id, event_id, data)
            elif operation == "cod_collection":
                await self._cod_collection(db, document, workflow, actor.actor_id, event_id, data)
            elif operation == "remittance":
                await self._remittance(db, document, actor.actor_id, event_id, data)
            elif operation == "refund":
                await self._refund(db, document, actor.actor_id, event_id, data)
            elif operation == "expense":
                await self._expense(db, document, actor.actor_id, event_id, data)
            elif operation == "reverse_cost":
                await self._reverse_cost(db, document, actor.actor_id, event_id, data)
            elif operation == "cancel":
                await self._cancel(db, document, workflow, actor.actor_id, event_id, data)
            elif operation == "supplier_invoice":
                from .cost_sources import recognize_supplier_invoice_for_order
                await recognize_supplier_invoice_for_order(db, document, actor.actor_id, data.invoice_id)
            elif operation == "finalize_costs":
                await self._finalize_costs(db, document, actor.actor_id, data)
            document["revision"] += 1
            document["command_log"][command_id] = {"fingerprint": fingerprint, "revision": document["revision"], "event_id": event_id}
            add_event(document, "special_order.finance." + operation, actor.actor_id)
            await persist(db, document, expected_revision)
            from .integration import integrated_view
            return integrated_view(document, actor)
        from pymongo.errors import DuplicateKeyError
        for attempt in range(4):
            try:
                return await transaction(self.db, run, tenant_id=actor.tenant_id, scopes=frozenset({"workflow", "financial", "evidence"}))
            except DuplicateKeyError:
                if attempt == 3:
                    raise DomainError("financial_unique_reference_conflict") from None
        raise AssertionError("unreachable")

    async def _bank_collection(self, db, doc, actor_id, event_id, data):
        claim = next((r for r in doc["receipt_claims"] if r["claim_id"] == data.receipt_claim_id and r["state"] == "pending"), None)
        if not claim:
            raise DomainError("pending_receipt_claim_required")
        movement = data.movement
        if (claim["bank_account_id"], claim["evidence"]["object_id"], claim["evidence"]["sha256"]) != (
                movement.bank_account_id, movement.evidence.object_id, movement.evidence.sha256):
            raise DomainError("bank_receipt_movement_mismatch")
        amount = claim["amount_minor"]
        bal = balances(doc)
        if amount > bal["remaining_minor"]:
            raise DomainError("payment_exceeds_balance")
        fx = FxSnapshot.model_validate(doc["fx"])
        carry = fx.to_sar_minor(bal["net_collected_minor"] + amount) - fx.to_sar_minor(bal["net_collected_minor"])
        actual = movement.cash_fx.to_sar_minor(amount)
        entries = balance_fx([leg("bank", movement.bank_account_id, "debit", actual, "main"),
            *([leg("special_order_receivable", doc["order_id"], "credit", carry, "receivable")] if carry else [])])
        await require_cutover(db, doc["tenant_id"], movement.occurred_at.isoformat())
        metadata, facts = await bank_movement(db, doc, movement, amount_minor=amount, direction="in", event_id=event_id, expected_entries=entries)
        if facts is None:
            facts = await post_group(db, doc["tenant_id"], actor_id, order_id=doc["order_id"], event_id=event_id, entries=entries, metadata=metadata)
        proof = LedgerProof(tenant_id=doc["tenant_id"], order_id=doc["order_id"], movement_id=event_id,
            kind="bank_collection", currency=doc["fx"]["currency"], amount_minor=amount, amount_sar_minor=actual,
            account_id=movement.bank_account_id, evidence_id=movement.evidence.object_id, receipt_claim_id=data.receipt_claim_id)
        consume_financial_proof(doc, proof)
        await save_event(db, doc, event_id, operation="bank_collection", entries=facts, proof=proof, extra={"carrying_sar_minor": carry})

    async def _cod_collection(self, db, doc, workflow, actor_id, event_id, data):
        await EvidenceStore(db).verify(doc["tenant_id"], data.evidence)
        await resolve_party(db, doc["tenant_id"], data.collector_kind, data.collector_id)
        if data.collector_kind == "store_driver":
            driver = await resolve_party(db, doc["tenant_id"], "store_driver", data.collector_id)
            if not workflow or workflow.get("store_courier_assignee_id") != driver.get("account_user_id"):
                raise DomainError("cod_collector_not_assigned_driver")
        elif doc["delivery"]["method"] != "carrier" or doc["delivery"]["carrier_key"] != data.collector_id:
            raise DomainError("cod_collector_not_assigned_carrier")
        bal = balances(doc)
        if data.amount_minor > bal["remaining_minor"]:
            raise DomainError("payment_exceeds_balance")
        fx = FxSnapshot.model_validate(doc["fx"])
        carry = fx.to_sar_minor(bal["net_collected_minor"] + data.amount_minor) - fx.to_sar_minor(bal["net_collected_minor"])
        if carry <= 0:
            raise DomainError("collection_rounds_to_zero")
        proof = LedgerProof(tenant_id=doc["tenant_id"], order_id=doc["order_id"], movement_id=event_id,
            kind="cod_collection", currency=fx.currency, amount_minor=data.amount_minor, amount_sar_minor=carry,
            account_id=data.collector_id, evidence_id=data.evidence.object_id)
        # Domain validation precedes all posting; it checks delivery stage and agreement.
        consume_financial_proof(doc, proof)
        facts = await post_group(db, doc["tenant_id"], actor_id, order_id=doc["order_id"], event_id=event_id,
            entries=[leg(data.collector_kind, data.collector_id, "debit", carry, "cod_receivable"),
                     leg("special_order_receivable", doc["order_id"], "credit", carry, "receivable")])
        await save_event(db, doc, event_id, operation="cod_collection", entries=facts, proof=proof,
            extra={"collector_kind": data.collector_kind, "collector_id": data.collector_id, "carrying_sar_minor": carry})

    async def _parent_custody(self, db, doc, identity, amount):
        parent = next((p for p in doc["payments"] if p["movement_id"] == identity and p["kind"] == "cod_collection"), None)
        if parent is None:
            raise DomainError("cod_collection_parent_required")
        source = await verify_event(db, doc["tenant_id"], doc["order_id"], identity)
        used = sum(p["amount_minor"] for p in doc["payments"] if p.get("parent_movement_id") == identity
            and (p["kind"] == "remittance" or p.get("refund_from") == "custody"))
        if used + amount > parent["amount_minor"]:
            raise DomainError("amount_exceeds_custody")
        def part(value):
            return int((Decimal(parent["amount_sar_minor"]) * value / parent["amount_minor"]).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return parent, source["extra"]["collector_kind"], part(used + amount) - part(used)

    async def _remittance(self, db, doc, actor_id, event_id, data):
        parent, collector_kind, carry = await self._parent_custody(db, doc, data.parent_movement_id, data.amount_minor)
        movement = data.movement
        actual = movement.cash_fx.to_sar_minor(data.amount_minor)
        entries = balance_fx([leg("bank", movement.bank_account_id, "debit", actual, "main"),
            *([leg(collector_kind, parent["account_id"], "credit", carry, "cod_receivable")] if carry else [])])
        await require_cutover(db, doc["tenant_id"], movement.occurred_at.isoformat())
        metadata, facts = await bank_movement(db, doc, movement, amount_minor=data.amount_minor, direction="in", event_id=event_id, expected_entries=entries)
        if facts is None:
            facts = await post_group(db, doc["tenant_id"], actor_id, order_id=doc["order_id"], event_id=event_id, entries=entries, metadata=metadata)
        proof = LedgerProof(tenant_id=doc["tenant_id"], order_id=doc["order_id"], movement_id=event_id,
            kind="remittance", currency=doc["fx"]["currency"], amount_minor=data.amount_minor, amount_sar_minor=actual,
            account_id=movement.bank_account_id, evidence_id=movement.evidence.object_id, parent_movement_id=data.parent_movement_id)
        consume_financial_proof(doc, proof)
        await save_event(db, doc, event_id, operation="remittance", entries=facts, proof=proof, extra={"carrying_sar_minor": carry})

    async def _refund(self, db, doc, actor_id, event_id, data):
        await EvidenceStore(db).verify(doc["tenant_id"], data.evidence)
        bal = balances(doc)
        if data.amount_minor > bal["net_collected_minor"]:
            raise DomainError("refund_exceeds_collection")
        credit = None
        if doc["stage"] not in {"cancelled", "refunded"}:
            credit = await credit_agreement(db, doc, actor_id, event_id, bal["effective_agreed_minor"] - data.amount_minor)
        fx = FxSnapshot.model_validate(doc["fx"])
        return_ar = fx.to_sar_minor(bal["net_collected_minor"]) - fx.to_sar_minor(bal["net_collected_minor"] - data.amount_minor)
        entries = [leg("special_order_receivable", doc["order_id"], "debit", return_ar, "receivable")] if return_ar else []
        metadata, facts = {}, None
        if data.refund_from == "custody":
            parent, kind, actual = await self._parent_custody(db, doc, data.parent_movement_id, data.amount_minor)
            account_id = parent["account_id"]
            if actual:
                entries.append(leg(kind, account_id, "credit", actual, "cod_receivable"))
        else:
            account_id = data.movement.bank_account_id
            actual = data.movement.cash_fx.to_sar_minor(data.amount_minor)
            entries.append(leg("bank", account_id, "credit", actual, "main"))
        entries = balance_fx(entries)
        if data.refund_from == "bank":
            await require_cutover(db, doc["tenant_id"], data.movement.occurred_at.isoformat())
            metadata, facts = await bank_movement(db, doc, data.movement, amount_minor=data.amount_minor, direction="out", event_id=event_id, expected_entries=entries)
        if actual <= 0:
            raise DomainError("refund_rounds_to_zero")
        proof = LedgerProof(tenant_id=doc["tenant_id"], order_id=doc["order_id"], movement_id=event_id,
            kind="refund", currency=fx.currency, amount_minor=data.amount_minor, amount_sar_minor=actual,
            account_id=account_id, evidence_id=data.evidence.object_id,
            parent_movement_id=data.parent_movement_id, refund_from=data.refund_from,
            credit_reference=credit or (event_id + ":zero-rounded-credit" if doc["stage"] not in {"cancelled", "refunded"} else None))
        consume_financial_proof(doc, proof)
        if facts is None:
            facts = await post_group(db, doc["tenant_id"], actor_id, order_id=doc["order_id"], event_id=event_id, entries=entries, metadata=metadata)
        await save_event(db, doc, event_id, operation="refund", entries=facts, proof=proof, extra={"carrying_sar_minor": return_ar, "reason": data.reason})

    async def _expense(self, db, doc, actor_id, event_id, data):
        await EvidenceStore(db).verify(doc["tenant_id"], data.evidence)
        await resolve_party(db, doc["tenant_id"], data.counterparty_kind, data.counterparty_id)
        amount = data.cost_sar_minor
        proof = CostProof(tenant_id=doc["tenant_id"], order_id=doc["order_id"], movement_id=event_id,
            kind=data.kind, target_key=data.target_key, cost_sar_minor=amount, counterparty_id=data.counterparty_id,
            origin="carrier_charge" if data.kind == "shipping" else "service_receipt",
            expense_bucket=doc["policy"]["expense_bucket"], evidence_id=data.evidence.object_id)
        consume_financial_proof(doc, proof)
        entries = [] if not amount else [leg("expense", counterparty_key(proof.expense_bucket), "debit", amount),
            leg(data.counterparty_kind, data.counterparty_id, "credit", amount,
                "delivery_fee_payable" if data.counterparty_kind == "store_driver" else "payable")]
        facts = await post_group(db, doc["tenant_id"], actor_id, order_id=doc["order_id"], event_id=event_id, entries=entries,
            metadata={"purpose": doc["purpose"], "expense_bucket": proof.expense_bucket, "cost_kind": data.kind})
        await save_event(db, doc, event_id, operation="expense", entries=facts, proof=proof,
            extra={"verified_zero_cost": amount == 0, "reason": data.reason, "new_payable": amount > 0})
        doc.pop("costs_finalized", None)

    async def _reverse_cost(self, db, doc, actor_id, event_id, data):
        await EvidenceStore(db).verify(doc["tenant_id"], data.evidence)
        original = next((c for c in active_costs(doc) if c["movement_id"] == data.movement_id), None)
        if not original:
            raise DomainError("active_cost_required")
        source = await verify_event(db, doc["tenant_id"], doc["order_id"], data.movement_id)
        if source["operation"] in {"supplier_invoice", "inventory_issue"}:
            # Reclassifying a purchase without an actual source reversal would
            # pretend the physical expense disappeared. Require its owner first.
            raise DomainError("source_inventory_or_supplier_reversal_required")
        proof = CostProof.model_validate({**original, "movement_id": event_id,
            "reverses_movement_id": data.movement_id, "evidence_id": data.evidence.object_id})
        consume_financial_proof(doc, proof)
        entries = [leg(r["entity_type"], r["entity_id"], "credit" if r["side"] == "debit" else "debit", r["amount_minor"], r["sub_account"]) for r in source["entries"]]
        facts = await post_group(db, doc["tenant_id"], actor_id, order_id=doc["order_id"], event_id=event_id, entries=entries,
            metadata={"reverses_special_event": data.movement_id, "purpose": doc["purpose"]})
        await save_event(db, doc, event_id, operation="reverse_cost", entries=facts, proof=proof,
            extra={"verified_zero_cost": not entries, "reason": data.reason})
        doc.pop("costs_finalized", None)

    async def _cancel(self, db, doc, workflow, actor_id, event_id, data):
        await EvidenceStore(db).verify(doc["tenant_id"], data.evidence)
        if doc["stage"] in {"delivering", "delivered", "refunded"} or (workflow or {}).get("carrier_handoff_at"):
            raise DomainError("dispatched_order_requires_return_not_cancellation")
        await credit_agreement(db, doc, actor_id, event_id, 0)
        now = datetime.now(timezone.utc).isoformat()
        doc["stage"] = "cancelled"
        doc["cancellation"] = {"reason": data.reason, "evidence_id": data.evidence.object_id, "actor_id": actor_id, "at": now}
        await db["order_review_workflows"].update_one({"user_id": doc["tenant_id"], "order_number": doc["order_number"]},
            {"$set": {"stage": "cancelled", "cancelled_at": now, "cancelled_by": actor_id,
                "carrier_label_ready": False, "source_provider": "mezan", "updated_at": now}, "$inc": {"revision": 1}})
        from preparation_piece_operations import PIECES, PIECE_STATUS_CANCELLED
        await db[PIECES].update_many({"user_id": doc["tenant_id"], "order_number": doc["order_number"],
            "status": {"$ne": PIECE_STATUS_CANCELLED}}, [{"$set": {
                "cancelled_from_status": "$status", "status": PIECE_STATUS_CANCELLED,
                "cancelled_at": now, "cancelled_by": actor_id,
                "inventory_disposition": "requires_physical_reconciliation", "source_order_closed": True}}])
        # Existing inventory owner releases only still-active reservations;
        # received/consumed stock is not magically restored on cancellation.
        from fulfillment_v2_routes import _release_order_inventory_reservations
        await _release_order_inventory_reservations(db, user_id=doc["tenant_id"], order_number=doc["order_number"], reason="special_order_cancelled")
        await db["order_review_events"].insert_one({"user_id": doc["tenant_id"], "order_number": doc["order_number"],
            "event_type": "special_order_cancelled", "actor_id": actor_id, "occurred_at": now, "evidence_id": data.evidence.object_id})

    async def _finalize_costs(self, db, doc, actor_id, data):
        await EvidenceStore(db).verify(doc["tenant_id"], data.evidence)
        report = cost_report(doc)
        if not report["costs_complete"]:
            raise DomainError("costs_not_complete")
        unfinished = await db["mezan_preparation_pieces_v1"].find_one({"user_id": doc["tenant_id"],
            "order_number": doc["order_number"], "remaining_service_count": {"$gt": 0}}, {"_id": 1})
        if unfinished:
            raise DomainError("required_services_not_complete")
        doc["costs_finalized"] = {"actor_id": actor_id, "evidence_id": data.evidence.object_id,
            "at": datetime.now(timezone.utc).isoformat(), "reason": data.reason,
            "cost_digest": digest(doc["costs"])}
