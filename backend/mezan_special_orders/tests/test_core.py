"""Synthetic-data tests. Shared-workflow/MZ2 ports below are explicit test doubles."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from mezan_special_orders.contracts import (
    Actor, CostProof, CreateOrder, FxSnapshot, LedgerProof, OptionRule, OptionValue,
    OriginalLine, OriginalOrder, Product, Recipient, Address, SettlementAdjustment,
    WorkflowProof,
)
from mezan_special_orders.domain import DomainError, balances, cost_report, digest, dispatch_blockers
from mezan_special_orders.repository import MemoryStore
from mezan_special_orders.service import Gates, SpecialOrderService, UnboundPorts
from mezan_special_orders.sidecar import collection_overlay


def run(coro):
    return asyncio.run(coro)


PERMISSIONS = frozenset("special_orders." + p for p in ("create", "read", "edit", "review", "finance", "reconcile", "integrate"))
OWNER = Actor(tenant_id="test-store", actor_id="test-owner", permissions=PERMISSIONS)
NOW = "2026-09-27T12:00:00+00:00"


class TestPorts:
    __test__ = False

    def __init__(self):
        self.original_calls = self.product_calls = 0
        self.published = []
        self.payments, self.costs, self.proofs = {}, {}, {}
        self.evidence_ok = self.fx_ok = True
        self.product_value = Product(tenant_id="test-store", product_id="p-demo", sku="TEST-01", name="منتج اختباري",
            catalog_revision="catalog-1", requires_options=True,
            option_rules=(OptionRule(key="size", label="المقاس", choices=("56", "58")), OptionRule(key="name", label="الاسم", max_length=50)))
        self.recipient = Recipient(name="مستلم اختباري", mobile="0500000000", address=Address(city="مدينة اختبار", district="حي اختبار", formatted="عنوان اصطناعي للاختبارات فقط"))
        self.original_value = OriginalOrder(tenant_id="test-store", order_id="original-demo", order_number="TEST-ORIGINAL",
            source_revision="source-1", recipient=self.recipient,
            label_object_id="old-label", tracking_number="OLD-TRACKING",
            items=(OriginalLine(item_id="original-line", product=self.product_value, quantity=2,
                options=(OptionValue(key="size", value="56"), OptionValue(key="name", value="تجربة"))),))

    async def original(self, tenant_id, number):
        await asyncio.sleep(0)
        self.original_calls += 1
        return self.original_value

    async def product(self, tenant_id, product_id, variant_id):
        await asyncio.sleep(0)
        self.product_calls += 1
        return self.product_value

    async def evidence(self, tenant_id, evidence, **kwargs):
        return self.evidence_ok

    async def fx(self, tenant_id, snapshot):
        return self.fx_ok

    async def payment(self, tenant_id, order_id, movement_id):
        return self.payments[movement_id]

    async def cost(self, tenant_id, order_id, movement_id):
        return self.costs[movement_id]

    async def workflow(self, tenant_id, order_id, event_id):
        return self.proofs[event_id]

    async def publish(self, tenant_id, event_id, event, order):
        self.published.append((tenant_id, event_id, deepcopy(order)))


def request_data(purpose="replacement", partial=False):
    data = {"purpose": purpose, "expense_bucket": "compensation" if purpose == "replacement" else "marketing",
        "reason": "بيانات اختبار فقط", "delivery": {"method": "courier", "customer_charge_minor": 2000 if partial else 0},
        "items": [{"line_key": "line-1", "quantity": 1, "customer_charge_minor": 4000 if partial else 0}],
        "fx": {"currency": "SAR", "rate_to_sar": "1", "captured_at": NOW, "evidence_id": "fx-demo"},
        "collection": {"bank_transfer_minor": 4000 if partial else 0, "cod_minor": 2000 if partial else 0}}
    if purpose == "replacement":
        data["original_order_number"] = "TEST-ORIGINAL"
        data["items"][0]["original_item_id"] = "original-line"
    else:
        data["recipient"] = TestPorts().recipient.model_dump(mode="json")
        data["items"][0].update(product_id="p-demo", options=[{"key": "size", "value": "58"}, {"key": "name", "value": "هدية"}])
    return data


class Harness:
    def __init__(self):
        self.store = MemoryStore()
        self.ports = TestPorts()
        self.service = SpecialOrderService(self.store, self.ports, Gates(True, True, True))
        self.count = 0

    async def create(self, purpose="replacement", partial=False, key="create-demo-001", data=None):
        return await self.service.create(OWNER, CreateOrder.model_validate(data or request_data(purpose, partial)), key)

    async def doc(self, order):
        return await self.store.get(OWNER.tenant_id, order["order_id"])

    async def command(self, order, operation, payload=None, key=None):
        self.count += 1
        return await self.service.command(OWNER, order["order_id"], order["revision"], key or f"command-{self.count:04d}", operation, payload or {})

    async def receipt(self, order, amount=4000, object_id="receipt-demo"):
        return await self.command(order, "attach_receipt", {"evidence": {"object_id": object_id, "kind": "bank_receipt", "sha256": "a" * 64}, "bank_account_id": "bank-demo", "amount_minor": amount, "transferred_at": NOW})

    async def bank(self, order, movement="bank-movement-demo"):
        doc = await self.doc(order)
        claim = next(c for c in doc["receipt_claims"] if c["state"] == "pending")
        proof = LedgerProof(tenant_id=OWNER.tenant_id, order_id=order["order_id"], movement_id=movement,
            kind="bank_collection", currency="SAR", amount_minor=claim["amount_minor"], amount_sar_minor=claim["amount_minor"],
            account_id=claim["bank_account_id"], evidence_id=claim["evidence"]["object_id"], receipt_claim_id=claim["claim_id"])
        self.ports.payments[movement] = proof
        return await self.command(order, "observe_payment", {"movement_id": movement})

    async def stage(self, order, stage):
        doc = await self.doc(order)
        event = f"workflow-{doc['workflow_revision'] + 1}"
        self.ports.proofs[event] = WorkflowProof(tenant_id=OWNER.tenant_id, order_id=order["order_id"],
            source_revision=doc["source_revision"], workflow_revision=doc["workflow_revision"] + 1,
            snapshot_digest=doc["snapshot_digest"], stage=stage, event_id=event)
        return await self.command(order, "observe_workflow", {"event_id": event})

    async def finance(self, order, kind, amount, movement, parent=None, refund_from=None, credit=None):
        self.ports.payments[movement] = LedgerProof(tenant_id=OWNER.tenant_id, order_id=order["order_id"],
            movement_id=movement, kind=kind, amount_minor=amount, amount_sar_minor=amount, currency="SAR",
            account_id="driver-demo" if kind == "cod_collection" else "bank-demo", evidence_id="proof-demo",
            parent_movement_id=parent, refund_from=refund_from, credit_reference=credit)
        return await self.command(order, "observe_payment", {"movement_id": movement})


@pytest.mark.parametrize("purpose", ["replacement", "gift", "creator", "marketing"])
def test_all_purposes_start_pending_review_and_never_count_as_sales(purpose):
    async def scenario():
        h = Harness()
        out = await h.create(purpose)
        doc = await h.doc(out)
        assert out["stage"] == "pending_review" and out["source"]["provider"] == "mezan"
        assert out["source"]["source_order_id"] is None
        assert all(not out["policy"][key] for key in ("counts_in_sales_orders", "counts_in_marketing_orders", "counts_in_aov", "counts_in_roas_revenue", "send_to_salla", "automatic_qoyod_sale"))
        assert not doc["payments"] and not doc["costs"] and len(doc["outbox"]) == 1
        assert out["cost_report"]["costs_complete"] is False
        assert out["policy"]["included_in_total_marketing_cost"] == (purpose != "replacement")
    run(scenario())


def test_original_is_not_modified_and_print_projection_uses_amended_options():
    async def scenario():
        h = Harness()
        before = h.ports.original_value.model_dump(mode="json")
        o = await h.create()
        assert o["items"][0]["options"][0]["value"] == "56"
        o = await h.command(o, "amend_options", {"line_key": "line-1", "options": [{"key": "size", "value": "58"}, {"key": "name", "value": "جديد"}]})
        assert o["items"][0]["options"][0]["value"] == "58"
        assert o["items"][0]["badge"] == "بدل"
        assert h.ports.original_value.model_dump(mode="json") == before
        doc = await h.doc(o)
        assert doc["items"][0]["original_options"][0]["value"] == "56"
        assert doc["source_revision"] == 2
    run(scenario())


@pytest.mark.parametrize("values", [[], [{"key": "size", "value": "60"}], [{"key": "size", "value": "56"}, {"key": "size", "value": "58"}], [{"key": "unknown", "value": "test"}]])
def test_missing_invalid_and_duplicate_options_are_blocked(values):
    async def scenario():
        h = Harness()
        data = request_data()
        data["items"][0]["options"] = values
        with pytest.raises(DomainError):
            await h.create(data=data)
        assert not h.store.documents
    run(scenario())


@pytest.mark.parametrize("value", [True, 1.5, "40", -1, 10**13])
def test_minor_money_rejects_coercion_and_invalid_values(value):
    data = request_data()
    data["items"][0]["customer_charge_minor"] = value
    with pytest.raises(ValidationError):
        CreateOrder.model_validate(data)


@pytest.mark.parametrize("rate", ["NaN", "Infinity", "0", "-1", "1e999999", "abc", "1e-100000"])
def test_invalid_fx_rejected(rate):
    with pytest.raises(ValidationError):
        FxSnapshot(currency="USD", rate_to_sar=rate, captured_at=NOW, evidence_id="fx-demo")


def test_sar_rate_and_timezone_enforced():
    with pytest.raises(ValidationError):
        FxSnapshot(rate_to_sar="2", captured_at=NOW, evidence_id="fx-demo")
    with pytest.raises(ValidationError):
        FxSnapshot(captured_at="2026-09-27T12:00:00", evidence_id="fx-demo")


def test_creation_idempotency_concurrency_and_conflicting_payload():
    async def scenario():
        h = Harness()
        results = await asyncio.gather(*(h.create() for _ in range(16)))
        assert len({r["order_id"] for r in results}) == 1 and len(h.store.documents) == 1
        data = request_data()
        data["reason"] = "طلب مختلف بنفس مفتاح الإنشاء"
        with pytest.raises(DomainError, match="idempotency_payload_conflict"):
            await h.create(data=data)
    run(scenario())


def test_cross_tenant_original_and_record_access_are_blocked():
    async def scenario():
        h = Harness()
        o = await h.create()
        stranger = Actor(tenant_id="another-store", actor_id="another-owner", permissions=PERMISSIONS)
        with pytest.raises(DomainError, match="order_not_found"):
            await h.service.get(stranger, o["order_id"])
        with pytest.raises(DomainError, match="original_order_not_found"):
            await h.service.create(stranger, CreateOrder.model_validate(request_data()), "other-store-create")
        assert len(h.store.documents) == 1
    run(scenario())


def test_permission_denied_before_source_calls():
    async def scenario():
        h = Harness()
        actor = Actor(tenant_id="test-store", actor_id="employee")
        with pytest.raises(DomainError, match="permission_required"):
            await h.service.create(actor, CreateOrder.model_validate(request_data()), "create-without-access")
        assert h.ports.original_calls == 0 and not h.store.documents
    run(scenario())


def test_defaults_disabled_and_missing_adapters_fail_closed():
    async def scenario():
        store = MemoryStore()
        svc = SpecialOrderService(store)
        with pytest.raises(DomainError, match="creation_disabled"):
            await svc.create(OWNER, CreateOrder.model_validate(request_data()), "gated-create")
        svc.gates.creation_enabled = True
        with pytest.raises(DomainError, match="integration_adapter_not_bound"):
            await svc.create(OWNER, CreateOrder.model_validate(request_data()), "unbound-create")
        assert not store.documents
    run(scenario())


def test_pausing_new_creation_keeps_inflight_records_readable():
    async def scenario():
        h = Harness()
        o = await h.create()
        h.service.gates.creation_enabled = False
        assert (await h.service.get(OWNER, o["order_id"]))["order_id"] == o["order_id"]
        assert (await h.create())["order_id"] == o["order_id"]
        with pytest.raises(DomainError, match="creation_disabled"):
            await h.create(key="new-order-paused")
    run(scenario())


def test_receipt_does_not_change_balances_and_bank_confirmation_does():
    async def scenario():
        h = Harness()
        o = await h.create(partial=True)
        o = await h.receipt(o)
        assert o["balances"]["pending_receipt_minor"] == 4000
        assert o["balances"]["collected_minor"] == 0 and o["balances"]["remaining_minor"] == 6000
        o = await h.bank(o)
        assert o["balances"]["remaining_minor"] == 2000
        assert o["balances"]["pending_receipt_minor"] == 0
        assert o["balances"]["cod_to_collect_minor"] == 2000
        assert not o["policy"]["counts_in_sales_orders"]
        proof_id = "bank-movement-demo"
        with pytest.raises(DomainError, match="financial_movement_already_used"):
            await h.command(o, "observe_payment", {"movement_id": proof_id})
        assert len((await h.doc(o))["payments"]) == 1
    run(scenario())


def test_pending_receipt_can_be_rejected_without_ledger_effect():
    async def scenario():
        h = Harness()
        o = await h.receipt(await h.create(partial=True))
        claim_id = o["receipt_claims"][0]["claim_id"]
        o = await h.command(o, "reject_receipt", {"claim_id": claim_id, "reason": "إيصال غير مطابق"})
        assert o["balances"]["pending_receipt_minor"] == 0 and o["balances"]["remaining_minor"] == 6000
        assert not o["payments"]
    run(scenario())


def test_fake_bank_or_tenant_proof_never_commits_partial_state():
    async def scenario():
        h = Harness()
        o = await h.receipt(await h.create(partial=True))
        claim = o["receipt_claims"][0]
        for field, value in [("account_id", "wrong-bank"), ("tenant_id", "other-store"), ("amount_minor", 3999), ("currency", "QAR"), ("evidence_id", "wrong-evidence")]:
            data = dict(tenant_id="test-store", order_id=o["order_id"], movement_id="bad-proof", kind="bank_collection", currency="SAR", amount_minor=4000, amount_sar_minor=4000, account_id="bank-demo", evidence_id="receipt-demo", receipt_claim_id=claim["claim_id"])
            data[field] = value
            h.ports.payments["bad-proof"] = LedgerProof(**data)
            with pytest.raises(DomainError):
                await h.command(o, "observe_payment", {"movement_id": "bad-proof"})
            stored = await h.doc(o)
            assert stored["revision"] == o["revision"] and not stored["payments"]
            assert stored["receipt_claims"][0]["state"] == "pending"
    run(scenario())


def test_receipt_privacy_for_operations_employee():
    async def scenario():
        h = Harness()
        o = await h.receipt(await h.create(partial=True))
        employee = Actor(tenant_id="test-store", actor_id="employee", permissions=frozenset({"special_orders.read"}))
        out = await h.service.get(employee, o["order_id"])
        assert "receipt_claims" not in out and "cost_report" not in out and "payments" not in out
        assert "pending_receipt_minor" not in out["balances"]
    run(scenario())


def test_simulated_shared_workflow_and_cod_custody_remittance_are_separate():
    async def scenario():
        h = Harness()
        o = await h.bank(await h.receipt(await h.create(partial=True)))
        o = await h.command(o, "freeze_source")
        for stage in ("reviewed", "processing", "ready_to_ship", "completed", "delivering", "delivered"):
            o = await h.stage(o, stage)
            assert o["stage"] == stage
        o = await h.finance(o, "cod_collection", 2000, "cod-1")
        assert o["balances"]["remaining_minor"] == 0 and o["balances"]["custody_minor"] == 2000
        o = await h.finance(o, "remittance", 1200, "remit-1", parent="cod-1")
        assert o["balances"]["collected_minor"] == 6000 and o["balances"]["custody_minor"] == 800
        o = await h.finance(o, "remittance", 800, "remit-2", parent="cod-1")
        assert o["balances"]["custody_minor"] == 0 and o["balances"]["collected_minor"] == 6000
        with pytest.raises(DomainError, match="remittance_exceeds_custody"):
            await h.finance(o, "remittance", 1, "remit-too-much", parent="cod-1")
    run(scenario())


def test_unconfirmed_bank_receipt_blocks_dispatch_not_initial_review():
    async def scenario():
        h = Harness()
        o = await h.receipt(await h.create(partial=True))
        o = await h.command(o, "freeze_source")
        o = await h.stage(o, "reviewed")
        with pytest.raises(DomainError, match="dispatch_financial_guard"):
            await h.stage(o, "ready_to_ship")
        assert (await h.doc(o))["stage"] == "reviewed"
    run(scenario())


def test_freeze_blocks_option_changes_and_stale_source_workflow_proofs():
    async def scenario():
        h = Harness()
        o = await h.create()
        o = await h.command(o, "freeze_source")
        with pytest.raises(DomainError, match="options_frozen_for_fulfillment"):
            await h.command(o, "amend_options", {"line_key": "line-1", "options": [{"key": "size", "value": "58"}, {"key": "name", "value": "جديد"}]})
        h.ports.proofs["stale-proof"] = WorkflowProof(tenant_id="test-store", order_id=o["order_id"], source_revision=2,
            workflow_revision=1, snapshot_digest="f" * 64, stage="reviewed", event_id="stale-proof")
        with pytest.raises(DomainError, match="workflow_snapshot_stale"):
            await h.command(o, "observe_workflow", {"event_id": "stale-proof"})
    run(scenario())


def test_command_replay_and_concurrent_different_commands():
    async def scenario():
        h = Harness()
        o = await h.create()
        a, b = await asyncio.gather(h.command(o, "freeze_source", key="same-command-key"), h.command(o, "freeze_source", key="same-command-key"))
        assert a["revision"] == b["revision"] == 2
        other = await h.create(key="other-new-order")
        result = await asyncio.gather(h.command(other, "freeze_source", key="parallel-one"), h.command(other, "freeze_source", key="parallel-two"), return_exceptions=True)
        assert sum(isinstance(r, DomainError) for r in result) == 1
        assert (await h.doc(other))["revision"] == 2
    run(scenario())


def test_new_label_required_and_uniqueness_enforced():
    async def scenario():
        h = Harness()
        data = request_data()
        data["delivery"] = {"method": "carrier", "carrier_key": "carrier-demo", "tracking_number": "NEW-TRACKING", "label": {"object_id": "new-label", "kind": "carrier_label", "sha256": "b" * 64}}
        first = await h.create(data=data)
        assert first["delivery"]["label"]["object_id"] == "new-label"
        with pytest.raises(DomainError, match="unique_resource_already_used"):
            await h.create(key="reused-new-label", data=data)
        for field, value in [("tracking_number", "OLD-TRACKING"), ("label", {"object_id": "old-label", "kind": "carrier_label", "sha256": "c" * 64})]:
            bad = deepcopy(data)
            bad["delivery"][field] = value
            with pytest.raises(DomainError, match="original_shipping_label_must_not_be_reused"):
                await h.create(key="bad-old-label-001", data=bad)
    run(scenario())


def test_unverified_label_fx_and_receipt_are_rejected():
    async def scenario():
        h = Harness()
        h.ports.fx_ok = False
        with pytest.raises(DomainError, match="fx_snapshot_not_verified"):
            await h.create()
        h.ports.fx_ok = True
        o = await h.create(partial=True)
        h.ports.evidence_ok = False
        with pytest.raises(DomainError, match="receipt_evidence_not_verified"):
            await h.receipt(o)
    run(scenario())


def test_cost_facts_classified_without_posting_or_missing_cost_zero_assumption():
    async def scenario():
        h = Harness()
        o = await h.create()
        for kind, target, amount, origin in [("product", "line-1", 10000, "supplier_receipt"), ("shipping", "shipping", 1725, "carrier_charge")]:
            mid = "cost-" + kind
            h.ports.costs[mid] = CostProof(tenant_id="test-store", order_id=o["order_id"], movement_id=mid,
                kind=kind, target_key=target, unit_indices=(1,) if kind == "product" else (), cost_sar_minor=amount, counterparty_id="party-demo", origin=origin,
                expense_bucket="compensation", evidence_id="cost-proof-demo")
            o = await h.command(o, "observe_cost", {"movement_id": mid})
        assert o["cost_report"]["recognized_cost_minor"] == 11725
        assert o["cost_report"]["net_store_burden_minor"] == 11725
        assert o["cost_report"]["costs_complete"] is True
        assert not (await h.doc(o))["payments"]
    run(scenario())


def test_cancel_and_refund_do_not_delete_history_or_reopen_customer_debt():
    async def scenario():
        h = Harness()
        o = await h.bank(await h.receipt(await h.create(partial=True)))
        o = await h.stage(o, "cancelled")
        assert o["balances"]["remaining_minor"] == 0 and o["balances"]["refund_due_minor"] == 4000
        o = await h.finance(o, "refund", 4000, "refund-1", parent="bank-movement-demo", refund_from="bank")
        assert o["balances"]["net_collected_minor"] == 0 and o["balances"]["remaining_minor"] == 0
        assert len((await h.doc(o))["payments"]) == 2
        with pytest.raises(DomainError, match="refund_exceeds_collection"):
            await h.finance(o, "refund", 1, "refund-extra", parent="bank-movement-demo", refund_from="bank")
    run(scenario())


def test_outbox_delivery_is_opt_in_and_acknowledged_retry_is_safe():
    async def scenario():
        h = Harness()
        o = await h.create()
        event = (await h.doc(o))["outbox"][0]["event_id"]
        h.service.gates.dispatch_enabled = False
        with pytest.raises(DomainError, match="outbox_dispatch_disabled"):
            await h.service.dispatch(OWNER, o["order_id"], event)
        h.service.gates.dispatch_enabled = True
        assert (await h.service.dispatch(OWNER, o["order_id"], event))["acknowledged"]
        assert (await h.service.dispatch(OWNER, o["order_id"], event))["replayed"]
        assert len(h.ports.published) == 1
    run(scenario())


def test_pagination_has_no_duplicates_and_is_tenant_scoped():
    async def scenario():
        h = Harness()
        for n in range(6):
            await h.create(key=f"page-create-{n:04d}")
        seen, before = [], None
        while True:
            page = await h.service.list(OWNER, 2, before)
            seen.extend(o["order_id"] for o in page["items"])
            before = page["next"]
            if before is None:
                break
        assert len(seen) == len(set(seen)) == 6
        stranger = Actor(tenant_id="other-store", actor_id="other-owner", permissions=PERMISSIONS)
        assert not (await h.service.list(stranger))["items"]
    run(scenario())


def adjustment(basis="existing_source_balance", paid=30000, amount=20000):
    return SettlementAdjustment(original_order_id="salla-demo", original_order_number="SALLA-DEMO", basis=basis,
        source_total_minor=50000, source_paid_minor=paid, source_revision="salla-1", amount_minor=amount,
        reason="اتفاق اختباري موثق", evidence_id="agreement-demo", fx=FxSnapshot(captured_at=NOW, evidence_id="fx-demo"))


def test_existing_salla_remainder_is_not_added_again():
    out = collection_overlay(adjustment(), current_source_revision="salla-1", source_total_minor=50000, source_paid_minor=30000)
    assert out["cod_to_collect_minor"] == 20000 and out["new_receivable_required_minor"] == 0
    assert out["source_total_minor"] == 50000 and out["source_paid_minor"] == 30000
    assert out["new_sales_order_count"] == 0


def test_additional_agreement_and_salla_payment_overlap():
    adj = adjustment("additional_agreed_charge", paid=50000, amount=20000)
    out = collection_overlay(adj, current_source_revision="salla-1", source_total_minor=50000,
        source_paid_minor=50000, local_confirmed_minor=10000, already_reflected_in_salla_minor=0)
    assert out["cod_to_collect_minor"] == 10000 and out["new_receivable_required_minor"] == 20000
    # A payment already reflected in Salla is not subtracted a second time.
    out = collection_overlay(adjustment(), current_source_revision="salla-1", source_total_minor=50000,
        source_paid_minor=30000, local_confirmed_minor=10000, already_reflected_in_salla_minor=10000)
    assert out["cod_to_collect_minor"] == 20000


def test_changed_salla_source_blocks_overlay_until_reconciliation():
    with pytest.raises(DomainError, match="salla_balance_snapshot_stale"):
        collection_overlay(adjustment(), current_source_revision="salla-2", source_total_minor=50000, source_paid_minor=30000)


def test_non_sar_precision_and_allocation_rounding():
    async def scenario():
        h = Harness()
        data = request_data()
        data["fx"].update(currency="KWD", rate_to_sar="12.17")
        data["items"][0]["customer_charge_minor"] = 7
        data["delivery"]["customer_charge_minor"] = 3
        data["collection"] = {"cod_minor": 10}
        o = await h.create(data=data)
        assert sum(a["amount_sar_minor"] for a in o["allocations"]) == 12
        assert o["cost_report"]["agreed_contribution_minor"] == 12
    run(scenario())


def test_physical_units_cannot_receive_inventory_and_supplier_cost_twice():
    async def scenario():
        h = Harness()
        o = await h.create()
        base = dict(tenant_id="test-store", order_id=o["order_id"], kind="product", target_key="line-1",
            unit_indices=(1,), cost_sar_minor=10000, counterparty_id="supplier-demo", expense_bucket="compensation", evidence_id="cost-evidence")
        h.ports.costs["cost-a"] = CostProof(**base, movement_id="cost-a", origin="supplier_receipt")
        o = await h.command(o, "observe_cost", {"movement_id": "cost-a"})
        h.ports.costs["cost-b"] = CostProof(**base, movement_id="cost-b", origin="inventory_issue")
        with pytest.raises(DomainError, match="physical_cost_already_recognized"):
            await h.command(o, "observe_cost", {"movement_id": "cost-b"})
        assert len((await h.doc(o))["costs"]) == 1
    run(scenario())


def test_cost_reversal_preserves_audit_then_allows_corrected_cost():
    async def scenario():
        h = Harness()
        o = await h.create()
        fields = dict(tenant_id="test-store", order_id=o["order_id"], kind="product", target_key="line-1",
            unit_indices=(1,), cost_sar_minor=10000, counterparty_id="supplier-demo", expense_bucket="compensation", evidence_id="cost-evidence", origin="supplier_receipt")
        for mid, reverse in [("cost-a", None), ("cost-reversal", "cost-a"), ("cost-corrected", None)]:
            h.ports.costs[mid] = CostProof(**fields, movement_id=mid, reverses_movement_id=reverse)
            o = await h.command(o, "observe_cost", {"movement_id": mid})
        assert len((await h.doc(o))["costs"]) == 3
        assert o["cost_report"]["recognized_cost_minor"] == 10000
    run(scenario())


def test_collected_contribution_is_allocated_to_products_and_shipping_exactly():
    async def scenario():
        h = Harness()
        o = await h.bank(await h.receipt(await h.create(partial=True)))
        report = o["cost_report"]
        assert [a["collected_minor"] for a in report["allocations"]] == [2667, 1333]
        assert sum(a["collected_sar_minor"] for a in report["allocations"]) == 4000
        assert report["provisional"] and report["final_net_store_burden_minor"] is None
    run(scenario())


@pytest.mark.parametrize("weights", [[1, 1, 1], [4000, 2000, 1000], [0, 5, 2], [1], [999, 1, 1000]])
def test_integer_distribution_property_sums_and_zero_weights(weights):
    from mezan_special_orders.domain import distribute_minor
    for total in range(201):
        parts = distribute_minor(total, weights)
        assert sum(parts) == total
        assert all(p >= 0 for p in parts)
        assert all(p == 0 for p, w in zip(parts, weights) if w == 0)


def test_two_quantity_line_requires_both_cost_units():
    async def scenario():
        h = Harness()
        data = request_data()
        data["items"][0]["quantity"] = 2
        o = await h.create(data=data)
        fields = dict(tenant_id="test-store", order_id=o["order_id"], kind="product", target_key="line-1",
            cost_sar_minor=10000, counterparty_id="supplier-demo", expense_bucket="compensation", evidence_id="cost-evidence", origin="supplier_receipt")
        h.ports.costs["one-unit"] = CostProof(**fields, movement_id="one-unit", unit_indices=(1,))
        o = await h.command(o, "observe_cost", {"movement_id": "one-unit"})
        assert "product:line-1" in o["cost_report"]["unrecognized_cost_targets"]
        h.ports.costs["two-unit"] = CostProof(**fields, movement_id="two-unit", unit_indices=(2,))
        o = await h.command(o, "observe_cost", {"movement_id": "two-unit"})
        assert "product:line-1" not in o["cost_report"]["unrecognized_cost_targets"]
    run(scenario())
