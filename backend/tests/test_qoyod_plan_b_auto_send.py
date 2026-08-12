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
    assert "completed_trigger_required" in codes
    assert "trigger_once_required" in codes

    issues = auto_send.activation_issues(
        _ready_settings(),
        credentials_configured=True,
        canary_succeeded=True,
        salla_connected=False,
    )
    assert {issue["code"] for issue in issues} == {
        "salla_connection_required"
    }


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

    async def fake_eligible(db, order_number, *, orders_user_id):
        assert orders_user_id == "orders-user"
        return True

    monkeypatch.setattr(auto_send, "resync_single_order", fake_resync)
    monkeypatch.setattr(auto_send, "_still_qoyod_eligible", fake_eligible)

    exact, refresh = await auto_send._refresh_and_verify_salla_status(
        object(),
        orders_user_id="orders-user",
        order_number="273000001",
    )

    assert exact is True
    assert refresh["plan_b_status_snapshot"]["status_native"] == "تم التنفيذ"
    assert calls == [("orders-user", "273000001")]


@pytest.mark.parametrize(
    ("slug", "native"),
    [
        ("completed", "تم التنفيذ"),
        ("in_delivery", "جاري التوصيل"),
        ("shipping", "جاري التوصيل"),
        ("delivering", "جاري التوصيل"),
        ("delivered", "تم التوصيل"),
        # Store custom label: safe only with the trusted completed slug.
        ("completed", "تم التجهيز"),
        # Old snapshots can carry the native label in the slug field too.
        ("تم التنفيذ", "تم التنفيذ"),
        ("جاري_التوصيل", "جاري التوصيل"),
        ("تم التوصيل", "تم التوصيل"),
        # Legacy snapshots without a slug retain the exact three aliases.
        ("", "completed"),
        ("", "جاري التوصيل"),
        ("", "shipping"),
        ("", "تم التوصيل"),
    ],
)
def test_live_status_gate_accepts_only_the_three_eligible_states(slug, native):
    assert auto_send._live_salla_status_is_eligible({
        "order_status": slug,
        "order_status_native": native,
    }) is True


@pytest.mark.parametrize(
    ("slug", "native"),
    [
        ("processing", "تم التجهيز"),
        ("delivering", "ملغي"),
        # `shipped` is deliberately outside the three-status policy.
        ("shipped", "تم الشحن"),
        ("", "تم التجهيز"),
        ("cancelled", "ملغي"),
        ("refunded", "مسترجع"),
        # Conflicting native evidence must not be hidden by an eligible slug.
        ("completed", "ملغي"),
        ("delivered", "مسترجع"),
        ("processing", "تم التنفيذ"),
    ],
)
def test_live_status_gate_rejects_untrusted_or_cancelled_states(slug, native):
    assert auto_send._live_salla_status_is_eligible({
        "order_status": slug,
        "order_status_native": native,
    }) is False


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


class _AutoRunsCollection:
    def __init__(self):
        self.inserted = []
        self.updated = []

    async def insert_one(self, document):
        self.inserted.append(document)

    async def update_one(self, selector, update):
        self.updated.append((selector, update))
        return _UpdateResult()


class _AsyncCursor:
    def __init__(self, rows):
        self._rows = list(rows)

    def __aiter__(self):
        self._iterator = iter(self._rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _QuarantinesCollection:
    def __init__(self):
        self.rows = {}
        self.updated = []

    def find(self, selector, projection):
        wanted = set(selector["order_number"]["$in"])
        rows = [
            row for row in self.rows.values()
            if row["user_id"] == selector["user_id"]
            and row["order_number"] in wanted
            and row["status"] == selector["status"]
        ]
        return _AsyncCursor(rows)

    async def update_one(self, selector, update, upsert=False):
        self.updated.append((selector, update, upsert))
        row = self.rows.setdefault(selector["_id"], {"_id": selector["_id"]})
        for key, value in update.get("$setOnInsert", {}).items():
            row.setdefault(key, value)
        row.update(update.get("$set", {}))
        for key, value in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + value
        return _UpdateResult()


class _RunDb:
    def __init__(self):
        self.qoyod_manual_auto_runs = _AutoRunsCollection()
        self.qoyod_manual_auto_quarantines = _QuarantinesCollection()


def _candidate(order_number):
    return {
        "order_number": order_number,
        "salla_status": "تم التنفيذ",
    }


async def _prepare_run(monkeypatch, *, candidates, send_one, trip_breaker):
    async def fake_settings(db):
        return _ready_settings()

    async def fake_acquire(db):
        return "lease-test"

    async def fake_release(db, owner):
        assert owner == "lease-test"

    async def fake_pending(db, **kwargs):
        assert kwargs["status"] == "completed"
        return {"ok": True, "orders": candidates}

    async def fake_refresh(db, *, orders_user_id, order_number):
        assert orders_user_id == "orders-user"
        return True, {
            "ok": True,
            "found": True,
            "plan_b_status_snapshot": {"status_native": "تم التنفيذ"},
        }

    monkeypatch.setattr(auto_send, "_current_settings", fake_settings)
    monkeypatch.setattr(auto_send, "_acquire_lease", fake_acquire)
    monkeypatch.setattr(auto_send, "_release_lease", fake_release)
    monkeypatch.setattr(auto_send, "list_pending_orders", fake_pending)
    monkeypatch.setattr(
        auto_send, "_refresh_and_verify_salla_status", fake_refresh
    )
    monkeypatch.setattr(auto_send, "manual_send_one", send_one)
    monkeypatch.setattr(auto_send, "_trip_circuit_breaker", trip_breaker)


@pytest.mark.asyncio
async def test_actual_total_mismatch_isolated_and_next_candidate_sends(
    monkeypatch,
):
    calls = []
    breaker_calls = []

    async def fake_send(
        db, *, user_id, orders_user_id, order_number, actor,
    ):
        calls.append(order_number)
        assert user_id == "main"
        assert orders_user_id == "orders-user"
        assert actor.startswith("auto-plan-b:qoyod-auto-")
        if order_number == "273811870":
            raise ManualSendRefused(
                "qoyod_actual_total_mismatch",
                "إجمالي قيود الفعلي يختلف عن إجمالي سلة",
                {
                    "invoice_id": 900,
                    "difference": 0.02,
                    "payment_created": False,
                    "requires_manual_review": True,
                },
            )
        return {
            "invoice_only": False,
            "invoice_id": 901,
            "payment_id": 902,
        }

    async def fake_trip(db, **kwargs):
        breaker_calls.append(kwargs)

    await _prepare_run(
        monkeypatch,
        candidates=[_candidate("273811870"), _candidate("273811871")],
        send_one=fake_send,
        trip_breaker=fake_trip,
    )

    db = _RunDb()
    result = await auto_send.run_once(db, batch_limit=5)

    assert calls == ["273811870", "273811871"]
    assert breaker_calls == []
    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert result["sent_count"] == 1
    assert result["manual_review_count"] == 1
    assert result["results"][0]["outcome"] == "manual_review"
    assert result["results"][0]["detail"]["invoice_id"] == 900
    assert result["results"][1]["outcome"] == "sent"
    assert db.qoyod_manual_auto_runs.updated[0][1]["$set"]["status"] == "succeeded"
    quarantine = db.qoyod_manual_auto_quarantines.rows["main:273811870"]
    assert quarantine["status"] == "open"
    assert quarantine["code"] == "qoyod_actual_total_mismatch"
    assert quarantine["attempt_count"] == 1


@pytest.mark.asyncio
async def test_preflight_mismatch_is_quarantined_before_later_order_sends(
    monkeypatch,
):
    calls = []

    async def fake_send(
        db, *, user_id, orders_user_id, order_number, actor,
    ):
        calls.append(order_number)
        if order_number == "273809026":
            raise ManualSendRefused(
                "qoyod_preflight_total_mismatch",
                "عُزل الطلب قبل إنشاء الفاتورة.",
                {
                    "difference": 0.02,
                    "qoyod_write_performed": False,
                    "requires_manual_review": True,
                },
            )
        return {"invoice_id": 901, "payment_id": 902}

    async def fake_trip(db, **kwargs):
        raise AssertionError("order-local mismatch must not trip breaker")

    await _prepare_run(
        monkeypatch,
        candidates=[_candidate("273809026"), _candidate("273809027")],
        send_one=fake_send,
        trip_breaker=fake_trip,
    )

    db = _RunDb()
    result = await auto_send.run_once(db, batch_limit=5)

    assert calls == ["273809026", "273809027"]
    assert result["status"] == "succeeded"
    assert result["sent_count"] == 1
    assert result["manual_review_count"] == 1
    quarantine = db.qoyod_manual_auto_quarantines.rows["main:273809026"]
    assert quarantine["code"] == "qoyod_preflight_total_mismatch"
    assert quarantine["detail"]["qoyod_write_performed"] is False


@pytest.mark.asyncio
async def test_open_quarantine_is_skipped_without_consuming_batch_slot(
    monkeypatch,
):
    calls = []

    async def fake_send(
        db, *, user_id, orders_user_id, order_number, actor,
    ):
        calls.append(order_number)
        return {"invoice_id": 901, "payment_id": 902}

    async def fake_trip(db, **kwargs):
        raise AssertionError("breaker must not run")

    await _prepare_run(
        monkeypatch,
        candidates=[
            _candidate("273809026"),
            _candidate("273809027"),
        ],
        send_one=fake_send,
        trip_breaker=fake_trip,
    )
    db = _RunDb()
    db.qoyod_manual_auto_quarantines.rows["main:273809026"] = {
        "_id": "main:273809026",
        "user_id": "main",
        "order_number": "273809026",
        "status": "open",
    }

    result = await auto_send.run_once(db, batch_limit=1)

    assert calls == ["273809027"]
    assert result["sent_count"] == 1


@pytest.mark.asyncio
async def test_systemic_error_still_trips_breaker_and_stops_batch(monkeypatch):
    calls = []
    breaker_calls = []

    async def fake_send(
        db, *, user_id, orders_user_id, order_number, actor,
    ):
        calls.append(order_number)
        raise ManualSendRefused(
            "qoyod_http_error",
            "تعذر الاتصال بقيود",
            {"status_code": 503},
        )

    async def fake_trip(db, **kwargs):
        breaker_calls.append(kwargs)

    await _prepare_run(
        monkeypatch,
        candidates=[_candidate("273811870"), _candidate("273811871")],
        send_one=fake_send,
        trip_breaker=fake_trip,
    )

    result = await auto_send.run_once(_RunDb(), batch_limit=5)

    assert calls == ["273811870"]
    assert len(breaker_calls) == 1
    assert breaker_calls[0]["code"] == "qoyod_http_error"
    assert result["ok"] is False
    assert result["status"] == "stopped_on_error"
    assert result["sent_count"] == 0
    assert result["manual_review_count"] == 0


@pytest.mark.asyncio
async def test_salla_refresh_failure_retries_later_and_next_candidate_sends(
    monkeypatch,
):
    sent = []
    breaker_calls = []

    async def fake_send(
        db, *, user_id, orders_user_id, order_number, actor,
    ):
        sent.append(order_number)
        return {"invoice_id": 901, "payment_id": 902}

    async def fake_trip(db, **kwargs):
        breaker_calls.append(kwargs)

    await _prepare_run(
        monkeypatch,
        candidates=[_candidate("276776919"), _candidate("276565610")],
        send_one=fake_send,
        trip_breaker=fake_trip,
    )

    async def fake_refresh(db, *, orders_user_id, order_number):
        if order_number == "276776919":
            raise ManualSendRefused(
                "salla_status_refresh_failed",
                "تعذر التحقق من الحالة الحالية للطلب في سلة",
                {
                    "stage": "fetch_order_details",
                    "needs_reauth": False,
                },
            )
        return True, {
            "ok": True,
            "found": True,
            "plan_b_status_snapshot": {"status_native": "تم التنفيذ"},
        }

    monkeypatch.setattr(
        auto_send, "_refresh_and_verify_salla_status", fake_refresh
    )

    result = await auto_send.run_once(_RunDb(), batch_limit=5)

    assert breaker_calls == []
    assert sent == ["276565610"]
    assert result["ok"] is True
    assert result["status"] == "succeeded"
    assert result["sent_count"] == 1
    assert result["retry_later_count"] == 1
    assert result["results"][0]["outcome"] == "retry_later"
    assert result["results"][0]["order_number"] == "276776919"
    assert result["results"][1]["outcome"] == "sent"
