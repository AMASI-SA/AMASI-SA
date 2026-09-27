"""Pure domain functions; no database, provider requests, or ledger posting."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from .contracts import (Actor, CostProof, CreateOrder, FxSnapshot, LedgerProof,
                        OptionValue, OriginalOrder, Product, ReceiptClaim, WorkflowProof)

BADGES = {"replacement": "بدل", "gift": "هدية", "creator": "تصوير", "marketing": "تسويق"}
DISPATCH_STAGES = {"ready_to_ship", "completed", "delivering", "delivered"}
CLOSED_STAGES = {"cancelled", "refunded"}


class DomainError(ValueError):
    def __init__(self, code: str, http_status: int = 409):
        self.code = code
        self.http_status = http_status
        super().__init__(code)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def policy(purpose: str, bucket: str) -> dict:
    if purpose not in BADGES:
        raise DomainError("unsupported_purpose", 422)
    return {"counts_in_sales_orders": False, "counts_in_marketing_orders": False,
            "counts_in_aov": False, "counts_in_roas_revenue": False,
            "expense_bucket": bucket, "included_in_total_marketing_cost": bucket == "marketing",
            "inventory_effect": True, "accounting_owner": "mezan_v2",
            "send_to_salla": False, "automatic_qoyod_sale": False}


def validated_options(product: Product, values: tuple[OptionValue, ...]) -> list[dict]:
    rules = {rule.key: rule for rule in product.option_rules}
    selected = {v.key: v.value for v in values}
    if len(selected) != len(values):
        raise DomainError("duplicate_option", 422)
    if set(selected) - set(rules):
        raise DomainError("unknown_option", 422)
    for fixed in product.variant_selections:
        if selected.get(fixed.key) != fixed.value:
            raise DomainError("selected_options_do_not_match_variant", 422)
    out = []
    for rule in product.option_rules:  # Preserve source display order for printing.
        value = selected.get(rule.key)
        if value is None:
            if rule.required:
                raise DomainError("required_option_missing", 422)
            continue
        values = list(value) if isinstance(value, tuple) else [value]
        if rule.selection_mode == "single" and len(values) != 1:
            raise DomainError("multiple_values_for_single_option", 422)
        if len(values) != len(set(values)):
            raise DomainError("duplicate_selected_option_value", 422)
        if any(len(v) > rule.max_length or (rule.choices and v not in rule.choices) for v in values):
            raise DomainError("invalid_option_value", 422)
        selected_value = values if rule.selection_mode == "multi" else values[0]
        row = {"key": rule.key, "name": rule.label, "value": selected_value}
        if rule.source_option_id:
            row["option_id"] = rule.source_option_id
        choice_ids = dict(rule.choice_ids)
        if rule.selection_mode == "single" and values[0] in choice_ids:
            row["value_id"] = choice_ids[values[0]]
        elif rule.selection_mode == "multi":
            row["values"] = [{"name": v, "id": choice_ids[v]} if v in choice_ids else {"name": v} for v in values]
        out.append(row)
    return out


def source_snapshot(order: dict) -> dict:
    return {key: deepcopy(order[key]) for key in ("order_id", "order_number", "purpose",
            "original", "recipient", "delivery", "items", "services", "collection", "fx")}


def creation(actor: Actor, request: CreateOrder, idempotency_key: str,
             original: OriginalOrder | None, products: dict[tuple[str, str | None], Product]) -> dict:
    fingerprint = digest(request.model_dump(mode="json"))
    identity = str(uuid5(NAMESPACE_URL, json.dumps(["mezan-special-v1", actor.tenant_id, idempotency_key])))
    order_number = "MZ-" + identity.replace("-", "").upper()
    if request.purpose == "replacement":
        if original is None or original.tenant_id != actor.tenant_id:
            raise DomainError("original_order_not_found", 404)
        if original.order_number != request.original_order_number:
            raise DomainError("original_order_identity_mismatch")
    recipient = request.recipient or (original.recipient if original else None)
    if recipient is None:
        raise DomainError("recipient_required", 422)
    if request.delivery.method == "courier" and recipient.address is None:
        raise DomainError("courier_address_required", 422)
    if original and request.delivery.method == "carrier":
        if request.delivery.label.object_id == original.label_object_id or request.delivery.tracking_number == original.tracking_number:
            raise DomainError("original_shipping_label_must_not_be_reused", 422)
    original_items = {item.item_id: item for item in original.items} if original else {}
    items, allocations = [], []
    for selection in request.items:
        source_line = original_items.get(selection.original_item_id)
        if request.purpose == "replacement" and source_line is None:
            raise DomainError("original_item_not_found", 404)
        if source_line and selection.quantity > source_line.quantity:
            raise DomainError("replacement_quantity_exceeds_original", 422)
        explicit = selection.product_id is not None
        product = products.get((selection.product_id, selection.variant_id)) if explicit else (source_line.product if source_line else None)
        if product is None or product.tenant_id != actor.tenant_id:
            raise DomainError("product_not_found", 404)
        if explicit and (product.product_id != selection.product_id or product.variant_id != selection.variant_id):
            raise DomainError("catalog_identity_mismatch")
        substituted = bool(source_line and (product.product_id, product.variant_id) != (source_line.product.product_id, source_line.product.variant_id))
        if substituted and (not selection.substitution_reason or selection.options is None):
            raise DomainError("substitution_reason_and_options_required", 422)
        if not explicit and selection.variant_id is not None:
            raise DomainError("variant_requires_product", 422)
        values = selection.options
        if values is None:
            values = source_line.options if source_line and not substituted else ()
        options = validated_options(product, values)
        item_id = str(uuid5(NAMESPACE_URL, identity + ":" + selection.line_key))
        item = {"order_item_id": item_id, "line_key": selection.line_key,
                "original_order_item_id": selection.original_item_id, "product": product.model_dump(mode="json"),
                "quantity": selection.quantity, "options": options,
                "original_options": [v.model_dump(mode="json") for v in source_line.options] if source_line else [],
                "customer_charge_minor": selection.customer_charge_minor,
                "substitution_reason": selection.substitution_reason}
        items.append(item)
        allocations.append({"key": "product:" + selection.line_key, "kind": "product",
                            "target_key": selection.line_key, "amount_minor": selection.customer_charge_minor})
    allocations.append({"key": "shipping", "kind": "shipping", "target_key": "shipping",
                        "amount_minor": request.delivery.customer_charge_minor})
    allocations.extend({"key": "service:" + s.key, "kind": "service", "target_key": s.key,
                        "amount_minor": s.customer_charge_minor} for s in request.services)
    total = sum(a["amount_minor"] for a in allocations)
    # Cumulative rounding reconciles every allocation to the converted order total.
    cumulative = prior_sar = 0
    for allocation in allocations:
        cumulative += allocation["amount_minor"]
        current_sar = request.fx.to_sar_minor(cumulative)
        allocation["amount_sar_minor"] = current_sar - prior_sar
        prior_sar = current_sar
    order = {"schema_version": 1, "tenant_id": actor.tenant_id, "order_id": identity,
             "order_number": order_number, "provider": "mezan", "purpose": request.purpose,
             "reason": request.reason, "badge": BADGES[request.purpose],
             "original": ({"order_id": original.order_id, "order_number": original.order_number,
                           "source": original.source, "source_revision": original.source_revision} if original else None),
             "recipient": recipient.model_dump(mode="json"), "delivery": request.delivery.model_dump(mode="json"),
             "items": items, "services": [s.model_dump(mode="json") for s in request.services],
             "collection": request.collection.model_dump(mode="json"),
             "fx": request.fx.model_dump(mode="json"), "allocations": allocations,
             "customer_agreed_minor": total, "customer_agreed_sar_minor": prior_sar,
             "cost_center_id": request.cost_center_id, "campaign_cost_id": request.campaign_id,
             "policy": policy(request.purpose, request.expense_bucket),
             "stage": "pending_review", "source_revision": 1, "revision": 1,
             "source_frozen": False, "workflow_revision": 0, "workflow_event_ids": [],
             "receipt_claims": [], "financial_uses": [], "payments": [], "costs": [],
             "created_at": now_iso(), "created_by": actor.actor_id,
             "create_fingerprint": fingerprint, "command_log": {}, "outbox": []}
    order["snapshot_digest"] = digest(source_snapshot(order))
    add_event(order, "special_order.created", actor.actor_id)
    return order


def add_event(order: dict, topic: str, actor_id: str) -> str:
    event_id = digest([order["order_id"], order["revision"], len(order["outbox"]), topic])
    order["outbox"].append({"event_id": event_id, "topic": topic, "source_revision": order["source_revision"],
                            "revision": order["revision"], "actor_id": actor_id,
                            "occurred_at": now_iso(), "state": "pending"})
    return event_id


def balances(order: dict) -> dict:
    payments = order["payments"]
    collections = [p for p in payments if p["kind"] in {"bank_collection", "cod_collection"}]
    collected = sum(p["amount_minor"] for p in collections)
    refunds = sum(p["amount_minor"] for p in payments if p["kind"] == "refund")
    credited = sum(p["amount_minor"] for p in payments if p["kind"] == "refund" and p.get("credit_reference"))
    closed = order["stage"] in CLOSED_STAGES
    agreed = 0 if closed else max(order["customer_agreed_minor"] - credited, 0)
    net = collected - refunds
    remaining = max(agreed - net, 0)
    custody = sum(p["amount_minor"] for p in collections if p["kind"] == "cod_collection")
    custody -= sum(p["amount_minor"] for p in payments if p["kind"] == "remittance" or (p["kind"] == "refund" and p.get("refund_from") == "custody"))
    bank = sum(p["amount_minor"] for p in collections if p["kind"] == "bank_collection")
    return {"agreed_minor": order["customer_agreed_minor"], "effective_agreed_minor": agreed,
            "collected_minor": collected, "refunded_minor": refunds, "net_collected_minor": net,
            "bank_collected_minor": bank, "remaining_minor": remaining,
            "cod_to_collect_minor": remaining if order["collection"]["cod_minor"] > 0 and not closed else 0,
            "custody_minor": custody, "refund_due_minor": max(net - agreed, 0),
            "pending_receipt_minor": sum(c["amount_minor"] for c in order["receipt_claims"] if c["state"] == "pending")}


def dispatch_blockers(order: dict) -> list[str]:
    balance = balances(order)
    blockers = []
    if order["stage"] in CLOSED_STAGES:
        blockers.append("order_closed")
    if balance["bank_collected_minor"] < order["collection"]["bank_transfer_minor"]:
        blockers.append("required_bank_payment_unconfirmed")
    if balance["remaining_minor"] and not order["collection"]["cod_minor"]:
        blockers.append("balance_without_cod_agreement")
    return blockers


def add_receipt(order: dict, claim: ReceiptClaim) -> str:
    if order["stage"] in CLOSED_STAGES:
        raise DomainError("order_closed")
    if claim.amount_minor > balances(order)["remaining_minor"]:
        raise DomainError("receipt_amount_exceeds_balance")
    claim_id = digest([order["order_id"], claim.evidence.object_id])
    data = claim.model_dump(mode="json")
    for old in order["receipt_claims"]:
        if old["claim_id"] == claim_id:
            if old["claim_digest"] != digest(data):
                raise DomainError("receipt_claim_conflict")
            return claim_id
    if claim.amount_minor + balances(order)["pending_receipt_minor"] > balances(order)["remaining_minor"]:
        raise DomainError("pending_receipts_exceed_balance")
    order["receipt_claims"].append({**data, "claim_id": claim_id, "claim_digest": digest(data),
                                     "state": "pending"})
    return claim_id


def consume_financial_proof(order: dict, proof: LedgerProof | CostProof) -> None:
    if proof.tenant_id != order["tenant_id"] or proof.order_id != order["order_id"]:
        raise DomainError("financial_proof_scope_mismatch", 403)
    use_key = "mz2:" + proof.movement_id
    if any(u["key"] == use_key for u in order["financial_uses"]):
        raise DomainError("financial_movement_already_used")
    data = proof.model_dump(mode="json")
    if isinstance(proof, CostProof):
        if proof.expense_bucket != order["policy"]["expense_bucket"]:
            raise DomainError("expense_bucket_mismatch")
        allowed = {(a["kind"], a["target_key"]) for a in order["allocations"]}
        if (proof.kind, proof.target_key) not in allowed:
            raise DomainError("unknown_cost_target")
        allowed_origins = {"product": {"inventory_issue", "supplier_receipt"}, "shipping": {"carrier_charge"}, "service": {"service_receipt"}}
        if proof.origin not in allowed_origins[proof.kind]:
            raise DomainError("cost_origin_mismatch")
        if proof.kind == "product":
            item = next(i for i in order["items"] if i["line_key"] == proof.target_key)
            if not proof.unit_indices or len(set(proof.unit_indices)) != len(proof.unit_indices) or max(proof.unit_indices) > item["quantity"]:
                raise DomainError("cost_units_must_match_order_line")
        elif proof.unit_indices:
            raise DomainError("units_only_for_product_cost")
        active = active_costs(order)
        if proof.reverses_movement_id:
            original = next((c for c in active if c["movement_id"] == proof.reverses_movement_id), None)
            fields = ("kind", "target_key", "cost_sar_minor", "counterparty_id", "origin", "expense_bucket", "unit_indices")
            if original is None or any(original.get(k) != data.get(k) for k in fields):
                raise DomainError("cost_reversal_must_match_active_original")
        else:
            for old in active:
                if (old["kind"], old["target_key"]) != (proof.kind, proof.target_key):
                    continue
                if proof.kind != "product" or set(old["unit_indices"]) & set(proof.unit_indices):
                    raise DomainError("physical_cost_already_recognized")
        order["costs"].append(data)
    else:
        if proof.currency != order["fx"]["currency"]:
            raise DomainError("payment_currency_mismatch")
        if proof.kind in {"bank_collection", "cod_collection"}:
            if order["stage"] in CLOSED_STAGES:
                raise DomainError("collection_on_closed_order")
            if proof.amount_minor > balances(order)["remaining_minor"]:
                raise DomainError("payment_exceeds_balance")
            if proof.parent_movement_id or proof.refund_from or proof.credit_reference:
                raise DomainError("invalid_collection_fields")
            if proof.kind == "bank_collection":
                claim = next((c for c in order["receipt_claims"] if c["claim_id"] == proof.receipt_claim_id), None)
                if not claim or claim["state"] != "pending":
                    raise DomainError("pending_receipt_claim_required")
                if (claim["amount_minor"], claim["bank_account_id"], claim["evidence"]["object_id"]) != (proof.amount_minor, proof.account_id, proof.evidence_id):
                    raise DomainError("bank_receipt_movement_mismatch")
                claim["state"] = "confirmed"
                claim["movement_id"] = proof.movement_id
            elif order["stage"] not in {"delivering", "delivered"} or not order["collection"]["cod_minor"]:
                raise DomainError("cod_requires_delivery_and_agreement")
        else:
            parent = next((p for p in order["payments"] if p["movement_id"] == proof.parent_movement_id and p["kind"] in {"bank_collection", "cod_collection"}), None)
            if parent is None:
                raise DomainError("collection_parent_required")
            children = [p for p in order["payments"] if p.get("parent_movement_id") == parent["movement_id"]]
            if proof.kind == "remittance":
                used = sum(p["amount_minor"] for p in children if p["kind"] == "remittance" or p.get("refund_from") == "custody")
                if parent["kind"] != "cod_collection" or proof.amount_minor + used > parent["amount_minor"]:
                    raise DomainError("remittance_exceeds_custody")
                if proof.refund_from or proof.credit_reference or proof.receipt_claim_id:
                    raise DomainError("invalid_remittance_fields")
            elif proof.kind == "refund":
                refunded = sum(p["amount_minor"] for p in children if p["kind"] == "refund")
                if proof.amount_minor + refunded > parent["amount_minor"]:
                    raise DomainError("refund_exceeds_collection")
                if order["stage"] not in CLOSED_STAGES and not proof.credit_reference:
                    raise DomainError("credit_reference_required")
                if proof.refund_from not in {"bank", "custody"}:
                    raise DomainError("refund_source_required")
                if proof.refund_from == "custody":
                    used = sum(p["amount_minor"] for p in children if p["kind"] == "remittance" or p.get("refund_from") == "custody")
                    if parent["kind"] != "cod_collection" or used + proof.amount_minor > parent["amount_minor"]:
                        raise DomainError("refund_exceeds_custody")
        order["payments"].append(data)
    order["financial_uses"].append({"key": use_key})


def amend_options(order: dict, line_key: str, values: tuple[OptionValue, ...]) -> None:
    if order["stage"] != "pending_review" or order["source_frozen"]:
        raise DomainError("options_frozen_for_fulfillment")
    item = next((i for i in order["items"] if i["line_key"] == line_key), None)
    if item is None:
        raise DomainError("order_item_not_found", 404)
    item["options"] = validated_options(Product.model_validate(item["product"]), values)
    order["source_revision"] += 1
    order["snapshot_digest"] = digest(source_snapshot(order))


def observe_workflow(order: dict, proof: WorkflowProof) -> None:
    # The shared workflow validates transitions; this package never invents a second state machine.
    if proof.tenant_id != order["tenant_id"] or proof.order_id != order["order_id"]:
        raise DomainError("workflow_scope_mismatch", 403)
    if proof.source_revision != order["source_revision"] or proof.snapshot_digest != order["snapshot_digest"]:
        raise DomainError("workflow_snapshot_stale")
    if proof.workflow_revision <= order["workflow_revision"] or proof.event_id in order["workflow_event_ids"]:
        raise DomainError("workflow_observation_stale")
    if not order["source_frozen"] and proof.stage not in {"pending_review", "cancelled"}:
        raise DomainError("source_freeze_required")
    if proof.stage in DISPATCH_STAGES and dispatch_blockers(order):
        raise DomainError("dispatch_financial_guard")
    order["stage"] = proof.stage
    order["workflow_revision"] = proof.workflow_revision
    order["workflow_event_ids"].append(proof.event_id)


def active_costs(order: dict) -> list[dict]:
    reversed_ids = {c["reverses_movement_id"] for c in order["costs"] if c.get("reverses_movement_id")}
    return [c for c in order["costs"] if not c.get("reverses_movement_id") and c["movement_id"] not in reversed_ids]


def distribute_minor(total: int, weights: list[int]) -> list[int]:
    """Largest-remainder allocation; stable source order breaks ties, no float."""
    if type(total) is not int or total < 0 or any(type(w) is not int or w < 0 for w in weights):
        raise DomainError("invalid_allocation_amount")
    denominator = sum(weights)
    if not denominator:
        if total:
            raise DomainError("cannot_allocate_without_charges")
        return [0] * len(weights)
    values = [total * w // denominator for w in weights]
    remainders = [total * w % denominator for w in weights]
    for index in sorted(range(len(weights)), key=lambda i: (-remainders[i], i))[:total - sum(values)]:
        values[index] += 1
    return values


def cost_report(order: dict) -> dict:
    active = active_costs(order)
    actual = sum(c["cost_sar_minor"] for c in active)
    net_sar = sum(p["amount_sar_minor"] for p in order["payments"] if p["kind"] in {"bank_collection", "cod_collection"})
    net_sar -= sum(p["amount_sar_minor"] for p in order["payments"] if p["kind"] == "refund")
    weights = [a["amount_minor"] for a in order["allocations"]]
    # A currency loss can make net SAR negative; distribute its magnitude and restore the sign.
    native_shares = distribute_minor(balances(order)["net_collected_minor"], weights)
    sar_shares = distribute_minor(abs(net_sar), weights)
    if net_sar < 0:
        sar_shares = [-v for v in sar_shares]
    pending, lines = [], []
    for index, allocation in enumerate(order["allocations"]):
        matching = [c for c in active if (c["kind"], c["target_key"]) == (allocation["kind"], allocation["target_key"])]
        complete = bool(matching)
        if allocation["kind"] == "product":
            item = next(i for i in order["items"] if i["line_key"] == allocation["target_key"])
            units = {u for c in matching for u in c["unit_indices"]}
            complete = units == set(range(1, item["quantity"] + 1))
        if not complete:
            pending.append(allocation["key"])
        cost = sum(c["cost_sar_minor"] for c in matching)
        lines.append({**deepcopy(allocation), "collected_minor": native_shares[index],
                      "collected_sar_minor": sar_shares[index], "known_cost_sar_minor": cost,
                      "cost_complete": complete,
                      "final_store_burden_sar_minor": cost - sar_shares[index] if complete else None})
    return {"currency": "SAR", "recognized_cost_minor": actual,
            "agreed_contribution_minor": order["customer_agreed_sar_minor"],
            "net_collected_minor": net_sar, "net_store_burden_minor": actual - net_sar,
            "final_net_store_burden_minor": actual - net_sar if not pending else None,
            "provisional": bool(pending), "allocation_method": "agreed-charge-proportional-largest-remainder",
            "allocations": lines, "unrecognized_cost_targets": pending, "costs_complete": not pending,
            "expense_bucket": order["policy"]["expense_bucket"],
            "by_kind": {k: sum(c["cost_sar_minor"] for c in active if c["kind"] == k) for k in ("product", "shipping", "service")}}


def operational_projection(order: dict) -> dict:
    """Integration contract for the existing Order Engine; not a Salla-shaped fake."""
    return {"schema_version": "mezan.special-order.operational.v1", "order_id": order["order_id"],
            "order_number": order["order_number"], "source": {"provider": "mezan", "source_order_id": None},
            "original": deepcopy(order["original"]), "purpose": order["purpose"], "badge": order["badge"],
            "stage": order["stage"], "source_revision": order["source_revision"],
            "snapshot_digest": order["snapshot_digest"], "recipient": deepcopy(order["recipient"]),
            "delivery": deepcopy(order["delivery"]),
            "items": [{"order_item_id": i["order_item_id"], "product_id": i["product"]["product_id"],
                       "variant_id": i["product"]["variant_id"], "sku": i["product"]["sku"],
                       "name": i["product"]["name"], "quantity": i["quantity"],
                       "options": deepcopy(i["options"]), "badge": order["badge"]} for i in order["items"]],
            "balances": balances(order), "policy": deepcopy(order["policy"])}
