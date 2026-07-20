import pytest

from integrations.qoyod_manual import auto_send
from integrations.qoyod_manual.send import ManualSendRefused


def _ready_settings(**overrides):
    value = {
        "enabled": True,
        "auto_send": True,
        "auto_receipt": True,
        "dry_run_mode": False,
        "legacy_pipeline_frozen": True,
        "invoice_trigger_statuses": ["completed"],
        "trigger_once_only": True,
        "plan_b_auto_send_armed_at": "2026-07-20T10:00:00+00:00",
        "plan_b_auto_send_orders_user_id": "orders-user",
        "capabilities": {
            "create_customers": True,
            "create_products": True,
            "create_invoices": True,
            "create_receipts": True,
        },
    }
    value.update(overrides)
    return value


def test_live_sender_requires_the_existing_safe_settings_contract():
    assert auto_send.is_armed(_ready_settings()) is True

    issues = auto_send.activation_issues(
        _ready_settings(
            invoice_trigger_statuses=["completed", "delivered"],
            trigger_once_only=False,
        ),
        credentials_configured=True,
        canary_succeeded=True,
    )
    codes = {issue["code"] for issue in issues}
    assert "completed_only_required" in codes
    assert "trigger_once_required" in codes


@pytest.mark.asyncio
async def test_live_sender_refreshes_salla_before_accepting_status(monkeypatch):
    calls = []

    async def fake_resync(db, user_id, order_number):
        calls.append((user_id, order_number))
        return {
            "ok": True,
            "found": True,
            "plan_b_status_snapshot": {
                "status_native": "تم التنفيذ",
            },
        }

    async def fake_exact(db, order_number, *, orders_user_id):
        assert orders_user_id == "orders-user"
        return True

    monkeypatch.setattr(auto_send, "resync_single_order", fake_resync)
    monkeypatch.setattr(auto_send, "_still_exactly_completed", fake_exact)

    exact, refresh = await auto_send._refresh_and_verify_salla_status(
        object(),
        orders_user_id="orders-user",
        order_number="273000001",
    )

    assert exact is True
    assert refresh["plan_b_status_snapshot"]["status_native"] == "تم التنفيذ"
    assert calls == [("orders-user", "273000001")]


@pytest.mark.asyncio
async def test_salla_refresh_failure_refuses_before_qoyod(monkeypatch):
    async def fake_resync(db, user_id, order_number):
        return {
            "ok": False,
            "found": False,
            "error": "Salla API 401",
            "stage": "fetch_order_details",
            "needs_reauth": True,
        }

    monkeypatch.setattr(auto_send, "resync_single_order", fake_resync)

    with pytest.raises(ManualSendRefused) as exc_info:
        await auto_send._refresh_and_verify_salla_status(
            object(),
            orders_user_id="orders-user",
            order_number="273000002",
        )

    assert exc_info.value.code == "salla_status_refresh_failed"
    assert exc_info.value.extra["needs_reauth"] is True


class _UpdateResult:
    modified_count = 1


class _SettingsCollection:
    def __init__(self):
        self.selector = None
        self.update = None

    async def update_one(self, selector, update):
        self.selector = selector
        self.update = update
        return _UpdateResult()


class _FakeDb:
    def __init__(self):
        self.qoyod_settings = _SettingsCollection()


@pytest.mark.asyncio
async def test_circuit_breaker_disables_automatic_sending():
    db = _FakeDb()
    await auto_send._trip_circuit_breaker(
        db,
        code="qoyod_timeout",
        message="تعذر الاتصال بقيود",
        run_id="run-1",
    )

    persisted = db.qoyod_settings.update["$set"]
    assert persisted["auto_send"] is False
    assert persisted["plan_b_auto_send_armed_at"] is None
    assert persisted["plan_b_auto_send_disabled_reason"] == "circuit_breaker"
    assert persisted["plan_b_auto_send_last_error"]["run_id"] == "run-1"
