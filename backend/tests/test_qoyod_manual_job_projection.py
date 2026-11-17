"""Queued single/bulk actions retain the current unified payment boundary."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mongomock_motor import AsyncMongoMockClient

from integrations.qoyod_manual import auto_send, send, send_jobs
from qoyod_auto_unified.installer import _uses_unified_sender_projection


OWNER = {"id": "synthetic-owner", "role": "owner"}
ORDER = "100000021"


@pytest.fixture(autouse=True)
def isolated_runner_boundary(monkeypatch):
    # Projection cases use mongomock; the dedicated real-Mongo suite verifies
    # server-clock runner ownership rather than emulating its update pipeline.
    from integrations.qoyod_manual import runner_lease
    for method in ("claim", "renew", "is_current"):
        monkeypatch.setattr(runner_lease, method, AsyncMock(return_value=True))
    monkeypatch.setattr(runner_lease, "release", AsyncMock())


async def make_db(*, paid=True, customer_name="اسم المشتري التجريبي"):
    db = AsyncMongoMockClient().manual_projection_test
    await db.users.insert_one(OWNER)
    await db.qoyod_settings.insert_one({
        "user_id": "main", "security_owner_id": OWNER["id"],
        "qoyod_tax_percent": 15, "default_inventory_id": 1, "default_branch_id": 1,
        "default_product_category_id": 1, "default_product_tax_id": 1,
        "default_sales_account_id": 100, "default_product_unit_type_id": 1,
        "payment_method_mapping": [{"salla_method": "mada", "qoyod_account_id": "11",
                                    "posting_mode": "paid_receipt"}],
    })
    await db.unified_orders.insert_one({
        "user_id": OWNER["id"], "order_number": ORDER, "order_id": "synthetic-salla-id",
        "order_date": "2026-11-01", "order_status_slug": "completed",
        "order_status": "تم التنفيذ", "payment_method": "mada",
        "payment_status": "paid" if paid else "pending",
        "payment_collection_status": "paid" if paid else "pending",
        "paid_amount": 115 if paid else 0, "remaining_amount": 0 if paid else 115,
        "has_remaining_amount": not paid, "total_amount": 115,
        "subtotal": 100, "tax_amount": 15, "currency": "SAR",
        "customer_name": customer_name, "customer_mobile": "+966500000001",
        "customer_email": "buyer@example.invalid",
        "items": [{"sku": "SYNTHETIC-SKU", "name": "منتج تجريبي", "quantity": 1,
                   "unit_price": 100, "tax_amount": 15, "total": 115}],
    })
    return db


@pytest.fixture
def isolated_transaction(monkeypatch):
    # These cases exercise sender projection, not Mongo transaction isolation;
    # real transaction ownership has its own replica-set acceptance suite.
    async def owner(db, merchant, callback, **kwargs):
        assert merchant == OWNER["id"]
        return await callback(db)

    monkeypatch.setattr("operational_atomic.operational_owner", owner)


def test_job_actor_uses_same_projection_as_the_existing_manual_ui():
    assert _uses_unified_sender_projection("manual-job:synthetic-owner:generation")
    assert _uses_unified_sender_projection("manual-ui:synthetic-owner")
    assert not _uses_unified_sender_projection("unrelated-historical-caller")


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "retry"])
@pytest.mark.parametrize("paid,name,expected", [
    (False, "اسم المشتري التجريبي", "authoritative_payment_not_eligible"),
    (True, "", "authoritative_customer_identity_missing"),
])
async def test_queued_actions_refuse_unpaid_or_nameless_unified_order_before_provider(
    monkeypatch, isolated_transaction, operation, paid, name, expected,
):
    db = await make_db(paid=paid, customer_name=name)
    refresh = AsyncMock(return_value=(True, {"ok": True, "found": True}))
    forbidden = Mock(side_effect=AssertionError("provider must not be reached"))
    monkeypatch.setattr(auto_send, "_refresh_and_verify_salla_status", refresh)
    monkeypatch.setattr(send, "ManualQoyodClient", forbidden)
    queued = await send_jobs.enqueue(db, user=OWNER, order_number=ORDER, operation=operation,
                                     request_id="explicit-click")

    await send_jobs.run_once(db)

    result = await send_jobs.read_job(db, user=OWNER, job_id=queued["job"]["id"])
    assert result["job"]["status"] == "refused"
    assert result["job"]["error"]["code"] == expected
    if not name:
        assert "اسم العميل" in result["job"]["error"]["message"]
    refresh.assert_awaited_once_with(db, orders_user_id=OWNER["id"], order_number=ORDER)
    forbidden.assert_not_called()
    assert await db.qoyod_manual_send_locks.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "retry"])
@pytest.mark.parametrize("customer_name", ["اسم المشتري التجريبي", ""])
async def test_paid_unified_only_job_materializes_customer_and_preserves_existing_invoice(
    monkeypatch, isolated_transaction, operation, customer_name,
):
    db = await make_db(customer_name=customer_name)
    # No legacy inbox row exists. The current unified source already knows
    # the sent invoice/payment, so projection must carry both identity and
    # duplicate evidence before the original sender observes the request.
    await db.unified_orders.update_one({"order_number": ORDER}, {"$set": {
        "qoyod_invoice_id": "501", "qoyod_payment_id": "601",
        "qoyod_invoice_source": "manual_plan_b",
    }})
    refresh = AsyncMock(return_value=(True, {"ok": True, "found": True}))
    forbidden = Mock(side_effect=AssertionError("must not recreate existing invoice"))
    monkeypatch.setattr(auto_send, "_refresh_and_verify_salla_status", refresh)
    monkeypatch.setattr(send, "ManualQoyodClient", forbidden)
    queued = await send_jobs.enqueue(db, user=OWNER, order_number=ORDER, operation=operation,
                                     request_id="explicit-click")

    await send_jobs.run_once(db)

    result = await send_jobs.read_job(db, user=OWNER, job_id=queued["job"]["id"])
    assert result["job"]["status"] == "succeeded"
    assert result["job"]["result"]["retry_outcome"] == "already_sent"
    assert result["job"]["result"]["invoice_id"] == "501"
    projection = await db.integration_inbox.find_one({"salla_order_number": ORDER})
    assert projection["canonical_payload"]["customer"] == {
        "name": customer_name or None, "phone": "+966500000001",
        "email": "buyer@example.invalid",
    }
    refresh.assert_awaited_once()
    forbidden.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "retry"])
async def test_paid_unified_only_job_sends_real_buyer_through_complete_guarded_path(
    monkeypatch, isolated_transaction, operation,
):
    db = await make_db()
    refresh = AsyncMock(return_value=(True, {"ok": True, "found": True}))
    provider = SimpleNamespace(
        invalidate_invoice_absence=Mock(),
        find_invoice_by_reference=AsyncMock(return_value=None),
        find_customers_by_phone=AsyncMock(return_value=[]),
        find_customers_by_email=AsyncMock(return_value=[]),
        create_customer=AsyncMock(return_value={"contact": {"id": 33}}),
        find_product_by_sku=AsyncMock(return_value={"id": 77}),
        create_product=AsyncMock(side_effect=AssertionError("existing product")),
        create_invoice=AsyncMock(return_value={"invoice": {
            "id": 501, "number": "INV-SYNTHETIC", "reference": ORDER, "total": 115,
        }}),
        create_invoice_payment=AsyncMock(return_value={"invoice_payment": {"id": 601}}),
    )
    monkeypatch.setattr(auto_send, "_refresh_and_verify_salla_status", refresh)
    monkeypatch.setattr(send, "get_api_key", AsyncMock(return_value="synthetic-api-key"))
    monkeypatch.setattr(send, "ManualQoyodClient", Mock(return_value=provider))
    queued = await send_jobs.enqueue(db, user=OWNER, order_number=ORDER, operation=operation,
                                     request_id="explicit-click")

    await send_jobs.run_once(db)

    result = await send_jobs.read_job(db, user=OWNER, job_id=queued["job"]["id"])
    assert result["job"]["status"] == "succeeded"
    assert result["job"]["result"]["invoice_id"] == 501
    provider.create_customer.assert_awaited_once_with({"contact": {
        "name": "اسم المشتري التجريبي", "type": "individual", "phone": "+966500000001",
        "email": "buyer@example.invalid",
    }}, idem=f"cust-{ORDER}")
    provider.create_invoice.assert_awaited_once()
    provider.create_invoice_payment.assert_awaited_once()
    refresh.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["send", "retry"])
async def test_queued_payment_only_recovery_does_not_require_a_new_customer_name(
    monkeypatch, isolated_transaction, operation,
):
    db = await make_db(customer_name="")
    await db.unified_orders.update_one({"order_number": ORDER}, {"$set": {
        "qoyod_invoice_id": "501", "qoyod_invoice_source": "manual_plan_b",
    }})
    refresh = AsyncMock(return_value=(True, {"ok": True, "found": True}))
    provider = SimpleNamespace(
        get_invoice=AsyncMock(return_value={"invoice": {"id": 501, "total": 115}}),
        create_invoice_payment=AsyncMock(return_value={"invoice_payment": {"id": 601}}),
        create_customer=AsyncMock(side_effect=AssertionError("existing invoice needs no new contact")),
        create_invoice=AsyncMock(side_effect=AssertionError("existing invoice must not be recreated")),
    )
    monkeypatch.setattr(auto_send, "_refresh_and_verify_salla_status", refresh)
    monkeypatch.setattr(send, "get_api_key", AsyncMock(return_value="synthetic-api-key"))
    monkeypatch.setattr(send, "ManualQoyodClient", Mock(return_value=provider))
    queued = await send_jobs.enqueue(db, user=OWNER, order_number=ORDER, operation=operation,
                                     request_id="payment-recovery")

    await send_jobs.run_once(db)

    result = await send_jobs.read_job(db, user=OWNER, job_id=queued["job"]["id"])
    assert result["job"]["status"] == "succeeded"
    assert str(result["job"]["result"]["invoice_id"]) == "501"
    provider.create_invoice_payment.assert_awaited_once()
    provider.create_customer.assert_not_awaited()
    provider.create_invoice.assert_not_awaited()
    refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_synthetic_invoice_marker_never_waives_required_buyer_name(
    monkeypatch, isolated_transaction,
):
    db = await make_db(customer_name="")
    await db.unified_orders.update_one({"order_number": ORDER}, {"$set": {
        "qoyod_invoice_id": "DRY-501", "qoyod_invoice_source": "manual_plan_b",
    }})
    monkeypatch.setattr(auto_send, "_refresh_and_verify_salla_status",
                        AsyncMock(return_value=(True, {"ok": True, "found": True})))
    forbidden = Mock(side_effect=AssertionError("a synthetic marker cannot authorize a send"))
    monkeypatch.setattr(send, "ManualQoyodClient", forbidden)
    queued = await send_jobs.enqueue(db, user=OWNER, order_number=ORDER)

    await send_jobs.run_once(db)

    result = await send_jobs.read_job(db, user=OWNER, job_id=queued["job"]["id"])
    assert result["job"]["status"] == "refused"
    assert result["job"]["error"]["code"] == "authoritative_customer_identity_missing"
    forbidden.assert_not_called()


@pytest.mark.asyncio
async def test_status_refusal_never_reaches_the_payment_projection(monkeypatch):
    db = await make_db()
    refresh = AsyncMock(return_value=(False, {"ok": True, "found": True}))
    projection = AsyncMock(side_effect=AssertionError("cancelled order must stop first"))
    monkeypatch.setattr(auto_send, "_refresh_and_verify_salla_status", refresh)
    monkeypatch.setattr(auto_send, "manual_send_one", projection)
    queued = await send_jobs.enqueue(db, user=OWNER, order_number=ORDER)

    await send_jobs.run_once(db)

    result = await send_jobs.read_job(db, user=OWNER, job_id=queued["job"]["id"])
    assert result["job"]["status"] == "refused"
    assert result["job"]["error"]["code"] == "not_qoyod_eligible_status"
    projection.assert_not_awaited()
