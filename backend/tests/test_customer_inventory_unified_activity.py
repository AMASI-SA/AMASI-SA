"""Supplier attribution and customer cash share one isolated operational state."""
from copy import deepcopy
from decimal import Decimal

from operational_balance_inventory import purchase_view, save_purchase
from operational_balance_service import account_statement, create_movement, report
from operational_balance_store import STATES, digest, mutate, read
from operational_financial_activity import attach_snapshots, operational_rows
from operational_inventory_payment_allocation import allocate
from test_operational_balance_workspace import run
from test_operational_customer_adjustments import adjusted_order
from test_operational_workspace_customer_collections import BASE as COLLECTIONS, command as collection_command
from test_operational_workspace_refund_execution import execution as refund_command
from test_operational_workspace_refunds import BASE as REFUNDS


OWNER = {"id": "owner"}
STAMP = "2026-11-11T12:00:00+00:00"
ACCOUNT = {"kind": "bank", "id": "bank", "currency": "SAR", "name": "الإنماء"}


def activity_rows(state):
    rows, _ = operational_rows(state)
    attach_snapshots(state, rows)
    return {row["id"]: {key: value for key, value in row.items() if not key.startswith("_")} for row in rows}


def bank_and_supplier(state):
    parties = report(state)["parties"]
    bank = next(row for row in parties if (row["party_type"], row["party_id"]) == ("bank", "bank"))
    supplier = next(row for row in parties if (row["party_type"], row["party_id"]) == ("supplier", "supplier"))
    return bank["actual"], Decimal(supplier["outstanding_payable"]) - Decimal(supplier["outstanding_receivable"])


async def interleaved_commands(db, client):
    # Keep the existing isolated users/router fixture, with only the bank's
    # 1,000 opening. The 100 supplier liability below comes from a real invoice.
    initial = await read(db, "owner")
    await db[STATES].update_one({"_id": "owner"}, {"$set": {
        "openings": {"bank:bank": initial["openings"]["bank:bank"]}}})
    await db.mezan_products_v2.insert_one({"user_id": "owner", "mezan_product_id": "unified-product",
        "name": "مخزون الاختبار", "status": "sale", "archived": False})
    invoice = await save_purchase(db, "owner", "owner", {
        "request_id": "unified-invoice-1", "expected_session_scope": digest(["owner", "owner"]),
        "supplier_id": "supplier", "invoice_number": "UNIFIED-1", "invoice_date": "2026-11-02", "note": "",
        "lines": [{"kind": "product", "item_id": "unified-product", "quantity": 5,
                   "unit_price": "20.00", "tax": "0.00"}]}, clock=STAMP)
    payment = await create_movement(db, "owner", "owner", {
        "request_id": "unified-old-payment", "expected_session_scope": digest(["owner", "owner"]),
        "party_type": "supplier", "party_id": "supplier", "bank_id": "bank", "amount": "70.00",
        "currency": "SAR", "direction": "outgoing", "kind": "payment", "reference": "supplier-paid-70",
        "allocations": [], "note": "دفعة المورد السابقة", "receipt_id": None, "order_number": None},
        on_account=True, clock="2026-11-03T12:00:00+00:00")
    state = await read(db, "owner")
    original_event = deepcopy(next(event for event in state["audit"] if event["action"] == "movement_saved"))
    original_row = deepcopy(activity_rows(state)["movement:" + payment["id"]])
    assert original_row["account_balance_after"] == "930.00"  # 1,000 - 70, before customer cash.
    assert original_row["counterparty_balance_after"] == "-30.00"  # 100 owed - 70 already paid.

    await adjusted_order(db, number="1001", total="300.00", hint="0.00")
    await adjusted_order(db, number="1002", total="199.20", hint="0.80")
    collection_payload = await collection_command(client)
    received = await client.post(COLLECTIONS, json=collection_payload)
    assert received.status_code == 200, received.text
    refund_payload = await refund_command(client, order_number="1002")
    refunded = await client.post(REFUNDS + "/record-execution", json=refund_payload)
    assert refunded.status_code == 200, refunded.text
    state = await read(db, "owner")
    assert bank_and_supplier(state) == ("969.20", Decimal("30.00"))  # 1,000 - 70 + 40 - 0.80.
    rows = activity_rows(state)
    assert rows["customer_collection:" + received.json()["item"]["id"]]["account_balance_after"] == "970.00"
    assert rows["refund:" + refunded.json()["item"]["id"]]["account_balance_after"] == "969.20"
    allocation_payload = {"request_id": "unified-allocation-1", "expected_session_scope": digest(["owner", "owner"]),
        "movement_id": payment["id"], "amount": "50.00", "business_date": "2026-11-11"}
    allocated = await allocate(db, OWNER, invoice["id"], allocation_payload, clock=STAMP)
    assert allocated["invoice"]["settled"] == "50.00"
    assert allocated["invoice"]["outstanding"] == "50.00"
    return invoice, payment, original_event, original_row, collection_payload, refund_payload, allocation_payload, allocated


def test_old_supplier_allocation_and_late_customer_matches_keep_cash_and_history_once():
    async def scenario(db, client):
        invoice, payment, original_event, original_row, collection_payload, refund_payload, allocation_payload, allocated = await interleaved_commands(db, client)
        state = await read(db, "owner")
        assert bank_and_supplier(state) == ("969.20", Decimal("30.00"))
        assert len(state["movements"]) == 1  # Allocation added neither a new payment nor a cash row.
        assert activity_rows(state)["movement:" + payment["id"]] == original_row
        assert next(event for event in state["audit"] if event["id"] == original_event["id"]) == original_event
        allocation_event = next(event for event in state["audit"] if event["action"] == "inventory_payment_allocated")
        assert "financial_activity_snapshots" not in allocation_event
        before_statement = account_statement(state, ACCOUNT)
        assert len(before_statement["items"]) == 3

        # This is source evidence arriving later, not a second accountant
        # command. Keep exact order/reference/amount identity and real cash legs.
        async def source_arrived(current):
            for identity, command, direction, kind in (
                ("late-customer-receipt", collection_payload, "incoming", "collection"),
                ("late-customer-refund", refund_payload, "outgoing", "refund"),
            ):
                current["movements"].append({"id": identity, "request_id": identity,
                    "party_type": "bank", "party_id": "bank", "name": ACCOUNT["name"],
                    "bank_id": "bank", "bank_kind": "bank", "bank_name": ACCOUNT["name"],
                    "currency": "SAR", "amount": command["amount"], "direction": direction, "kind": kind,
                    "reference": command["reference"], "order_id": "order-" + command["order_number"],
                    "order_number": command["order_number"], "actor_id": "owner",
                    "occurred_at": command["executed_at"], "allocations": [], "note": "دليل المصدر"})
        await mutate(db, "owner", source_arrived)
        state = await read(db, "owner")
        assert bank_and_supplier(state) == ("969.20", Decimal("30.00"))
        assert activity_rows(state)["movement:" + payment["id"]] == original_row
        statement = account_statement(state, ACCOUNT)
        assert statement["account"]["actual"] == before_statement["account"]["actual"] == "969.20"
        assert len(statement["items"]) == 3
        rows = [row for row in activity_rows(state).values() if row["account_type"] == "bank"]
        assert len(rows) == 3
        assert sum((Decimal(row["amount"]) for row in rows if row["direction"] == "incoming"), Decimal(0)) == Decimal("40.00")
        assert sum((Decimal(row["amount"]) for row in rows if row["direction"] == "outgoing"), Decimal(0)) == Decimal("70.80")
        collection_order = await client.get(COLLECTIONS + "/order/1001")
        assert collection_order.status_code == 200, collection_order.text
        assert collection_order.json()["paid_amount"] == "240.00"
        assert collection_order.json()["pending_collection_amount"] == "60.00"
        refund_order = await client.get(REFUNDS + "/order/1002")
        assert refund_order.status_code == 200, refund_order.text
        assert refund_order.json()["pending_refund_amount"] == "0.00"
        assert refund_order.json()["pending_collection_amount"] == "0.00"

        # Retrying old immutable commands after both attribution and source
        # arrival cannot repeat money, reopen the remainder, or add an audit.
        before_retry = await read(db, "owner")
        assert await allocate(db, OWNER, invoice["id"], allocation_payload, clock=STAMP) == allocated
        assert (await client.post(COLLECTIONS, json=collection_payload)).status_code == 200
        assert (await client.post(REFUNDS + "/record-execution", json=refund_payload)).status_code == 200
        assert await read(db, "owner") == before_retry
        final_invoice = purchase_view(state, state["inventory_purchases"][0])
        assert (final_invoice["settled"], final_invoice["outstanding"]) == ("50.00", "50.00")
    run(scenario)


def test_missing_supplier_lineage_keeps_customer_cash_evidence_but_never_backfills_supplier_history():
    async def scenario(db, client):
        _, payment, _, _, _, _, _, _ = await interleaved_commands(db, client)
        complete = await read(db, "owner")
        before = activity_rows(complete)
        damaged = deepcopy(complete)
        event = next(item for item in damaged["audit"] if item["action"] == "inventory_payment_allocated")
        event.pop("financial_activity_allocation_lineage")
        rows = activity_rows(damaged)
        supplier_payment = rows["movement:" + payment["id"]]
        assert supplier_payment["account_balance_after"] is None
        assert supplier_payment["account_balance_status"] == "unavailable"
        customer_ids = [identity for identity in before if identity.startswith(("customer_collection:", "refund:"))]
        assert len(customer_ids) == 2
        assert all(rows[identity] == before[identity] for identity in customer_ids)
        assert bank_and_supplier(damaged) == ("969.20", Decimal("30.00"))
        assert await read(db, "owner") == complete  # Review never repairs or writes historical evidence.
    run(scenario)


def test_demand_resources_keep_pending_adjustments_and_confirmed_interleaved_money():
    import pytest
    from fastapi import HTTPException
    from operational_configured_provider_fees import refresh_provider_fee_inputs
    from operational_workspace_read_builders import authorize_spec
    from test_operational_balance_configured_provider_fees import paid_state, settings
    from test_operational_balance_financial_position import obligation
    from test_operational_workspace_demand_reads import project

    async def scenario(db, client):
        stale, _ = await project(db, "operational_resource", "/resource/bank/bank", target=("bank", "bank"))
        await interleaved_commands(db, client)
        with pytest.raises(HTTPException) as changed:
            await authorize_spec(db, stale)
        assert changed.value.status_code == 409
        assert changed.value.detail["code"] == "operational_read_source_changed"

        terms = settings("مدى", "1", "1", "15")
        _, original = paid_state("salla", method="mada", terms=terms)
        orders = []
        for identity, total, refund_hint, collection_due in (
            ("demand-refund", "99.20", "0.80", "0.00"),
            ("demand-collection", "150.00", "0.00", "50.00"),
        ):
            order = deepcopy(original)
            order.update(id=identity, order_number=identity)
            order["payment"]["captures"][0]["id"] = identity + "-capture"
            order["payment"]["customer_adjustment"] = {
                "order_total": total, "paid_amount": "100.00", "refund_hint_amount": refund_hint,
                "suggested_refund_amount": refund_hint, "suggested_collection_amount": collection_due,
                "evidence_source": "canonical_salla_adjusted_payment",
            }
            orders.append(order)

        async def provider_evidence(current):
            for order in orders:
                identity = order["id"]
                obligation(current, "provider:" + identity, "provider", "provider", "100.00",
                           party="salla", direction="receivable")
                current["engine"].setdefault("facts", {})["capture:" + identity + "-capture"] = {
                    "order_id": identity, "provider": "salla", "amount": "100.00"}
            refresh_provider_fee_inputs(current, {"provider_fee_settings": terms, "orders": orders})
            current["engine"]["as_of"] = STAMP

        await mutate(db, "owner", provider_evidence)
        before = await read(db, "owner")
        _, bank = await project(db, "operational_resource", "/resource/bank/bank", target=("bank", "bank"))
        _, bank_ledger = await project(db, "operational_ledger", "/ledger/bank/bank", target=("bank", "bank"))
        assert bank["header"]["balance"]["amount"] == "969.20"  # 1,000 - 70 + 40 - 0.80.
        assert bank_ledger["header"]["resource"]["balance"]["amount"] == "969.20"

        _, provider = await project(db, "operational_resource", "/resource/provider/salla",
                                    target=("provider", "salla"))
        _, provider_ledger = await project(db, "operational_ledger", "/ledger/provider/salla",
            target=("provider", "salla"), filters={"provider_orders": True})
        expected_pending = {"currency": "SAR", "refund_pending_count": 1, "refund_pending_amount": "0.80",
                            "collection_pending_count": 1, "collection_pending_amount": "50.00"}
        assert provider["header"]["provider_position"]["customer_adjustments"] == expected_pending
        assert provider_ledger["header"]["resource"]["provider_position"]["customer_adjustments"] == expected_pending
        amounts, = provider_ledger["header"]["amounts"]
        # Each proved 100 payment costs 1% + 1 fixed + 15% VAT = 2.30.
        # Neither the 0.80 refund observation nor 50 still due is executed cash.
        assert (amounts["known_gross"], amounts["known_fees"], amounts["known_net"]) == ("200.00", "4.60", "195.40")
        assert provider["header"]["provider_position"]["known_net"] == "195.40"
        assert bank_and_supplier(await read(db, "owner")) == ("969.20", Decimal("30.00"))
        assert await read(db, "owner") == before
    run(scenario)
