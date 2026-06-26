"""Existing-Data Migration — tests.

Locks in the matching policy laid down by the user on 2026-06-26:

  Products
    • SKU exact         → auto_mapped
    • SKU + name diff   → mapped_with_warning  (warnings: name_differs)
    • SKU + price diff  → mapped_with_warning  (warnings: price_differs)
    • Name only         → candidate_match      (NO mapping)
    • Nothing           → unmapped

  Customers
    • Phone (E.164)     → auto_mapped
    • Email             → auto_mapped (when no phone)
    • Name only         → candidate_match
    • Nothing           → unmapped

  Read-only: no Qoyod write is ever performed.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from integrations.qoyod.migration import (
    normalize_phone, normalize_sku, normalize_name, normalize_email,
    import_qoyod_products, import_qoyod_customers,
    extract_mezan_products, extract_mezan_customers,
    match_products, match_customers,
    run_migration, latest_run, confirm_candidate,
    _classify_product_match, _classify_customer_match,
)


# ─── Fixtures ────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    name = f"mig_test_{uuid.uuid4().hex[:8]}"
    yield client[name]
    await client.drop_database(name)
    client.close()


class _FakeClient:
    """Substitute for QoyodAPIClient — returns canned product/customer pages."""
    def __init__(self, products_pages, customers_pages):
        self._products = products_pages
        self._customers = customers_pages
        self.calls: list[tuple[str, int]] = []

    async def list_products(self, *, page: int, limit: int):
        self.calls.append(("products", page))
        if page <= len(self._products):
            return {"products": self._products[page - 1]}
        return {"products": []}

    async def list_contacts(self, *, page: int, limit: int):
        self.calls.append(("customers", page))
        if page <= len(self._customers):
            return {"customers": self._customers[page - 1]}
        return {"customers": []}


# ─── Normalisation ───────────────────────────────────────────────────
class TestNormalisation:
    def test_phone_local_to_e164(self):
        assert normalize_phone("0501234567") == "+966501234567"

    def test_phone_with_country_code(self):
        assert normalize_phone("+966501234567") == "+966501234567"
        assert normalize_phone("00966501234567") == "+966501234567"

    def test_phone_bare_5_prefix(self):
        assert normalize_phone("501234567") == "+966501234567"

    def test_phone_with_spaces_and_dashes(self):
        assert normalize_phone("050-123 4567") == "+966501234567"

    def test_phone_empty_or_garbage(self):
        assert normalize_phone(None) == ""
        assert normalize_phone("") == ""
        assert normalize_phone("abc") == ""
        assert normalize_phone("123") == ""        # too short

    def test_sku_normalisation(self):
        assert normalize_sku("  Sku-1 ") == "SKU-1"
        assert normalize_sku(None) == ""

    def test_name_normalisation(self):
        assert normalize_name("  Hello   World  ") == "hello world"
        assert normalize_name("منتج   A") == "منتج a"

    def test_email_normalisation(self):
        assert normalize_email(" A@B.com ") == "a@b.com"
        assert normalize_email("not-an-email") == ""


# ─── Pure classifier behaviour (no DB) ───────────────────────────────
def _q_prod(qid, sku="", name="", price=None):
    return {"qoyod_id": qid, "sku": sku, "name": name, "price": price,
            "sku_norm": normalize_sku(sku), "name_norm": normalize_name(name)}


def _q_cust(qid, name="", phone="", email=""):
    return {"qoyod_id": qid, "name": name, "phone": phone, "email": email,
            "phone_norm": normalize_phone(phone),
            "email_norm": normalize_email(email),
            "name_norm": normalize_name(name)}


class TestProductClassifier:
    def test_sku_match_exact_auto_mapped(self):
        q = _q_prod("Q-1", "SKU-A", "Product A", 100.0)
        mz = {"sku": "sku-a", "name": "Product A", "unit_price": 100.0,
              "sku_norm": "SKU-A", "name_norm": "product a"}
        out = _classify_product_match(mz, {"SKU-A": q}, {})
        assert out["status"] == "auto_mapped"
        assert out["qoyod_id"] == "Q-1"
        assert out["matched_on"] == "sku"
        assert out["warnings"] == []

    def test_sku_match_with_name_diff_warning(self):
        q = _q_prod("Q-1", "SKU-A", "Old Name", 100.0)
        mz = {"sku": "SKU-A", "name": "New Name", "unit_price": 100.0,
              "sku_norm": "SKU-A", "name_norm": "new name"}
        out = _classify_product_match(mz, {"SKU-A": q}, {})
        assert out["status"] == "mapped_with_warning"
        assert "name_differs" in out["warnings"]
        assert out["qoyod_id"] == "Q-1"

    def test_sku_match_with_price_diff_warning(self):
        q = _q_prod("Q-1", "SKU-A", "Product A", 100.0)
        mz = {"sku": "SKU-A", "name": "Product A", "unit_price": 120.0,
              "sku_norm": "SKU-A", "name_norm": "product a"}
        out = _classify_product_match(mz, {"SKU-A": q}, {})
        assert out["status"] == "mapped_with_warning"
        assert "price_differs" in out["warnings"]

    def test_name_only_match_is_candidate_no_auto_mapping(self):
        q = _q_prod("Q-9", "QSKU-9", "Generic Item", 50.0)
        mz = {"sku": "", "name": "Generic Item", "unit_price": None,
              "sku_norm": "", "name_norm": "generic item"}
        out = _classify_product_match(mz, {}, {"generic item": q})
        assert out["status"] == "candidate_match"
        assert out["qoyod_id"] is None                    # CRITICAL
        assert out["candidate_qoyod_id"] == "Q-9"
        assert out["matched_on"] == "name_only"

    def test_no_match_is_unmapped(self):
        mz = {"sku": "X", "name": "X", "unit_price": None,
              "sku_norm": "X", "name_norm": "x"}
        out = _classify_product_match(mz, {}, {})
        assert out["status"] == "unmapped"
        assert out["qoyod_id"] is None


class TestCustomerClassifier:
    def test_phone_match_auto_mapped(self):
        q = _q_cust("C-1", "Ahmad", "+966500000001", "a@x.com")
        mz = {"phone_norm": "+966500000001", "email_norm": "",
              "name_norm": "different name"}
        out = _classify_customer_match(mz, {"+966500000001": q}, {}, {})
        assert out["status"] == "auto_mapped"
        assert out["matched_on"] == "phone"

    def test_email_match_when_no_phone(self):
        q = _q_cust("C-2", "Sara", "+966500000002", "s@x.com")
        mz = {"phone_norm": "", "email_norm": "s@x.com",
              "name_norm": "different"}
        out = _classify_customer_match(mz, {}, {"s@x.com": q}, {})
        assert out["status"] == "auto_mapped"
        assert out["matched_on"] == "email"

    def test_name_only_is_candidate(self):
        q = _q_cust("C-3", "Khaled", "", "")
        mz = {"phone_norm": "", "email_norm": "", "name_norm": "khaled"}
        out = _classify_customer_match(mz, {}, {}, {"khaled": q})
        assert out["status"] == "candidate_match"
        assert out["qoyod_id"] is None                    # CRITICAL
        assert out["candidate_qoyod_id"] == "C-3"

    def test_no_match_unmapped(self):
        mz = {"phone_norm": "+966500000099", "email_norm": "",
              "name_norm": "ghost"}
        out = _classify_customer_match(mz, {}, {}, {})
        assert out["status"] == "unmapped"


# ─── DB-level: import + match + report ───────────────────────────────
@pytest.mark.asyncio
async def test_import_qoyod_products_paginates_and_upserts(db):
    client = _FakeClient(
        products_pages=[
            [{"id": "Q1", "sku": "SKU-A", "name": "Product A", "price": 100},
             {"id": "Q2", "sku": "SKU-B", "name": "Product B", "price": 200}],
            [{"id": "Q3", "sku": "SKU-C", "name": "Product C", "price": 300}],
        ],
        customers_pages=[],
    )
    n = await import_qoyod_products(
        db, user_id="main", api_client=client, page_size=2)
    assert n == 3
    assert await db.qoyod_external_products.count_documents(
        {"user_id": "main"}) == 3
    # Idempotent: rerun should not duplicate
    n2 = await import_qoyod_products(
        db, user_id="main", api_client=client, page_size=2)
    assert await db.qoyod_external_products.count_documents(
        {"user_id": "main"}) == 3
    assert n2 == 3


@pytest.mark.asyncio
async def test_import_qoyod_customers_paginates_and_upserts(db):
    client = _FakeClient(
        products_pages=[],
        customers_pages=[
            [{"id": "C1", "name": "Ahmad", "phone": "0500000001",
              "email": "ah@x.com"},
             {"id": "C2", "name": "Sara",  "mobile": "+966500000002"}],
        ],
    )
    n = await import_qoyod_customers(
        db, user_id="main", api_client=client, page_size=10)
    assert n == 2
    docs = await db.qoyod_external_customers.find(
        {"user_id": "main"}).to_list(10)
    by_id = {d["qoyod_id"]: d for d in docs}
    assert by_id["C1"]["phone_norm"] == "+966500000001"
    assert by_id["C2"]["phone_norm"] == "+966500000002"


@pytest.mark.asyncio
async def test_extract_mezan_products_from_order_items(db):
    await db.order_items.insert_many([
        {"user_id": "main", "sku": "SKU-A", "product_name": "A",
         "unit_price": 100, "order_number": "O1"},
        {"user_id": "main", "sku": "SKU-A", "product_name": "A",
         "unit_price": 100, "order_number": "O2"},
        {"user_id": "main", "sku": "SKU-B", "product_name": "B",
         "unit_price": 50, "order_number": "O3"},
    ])
    out = await extract_mezan_products(db, user_id="main")
    by_sku = {p["sku"]: p for p in out}
    assert set(by_sku) == {"SKU-A", "SKU-B"}
    assert by_sku["SKU-A"]["occurrences"] == 2


@pytest.mark.asyncio
async def test_extract_mezan_customers_dedupes_by_phone(db):
    await db.unified_orders.insert_many([
        {"user_id": "main", "customer_name": "Ahmad",
         "raw": {"customer_mobile": "0500000001",
                 "customer_email": "a@x.com"}},
        {"user_id": "main", "customer_name": "Ahmad",
         "raw": {"customer_mobile": "+966500000001",
                 "customer_email": "a@x.com"}},
        {"user_id": "main", "customer_name": "Sara",
         "raw": {"customer_mobile": "0500000002"}},
    ])
    await db.custom_app_customers.insert_one({
        "user_id": "main", "name": "Khaled", "mobile": "",
        "email": "k@x.com"})
    out = await extract_mezan_customers(db, user_id="main")
    keys = {(c["phone_norm"], c["email_norm"], c["name_norm"]) for c in out}
    # Ahmad de-duped to one row by phone
    phones = [c["phone_norm"] for c in out if c["phone_norm"]]
    assert phones.count("+966500000001") == 1
    assert "+966500000002" in phones
    # Khaled comes through email (no phone)
    assert any(c["email_norm"] == "k@x.com" for c in out)


@pytest.mark.asyncio
async def test_match_products_end_to_end(db):
    # Seed Qoyod side: one exact, one price-diff, one name-only candidate
    await db.qoyod_external_products.insert_many([
        {"user_id": "main", "qoyod_id": "Q1", "sku": "SKU-A", "name": "A",
         "price": 100.0, "sku_norm": "SKU-A", "name_norm": "a"},
        {"user_id": "main", "qoyod_id": "Q2", "sku": "SKU-B", "name": "B",
         "price": 50.0,  "sku_norm": "SKU-B", "name_norm": "b"},
        {"user_id": "main", "qoyod_id": "Q3", "sku": "QONLY", "name": "Item C",
         "price": None,  "sku_norm": "QONLY", "name_norm": "item c"},
    ])
    # Seed Mezan side via order_items
    await db.order_items.insert_many([
        {"user_id": "main", "sku": "SKU-A", "product_name": "A",
         "unit_price": 100.0, "order_number": "O1"},          # auto_mapped
        {"user_id": "main", "sku": "SKU-B", "product_name": "B different",
         "unit_price": 75.0, "order_number": "O2"},           # mapped_with_warning
        {"user_id": "main", "sku": "", "product_name": "Item C",
         "unit_price": None, "order_number": "O3"},           # candidate
        {"user_id": "main", "sku": "SKU-Z", "product_name": "Unknown",
         "unit_price": None, "order_number": "O4"},           # unmapped
    ])
    counts = await match_products(db, user_id="main", run_id="r1")
    assert counts["auto_mapped"] == 1
    assert counts["mapped_with_warning"] == 1
    assert counts["candidate_match"] == 1
    assert counts["unmapped"] == 1
    assert counts["sku_mismatch_warnings"] == 1
    rows = await db.qoyod_migration_products.find(
        {"user_id": "main"}).to_list(20)
    # Candidate row must NOT have qoyod_product_id set
    cand = next(r for r in rows if r["status"] == "candidate_match")
    assert cand["qoyod_product_id"] is None
    assert cand["candidate_qoyod_id"] == "Q3"


@pytest.mark.asyncio
async def test_match_customers_end_to_end(db):
    await db.qoyod_external_customers.insert_many([
        {"user_id": "main", "qoyod_id": "C1", "name": "Ahmad",
         "phone": "+966500000001", "email": "",
         "phone_norm": "+966500000001", "email_norm": "",
         "name_norm": "ahmad"},
        {"user_id": "main", "qoyod_id": "C2", "name": "Sara",
         "phone": "", "email": "s@x.com",
         "phone_norm": "", "email_norm": "s@x.com",
         "name_norm": "sara"},
        {"user_id": "main", "qoyod_id": "C3", "name": "Khaled",
         "phone": "", "email": "", "phone_norm": "",
         "email_norm": "", "name_norm": "khaled"},
    ])
    await db.unified_orders.insert_many([
        {"user_id": "main", "customer_name": "Ahmad Y",
         "raw": {"customer_mobile": "0500000001"}},          # phone match
        {"user_id": "main", "customer_name": "Sara",
         "raw": {"customer_email": "s@x.com"}},              # email match
        {"user_id": "main", "customer_name": "Khaled",
         "raw": {}},                                          # name only
        {"user_id": "main", "customer_name": "Ghost",
         "raw": {"customer_mobile": "0500000099"}},          # unmapped
    ])
    counts = await match_customers(db, user_id="main", run_id="r1")
    assert counts["auto_mapped"] == 2
    assert counts["candidate_match"] == 1
    assert counts["unmapped"] == 1
    rows = await db.qoyod_migration_customers.find(
        {"user_id": "main"}).to_list(20)
    cand = next(r for r in rows if r["status"] == "candidate_match")
    # Critical: name-only customer must NOT have qoyod_customer_id
    assert cand["qoyod_customer_id"] is None
    assert cand["candidate_qoyod_id"] == "C3"


@pytest.mark.asyncio
async def test_run_migration_orchestrator_produces_report(db):
    await db.order_items.insert_many([
        {"user_id": "main", "sku": "SKU-A", "product_name": "A",
         "unit_price": 100.0, "order_number": "O1"},
    ])
    await db.unified_orders.insert_many([
        {"user_id": "main", "customer_name": "Ahmad",
         "raw": {"customer_mobile": "0500000001"}},
    ])
    client = _FakeClient(
        products_pages=[[
            {"id": "Q1", "sku": "SKU-A", "name": "A", "price": 100},
        ]],
        customers_pages=[[
            {"id": "C1", "name": "Ahmad", "phone": "0500000001"},
        ]],
    )
    result = await run_migration(
        db, user_id="main", api_client=client)
    assert result["status"] == "completed"
    s = result["summary"]
    assert s["qoyod_products_imported"] == 1
    assert s["qoyod_customers_imported"] == 1
    assert s["products_mapped"] == 1
    assert s["customers_mapped"] == 1
    assert s["needs_manual_review"] == 0
    # latest_run reflects the same data
    latest = await latest_run(db, user_id="main")
    assert latest["run_id"] == result["run_id"]
    assert latest["status"] == "completed"


@pytest.mark.asyncio
async def test_rerun_migration_does_not_duplicate(db):
    await db.order_items.insert_one({
        "user_id": "main", "sku": "SKU-A", "product_name": "A",
        "unit_price": 100.0, "order_number": "O1"})
    client = _FakeClient(
        products_pages=[[
            {"id": "Q1", "sku": "SKU-A", "name": "A", "price": 100},
        ]],
        customers_pages=[],
    )
    await run_migration(db, user_id="main", api_client=client)
    await run_migration(db, user_id="main", api_client=client)
    assert await db.qoyod_external_products.count_documents(
        {"user_id": "main"}) == 1
    assert await db.qoyod_migration_products.count_documents(
        {"user_id": "main"}) == 1


@pytest.mark.asyncio
async def test_confirm_candidate_upgrades_status(db):
    await db.qoyod_migration_products.insert_one({
        "user_id": "main", "mezan_key": "NAME:item c",
        "status": "candidate_match", "qoyod_product_id": None,
        "candidate_qoyod_id": "Q3"})
    res = await confirm_candidate(
        db, user_id="main", kind="products",
        mezan_key="NAME:item c", qoyod_id="Q3")
    assert res["matched"] == 1
    doc = await db.qoyod_migration_products.find_one(
        {"user_id": "main", "mezan_key": "NAME:item c"})
    assert doc["status"] == "auto_mapped"
    assert doc["qoyod_product_id"] == "Q3"
    assert doc["matched_on"] == "manual_confirmation"


@pytest.mark.asyncio
async def test_migration_is_read_only_against_qoyod(db):
    """Defence in depth: the fake client must NOT receive any POST/PUT calls."""
    calls_made: list[str] = []

    class _AuditClient(_FakeClient):
        async def create_product(self, *a, **kw):
            calls_made.append("create_product")
        async def create_contact(self, *a, **kw):
            calls_made.append("create_contact")
        async def create_invoice(self, *a, **kw):
            calls_made.append("create_invoice")
        async def create_receipt(self, *a, **kw):
            calls_made.append("create_receipt")

    client = _AuditClient(
        products_pages=[[{"id": "Q1", "sku": "X", "name": "X"}]],
        customers_pages=[[{"id": "C1", "phone": "0500000001"}]],
    )
    await db.order_items.insert_one({
        "user_id": "main", "sku": "X", "product_name": "X",
        "unit_price": 1.0, "order_number": "O1"})
    await run_migration(db, user_id="main", api_client=client)
    assert calls_made == [], "Migration must not perform writes on Qoyod"
