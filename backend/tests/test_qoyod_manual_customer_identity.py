"""Buyer identity survives the queued Plan-B sender's flat status snapshot."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from mongomock_motor import AsyncMongoMockClient

from integrations.qoyod_manual import send


ORDER = "123456789"


def _row(customer=None, **buyer_fields):
    return {
        "id": "customer-identity-row",
        "user_id": "owner",
        "salla_order_number": ORDER,
        "received_at": datetime.now(timezone.utc),
        "canonical_payload": {
            "order_number": ORDER,
            "order_date": "2026-07-05",
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "payment_method": "credit_card",
            "total_amount": 115.0,
            "subtotal": 115.0,
            "shipping_amount": 0.0,
            "discount_amount": 0.0,
            "tax_amount": 0.0,
            "currency": "SAR",
            "customer": customer or {},
            "items": [{
                "sku": "TEST-SKU", "name": "منتج اختباري",
                "quantity": 1, "unit_price": 115.0, "total": 115.0,
            }],
            **buyer_fields,
        },
    }


@pytest.fixture
def provider(monkeypatch):
    client = SimpleNamespace(
        invalidate_invoice_absence=Mock(),
        find_invoice_by_reference=AsyncMock(return_value=None),
        find_customers_by_phone=AsyncMock(return_value=[]),
        find_customers_by_email=AsyncMock(return_value=[]),
        create_customer=AsyncMock(return_value={"contact": {"id": 33}}),
        find_product_by_sku=AsyncMock(return_value={"id": 77}),
        create_product=AsyncMock(side_effect=AssertionError("existing product")),
        create_invoice=AsyncMock(return_value={"invoice": {
            "id": 501, "number": "INV-TEST", "reference": ORDER, "total": 115.0,
        }}),
        create_invoice_payment=AsyncMock(return_value={"invoice_payment": {"id": 801}}),
    )
    monkeypatch.setattr(send, "ManualQoyodClient", Mock(return_value=client))
    monkeypatch.setattr(send, "get_api_key", AsyncMock(return_value="synthetic-api-key"))
    return client


async def _database(row):
    db = AsyncMongoMockClient().customer_identity
    if row is not None:
        await db.integration_inbox.insert_one(row)
    await db.qoyod_settings.insert_one({
        "user_id": "main", "qoyod_tax_percent": 15,
        "default_inventory_id": 1, "default_branch_id": 1,
        "default_product_category_id": 1, "default_product_tax_id": 1,
        "default_sales_account_id": 100, "default_product_unit_type_id": 1,
        "payment_method_mapping": [{
            "salla_method": "credit_card", "qoyod_account_id": "42",
            "posting_mode": "paid_receipt",
        }],
    })
    return db


@pytest.mark.asyncio
@pytest.mark.parametrize("entry_point", ["send", "retry"])
@pytest.mark.parametrize("nested", [{}, {
    "name": "اسم قديم", "phone": "+966500000099", "email": "old@example.test",
}])
async def test_flat_buyer_reaches_contact_matching_creation_and_saved_invoice(provider, nested, entry_point):
    db = await _database(_row(nested,
        customer_name="  اسم المشتري  ", customer_mobile=" +966500000001 ",
        customer_email=" buyer@example.test ",
        shipping_address={"name": "اسم المستلم", "phone": "+966500000002"},
    ))

    if entry_point == "retry":
        from integrations.qoyod_manual.failed_retry import retry_failed_order
        result = await retry_failed_order(db, orders_user_id="owner", order_number=ORDER,
            actor="manual-job:owner:synthetic", refresh_fn=AsyncMock(return_value=(True, {})))
    else:
        result = await send.manual_send_one(db, user_id="main", orders_user_id="owner",
            order_number=ORDER, actor="manual-job:owner:synthetic")

    assert result["invoice_id"] == 501
    provider.find_customers_by_phone.assert_awaited_once_with("+966500000001")
    provider.find_customers_by_email.assert_awaited_once_with("buyer@example.test")
    provider.create_customer.assert_awaited_once_with({"contact": {
        "name": "اسم المشتري", "type": "individual",
        "phone": "+966500000001", "email": "buyer@example.test",
    }}, idem=f"cust-{ORDER}")
    provider.create_invoice.assert_awaited_once()
    provider.create_invoice_payment.assert_awaited_once()
    invoice = await db.qoyod_invoices.find_one({"salla_order_number": ORDER})
    assert invoice["customer_name"] == "اسم المشتري"


@pytest.mark.asyncio
async def test_flat_phone_matches_existing_customer_without_creating_duplicate(provider):
    db = await _database(_row(customer_name="اسم المشتري", customer_mobile="+966500000001"))
    provider.find_customers_by_phone.return_value = [{"id": 44}]

    await send.manual_send_one(db, user_id="main", orders_user_id="owner", order_number=ORDER)

    provider.find_customers_by_phone.assert_awaited_once_with("+966500000001")
    provider.create_customer.assert_not_awaited()
    invoice = provider.create_invoice.call_args.args[0]["invoice"]
    assert invoice["contact_id"] == 44


@pytest.mark.asyncio
async def test_nested_legacy_buyer_and_blank_flat_fields_remain_compatible(provider):
    db = await _database(_row({
        "name": " اسم قديم صالح ", "phone": "+966500000003", "email": "legacy@example.test",
    }, customer_name="  ", customer_mobile="", customer_email="  "))

    await send.manual_send_one(db, user_id="main", orders_user_id="owner", order_number=ORDER)

    assert provider.create_customer.call_args.args[0]["contact"]["name"] == "اسم قديم صالح"
    provider.find_customers_by_phone.assert_awaited_once_with("+966500000003")


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "  ", f"عميل {ORDER}", f"ضيف #{ORDER}", "عميل +966500000001"])
async def test_missing_or_generated_name_refuses_before_any_provider_write(provider, name):
    db = await _database(_row({"name": name, "phone": "+966500000001"},
        customer_name=" ", shipping_address={"name": "اسم المستلم"}))
    # Even an existing contact must not let a nameless order through.
    provider.find_customers_by_phone.return_value = [{"id": 44}]

    with pytest.raises(send.ManualSendRefused) as exc:
        await send.manual_send_one(db, user_id="main", orders_user_id="owner", order_number=ORDER)

    assert exc.value.code == "customer_name_missing"
    assert exc.value.extra["qoyod_write_performed"] is False
    provider.create_customer.assert_not_awaited()
    provider.create_product.assert_not_awaited()
    provider.create_invoice.assert_not_awaited()
    provider.create_invoice_payment.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_name_still_observes_existing_provider_invoice(provider):
    db = await _database(_row())
    provider.find_invoice_by_reference.return_value = {"id": 800}

    with pytest.raises(send.ManualSendRefused) as exc:
        await send.manual_send_one(db, user_id="main", orders_user_id="owner", order_number=ORDER)

    assert exc.value.code == "duplicate_invoice_in_qoyod"
    assert exc.value.extra["qoyod_invoice_id"] == 800
    provider.create_invoice.assert_not_awaited()
    provider.create_customer.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_name_does_not_block_payment_only_recovery(provider):
    row = _row()
    row["manual_qoyod_invoice_id"] = "501"
    db = await _database(row)
    provider.get_invoice = AsyncMock(return_value={"invoice": {"id": 501, "total": 115.0}})

    result = await send.manual_send_one(db, user_id="main", orders_user_id="owner", order_number=ORDER)

    assert result["invoice_id"] == 501
    provider.create_invoice_payment.assert_awaited_once()
    provider.find_invoice_by_reference.assert_not_awaited()
    provider.find_customers_by_phone.assert_not_awaited()
    provider.create_customer.assert_not_awaited()
    provider.create_invoice.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_status_snapshot_without_legacy_inbox_uses_current_buyer(provider):
    from salla_integration.sync import _refresh_plan_b_status_snapshot

    db = await _database(None)
    await db.unified_orders.insert_one({
        "user_id": "owner", "order_number": ORDER,
        "raw_by_source": {"salla_direct": {
            "id": ORDER, "reference_id": ORDER, "currency": "SAR",
            "date": "2026-07-05T10:00:00+03:00",
            "status": {"slug": "completed", "name": "تم التنفيذ"},
            "payment_method": "credit_card",
            "customer": {"full_name": "اسم المشتري", "mobile": "+966500000001"},
            "amounts": {
                "total": {"amount": 115.0, "currency": "SAR"},
                "sub_total": {"amount": 100.0, "currency": "SAR"},
                "tax": {"amount": 15.0, "currency": "SAR"},
                "shipping_cost": {"amount": 0.0, "currency": "SAR"},
            },
            "items": [{
                "sku": "TEST-SKU", "quantity": 1,
                "product": {"id": "synthetic-product", "name": "منتج اختباري"},
                "amounts": {
                    "price_without_tax": {"amount": 100.0, "currency": "SAR"},
                    "total": {"amount": 115.0, "currency": "SAR"},
                    "tax": {"amount": 15.0, "currency": "SAR"},
                },
            }],
        }},
    })
    await _refresh_plan_b_status_snapshot(db, "owner", ORDER, {
        "order_status_slug": "completed", "order_status": "تم التنفيذ",
        "payment_method": "credit_card", "customer_name": "اسم المشتري",
        "customer_mobile": "+966500000001",
    })

    result = await send.manual_send_one(db, user_id="main", orders_user_id="owner",
        order_number=ORDER, actor="manual-job:owner:synthetic",
        allow_missing_salla_order_date=True, allow_historical_positive_total=True)

    assert result["invoice_id"] == 501
    assert provider.create_customer.call_args.args[0]["contact"]["name"] == "اسم المشتري"
    provider.find_customers_by_phone.assert_awaited_once_with("+966500000001")
