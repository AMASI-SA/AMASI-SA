"""Iter-001k+ — Canary Readiness Preview (Read-Only) tests."""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod.canary_readiness import (   # noqa: E402
    build_canary_readiness_preview,
)
from integrations.qoyod.dry_rca_report import (     # noqa: E402
    GatesNotFailClosedError,
)
from tests.test_dry_rca_report import (             # noqa: E402
    _FakeDB, _fail_closed_settings,
)


def _canary_inbox_row():
    """Mirror of Production 269629400 canary shape."""
    return {
        "user_id":               "main",
        "salla_order_number":    "269629400",
        "existing_qoyod_invoice_id": "DRY:invoice:4356d383",
        "qoyod_customer_id":     "DRY:contact:78087d36",
        "canonical_payload": {
            "order_number":  "269629400",
            "order_id":      "269629400",
            "payment_method": "tabby_installment",
            "status":        "completed",
            "subtotal":       80.00,
            "shipping_amount": 13.05,
            "tax_amount":       0.00,
            "discount_amount":  0.00,
            "total_amount":   100.00,
            "customer": {
                "mobile":  "+966557951913",
                "name":    "سوزان عوض الله",
                "email":   None,
                "city":    "الرياض",
                "country": "SA",
            },
            "items": [{
                "sku":              "AMS11237",
                "name":             "Product X",
                "quantity":         1,
                "unit_price":       80.00,
                "discount_amount":  0.0,
                "tax_amount":       6.95,
                "total":            86.95,
                "qoyod_product_id": "DRY:product:cccc",
            }],
        },
    }


def _canary_db(**overrides):
    return _FakeDB(
        qoyod_settings=_fail_closed_settings(),
        integration_inbox=[_canary_inbox_row()],
        qoyod_customers_mapping=overrides.get(
            "customers_mapping", []),
        qoyod_external_customers=overrides.get(
            "external_customers", []),
        qoyod_products_mapping=overrides.get(
            "products_mapping",
            [{"user_id": "main", "sku": "AMS11237",
              "qoyod_product_id": 45,
              "dry_run_only": False}]),
        qoyod_external_products=overrides.get(
            "external_products", []))


# ── Gate refusal ────────────────────────────────────────────────────
class TestGateGuard:

    @pytest.mark.asyncio
    async def test_refuses_when_gate_open(self):
        from tests.test_dry_rca_report import _FakeDB
        db = _FakeDB(
            qoyod_settings=[{"user_id": "main",
                             "selective_live_send_enabled": True,
                             "production_writes_locked":    True}],
            integration_inbox=[_canary_inbox_row()],
            qoyod_customers_mapping=[],
            qoyod_external_customers=[],
            qoyod_products_mapping=[],
            qoyod_external_products=[])
        with pytest.raises(GatesNotFailClosedError):
            await build_canary_readiness_preview(
                db, user_id="main", order_number="269629400")


# ── Product resolution ─────────────────────────────────────────────
class TestProductResolution:

    @pytest.mark.asyncio
    async def test_product_mapping_used_no_db_write_needed(self):
        db = _canary_db()
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        prods = r["products_resolution"]
        assert len(prods) == 1
        p = prods[0]
        assert p["sku"] == "AMS11237"
        assert p["is_current_dry"] is True
        assert p["resolved_from"] == "qoyod_products_mapping"
        assert p["resolved_qoyod_product_id"] == 45
        assert p["needs_db_write"] is False

    @pytest.mark.asyncio
    async def test_external_only_needs_db_write_adopt(self):
        db = _canary_db(
            products_mapping=[],
            external_products=[
                {"user_id": "main", "sku": "AMS11237",
                 "qoyod_product_id": 45, "name": "Product X"}])
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        p = r["products_resolution"][0]
        assert p["resolved_from"] == "qoyod_external_products"
        assert p["resolved_qoyod_product_id"] == 45
        assert p["needs_db_write"] is True


# ── Customer payload preview ───────────────────────────────────────
class TestCustomerPayloadPreview:

    @pytest.mark.asyncio
    async def test_payload_uses_contact_wrapper(self):
        db = _canary_db()
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        payload = r["customer_preview_payload"]
        # Iter-267 contract — Rails strong_params requires `contact`.
        assert "contact" in payload
        assert "customer" not in payload

    @pytest.mark.asyncio
    async def test_payload_includes_name_and_contact_name(self):
        db = _canary_db()
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        contact = r["customer_preview_payload"]["contact"]
        assert contact["name"] == "سوزان عوض الله"
        assert contact["contact_name"] == "سوزان عوض الله"
        assert contact["phone_number"] == "+966557951913"

    @pytest.mark.asyncio
    async def test_optional_fields_present_when_available(self):
        db = _canary_db()
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        contact = r["customer_preview_payload"]["contact"]
        assert contact["city"] == "الرياض"
        assert contact["country"] == "SA"

    @pytest.mark.asyncio
    async def test_required_field_status_flags_missing_email(self):
        db = _canary_db()
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        fs = r["customer_required_field_status"]
        assert fs["name_present"] is True
        assert fs["phone_present"] is True
        assert fs["email_present"] is False
        assert fs["used_guest_fallback"] is False


# ── Post-simulation state ──────────────────────────────────────────
class TestPostSimulation:

    @pytest.mark.asyncio
    async def test_gate_and_write_lock_remain_after_customer_create(
            self):
        db = _canary_db()
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        post = r["post_simulation"]
        # gate_disabled and write_lock_active are ALWAYS in the
        # remaining list — the operator flips them explicitly.
        assert "gate_disabled" in post["remaining_blockers"]
        assert "write_lock_active" in post["remaining_blockers"]

    @pytest.mark.asyncio
    async def test_bank_transfer_still_blocks_after_customer(self):
        row = _canary_inbox_row()
        row["canonical_payload"]["payment_method"] = "bank_transfer"
        db = _FakeDB(
            qoyod_settings=_fail_closed_settings(),
            integration_inbox=[row],
            qoyod_customers_mapping=[],
            qoyod_external_customers=[],
            qoyod_products_mapping=[
                {"user_id": "main", "sku": "AMS11237",
                 "qoyod_product_id": 45, "dry_run_only": False}],
            qoyod_external_products=[])
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        assert "bank_transfer_on_hold_iter_294" \
            in r["post_simulation"]["remaining_blockers"]

    @pytest.mark.asyncio
    async def test_missing_product_still_blocks_after_simulation(self):
        db = _canary_db(products_mapping=[])   # AMS11237 unresolved
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        remaining = r["post_simulation"]["remaining_blockers"]
        assert any(
            b.startswith("product_still_missing_after_adopt")
            for b in remaining)


# ── DRY invoice treatment ──────────────────────────────────────────
class TestDryInvoiceTreatment:

    @pytest.mark.asyncio
    async def test_dry_sentinel_marked_safe(self):
        db = _canary_db()
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        note = r["post_simulation"]["dry_invoice_treatment"]
        assert "sentinel" in note.lower()
        assert "new real invoice" in note.lower()

    @pytest.mark.asyncio
    async def test_real_existing_invoice_marked_refused(self):
        row = _canary_inbox_row()
        row["canonical_payload"]["existing_qoyod_invoice_id"] = 999999
        row["existing_qoyod_invoice_id"] = 999999
        db = _FakeDB(
            qoyod_settings=_fail_closed_settings(),
            integration_inbox=[row],
            qoyod_customers_mapping=[],
            qoyod_external_customers=[],
            qoyod_products_mapping=[
                {"user_id": "main", "sku": "AMS11237",
                 "qoyod_product_id": 45, "dry_run_only": False}],
            qoyod_external_products=[])
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        note = r["post_simulation"]["dry_invoice_treatment"]
        assert "already_sent" in note.lower() or \
               "refused" in note.lower()


# ── send_date contract ─────────────────────────────────────────────
class TestSendDateContract:

    @pytest.mark.asyncio
    async def test_all_dates_use_send_date(self):
        db = _canary_db()
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        sdd = r["send_date_diagnostic"]
        assert sdd["payload_date_source"] == "send_date"
        assert sdd["invoice_date_will_use"] == "send_date_riyadh"
        assert sdd["payment_date_will_use"] == "send_date_riyadh"
        assert sdd["due_date_will_use"] == "send_date_riyadh"
        assert sdd["salla_order_created_at_ignored"] is True


# ── gates_snapshot bug #1 fix regression ───────────────────────────
class TestGatesSnapshotComplete:

    @pytest.mark.asyncio
    async def test_gates_snapshot_carries_all_gates(self):
        db = _canary_db()
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="269629400")
        gs = r["gates_snapshot"]
        for k in ("selective_live_send_enabled",
                  "production_writes_locked",
                  "qoyod_sync_start_date",
                  "qoyod_tax_period",
                  "bank_transfer_routing_enabled",
                  "qoyod_invoice_date_source",
                  "qoyod_enabled_invoice_trigger_statuses"):
            assert k in gs, f"missing gate: {k}"


# ── Read-Only invariants ────────────────────────────────────────────
class TestReadOnlyInvariants:

    def test_module_has_no_qoyod_api_client(self):
        import integrations.qoyod.canary_readiness as mod
        src = open(mod.__file__, encoding="utf-8").read()
        assert "QoyodAPIClient" not in src
        assert "httpx" not in src
        assert "requests." not in src

    def test_module_has_no_db_writes(self):
        import integrations.qoyod.canary_readiness as mod
        src = open(mod.__file__, encoding="utf-8").read()
        for banned in ("insert_one", "update_one", "delete_one",
                       "$set", "$unset", "insert_many",
                       "update_many", ".adopt(", ".create_"):
            assert banned not in src

    def test_response_carries_read_only_markers(self):
        import asyncio
        db = _canary_db()
        r = asyncio.run(build_canary_readiness_preview(
            db, user_id="main", order_number="269629400"))
        assert r["read_only"] is True
        assert r["no_qoyod_api_calls"] is True
        assert r["no_db_writes"] is True


# ── Not-found path ─────────────────────────────────────────────────
class TestNotFound:

    @pytest.mark.asyncio
    async def test_returns_stub_without_error(self):
        db = _FakeDB(
            qoyod_settings=_fail_closed_settings(),
            integration_inbox=[],
            qoyod_customers_mapping=[],
            qoyod_external_customers=[],
            qoyod_products_mapping=[],
            qoyod_external_products=[])
        r = await build_canary_readiness_preview(
            db, user_id="main", order_number="999")
        assert r["found"] is False
        assert r["read_only"] is True
