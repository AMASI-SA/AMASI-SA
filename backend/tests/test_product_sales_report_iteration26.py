"""Iteration 26 — Product Sales Report + Dashboard Card extensions +
Auto-recompute last 2 days.

Acceptance covered:
- GET /api/product-costs/product-sales returns rows with per-product KPIs.
- Rows for uncosted products are flagged cost_status='incomplete' and
  their total_profit/profit_margin_pct are null (NOT 0).
- Totals exclude incomplete rows (real profit only).
- /summary returns linked_products_count + missing_products_count.
- After cost create/update, last-2-days orders are unconditionally
  recomputed (recent_orders_recomputed > 0 when there ARE orders).
- Default range is last 2 days when no from_date/to_date supplied.

Run:
  pytest /app/backend/tests/test_product_sales_report_iteration26.py -v
"""
from __future__ import annotations

import os
import uuid
import requests
from dotenv import load_dotenv

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"


def _register():
    email = f"i26-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "I26", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["access_token"]


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _wh(token):
    r = requests.get(f"{API}/webhook/settings", headers=_hdr(token), timeout=15)
    return r.json()["token"]


def _post_make(wh, payload):
    return requests.post(f"{API}/webhook/make/{wh}", json=payload, timeout=15)


def _seed_cost(token, sku=None, pid=None, name="منتج", price=10.0):
    body = {"product_name": name, "cost_price": price}
    if sku:
        body["sku"] = sku
    if pid:
        body["product_id"] = pid
    r = requests.post(f"{API}/product-costs/", json=body,
                      headers={**_hdr(token), "Content-Type": "application/json"},
                      timeout=15)
    r.raise_for_status()
    return r.json()


# ──────────────────────────────────────────────────────────────────────────
class TestProductSalesReport:

    def test_default_range_is_last_2_days(self):
        token = _register()
        r = requests.get(f"{API}/product-costs/product-sales",
                         headers=_hdr(token), timeout=15)
        assert r.status_code == 200, r.text
        body = r.json()
        # range.from_date should be ~yesterday, range.to_date ~today
        assert body["range"]["from_date"] <= body["range"]["to_date"]
        # Exactly 2 days span (yesterday..today inclusive)
        from datetime import date
        d_from = date.fromisoformat(body["range"]["from_date"])
        d_to = date.fromisoformat(body["range"]["to_date"])
        assert (d_to - d_from).days == 1

    def test_returns_full_kpi_columns_for_complete_product(self):
        """Order arrives for a product with cost → returns full KPIs."""
        token = _register()
        wh = _wh(token)
        _seed_cost(token, pid="P-OK-1", name="منتج كامل", price=20.0)
        # 2 units * 50 = 100 sales, 2 * 20 = 40 cost → 60 profit, 60% margin
        _post_make(wh, {
            "order_number": f"O-{uuid.uuid4().hex[:6]}",
            "total": 100, "total_amount": 100,
            "products": [{"product_id": "P-OK-1", "name": "منتج كامل",
                          "quantity": 2, "price": 50,
                          "image_url": "https://cdn.x/img.jpg"}],
        })
        r = requests.get(f"{API}/product-costs/product-sales",
                         headers=_hdr(token), timeout=15)
        body = r.json()
        row = next((it for it in body["items"]
                    if it["product_id"] == "P-OK-1"), None)
        assert row is not None, body
        # Required columns
        assert set(row.keys()) >= {
            "product_id", "sku", "name", "image_url",
            "units_sold", "total_sales", "total_cost",
            "total_profit", "profit_margin_pct", "cost_status",
        }
        assert row["units_sold"] == 2.0
        assert row["total_sales"] == 100.0
        assert row["total_cost"] == 40.0
        assert row["total_profit"] == 60.0
        assert row["profit_margin_pct"] == 60.0
        assert row["cost_status"] == "complete"
        # image_url propagated from webhook
        assert row["image_url"] == "https://cdn.x/img.jpg"

    def test_uncosted_product_flagged_incomplete_with_null_profit(self):
        """Product without cost → cost_status='incomplete', profit=null."""
        token = _register()
        wh = _wh(token)
        _post_make(wh, {
            "order_number": f"O-{uuid.uuid4().hex[:6]}",
            "total": 80, "total_amount": 80,
            "products": [{"product_id": "P-MISS", "name": "بدون تكلفة",
                          "quantity": 1, "price": 80}],
        })
        r = requests.get(f"{API}/product-costs/product-sales",
                         headers=_hdr(token), timeout=15)
        body = r.json()
        row = next((it for it in body["items"]
                    if it["product_id"] == "P-MISS"), None)
        assert row is not None
        assert row["cost_status"] == "incomplete"
        # CRITICAL: profit must be null (NOT 0) per merchant rule.
        assert row["total_profit"] is None
        assert row["profit_margin_pct"] is None
        assert row["total_sales"] == 80.0

    def test_totals_exclude_incomplete_rows(self):
        """Range totals reflect ONLY products with full cost data."""
        token = _register()
        wh = _wh(token)
        _seed_cost(token, pid="GOOD", name="معروف", price=10.0)
        # Good order: 1*30=30 sales, 1*10=10 cost → 20 profit
        _post_make(wh, {
            "order_number": f"O-G-{uuid.uuid4().hex[:6]}",
            "total": 30, "total_amount": 30,
            "products": [{"product_id": "GOOD", "name": "معروف",
                          "quantity": 1, "price": 30}],
        })
        # Bad (incomplete) order: 1*100=100 sales, NO cost
        _post_make(wh, {
            "order_number": f"O-B-{uuid.uuid4().hex[:6]}",
            "total": 100, "total_amount": 100,
            "products": [{"product_id": "BAD", "name": "غير معروف",
                          "quantity": 1, "price": 100}],
        })
        r = requests.get(f"{API}/product-costs/product-sales",
                         headers=_hdr(token), timeout=15)
        body = r.json()
        t = body["totals"]
        # All sales (both rows)
        assert t["total_sales_all"] == 130.0
        # Complete-only sales (good row only)
        assert t["total_sales_complete"] == 30.0
        assert t["total_cost_complete"] == 10.0
        assert t["total_profit_complete"] == 20.0
        # 20/30 = 66.67%
        assert abs(t["margin_complete_pct"] - 66.67) < 0.1
        assert body["incomplete_count"] == 1


# ──────────────────────────────────────────────────────────────────────────
class TestDashboardCardSummary:

    def test_summary_returns_linked_and_missing_counts(self):
        token = _register()
        # 2 with cost, 1 pending
        _seed_cost(token, pid="L1", price=10.0)
        _seed_cost(token, pid="L2", price=20.0)
        # Manual create without cost → cost_pending=True
        requests.post(f"{API}/product-costs/",
                      json={"product_id": "P-PEND", "product_name": "بدون سعر"},
                      headers={**_hdr(token), "Content-Type": "application/json"},
                      timeout=15)
        r = requests.get(f"{API}/product-costs/summary",
                         headers=_hdr(token), timeout=15)
        body = r.json()
        assert "linked_products_count" in body
        assert "missing_products_count" in body
        assert body["linked_products_count"] == 2
        assert body["missing_products_count"] >= 1

    def test_summary_today_and_month_totals_present(self):
        token = _register()
        r = requests.get(f"{API}/product-costs/summary",
                         headers=_hdr(token), timeout=15)
        body = r.json()
        assert "today_total" in body
        assert "month_total" in body
        assert "currency" in body


# ──────────────────────────────────────────────────────────────────────────
class TestAutoRecomputeLast2Days:

    def test_create_cost_recomputes_recent_orders(self):
        token = _register()
        wh = _wh(token)
        # 2 orders without cost.
        for i in range(2):
            _post_make(wh, {
                "order_number": f"O-RC-{i}-{uuid.uuid4().hex[:4]}",
                "total": 50, "total_amount": 50,
                "products": [{"product_id": "RC-1", "name": "x",
                              "quantity": 1, "price": 50}],
            })
        # Create cost → both orders must be re-counted in recent_orders_recomputed
        r = requests.post(
            f"{API}/product-costs/",
            json={"product_id": "RC-1", "product_name": "x", "cost_price": 10.0},
            headers={**_hdr(token), "Content-Type": "application/json"},
            timeout=15,
        )
        body = r.json()
        # Both orders are within last 2 days (just created) → recomputed
        assert body["recent_orders_recomputed"] >= 2

    def test_update_cost_recomputes_recent_orders(self):
        token = _register()
        wh = _wh(token)
        seeded = _seed_cost(token, pid="U-1", price=10.0)
        _post_make(wh, {
            "order_number": f"O-U-{uuid.uuid4().hex[:6]}",
            "total": 50, "total_amount": 50,
            "products": [{"product_id": "U-1", "name": "x",
                          "quantity": 1, "price": 50}],
        })
        u = requests.put(
            f"{API}/product-costs/{seeded['id']}",
            json={"cost_price": 25.0},
            headers={**_hdr(token), "Content-Type": "application/json"},
            timeout=15,
        )
        body = u.json()
        assert "recent_orders_recomputed" in body
        assert body["recent_orders_recomputed"] >= 1
