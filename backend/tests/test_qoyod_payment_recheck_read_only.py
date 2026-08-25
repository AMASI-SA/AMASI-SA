import pytest

from integrations.qoyod_manual.payment_recheck import (
    recheck_payment_batch_read_only,
    recheck_payment_read_only,
)


class _NoWriteDb:
    """Any attempted collection access fails the test immediately."""

    def __getattr__(self, name):
        raise AssertionError(f"read-only recheck touched database collection: {name}")


def _fetch(order):
    async def fetch(db, user_id, order_number):
        assert user_id == "orders-user"
        assert isinstance(db, _NoWriteDb)
        return {
            "ok": True,
            "found": True,
            "order": {"order_number": order_number, **order},
        }
    return fetch


@pytest.mark.asyncio
async def test_read_only_recheck_reports_ready_without_database_or_qoyod_write():
    result = await recheck_payment_read_only(
        _NoWriteDb(),
        orders_user_id="orders-user",
        order_number="278100001",
        fetch_fn=_fetch({
            "order_status_slug": "completed",
            "order_status": "تم التنفيذ",
            "payment_method": "mada",
            "is_pending_payment": False,
            "total_amount": 100,
        }),
    )

    assert result["outcome"] == "ready"
    assert result["read_only"] is True
    assert result["invoice_sent"] is False


@pytest.mark.asyncio
async def test_read_only_recheck_fails_closed_on_unpaid_evidence():
    result = await recheck_payment_read_only(
        _NoWriteDb(),
        orders_user_id="orders-user",
        order_number="278100002",
        fetch_fn=_fetch({
            "order_status_slug": "delivered",
            "order_status": "تم التوصيل",
            "payment_method": "mada",
            "is_pending_payment": True,
            "remaining_amount": 100,
            "total_amount": 100,
        }),
    )

    assert result["outcome"] == "unpaid"
    assert result["payment_eligibility"] == "ineligible"


@pytest.mark.asyncio
async def test_batch_is_bounded_by_route_and_returns_ephemeral_counts():
    result = await recheck_payment_batch_read_only(
        _NoWriteDb(),
        orders_user_id="orders-user",
        order_numbers=["278100003", "278100004"],
        fetch_fn=_fetch({
            "order_status_slug": "completed",
            "order_status": "تم التنفيذ",
            "payment_method": "mada",
            "is_pending_payment": False,
        }),
    )

    assert result["total"] == 2
    assert result["counts"] == {"ready": 2}
    assert result["invoice_sent_count"] == 0
    assert result["read_only"] is True


@pytest.mark.asyncio
async def test_ready_payment_is_held_when_qoyod_preflight_total_is_zero():
    async def preflight(*_args, **_kwargs):
        return {"ok": True, "diagnosis_status": "pass", "salla_total": 0}

    result = await recheck_payment_read_only(
        _NoWriteDb(),
        orders_user_id="orders-user",
        qoyod_user_id="main",
        order_number="278100005",
        fetch_fn=_fetch({
            "order_status_slug": "completed",
            "order_status": "تم التنفيذ",
            "payment_method": "mada",
            "is_pending_payment": False,
        }),
        preflight_fn=preflight,
    )
    assert result["outcome"] == "review"
    assert result["code"] == "zero_total_refused"
    assert result["invoice_sent"] is False


@pytest.mark.asyncio
async def test_ready_payment_is_held_when_qoyod_preflight_is_blocked():
    async def preflight(*_args, **_kwargs):
        return {
            "ok": True,
            "diagnosis_status": "blocked",
            "code": "qoyod_preflight_total_mismatch",
            "message": "إجمالي قيود المتوقع يختلف عن إجمالي سلة",
            "salla_total": 117.34,
        }

    result = await recheck_payment_read_only(
        _NoWriteDb(),
        orders_user_id="orders-user",
        qoyod_user_id="main",
        order_number="278100006",
        fetch_fn=_fetch({
            "order_status_slug": "completed",
            "order_status": "تم التنفيذ",
            "payment_method": "mada",
            "is_pending_payment": False,
        }),
        preflight_fn=preflight,
    )
    assert result["outcome"] == "review"
    assert result["code"] == "qoyod_preflight_total_mismatch"
    assert result["invoice_sent"] is False
