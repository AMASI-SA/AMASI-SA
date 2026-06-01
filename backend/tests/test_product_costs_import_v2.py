"""Tests for iteration-20 import enhancements:
- Expanded HEADER_ALIASES (cost: 'الكلفة', 'سعر الشراء', 'purchase_price';
  sku: 'Reference', 'كود المنتج', 'Product Code').
- Supplier columns in Excel are NEVER imported, even if present.
- Unmapped columns are saved verbatim in `meta` dict.
- update_existing=False causes duplicate SKUs to be SKIPPED (not updated).
- Manual supplier_country + supplier_notes are NOT clobbered on subsequent imports.

Run:
  export REACT_APP_BACKEND_URL=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d '=' -f2)
  pytest /app/backend/tests/test_product_costs_import_v2.py -v
"""
from __future__ import annotations

import io
import os
import uuid
import asyncio
import pytest
import requests
from dotenv import load_dotenv
from openpyxl import Workbook

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"pcimp-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "PC Imp v2", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _xlsx(rows, headers):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(token, xlsx_bytes, update_existing=True):
    files = {"file": ("test.xlsx", xlsx_bytes,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    url = f"{API}/product-costs/import"
    if update_existing is False:
        url += "?update_existing=false"
    elif update_existing is True:
        url += "?update_existing=true"
    return requests.post(url,
                         headers={"Authorization": f"Bearer {token}"},
                         files=files, timeout=15)


# ── Expanded aliases ──────────────────────────────────────────────────────
class TestExpandedAliases:
    def test_arabic_kalfa_and_reference(self):
        token, _ = _register()
        xlsx = _xlsx(
            headers=["Reference", "اسم المنتج", "الكلفة"],
            rows=[
                ["P-001", "منتج أ", 12.5],
                ["P-002", "منتج ب", 30.0],
            ],
        )
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        b = r.json()
        assert b["created"] == 2
        assert b["updated"] == 0

    def test_english_purchase_price_and_product_code(self):
        token, _ = _register()
        xlsx = _xlsx(
            headers=["Product Code", "title", "purchase_price"],
            rows=[["X-99", "Item X", 7.7]],
        )
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 1

    def test_arabic_sa_arabic_price(self):
        token, _ = _register()
        xlsx = _xlsx(
            headers=["كود المنتج", "الاسم", "سعر الشراء"],
            rows=[["AR-1", "اسم", 50.0]],
        )
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        assert r.json()["created"] == 1


# ── Supplier NEVER imported ───────────────────────────────────────────────
class TestSupplierNeverImported:
    def test_supplier_column_in_excel_is_ignored(self):
        token, _ = _register()
        # Excel has a "supplier" column but it MUST NOT populate
        # supplier_name on the product_costs doc.
        xlsx = _xlsx(
            headers=["sku", "product_name", "cost", "supplier", "barcode"],
            rows=[["SKU1", "Name1", 10.0, "Should Be Ignored", "BAR123"]],
        )
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        # Read the row back
        r = requests.get(f"{API}/product-costs/", headers=_headers(token), timeout=10)
        items = r.json()["items"]
        assert len(items) == 1
        item = items[0]
        # supplier_name MUST be empty (NOT 'Should Be Ignored')
        assert item.get("supplier_name", "") == "", (
            f"Supplier was imported from Excel but should be ignored: "
            f"got {item.get('supplier_name')!r}"
        )
        # The 'supplier' AND 'barcode' columns must be preserved in meta.
        meta = item.get("meta") or {}
        # The header text "supplier" (lowercase) is in meta now since
        # supplier is NOT in HEADER_ALIASES anymore.
        assert "supplier" in {k.lower() for k in meta.keys()}
        assert "barcode" in {k.lower() for k in meta.keys()}

    def test_manual_supplier_preserved_across_imports(self):
        """Merchant adds supplier_name manually via UI, then re-imports
        Excel — supplier_name MUST survive."""
        token, _ = _register()
        # 1) Initial import (no supplier in file)
        xlsx1 = _xlsx(headers=["sku", "product_name", "cost"],
                      rows=[["KEEP1", "Item Keep", 10.0]])
        r1 = _import(token, xlsx1)
        assert r1.json()["created"] == 1
        # 2) Find the row and set supplier manually via PUT
        r_list = requests.get(f"{API}/product-costs/?search=KEEP1",
                              headers=_headers(token), timeout=10)
        item_id = r_list.json()["items"][0]["id"]
        requests.put(f"{API}/product-costs/{item_id}",
                     headers=_headers(token),
                     json={"supplier_name": "مورد محلي",
                           "supplier_country": "السعودية",
                           "supplier_notes": "دفع آجل 30 يوم"},
                     timeout=10).raise_for_status()
        # 3) Re-import the same SKU with NEW cost
        xlsx2 = _xlsx(headers=["sku", "product_name", "cost"],
                      rows=[["KEEP1", "Item Keep v2", 12.0]])
        r2 = _import(token, xlsx2)
        body = r2.json()
        assert body["updated"] == 1
        # 4) Verify supplier_* still intact, cost updated
        r_list = requests.get(f"{API}/product-costs/?search=KEEP1",
                              headers=_headers(token), timeout=10)
        it = r_list.json()["items"][0]
        assert it["cost_price"] == 12.0
        assert it["product_name"] == "Item Keep v2"
        assert it["supplier_name"] == "مورد محلي"
        assert it["supplier_country"] == "السعودية"
        assert it["supplier_notes"] == "دفع آجل 30 يوم"


# ── Meta preservation ─────────────────────────────────────────────────────
class TestMetaPreservation:
    def test_unmapped_columns_saved_in_meta_dict(self):
        token, _ = _register()
        # 7-column Salla-like export — only 3 are mapped, 4 go to meta.
        xlsx = _xlsx(
            headers=["sku", "name", "cost", "barcode", "weight", "category", "stock"],
            rows=[["S1", "Sample", 5.0, "BC1", 0.5, "Jewelry", 100]],
        )
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        body = r.json()
        # 4 unmapped columns
        assert set(body["meta_columns_preserved"]) == {"barcode", "weight",
                                                       "category", "stock"}
        r = requests.get(f"{API}/product-costs/", headers=_headers(token), timeout=10)
        item = r.json()["items"][0]
        meta = item.get("meta") or {}
        meta_lower = {k.lower(): v for k, v in meta.items()}
        assert meta_lower["barcode"] == "BC1"
        assert float(meta_lower["weight"]) == 0.5
        assert meta_lower["category"] == "Jewelry"
        assert int(meta_lower["stock"]) == 100


# ── update_existing flag ──────────────────────────────────────────────────
class TestUpdateExistingFlag:
    def test_update_existing_false_skips_duplicates(self):
        token, _ = _register()
        # Seed one
        _import(token,
                _xlsx(headers=["sku", "product_name", "cost"],
                      rows=[["DUP1", "Original", 50.0]])).raise_for_status() \
            if False else None  # keep test readable
        r0 = _import(token,
                     _xlsx(headers=["sku", "product_name", "cost"],
                           rows=[["DUP1", "Original", 50.0]]),
                     update_existing=True)
        assert r0.json()["created"] == 1

        # Re-import with update_existing=False — must SKIP, not update
        r1 = _import(token,
                     _xlsx(headers=["sku", "product_name", "cost"],
                           rows=[
                               ["DUP1", "Should Not Update", 999.99],
                               ["NEW1", "Brand New", 7.0],
                           ]),
                     update_existing=False)
        b = r1.json()
        assert b["skipped"] == 1, f"Expected skipped=1, got {b}"
        assert b["created"] == 1   # NEW1
        assert b["updated"] == 0
        # Verify DUP1's price was NOT changed
        r_list = requests.get(f"{API}/product-costs/?search=DUP1",
                              headers=_headers(token), timeout=10)
        it = r_list.json()["items"][0]
        assert it["cost_price"] == 50.0
        assert it["product_name"] == "Original"

    def test_update_existing_true_updates(self):
        token, _ = _register()
        _import(token,
                _xlsx(headers=["sku", "name", "cost"],
                      rows=[["UPD1", "v1", 10.0]]),
                update_existing=True).raise_for_status()
        r = _import(token,
                    _xlsx(headers=["sku", "name", "cost"],
                          rows=[["UPD1", "v2", 11.0]]),
                    update_existing=True)
        b = r.json()
        assert b["updated"] == 1
        assert b["created"] == 0
        assert b["skipped"] == 0
