"""Fresh provider eligibility with zero provider writes or canonical updates."""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from review_salla_pending_guard import require_pending_status, verify_salla_pending


@pytest.mark.parametrize("status", [
    {"slug": "under_review", "name": "بإنتظار المراجعة"},
    {"slug": "under_review", "customized": {"name": "بانتظار المراجعة"}},
])
def test_pending_allowed(status):
    require_pending_status(status)


@pytest.mark.parametrize("status", [
    {"slug": "delivered", "name": "تم التوصيل"},
    {"slug": "in_progress", "name": "قيد التنفيذ"},
    {"slug": "completed", "name": "تم التنفيذ"},
    {"slug": "canceled", "name": "ملغي"},
    {"slug": "under_review", "customized": {"name": "تم المراجعة"}},
    {"slug": "delivered", "customized": {"name": "بانتظار المراجعة"}},
    {}, None,
])
def test_non_pending_or_conflicting_status_rejected(status):
    with pytest.raises(HTTPException) as exc:
        require_pending_status(status)
    assert exc.value.status_code == 409
    assert exc.value.detail["salla_status"] in exc.value.detail["message"]


def invoke(call):
    return asyncio.run(verify_salla_pending(
        object(), user_id="owner", order_number="123",
        source={"raw_by_source": {"salla_direct": {"id": 456}}}, call_salla=call,
    ))


def test_single_get_allows_pending_without_any_other_provider_call():
    call = AsyncMock(return_value={"data": {
        "id": 456, "reference_id": 123, "status": {"slug": "under_review"},
    }})
    invoke(call)
    assert call.await_count == 1
    assert call.call_args.args[1:] == ("owner", "GET", "/orders/456")


def test_live_delivered_overrides_any_stale_local_eligibility():
    call = AsyncMock(return_value={"data": {
        "id": 456, "reference_id": 123, "status": {"slug": "delivered", "name": "تم التوصيل"},
    }})
    with pytest.raises(HTTPException) as exc:
        invoke(call)
    assert exc.value.status_code == 409
    assert exc.value.detail["salla_status"] == "تم التوصيل"
    assert call.await_count == 1


@pytest.mark.parametrize("response", [
    None, {}, {"data": {}},
    {"data": {"id": 457, "reference_id": 123, "status": {"slug": "under_review"}}},
    {"data": {"id": 456, "reference_id": 124, "status": {"slug": "under_review"}}},
])
def test_invalid_response_or_wrong_order_blocks_approval(response):
    with pytest.raises(HTTPException) as exc:
        invoke(AsyncMock(return_value=response))
    assert exc.value.status_code == 503


@pytest.mark.parametrize("error", [TimeoutError(), RuntimeError("provider failure")])
def test_transport_failure_blocks_approval_without_retry(error):
    call = AsyncMock(side_effect=error)
    with pytest.raises(HTTPException) as exc:
        invoke(call)
    assert exc.value.status_code == 503
    assert call.await_count == 1
