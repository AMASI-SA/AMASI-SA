import pytest

from integrations.qoyod_manual.canary_batch import (
    CANARY_ORDER_NUMBERS,
    execute_canary_batch,
)
from integrations.qoyod_manual.send import ManualSendRefused


@pytest.mark.asyncio
async def test_canary_uses_only_the_approved_four_and_runs_sequentially():
    seen = []

    async def send_order(order_number):
        seen.append(order_number)
        return {
            "ok": True,
            "invoice_id": f"inv-{order_number}",
            "payment_id": None if order_number == "273274882" else "pay-1",
            "invoice_only": order_number == "273274882",
            "difference": 0,
        }

    result = await execute_canary_batch(send_order)

    assert seen == list(CANARY_ORDER_NUMBERS)
    assert result["ok"] is True
    assert result["sent_count"] == 4
    assert result["invoice_only_count"] == 1
    assert result["failed_count"] == 0


@pytest.mark.asyncio
async def test_canary_stops_before_touching_remaining_orders_on_real_error():
    seen = []

    async def send_order(order_number):
        seen.append(order_number)
        if order_number == CANARY_ORDER_NUMBERS[1]:
            raise ManualSendRefused("test_failure", "رفض اختباري")
        return {"ok": True, "invoice_id": "1", "difference": 0}

    result = await execute_canary_batch(send_order)

    assert seen == list(CANARY_ORDER_NUMBERS[:2])
    assert result["ok"] is False
    assert result["stopped_on"] == CANARY_ORDER_NUMBERS[1]
    assert result["remaining_count"] == 2
    assert result["failed_count"] == 1


@pytest.mark.asyncio
async def test_duplicate_guard_is_safe_and_does_not_create_again():
    async def send_order(order_number):
        if order_number == CANARY_ORDER_NUMBERS[0]:
            raise ManualSendRefused(
                "duplicate_invoice_in_qoyod",
                "موجود مسبقاً",
                {"qoyod_invoice_id": 99},
            )
        return {"ok": True, "invoice_id": "1", "difference": 0}

    result = await execute_canary_batch(send_order)

    assert result["ok"] is True
    assert result["already_sent_count"] == 1
    assert result["sent_count"] == 3
    assert result["results"][0]["outcome"] == "already_sent"
