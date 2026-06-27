"""Tests for the SSOT Product Trust Gate (2026-02-27).

User directive: a Qoyod tenant frequently contains legacy historical
products (cod_item, custom_product, old Salla SKUs, …). A new order
must NEVER silently bind to one of these. The resolver:

  • Mezan mapping HIT  → use it.
  • Mezan mapping MISS + Qoyod has no row → create fresh.
  • Mezan mapping MISS + Qoyod HAS a row →
        - block with `qoyod_existing_untrusted` (default).
        - or, if operator opted out via settings, adopt.

A separate `adopt_qoyod_product` flow lets the operator explicitly
onboard a legacy product into the local mapping (manual review).
"""
from __future__ import annotations

import os
import uuid

import pytest
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")

from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod.product_resolver import (
    resolve_products, adopt_qoyod_product,
)


@pytest.fixture
def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


@pytest.fixture
def tenant():
    return f"test-ssot-{uuid.uuid4().hex[:8]}"


class _RecordingClient:
    """Test client mimicking QoyodAPIClient. `existing_skus` maps
    sku → existing Qoyod product dict (None means 'not in Qoyod')."""
    def __init__(self, existing_skus: dict | None = None):
        self.existing_skus = existing_skus or {}
        self.lookups: list[str] = []
        self.creates: list[dict] = []

    async def find_product_by_sku(self, sku):
        self.lookups.append(sku)
        return self.existing_skus.get(sku)

    async def create_product(self, payload, *, idem):
        self.creates.append({"payload": payload, "idem": idem})
        # Return a deterministic fake id from sku.
        sku = (payload.get("product") or {}).get("sku") or "x"
        return {"product": {"id": f"FAKE-{sku}"}}


async def _cleanup(db, tenant):
    await db.qoyod_products_mapping.delete_many({"user_id": tenant})


# ─────────────────────────────────────────────────────────────────────
# Trust gate behaviour
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_resolver_creates_when_sku_not_in_qoyod(db, tenant):
    """Mezan mapping MISS + Qoyod has no row → create fresh."""
    try:
        client = _RecordingClient(existing_skus={})
        res = await resolve_products(
            db, tenant,
            [{"sku": "FRESH-001", "name": "منتج جديد", "unit_price": 10}],
            settings={},
            trace_id="t-1", api_client=client,
        )
        assert res.success is True
        assert len(res.items) == 1
        assert res.items[0].trust_source == "created"
        assert client.lookups == ["FRESH-001"]
        assert len(client.creates) == 1
        # And the new mapping is persisted with source='mezan_created'.
        m = await db.qoyod_products_mapping.find_one(
            {"user_id": tenant, "sku": "FRESH-001"})
        assert m["source"] == "mezan_created"
        assert m.get("adopted") is False
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_resolver_blocks_qoyod_existing_untrusted(db, tenant):
    """Mezan mapping MISS + Qoyod HAS the SKU → block with
    `qoyod_existing_untrusted`. The error must include the Qoyod
    product id + name + remediation hint.
    """
    try:
        legacy = {"id": 1, "sku": "AMS11903", "name_ar": "أقمشة متنوعة"}
        client = _RecordingClient(existing_skus={"AMS11903": legacy})
        res = await resolve_products(
            db, tenant,
            [{"sku": "AMS11903", "name": "New", "unit_price": 50}],
            settings={},
            trace_id="t-2", api_client=client,
        )
        assert res.success is False
        assert res.error["code"] == "qoyod_existing_untrusted"
        assert res.error["qoyod_product_id"] == "1"
        assert res.error["qoyod_product_sku"] == "AMS11903"
        assert "أقمشة متنوعة" in (res.error.get("qoyod_product_name") or "")
        assert res.error["remediation"] == "adopt_or_archive"
        # No create call should have been made.
        assert client.creates == []
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_resolver_uses_mezan_mapping_without_gate_lookup(db, tenant):
    """When a local mapping already exists, the resolver MUST short-
    circuit and never even consult Qoyod (saves an API call + avoids
    spurious blocks when the SKU happens to also exist legacy-side)."""
    try:
        # Seed a mapping as if Mezan had created the product before.
        await db.qoyod_products_mapping.insert_one({
            "user_id": tenant, "sku": "SKU-MAPPED",
            "qoyod_product_id": "999", "source": "mezan_created",
        })
        # Even if Qoyod ALSO has this SKU, the gate must not run.
        client = _RecordingClient(
            existing_skus={"SKU-MAPPED": {"id": 999, "sku": "SKU-MAPPED"}})
        res = await resolve_products(
            db, tenant, [{"sku": "SKU-MAPPED", "name": "x", "unit_price": 1}],
            settings={}, trace_id="t-3", api_client=client,
        )
        assert res.success is True
        assert res.items[0].qoyod_product_id == "999"
        assert res.items[0].trust_source == "mezan"
        assert client.lookups == []   # gate not consulted
        assert client.creates == []   # no create either
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_resolver_uses_adopted_mapping_marks_trust_source(db, tenant):
    """A previously adopted mapping should be honoured with
    `trust_source='adopted'` for audit visibility."""
    try:
        await db.qoyod_products_mapping.insert_one({
            "user_id": tenant, "sku": "ADOPTED-1",
            "qoyod_product_id": "55", "adopted": True,
            "source": "operator_adopted",
        })
        client = _RecordingClient()
        res = await resolve_products(
            db, tenant, [{"sku": "ADOPTED-1", "name": "x", "unit_price": 1}],
            settings={}, trace_id="t-4", api_client=client,
        )
        assert res.success is True
        assert res.items[0].trust_source == "adopted"
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_resolver_can_disable_trust_gate(db, tenant):
    """`block_untrusted_existing_products=False` opts out — useful for
    tenants who explicitly want auto-adoption (NOT default behaviour)."""
    try:
        legacy = {"id": 7, "sku": "LEGACY", "name": "Legacy"}
        client = _RecordingClient(existing_skus={"LEGACY": legacy})
        res = await resolve_products(
            db, tenant,
            [{"sku": "LEGACY", "name": "x", "unit_price": 1}],
            settings={"block_untrusted_existing_products": False},
            trace_id="t-5", api_client=client,
        )
        # Trust gate disabled → resolver proceeds to create (which in
        # production would either return the existing row or duplicate;
        # for THIS test we just verify the gate didn't trip).
        assert res.success is True
        assert len(client.creates) == 1
    finally:
        await _cleanup(db, tenant)


# ─────────────────────────────────────────────────────────────────────
# Adoption flow
# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_adopt_creates_mapping_with_audit_trail(db, tenant):
    try:
        res = await adopt_qoyod_product(
            db, user_id=tenant, sku="ADOPT-1",
            qoyod_product_id="100",
            qoyod_product_name="أقمشة متنوعة",
            note="legacy Salla sync, reviewed by Khalid",
            actor="operator:khalid@hesab.app",
        )
        assert res["ok"] is True
        m = await db.qoyod_products_mapping.find_one(
            {"user_id": tenant, "sku": "ADOPT-1"})
        assert m["qoyod_product_id"] == "100"
        assert m["adopted"] is True
        assert m["adopted_by"] == "operator:khalid@hesab.app"
        assert m["adoption_note"] == "legacy Salla sync, reviewed by Khalid"
        assert m["source"] == "operator_adopted"

        # After adoption the resolver should now accept the SKU
        # without consulting the gate.
        client = _RecordingClient(
            existing_skus={"ADOPT-1": {"id": 100, "sku": "ADOPT-1"}})
        rres = await resolve_products(
            db, tenant, [{"sku": "ADOPT-1", "name": "x", "unit_price": 5}],
            settings={}, trace_id="t-adopt", api_client=client,
        )
        assert rres.success is True
        assert rres.items[0].trust_source == "adopted"
        assert client.lookups == []   # adopted mapping → no gate check
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_adopt_is_idempotent(db, tenant):
    try:
        a1 = await adopt_qoyod_product(
            db, user_id=tenant, sku="X", qoyod_product_id="1")
        a2 = await adopt_qoyod_product(
            db, user_id=tenant, sku="X", qoyod_product_id="1",
            note="updated note")
        assert a1["ok"] and a2["ok"]
        # Only one row should exist.
        count = await db.qoyod_products_mapping.count_documents(
            {"user_id": tenant, "sku": "X"})
        assert count == 1
        m = await db.qoyod_products_mapping.find_one(
            {"user_id": tenant, "sku": "X"})
        assert m["adoption_note"] == "updated note"
    finally:
        await _cleanup(db, tenant)


@pytest.mark.asyncio
async def test_adopt_rejects_missing_fields(db, tenant):
    r = await adopt_qoyod_product(
        db, user_id=tenant, sku="", qoyod_product_id="x")
    assert r["ok"] is False
    r = await adopt_qoyod_product(
        db, user_id=tenant, sku="x", qoyod_product_id="")
    assert r["ok"] is False
