"""Source/identity boundaries independent of receiving orchestration."""
import copy
import hashlib
import json
import os
import unittest
import uuid
from urllib.parse import urlparse
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio
import supplier_debit_identity_v2 as identity
from accounting_atomic import atomic_owner


class Collection:
    def __init__(self, rows):
        self.rows = rows

    async def find_one(self, query):
        return next((copy.deepcopy(row) for row in self.rows if all(row.get(k) == v for k, v in query.items())), None)


class Database:
    def __init__(self):
        self.rows = {}
        self.reads = []
        self._db = self
        self._session = object()

    def __getitem__(self, name):
        assert name in {identity.MAPPINGS, identity.EXPENSES, "mezan_products_v2", "mezan_cost_resources_v2", "settings", "mz2_opening_balance_drafts"}, name
        self.reads.append(name)
        return Collection(self.rows.get(name, []))


class DebitIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = Database()
        self.db.rows["mezan_products_v2"] = [{"user_id": "owner", "mezan_product_id": "product", "status": "active", "variants": [{"id": "variant"}]}]
        self.db.rows["mezan_cost_resources_v2"] = [{"user_id": "owner", "id": "service", "kind": "service", "status": "active"}]
        self.db.rows[identity.MAPPINGS] = []

    def mapping(self, kind="product", source="product", variant=None, target="stock"):
        key = identity.mapping_id("owner", kind, source, variant)
        row = {"_id": key, "id": key, "user_id": "owner", "source_kind": kind, "source_id": source,
               "variant_id": variant, "entity_type": "asset", "entity_id": target, "sub_account": "inventory",
               "currency": "SAR", "status": "active", "contract": identity.CONTRACT,
               "confirmed_by": "owner", "confirmed_at": "2026-09-30T00:00:00+00:00", "version": 1,
               "financial_treatment": "INVENTORY_ASSET"}
        self.db.rows[identity.MAPPINGS].append(row)
        return row

    async def test_variant_wins_and_selected_opening_identity_is_revalidated(self):
        self.mapping(target="product-stock")
        self.mapping(variant="variant", target="variant-stock")
        with patch.object(identity, "require_opening_identity", new_callable=AsyncMock) as verify:
            row = await identity.resolve_debit(self.db, "owner", "product", "product", "variant")
        self.assertEqual(row["entity_id"], "variant-stock")
        verify.assert_awaited_once_with(self.db, "owner", "asset", "variant-stock", "inventory")

    async def test_disabled_variant_never_falls_back(self):
        self.mapping()
        self.mapping(variant="variant")["status"] = "inactive"
        with self.assertRaises(HTTPException) as error:
            await identity.resolve_debit(self.db, "owner", "product", "product", "variant")
        self.assertEqual(error.exception.detail["code"], "MZ2_SUPPLIER_DEBIT_MAPPING_INACTIVE")

    async def test_no_mapping_has_named_gap_without_legacy_reads(self):
        with self.assertRaises(HTTPException) as error:
            await identity.resolve_debit(self.db, "owner", "product", "product")
        self.assertEqual(error.exception.detail["code"], "MZ2_SUPPLIER_PRODUCT_DEBIT_IDENTITY_REQUIRED")

    async def test_foreign_owner_mapping_is_not_available(self):
        self.mapping()["user_id"] = "other"
        with self.assertRaises(HTTPException):
            await identity.resolve_debit(self.db, "owner", "product", "product")

    async def test_expense_requires_independent_active_confirmed_identity(self):
        row = self.mapping("service", "service", target="expense-opaque")
        row.update(financial_treatment="EXPENSE", entity_type="expense", sub_account=None)
        self.db.rows[identity.EXPENSES] = [{"user_id": "owner", "id": "expense-opaque", "contract": identity.CONTRACT,
            "status": "active", "entity_type": "expense", "sub_account": None, "currency": "SAR", "version": 1,
            "confirmed_by": "owner", "confirmed_at": "2026-09-30T00:00:00+00:00"}]
        actual = await identity.resolve_debit(self.db, "owner", "service", "service")
        self.assertEqual(actual["entity_id"], "expense-opaque")
        self.db.rows[identity.EXPENSES][0]["deleted"] = True
        with self.assertRaises(HTTPException) as error:
            await identity.resolve_debit(self.db, "owner", "service", "service")
        self.assertEqual(error.exception.detail["code"], "MZ2_SUPPLIER_EXPENSE_IDENTITY_REQUIRED")

    async def test_service_has_no_product_default_fallback(self):
        self.mapping("default", "owner")
        with self.assertRaises(HTTPException) as error:
            await identity.resolve_debit(self.db, "owner", "service", "service")
        self.assertEqual(error.exception.detail["code"], "MZ2_SUPPLIER_SERVICE_DEBIT_IDENTITY_REQUIRED")

    async def test_deleted_canonical_source_rejected_before_mapping(self):
        self.mapping()
        self.db.rows["mezan_products_v2"][0]["deleted"] = True
        with self.assertRaises(HTTPException):
            await identity.resolve_debit(self.db, "owner", "product", "product")
        self.assertEqual(self.db.reads, ["mezan_products_v2"])

    async def test_lifecycle_timestamps_and_invalid_mapping_version_rejected(self):
        mapping = self.mapping()
        for key, value in (("deleted_at", "2026-09-30T00:00:00Z"), ("archived_at", "2026-09-30T00:00:00Z"), ("version", 0), ("version", True)):
            previous = mapping.get(key)
            mapping[key] = value
            with self.assertRaises(HTTPException):
                await identity.resolve_debit(self.db, "owner", "product", "product")
            if previous is None:
                mapping.pop(key)
            else:
                mapping[key] = previous

    async def test_opening_line_must_belong_to_sealed_approved_preview(self):
        line = {"category": "inventory_asset", "entity_type": "asset", "entity_id": "stock", "sub_account": "inventory", "ledger_currency": "SAR"}
        manifest = {"draft_id": "opening", "cutover_at": "2026-09-29T00:00:00Z", "lines": [line]}
        preview_hash = hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        draft = {"user_id": "owner", "id": "opening", "status": "posted", "txn_group_id": "opening-group",
                 "cutover_at": manifest["cutover_at"], "preview_manifest": copy.deepcopy(manifest), "preview_hash": preview_hash, "lines": [copy.deepcopy(line)]}
        self.db.rows["settings"] = [{"user_id": "owner", "mezan2_financial_cutover": {"opening_active_txn_group_id": "opening-group"}}]
        self.db.rows["mz2_opening_balance_drafts"] = [draft]
        with patch.object(identity, "verify_active_opening_v2", new=AsyncMock(return_value=True)), patch.object(
                identity, "get_journal_v2", new=AsyncMock(return_value={"group": {"metadata": {"approved_preview_hash": preview_hash}}})):
            selected = await identity.require_opening_identity(self.db, "owner", "asset", "stock", "inventory")
            self.assertEqual(selected["entity_id"], "stock")
            draft["lines"][0]["entity_id"] = "invented-stock"
            with self.assertRaises(HTTPException) as error:
                await identity.require_opening_identity(self.db, "owner", "asset", "invented-stock", "inventory")
            self.assertEqual(error.exception.detail["code"], "MZ2_SUPPLIER_OPENING_IDENTITY_MANIFEST_INVALID")
            # Rehashing a mutable draft cannot replace the journal's approval.
            draft["preview_manifest"]["lines"] = copy.deepcopy(draft["lines"])
            draft["preview_hash"] = hashlib.sha256(json.dumps(draft["preview_manifest"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
            with self.assertRaises(HTTPException):
                await identity.require_opening_identity(self.db, "owner", "asset", "invented-stock", "inventory")


@pytest_asyncio.fixture
async def management_api():
    """Self-contained synthetic administration; never connects off loopback."""
    uri = os.environ.get("MZ2_TEST_MONGO_URI", "")
    if not uri:
        pytest.skip("MZ2_TEST_MONGO_URI required")
    assert urlparse(uri).hostname in {"localhost", "127.0.0.1"}
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = mongo["mz2_c2_debit_admin_" + uuid.uuid4().hex]
    principal = {"id": "owner"}

    async def current_user():
        return dict(principal)

    try:
        await mongo.admin.command("ping")
        await db.users.insert_many([
            {"id": "owner", "role": "owner", "is_active": True},
            {"id": "other", "role": "owner", "is_active": True},
            {"id": "employee", "role": "employee", "created_by": "owner", "is_active": True,
             "accounting_permissions": ["accounting.inventory.view", "accounting.financial_accounts.manage"]},
        ])
        await db.mz2_atomic_owners.insert_many([
            {"_id": owner, "revision": 0, "writes_paused": False} for owner in ("owner", "other")])
        await db.mezan_cost_resources_v2.insert_one({"id": "service", "user_id": "owner", "kind": "service", "status": "active"})
        app = FastAPI()
        app.include_router(identity.make_supplier_debit_router(db, current_user))
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://local.test") as client:
            yield client, db, principal
    finally:
        await mongo.drop_database(db.name)
        mongo.close()


BASE = "/supplier-debit-mappings-v2"
CREATE_EXPENSE = {"name": "Owner approved service expense", "request_id": "explicit-expense-request",
                  "confirmed": True, "reason": "Owner confirms supplier service expense identity"}


@pytest.mark.asyncio
async def test_expense_creation_confirmation_replay_and_changed_request_conflict(management_api):
    client, db, _ = management_api
    denied = await client.post(BASE + "/expense-identities", json={**CREATE_EXPENSE, "confirmed": False})
    assert denied.status_code == 422
    assert await db[identity.EXPENSES].count_documents({}) == 0
    created = await client.post(BASE + "/expense-identities", json=CREATE_EXPENSE)
    assert created.status_code == 200, created.text
    replay = await client.post(BASE + "/expense-identities", json=CREATE_EXPENSE)
    assert replay.status_code == 200 and replay.json() == created.json()
    changed = await client.post(BASE + "/expense-identities", json={**CREATE_EXPENSE, "name": "Different treatment label"})
    assert changed.status_code == 409
    assert changed.json()["detail"]["code"] == "MZ2_SUPPLIER_EXPENSE_REQUEST_CONFLICT"
    assert await db[identity.EXPENSES].count_documents({}) == 1
    assert await db[identity.AUDIT].count_documents({}) == 1
    stored = await db[identity.EXPENSES].find_one({})
    assert stored["confirmed_by"] == "owner" and stored["confirmed_at"] and stored["version"] == 1


@pytest.mark.asyncio
async def test_management_requires_current_owner_despite_employee_permissions(management_api):
    client, db, principal = management_api
    principal["id"] = "employee"
    response = await client.post(BASE + "/expense-identities", json=CREATE_EXPENSE)
    assert response.status_code == 403
    assert (await client.get(BASE)).status_code == 403
    assert await db[identity.EXPENSES].count_documents({}) == 0
    assert await db[identity.AUDIT].count_documents({}) == 0


@pytest.mark.asyncio
async def test_paused_setup_creates_expense_and_audit_without_changing_controls(management_api):
    client, db, _ = management_api
    await db.mz2_atomic_owners.update_one({"_id": "owner"}, {"$set": {"writes_paused": True}})
    previous = await db.mz2_atomic_owners.find_one({"_id": "owner"})
    response = await client.post(BASE + "/expense-identities", json=CREATE_EXPENSE)
    assert response.status_code == 200, response.text
    assert await db[identity.EXPENSES].count_documents({}) == 1
    assert await db[identity.AUDIT].count_documents({}) == 1
    assert await db.mz2_atomic_owners.find_one({"_id": "owner"}) == {
        **previous, "revision": previous["revision"] + 1}


@pytest.mark.asyncio
async def test_expense_version_disable_and_revalidation_at_resolution(management_api):
    client, db, _ = management_api
    expense = (await client.post(BASE + "/expense-identities", json=CREATE_EXPENSE)).json()
    mapping_payload = {"confirmed": True, "reason": "Explicit service expense mapping", "source_kind": "service",
                       "source_id": "service", "financial_treatment": "EXPENSE", "entity_type": "expense",
                       "entity_id": expense["id"], "sub_account": None, "currency": "SAR", "version": 0}
    saved = await client.put(BASE, json=mapping_payload)
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    stale = await client.put(BASE, json=mapping_payload)
    assert stale.status_code == 409
    actual = await atomic_owner(db, "owner", lambda scoped: identity.resolve_debit(scoped, "owner", "service", "service"))
    assert actual["entity_id"] == expense["id"]
    change = {"confirmed": True, "reason": "Owner disables identity", "status": "inactive", "version": 1}
    disabled = await client.put(BASE + "/expense-identities/" + expense["id"], json=change)
    assert disabled.status_code == 200 and disabled.json()["version"] == 2
    stale = await client.put(BASE + "/expense-identities/" + expense["id"], json=change)
    assert stale.status_code == 409
    with pytest.raises(HTTPException) as exc:
        await atomic_owner(db, "owner", lambda scoped: identity.resolve_debit(scoped, "owner", "service", "service"))
    assert exc.value.detail["code"] == "MZ2_SUPPLIER_EXPENSE_IDENTITY_REQUIRED"
    assert await db[identity.AUDIT].count_documents({}) == 3


@pytest.mark.asyncio
async def test_foreign_owner_cannot_read_or_change_expense_identity(management_api):
    client, db, principal = management_api
    expense = (await client.post(BASE + "/expense-identities", json=CREATE_EXPENSE)).json()
    principal["id"] = "other"
    listing = await client.get(BASE)
    assert listing.status_code == 200 and listing.json()["expense_identities"] == []
    response = await client.put(BASE + "/expense-identities/" + expense["id"], json={
        "confirmed": True, "reason": "Foreign mutation rejected", "status": "inactive", "version": 1})
    assert response.status_code == 409
    original = await db[identity.EXPENSES].find_one({"id": expense["id"]})
    assert original["status"] == "active" and original["version"] == 1
    assert await db[identity.AUDIT].count_documents({}) == 1
