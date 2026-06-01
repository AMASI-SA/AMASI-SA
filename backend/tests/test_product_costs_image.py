"""Iteration 23: image_url import from column F + manual entry.

Covered:
- Excel with image URL in column F is auto-imported into product_costs.image_url.
- Excel with `صورة` header anywhere also imported.
- Non-URL text in column F is IGNORED (not stored as image).
- Manual create/update via API persists image_url.
- Re-import without an image column preserves the previously-stored image.

Run:
  pytest /app/backend/tests/test_product_costs_image.py -v
"""
from __future__ import annotations

import io
import os
import uuid
import requests
from dotenv import load_dotenv
from openpyxl import Workbook

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"pc-img-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "PC img", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _xlsx(rows, headers):
    wb = Workbook()
    ws = wb.active
    ws.append(headers)
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _import(token, xlsx_bytes):
    files = {"file": ("test.xlsx", xlsx_bytes,
                      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
    return requests.post(f"{API}/product-costs/import",
                         headers={"Authorization": f"Bearer {token}"},
                         files=files, timeout=15)


def _get_catalogue(token):
    r = requests.get(f"{API}/product-costs/?is_active=true",
                     headers={"Authorization": f"Bearer {token}"}, timeout=15)
    r.raise_for_status()
    return r.json()["items"]


# ──────────────────────────────────────────────────────────────────────────
class TestImageImport:

    def test_column_F_fallback_imports_image(self):
        """File with no `image` header — column F is treated as image URL."""
        token, _ = _register()
        # 6 columns: A=SKU, B=name, C=description, D=stock, E=price, F=image_url
        # Note: cost is in a later column with a recognised header.
        xlsx = _xlsx(
            headers=["SKU", "اسم المنتج", "وصف", "مخزون", "السعر", "غير معروف", "تكلفة المنتج"],
            rows=[
                ["SKU-IMG-1", "منتج بصورة", "وصف ما", 10, 50,
                 "https://cdn.salla.sa/p/img1.jpg", 18.0],
                ["SKU-IMG-2", "منتج بدون صورة", "وصف", 5, 30, "", 12.0],
            ],
        )
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["created"] == 2
        assert body["images_imported"] == 1
        assert body["image_column_detected"] == "column_F"
        # Verify catalogue
        rows = _get_catalogue(token)
        by_sku = {it["sku"]: it for it in rows}
        assert by_sku["SKU-IMG-1"]["image_url"] == "https://cdn.salla.sa/p/img1.jpg"
        assert by_sku["SKU-IMG-2"].get("image_url", "") == ""

    def test_named_header_overrides_column_F(self):
        """When `صورة` header is present (in any column), it is used instead of F."""
        token, _ = _register()
        # image is in column C; column F holds something unrelated.
        xlsx = _xlsx(
            headers=["SKU", "اسم المنتج", "صورة", "تكلفة المنتج", "بلد", "كود قديم"],
            rows=[
                ["SKU-H-1", "منتج", "https://cdn.salla.sa/p/h1.png", 25.0,
                 "SA", "OLD-001"],
            ],
        )
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["images_imported"] == 1
        assert body["image_column_detected"] == "header"
        rows = _get_catalogue(token)
        assert rows[0]["image_url"] == "https://cdn.salla.sa/p/h1.png"

    def test_non_url_text_in_column_F_is_ignored(self):
        """Random text in column F (not a URL/image path) must NOT be stored."""
        token, _ = _register()
        xlsx = _xlsx(
            headers=["SKU", "اسم المنتج", "X", "Y", "Z", "F-col", "تكلفة المنتج"],
            rows=[
                ["SKU-NOIMG", "منتج", "a", "b", "c", "random plain text", 10.0],
            ],
        )
        r = _import(token, xlsx)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["images_imported"] == 0
        rows = _get_catalogue(token)
        assert rows[0].get("image_url", "") == ""

    def test_reimport_without_image_preserves_previous(self):
        """Re-importing the same SKU without an image must NOT blank the image."""
        token, _ = _register()
        # First import WITH image (col F fallback).
        xlsx1 = _xlsx(
            headers=["SKU", "اسم المنتج", "C", "D", "E", "F", "تكلفة المنتج"],
            rows=[["SKU-PRES", "منتج محفوظ", "", "", "",
                   "https://cdn.salla.sa/p/keep.jpg", 30.0]],
        )
        r1 = _import(token, xlsx1)
        assert r1.status_code == 200
        # Confirm image was stored.
        rows = _get_catalogue(token)
        assert rows[0]["image_url"] == "https://cdn.salla.sa/p/keep.jpg"
        # Second import — only 2 cols (SKU + cost), no image column at all.
        xlsx2 = _xlsx(
            headers=["SKU", "تكلفة المنتج"],
            rows=[["SKU-PRES", 35.0]],
        )
        r2 = _import(token, xlsx2)
        assert r2.status_code == 200, r2.text
        rows2 = _get_catalogue(token)
        item = next(r for r in rows2 if r["sku"] == "SKU-PRES")
        # cost updated, image preserved.
        assert item["cost_price"] == 35.0
        assert item["image_url"] == "https://cdn.salla.sa/p/keep.jpg"


# ──────────────────────────────────────────────────────────────────────────
class TestImageManual:

    def test_create_with_image(self):
        token, _ = _register()
        r = requests.post(
            f"{API}/product-costs/",
            json={"sku": "MAN-IMG-1", "product_name": "يدوي",
                  "cost_price": 12.5,
                  "image_url": "https://example.com/manual.png"},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert r.status_code == 200, r.text
        assert r.json()["image_url"] == "https://example.com/manual.png"

    def test_update_image_url(self):
        token, _ = _register()
        c = requests.post(
            f"{API}/product-costs/",
            json={"sku": "MAN-IMG-2", "product_name": "تحديث",
                  "cost_price": 7.0},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert c.status_code == 200
        item_id = c.json()["id"]
        # Update image_url
        u = requests.put(
            f"{API}/product-costs/{item_id}",
            json={"image_url": "https://example.com/new.jpg"},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert u.status_code == 200, u.text
        assert u.json()["image_url"] == "https://example.com/new.jpg"
        # Clear image_url via empty string
        u2 = requests.put(
            f"{API}/product-costs/{item_id}",
            json={"image_url": ""},
            headers={"Authorization": f"Bearer {token}",
                     "Content-Type": "application/json"},
            timeout=15,
        )
        assert u2.status_code == 200
        assert u2.json()["image_url"] == ""
