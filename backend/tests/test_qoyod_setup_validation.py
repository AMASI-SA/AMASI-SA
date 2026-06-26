"""Tests for the Qoyod Settings final-setup validation module."""
from __future__ import annotations

import pytest
from datetime import datetime, timezone

from integrations.qoyod.setup_validation import (
    collect_used_payment_methods,
    validate_settings_for_setup,
    CANONICAL_PAYMENT_METHODS,
)


# ─── Mock async Mongo collection ───────────────────────────────────
class FakeCursor:
    def __init__(self, rows): self._rows = rows
    def __aiter__(self):
        async def gen():
            for r in self._rows:
                yield r
        return gen()


class FakeColl:
    def __init__(self, rows=None):
        self.rows = rows or []
    def find(self, *_args, **_kwargs):
        return FakeCursor(self.rows)
    async def find_one(self, query, projection=None):
        for r in self.rows:
            ok = True
            for k, v in query.items():
                if r.get(k) != v:
                    ok = False; break
            if ok:
                return r
        return None


class FakeDB:
    def __init__(self):
        self.qoyod_settings        = FakeColl()
        self.unified_orders        = FakeColl()
        self.integration_inbox     = FakeColl()


# ─────────────────────────────────────────────────────────────────


def test_canonical_catalogue_contains_all_user_required_methods():
    """The user spec lists these methods that MUST appear in the UI."""
    keys = {row["key"] for row in CANONICAL_PAYMENT_METHODS}
    required = {"mada", "apple_pay", "visa", "mastercard", "stc_pay",
                "bank_transfer", "tamara", "tabby", "emkan", "cod"}
    assert required.issubset(keys), (
        f"Missing canonical keys: {required - keys}")


@pytest.mark.asyncio
async def test_validate_blocks_missing_tax_but_branch_is_warning():
    """Branch ID is OPTIONAL (per user spec 2026-06-27: single-branch
    accounts). Tax ID remains REQUIRED."""
    db = FakeDB()
    db.qoyod_settings.rows = [{"user_id": "main"}]
    res = await validate_settings_for_setup(db, user_id="main")
    blockers = [i["code"] for i in res["issues"] if i["severity"] == "blocker"]
    warnings = [i["code"] for i in res["issues"] if i["severity"] == "warning"]
    assert "missing_branch_id" in warnings
    assert "missing_branch_id" not in blockers
    assert "missing_tax_id" in blockers
    assert res["ok"] is False  # tax_id still blocking


@pytest.mark.asyncio
async def test_validate_passes_when_minimal_setup_present():
    db = FakeDB()
    db.qoyod_settings.rows = [{
        "user_id": "main",
        "default_branch_id": "1234",
        "default_tax_id": "5678",
        "default_product_type": "service",
        "payment_method_mapping": [],
    }]
    res = await validate_settings_for_setup(db, user_id="main")
    assert res["ok"] is True
    # The "no default_customer" warning is expected (severity=warning).
    sev = [i["severity"] for i in res["issues"]]
    assert "blocker" not in sev


@pytest.mark.asyncio
async def test_validate_blocks_used_method_without_mapping():
    db = FakeDB()
    db.qoyod_settings.rows = [{
        "user_id": "main",
        "default_branch_id": "1",
        "default_tax_id":    "2",
        "default_product_type": "service",
        "payment_method_mapping": [
            {"salla_method": "mada", "qoyod_account_id": "ACC-9"},
        ],
    }]
    db.unified_orders.rows = [
        {"user_id": "main", "payment_method": "tamara"},
        {"user_id": "main", "payment_method": "mada"},
    ]
    res = await validate_settings_for_setup(db, user_id="main")
    blockers = [i for i in res["issues"] if i["severity"] == "blocker"]
    assert any(b["code"] == "unmapped_payment_methods" for b in blockers)
    assert "tamara" in res["context"]["missing_payment_methods"]
    assert "mada"  not in res["context"]["missing_payment_methods"]


@pytest.mark.asyncio
async def test_validate_inventory_mode_requires_inventory_and_cost_accounts():
    db = FakeDB()
    db.qoyod_settings.rows = [{
        "user_id": "main",
        "default_branch_id": "1", "default_tax_id": "2",
        "default_product_type": "inventory",
        "payment_method_mapping": [],
    }]
    res = await validate_settings_for_setup(db, user_id="main")
    codes = [i["code"] for i in res["issues"] if i["severity"] == "blocker"]
    assert "missing_inventory_account" in codes
    assert "missing_cost_account" in codes


@pytest.mark.asyncio
async def test_validate_service_mode_does_not_require_inventory_accounts():
    db = FakeDB()
    db.qoyod_settings.rows = [{
        "user_id": "main",
        "default_branch_id": "1", "default_tax_id": "2",
        "default_product_type": "service",
        "payment_method_mapping": [],
    }]
    res = await validate_settings_for_setup(db, user_id="main")
    codes = [i["code"] for i in res["issues"]]
    assert "missing_inventory_account" not in codes
    assert "missing_cost_account" not in codes


@pytest.mark.asyncio
async def test_collect_used_payment_methods_normalises_native_arabic():
    db = FakeDB()
    db.unified_orders.rows = [
        {"user_id": "main", "payment_method": "Mada"},
        {"user_id": "main", "payment_method": "Apple Pay"},
        {"user_id": "main", "payment_method": "إمكان",
         "raw": {"payment_method": "Emkan"}},
        {"user_id": "main", "payment_method": "tamara"},
    ]
    rows = await collect_used_payment_methods(db, user_id="main")
    keys = {r["key"] for r in rows}
    assert {"mada", "apple_pay", "emkan", "tamara"}.issubset(keys)


@pytest.mark.asyncio
async def test_collect_used_payment_methods_counts_grouped_by_canonical_key():
    db = FakeDB()
    # "Mada" and "mada" should collapse into a single canonical row.
    db.unified_orders.rows = [
        {"user_id": "main", "payment_method": "Mada"},
        {"user_id": "main", "payment_method": "mada"},
        {"user_id": "main", "payment_method": "Apple Pay"},
    ]
    rows = await collect_used_payment_methods(db, user_id="main")
    mada = next(r for r in rows if r["key"] == "mada")
    assert mada["count"] == 2


@pytest.mark.asyncio
async def test_validate_partial_mapping_row_counts_as_unmapped():
    """A row with salla_method but empty qoyod_account_id is NOT mapped."""
    db = FakeDB()
    db.qoyod_settings.rows = [{
        "user_id": "main",
        "default_branch_id": "1", "default_tax_id": "2",
        "default_product_type": "service",
        "payment_method_mapping": [
            {"salla_method": "mada", "qoyod_account_id": ""},
        ],
    }]
    db.unified_orders.rows = [
        {"user_id": "main", "payment_method": "mada"},
    ]
    res = await validate_settings_for_setup(db, user_id="main")
    assert "mada" in res["context"]["missing_payment_methods"]
