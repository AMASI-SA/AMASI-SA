"""The recovery entry cannot reach invoice/payment writes with stale money."""
from unittest.mock import AsyncMock, patch
import pytest
from test_qoyod_manual_send_plan_b import _inbox_row, _seed_settings
from mongomock_motor import AsyncMongoMockClient
from integrations.qoyod_manual.send import manual_send_one, ManualSendRefused


@pytest.mark.asyncio
async def test_recovery_rejects_stale_total_before_credentials_or_writes():
    db = AsyncMongoMockClient().db
    await _seed_settings(db)
    row = _inbox_row(order_number="900000111")
    await db.integration_inbox.insert_one(row)
    with patch("integrations.qoyod_manual.send.get_api_key", AsyncMock()) as credentials:
        with pytest.raises(ManualSendRefused) as exc:
            await manual_send_one(db, user_id="main", order_number="900000111",
                                  recovery_expected_total="403.11")
    assert exc.value.code == "recovery_live_facts_changed"
    credentials.assert_not_awaited()


@pytest.mark.asyncio
async def test_recovery_never_enters_existing_invoice_payment_retry():
    db = AsyncMongoMockClient().db
    await _seed_settings(db)
    row = _inbox_row(order_number="900000112")
    row["manual_qoyod_invoice_id"] = "existing-invoice"
    await db.integration_inbox.insert_one(row)
    with patch("integrations.qoyod_manual.send._retry_payment_only", AsyncMock()) as pay:
        with pytest.raises(ManualSendRefused) as exc:
            await manual_send_one(db, user_id="main", order_number="900000112",
                                  recovery_expected_total=str(row["canonical_payload"]["total_amount"]))
    assert exc.value.code == "recovery_live_facts_changed"
    pay.assert_not_awaited()
