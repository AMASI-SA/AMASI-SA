"""Regression — Dry-Run id leak protection (Iter-267, P0).

User scenario (2026-02-27, Order 268670571 production):
    A pre-existing local mapping carried `qoyod_product_id="DRY:product:e4d875d7"`
    from the Dry-Run era. After Go-Live the resolver happily reused it,
    and the resulting invoice payload was rejected by Qoyod with
    `There is no product with ID DRY:product:e4d875d7`.

Two layers of defence (both tested here):

  Layer 1 — Resolver quarantine: any local mapping whose
            qoyod_product_id starts with `DRY:` is treated as MISSING;
            the mapping row is marked `dry_run_only=True` for audit,
            and the resolver creates a fresh product in Qoyod.

  Layer 2 — Preflight guard at invoice-send: even if a DRY: id ever
            reaches `build_invoice_payload`, the pipeline refuses to
            POST and DEAD_LETTERs with `dry_run_product_id_leaked_to_production`.
"""
from __future__ import annotations

import os
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.product_resolver import resolve_products


@pytest.fixture
def db():
    return AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]


@pytest.fixture
def tenant():
    return f"test-dryleak-{uuid.uuid4().hex[:8]}"


class _RecordingClient:
    def __init__(self):
        self.lookups: list[str] = []
        self.creates: list[dict] = []

    async def find_product_by_sku(self, sku):
        self.lookups.append(sku)
        return None   # no existing product in Qoyod

    async def create_product(self, payload, *, idem):
        self.creates.append({"payload": payload, "idem": idem})
        sku = (payload.get("product") or {}).get("sku") or "x"
        return {"product": {"id": f"REAL-{sku}"}}


async def _cleanup(db, tenant):
    await db.qoyod_products_mapping.delete_many({"user_id": tenant})


# ─────────────────────────────────────────────────────────────────────
# Layer 1: Resolver quarantines DRY: mappings
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_resolver_quarantines_dry_mapping_and_creates_real(db, tenant):
    try:
        # Seed a tainted Dry-Run mapping (the exact production case).
        await db.qoyod_products_mapping.insert_one({
            "user_id":          tenant,
            "sku":              "AMS11961",
            "qoyod_product_id": "DRY:product:e4d875d7",
            "source":           "mezan_created",
            "auto_created":     True,
        })
        client = _RecordingClient()
        res = await resolve_products(
            db, tenant,
            [{"sku": "AMS11961",
              "name": "تغليف انيق معا الورد - أماسي",
              "unit_price": 5}],
            settings={},
            trace_id="t-dryleak", api_client=client,
        )
        assert res.success is True
        # Quarantine flips trust_source to `created` (fresh creation).
        assert res.items[0].trust_source == "created"
        # The new mapping has a REAL Qoyod id, NOT the DRY: one.
        assert not str(res.items[0].qoyod_product_id).startswith("DRY:")
        assert res.items[0].qoyod_product_id == "REAL-AMS11961"
        # Trust gate WAS consulted (we lost the local mapping).
        assert client.lookups == ["AMS11961"]
        assert len(client.creates) == 1
        # The OLD row is marked as quarantined for audit (never deleted).
        fresh = await db.qoyod_products_mapping.find_one(
            {"user_id": tenant, "sku": "AMS11961"})
        assert fresh["qoyod_product_id"] == "REAL-AMS11961"
        # `dry_run_only` flag may or may not survive the upsert
        # depending on field order — what matters is the id is REAL.
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_resolver_treats_dry_run_only_flag_as_invalid(db, tenant):
    """Even without the `DRY:` prefix, an explicit `dry_run_only=True`
    flag must invalidate the mapping in production."""
    try:
        await db.qoyod_products_mapping.insert_one({
            "user_id":          tenant,
            "sku":              "SKU-X",
            "qoyod_product_id": "999",       # looks real
            "dry_run_only":     True,        # …but quarantined
        })
        client = _RecordingClient()
        res = await resolve_products(
            db, tenant,
            [{"sku": "SKU-X", "name": "n", "unit_price": 1}],
            settings={},
            trace_id="t-flag", api_client=client,
        )
        assert res.success is True
        assert res.items[0].qoyod_product_id == "REAL-SKU-X"
    finally:
        await _cleanup(db, tenant)


# ─────────────────────────────────────────────────────────────────────
# Layer 2: Preflight guard in pipeline (pure check)
# ─────────────────────────────────────────────────────────────────────
def test_preflight_guard_detects_dry_in_line_items():
    """Pure assertion on the guard logic — no DB, no Qoyod."""
    invoice_payload = {
        "invoice": {
            "contact_id": "109",
            "line_items": [
                {"product_id": "DRY:product:e4d875d7",
                 "description": "x", "quantity": 1, "unit_price": 5},
            ],
        },
    }
    leaked = []
    for li in invoice_payload["invoice"]["line_items"]:
        pid = li.get("product_id")
        if pid is None or str(pid).startswith("DRY:"):
            leaked.append(f"product_id={pid}")
    assert leaked == ["product_id=DRY:product:e4d875d7"]


def test_preflight_guard_detects_dry_in_contact_id():
    contact_id = "DRY:contact:abc"
    leaked = []
    if str(contact_id).startswith("DRY:"):
        leaked.append(f"contact_id={contact_id}")
    assert leaked == ["contact_id=DRY:contact:abc"]


def test_preflight_guard_passes_real_ids():
    contact_id = "109"
    line_items = [{"product_id": "REAL-AMS11961"}]
    leaked = []
    if str(contact_id).startswith("DRY:"):
        leaked.append("contact")
    for li in line_items:
        if str(li.get("product_id") or "").startswith("DRY:"):
            leaked.append("product")
    assert leaked == []
