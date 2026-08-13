import pytest

from integrations.qoyod.unsent_orders import (
    FAILED,
    SENT,
    UNSENT,
    _overlay_manual_failure,
)
from integrations.qoyod_manual.failed_retry import retry_failed_order
from integrations.qoyod_manual.send import ManualSendRefused


class _UpdateResult:
    modified_count = 1


class _QuarantinesCollection:
    def __init__(self, order_number):
        self.row = {
            "_id": f"main:{order_number}",
            "user_id": "main",
            "order_number": order_number,
            "status": "open",
        }
        self.updates = []

    async def update_one(self, selector, update):
        self.updates.append((selector, update))
        if (
            selector.get("_id") == self.row["_id"]
            and selector.get("user_id") == self.row["user_id"]
            and selector.get("status") == self.row["status"]
        ):
            self.row.update(update.get("$set", {}))
            for key, value in update.get("$inc", {}).items():
                self.row[key] = self.row.get(key, 0) + value
        return _UpdateResult()


class _Db:
    def __init__(self, order_number):
        self.qoyod_manual_auto_quarantines = _QuarantinesCollection(
            order_number
        )


def _eligible_refresh():
    async def refresh(db, *, orders_user_id, order_number):
        assert orders_user_id == "orders-user"
        return True, {
            "ok": True,
            "found": True,
            "plan_b_status_snapshot": {
                "status_slug": "completed",
                "status_native": "تم التنفيذ",
            },
        }

    return refresh


@pytest.mark.asyncio
async def test_failed_retry_refreshes_salla_then_uses_guarded_sender():
    order_number = "278000001"
    db = _Db(order_number)
    calls = []

    async def send(db, **kwargs):
        calls.append(kwargs)
        return {"invoice_id": "501", "payment_id": "601"}

    result = await retry_failed_order(
        db,
        orders_user_id="orders-user",
        order_number=order_number,
        actor="failed-retry-ui:test@example.com",
        refresh_fn=_eligible_refresh(),
        send_fn=send,
    )

    assert result["retry_outcome"] == "sent"
    assert result["invoice_id"] == "501"
    assert calls[0]["user_id"] == "main"
    assert calls[0]["order_number"] == order_number
    assert calls[0]["allow_missing_salla_order_date"] is True
    assert calls[0]["allow_historical_positive_total"] is True
    assert db.qoyod_manual_auto_quarantines.row["status"] == "resolved"
    assert (
        db.qoyod_manual_auto_quarantines.row["resolution"]
        == "manual_retry_succeeded"
    )
    assert (
        db.qoyod_manual_auto_quarantines.row["manual_retry_attempt_count"]
        == 1
    )


@pytest.mark.asyncio
async def test_failed_retry_refuses_before_qoyod_when_live_status_changed():
    order_number = "278000002"
    db = _Db(order_number)
    send_called = False

    async def refresh(db, *, orders_user_id, order_number):
        return False, {
            "ok": True,
            "found": True,
            "plan_b_status_snapshot": {
                "status_slug": "cancelled",
                "status_native": "ملغي",
            },
        }

    async def send(db, **kwargs):
        nonlocal send_called
        send_called = True
        return {}

    with pytest.raises(ManualSendRefused) as exc_info:
        await retry_failed_order(
            db,
            orders_user_id="orders-user",
            order_number=order_number,
            actor="failed-retry-ui:test@example.com",
            refresh_fn=refresh,
            send_fn=send,
        )

    assert exc_info.value.code == "not_qoyod_eligible_status"
    assert exc_info.value.extra["current_status"] == "ملغي"
    assert send_called is False
    quarantine = db.qoyod_manual_auto_quarantines.row
    assert quarantine["status"] == "open"
    assert (
        quarantine["last_manual_retry_error"]["code"]
        == "not_qoyod_eligible_status"
    )


@pytest.mark.asyncio
async def test_failed_retry_treats_existing_qoyod_invoice_as_safe_success():
    order_number = "278000003"
    db = _Db(order_number)

    async def send(db, **kwargs):
        raise ManualSendRefused(
            "duplicate_invoice_in_qoyod",
            "يوجد فاتورة قيود مسبقة بنفس رقم المرجع",
            {"qoyod_invoice_id": "777"},
        )

    result = await retry_failed_order(
        db,
        orders_user_id="orders-user",
        order_number=order_number,
        actor="failed-retry-ui:test@example.com",
        refresh_fn=_eligible_refresh(),
        send_fn=send,
    )

    assert result["retry_outcome"] == "already_sent"
    assert result["invoice_id"] == "777"
    quarantine = db.qoyod_manual_auto_quarantines.row
    assert quarantine["status"] == "resolved"
    assert quarantine["resolution"] == "already_sent_verified"


@pytest.mark.asyncio
async def test_failed_retry_keeps_quarantine_open_on_guard_refusal():
    order_number = "278000004"
    db = _Db(order_number)

    async def send(db, **kwargs):
        raise ManualSendRefused(
            "qoyod_preflight_total_mismatch",
            "فرق مبلغ أكبر من 0.01 ريال",
            {"difference": 0.02},
        )

    with pytest.raises(ManualSendRefused) as exc_info:
        await retry_failed_order(
            db,
            orders_user_id="orders-user",
            order_number=order_number,
            actor="failed-retry-ui:test@example.com",
            refresh_fn=_eligible_refresh(),
            send_fn=send,
        )

    assert exc_info.value.code == "qoyod_preflight_total_mismatch"
    quarantine = db.qoyod_manual_auto_quarantines.row
    assert quarantine["status"] == "open"
    assert (
        quarantine["last_manual_retry_error"]["code"]
        == "qoyod_preflight_total_mismatch"
    )


def test_failure_overlay_marks_unsent_as_retryable_failure():
    result = _overlay_manual_failure(
        {"status": UNSENT, "reason": "بانتظار الإرسال إلى قيود"},
        {
            "source": "auto_quarantine",
            "code": "qoyod_preflight_total_mismatch",
            "message": "فرق مبلغ أكبر من 0.01 ريال",
        },
    )

    assert result["status"] == FAILED
    assert result["retry_allowed"] is True
    assert result["failure_source"] == "auto_quarantine"
    assert result["failure_code"] == "qoyod_preflight_total_mismatch"


def test_failure_overlay_never_overrides_a_sent_invoice():
    original = {"status": SENT, "reason": "فاتورة قيود #123"}
    assert _overlay_manual_failure(
        original,
        {
            "source": "manual_send_lock",
            "code": "qoyod_http_error",
            "message": "خطأ قديم",
        },
    ) is original
