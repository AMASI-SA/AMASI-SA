"""Iter-290g — Diagnostic accuracy fixes.

Two tiny operator-facing fixes shipped on 2026-02-28 after the live
test of order 268784455 (Invoice 55, Receipt 42, balance 0):

1. Invoice `notes` line carries the actual `pricing_mode` in use
   (`qoyod_tax_match_salla_total`), not just the legacy `tax_mode`
   customer-routing tag. Operator no longer sees a stale tag.

2. `_quarantine_dry_mappings` no longer lumps REAL Qoyod ids under
   the "quarantined" label just because the mapping carries a
   legacy `dry_run_only=True` flag. Real ids go into a separate
   `dry_run_only_flag_carried` bucket for transparency.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.invoice_builder import build_invoice_payload as build_invoice
from integrations.qoyod.one_shot_reprocess import _quarantine_dry_mappings


_SETTINGS = {
    "default_inventory_id":         "1",
    "default_product_category_id":  "1",
    "default_product_tax_id":       "1",
    "default_product_unit_type_id": "1",
    "default_sales_account_id":     "17",
    "qoyod_tax_percent":            15,
    "invoice_total_policy":         "match_salla_total",
}

_DTO = {
    "order_id":      "MZN-1",
    "order_number":  "268784455",
    "total_amount":  134.0,
    "currency":      "SAR",
    "items": [
        {"sku": "AMS10002",  "name": "ساعة",      "unit_price": 100.0, "quantity": 1,
         "total": 109.0},
        {"sku": "AMS11542",  "name": "كرت اهداء", "unit_price": 0.0,   "quantity": 1,
         "total": 0.0},
    ],
}

_RESOLUTIONS = [
    {"sku": "AMS10002", "qoyod_product_id": 21},
    {"sku": "AMS11542", "qoyod_product_id": 99},
]


def test_invoice_notes_carry_pricing_mode_not_just_tax_mode():
    """Iter-290g — operator-facing fix."""
    inv = build_invoice(
        dto_dict=_DTO,
        product_resolutions=_RESOLUTIONS,
        qoyod_customer_id=7,
        settings=_SETTINGS,
        invoice_date=None,
    )["invoice"]
    notes = inv["notes"]
    assert "pricing_mode=match_salla_total" in notes, notes
    # The legacy tax_mode tag still ships for backward-compat with
    # existing dashboards that filter on it — but it's NO LONGER the
    # primary label.
    assert "tax_mode=" in notes


# ─────────────────────────────────────────────────────────────────────
# Mongo-style stub for _quarantine_dry_mappings
# ─────────────────────────────────────────────────────────────────────
class _StubColl:
    def __init__(self, data: dict | None = None):
        self.data = data
        self.updates: list[dict] = []

    async def find_one(self, q, projection=None):
        return self.data

    async def update_one(self, q, u, upsert=False):
        self.updates.append({"q": q, "u": u})


class _StubDB:
    def __init__(self, customer_map=None, product_map=None):
        self.qoyod_customers_mapping = _StubColl(customer_map)
        self.qoyod_products_mapping  = _StubColl(product_map)
        self.integration_inbox       = _StubColl(None)


@pytest.mark.asyncio
async def test_real_pid_with_dry_run_only_flag_is_NOT_in_quarantined_list():
    """Live production case: AMS10002 had `qoyod_product_id=21` (real)
    + `dry_run_only=True` (legacy flag). The old code listed pid=21
    under `product_mappings_quarantined`, confusing the operator.
    Iter-290g lists it under `dry_run_only_flag_carried` instead."""
    db = _StubDB(
        customer_map=None,
        product_map={"qoyod_product_id": 21, "dry_run_only": True},
    )
    row = {
        "id": "r1", "qoyod_customer_id": "DRY:C1",
        "canonical_payload": {
            "customer": {"phone": "+9665", "email": "x@y.z"},
            "items": [{"sku": "AMS10002"}],
        },
    }
    summary = await _quarantine_dry_mappings(db, user_id="u1", row=row)

    # The carry-forward bucket holds it.
    assert summary["dry_run_only_flag_carried"] == [
        {"sku": "AMS10002", "qoyod_product_id": 21},
    ]
    # Back-compat key + new explicit key are EMPTY (no DRY-id quarantine
    # actually happened for this product).
    assert summary["product_mappings_quarantined"] == []
    assert summary["dry_id_quarantined"] == []
    # AND we did NOT write to the mapping (it was already flagged).
    assert db.qoyod_products_mapping.updates == []


@pytest.mark.asyncio
async def test_dry_prefix_pid_still_quarantines_and_appears_in_both_lists():
    """A pid like `"DRY:P1"` IS a genuine quarantine. Lands in the new
    `dry_id_quarantined` bucket AND the back-compat key."""
    db = _StubDB(
        customer_map=None,
        product_map={"qoyod_product_id": "DRY:P1", "dry_run_only": False},
    )
    row = {
        "id": "r1", "qoyod_customer_id": None,
        "canonical_payload": {
            "customer": {"phone": "", "email": ""},
            "items": [{"sku": "AMS10002"}],
        },
    }
    summary = await _quarantine_dry_mappings(db, user_id="u1", row=row)
    assert summary["dry_id_quarantined"] == [
        {"sku": "AMS10002", "quarantined_id": "DRY:P1"},
    ]
    assert summary["product_mappings_quarantined"] == [
        {"sku": "AMS10002", "quarantined_id": "DRY:P1"},
    ]
    # AND we DID write the quarantine flag.
    assert len(db.qoyod_products_mapping.updates) == 1
    write = db.qoyod_products_mapping.updates[0]["u"]["$set"]
    assert write["dry_run_only"] is True
    assert write["quarantine_reason"] == "one_shot_reprocess"


@pytest.mark.asyncio
async def test_customer_real_id_with_dry_run_only_flag_is_carried_not_quarantined():
    """Same logic applied to customer mapping."""
    db = _StubDB(
        customer_map={"qoyod_customer_id": 88, "dry_run_only": True},
        product_map=None,
    )
    row = {
        "id": "r1", "qoyod_customer_id": 88,
        "canonical_payload": {
            "customer": {"phone": "+9665", "email": ""},
            "items": [],
        },
    }
    summary = await _quarantine_dry_mappings(db, user_id="u1", row=row)
    assert summary["customer_mapping_quarantined"] is False
    assert summary["customer_quarantined_id"] is None
    assert summary["customer_dry_run_only_carried"] is True
    assert summary["customer_carried_real_id"] == 88
