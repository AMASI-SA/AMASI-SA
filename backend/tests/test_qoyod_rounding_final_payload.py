"""Sanitized numeric reproduction; no live customer, SKU, invoice or credentials."""
from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from mongomock_motor import AsyncMongoMockClient
from test_qoyod_manual_send_plan_b import _inbox_row, _seed_settings, _seed_credentials
from integrations.qoyod_manual.send import (
    _build_invoice_payload, _preflight_qoyod_invoice_payload, manual_send_one, ManualSendRefused,
)


def case_canon():
    row = _inbox_row(order_number="900000777", total=161.11, sku="synthetic-item")
    canon = row["canonical_payload"]
    canon["shipping_amount"] = 24.07
    canon["items"][0].update(unit_price=139, total=135.11)
    return row


def test_lrm_payload_is_normalized_and_one_halalah_is_reported_truthfully():
    payload, legacy_prediction, _ = _build_invoice_payload(
        canon=case_canon()["canonical_payload"], contact_id=1,
        line_resolutions={"synthetic-item": 1},
        settings={"qoyod_tax_percent": 15, "default_shipping_product_id": 2},
        send_date_iso="2026-09-19")
    assert legacy_prediction == 161.11
    assert payload["invoice"]["line_items"][0]["discount"] == 21.508
    result = _preflight_qoyod_invoice_payload(payload, salla_total=161.11)
    lines = payload["invoice"]["line_items"]
    assert lines[0]["discount"] == 21.51
    assert lines[1]["discount"] == 1.46  # do not move the residual into shipping
    assert result == {"salla_total": 161.11, "qoyod_predicted_total": 161.12, "difference": 0.01}
    # Independent decimal document arithmetic after supported 2dp normalization.
    net = Decimal("139") - Decimal("21.51") + Decimal("24.07") - Decimal("1.46")
    assert net + (net * Decimal("0.15")).quantize(Decimal("0.01")) == Decimal("161.12")


@pytest.mark.asyncio
async def test_real_sender_posts_only_normalized_invoice_and_true_collected_payment_once():
    db = AsyncMongoMockClient().db
    await _seed_settings(db)
    await _seed_credentials(db)
    await db.qoyod_settings.update_one({"user_id": "main"}, {"$set": {"default_shipping_product_id": 2}})
    await db.integration_inbox.insert_one(case_canon())

    async def invoice(payload, *, idem):
        lines = payload["invoice"]["line_items"]
        assert lines[0]["discount"] == 21.51
        assert lines[1]["discount"] == 1.46
        for line in lines:
            assert line["tax_percent"] == 15
            for key in ("unit_price", "discount"):
                value = Decimal(str(line[key]))
                assert value == value.quantize(Decimal("0.01"))
        return {"invoice": {"id": 7001, "reference": "900000777", "total": 161.12}}

    base = "integrations.qoyod_manual.client.ManualQoyodClient."
    with patch(base + "find_invoice_by_reference", AsyncMock(return_value=None)), \
         patch(base + "find_customers_by_phone", AsyncMock(return_value=[{"id": 33}])), \
         patch(base + "find_product_by_sku", AsyncMock(return_value={"id": 77, "sku": "synthetic-item"})), \
         patch(base + "create_invoice", AsyncMock(side_effect=invoice)) as create, \
         patch(base + "create_invoice_payment", AsyncMock(return_value={"invoice_payment": {"id": 8001}})) as pay:
        result = await manual_send_one(db, user_id="main", order_number="900000777")
        assert result["expected_total"] == 161.12
        assert result["salla_total"] == 161.11
        assert result["payment_amount"] == 161.11
        create.assert_awaited_once()
        pay.assert_awaited_once()
        with pytest.raises(ManualSendRefused):
            await manual_send_one(db, user_id="main", order_number="900000777")
        assert create.await_count == pay.await_count == 1


def test_larger_unrepresentable_difference_still_refuses():
    with pytest.raises(ManualSendRefused) as exc:
        _preflight_qoyod_invoice_payload({"invoice": {"line_items": [
            {"unit_price": 100, "discount": 0, "quantity": 1, "tax_percent": 15}
        ]}}, salla_total=100)
    assert exc.value.code == "qoyod_preflight_total_mismatch"
