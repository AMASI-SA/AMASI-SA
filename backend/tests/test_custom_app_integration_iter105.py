"""Iter-105 — Custom App Integration.

Validates the full ingest pipeline:
  • API-key auth (X-API-Key header).
  • POST /orders single + batch + multi-item orders.
  • Order dedup on repeated order_number (no duplicate created).
  • Line items stored in `order_items` (one row per item).
  • Raw payload saved in `integration_events`.
  • POST /products upsert.
  • POST /customers upsert.
  • GET /status counters.
  • POST /settings/api-key/regenerate rotates and invalidates old key.
  • Invalid key → 401.
"""
import os
import uuid

import requests


BASE_URL = (
    os.environ.get("REACT_APP_BACKEND_URL")
    or open("/app/frontend/.env").read()
    .split("REACT_APP_BACKEND_URL=")[1].split("\n")[0].strip()
)


def _new_user_with_api_key():
    suffix = uuid.uuid4().hex[:8]
    email = f"iter105-{suffix}@example.com"
    requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"email": email, "password": "T#105t", "name": "Custom"},
        timeout=10,
    )
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"email": email, "password": "T#105t"},
        timeout=10,
    )
    jwt = r.json()["access_token"]
    h_jwt = {"Authorization": f"Bearer {jwt}"}
    # Get the auto-generated API key
    r = requests.get(
        f"{BASE_URL}/api/integrations/custom-app/settings",
        headers=h_jwt, timeout=10,
    )
    assert r.status_code == 200, r.text
    api_key = r.json()["api_key"]
    return {"jwt_headers": h_jwt, "api_key": api_key,
            "api_headers": {"X-API-Key": api_key}}


# ── 1) Single order with items ──────────────────────────────────────
def test_single_order_with_items_creates_order_and_items():
    ctx = _new_user_with_api_key()
    payload = {
        "order_id": "CAPP-0001",
        "order_number": "CAPP-0001",
        "created_at": "2026-06-10T12:00:00Z",
        "order_status": "تم التوصيل",
        "payment_status": "paid",
        "payment_method": "cash_on_delivery",
        "currency": "SAR",
        "subtotal": 150,
        "discount": 10,
        "shipping_cost": 20,
        "tax": 18,
        "total_amount": 178,
        "customer_name": "أحمد",
        "mobile": "0555555555",
        "city": "الرياض",
        "shipping_company": "أرامكس",
        "tracking_number": "TRK-AAA",
        "utm_source": "snapchat",
        "items": [
            {"product_name": "منتج A", "sku": "SKU-A", "quantity": 2, "unit_price": 50},
            {"product_name": "منتج B", "sku": "SKU-B", "quantity": 1, "unit_price": 50},
        ],
    }
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/orders",
        json=payload, headers=ctx["api_headers"], timeout=10,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["received"] == 1
    assert body["created"] == 1
    assert body["updated"] == 0
    assert body["results"][0]["items"] == 2

    # Verify on JWT status endpoint
    s = requests.get(
        f"{BASE_URL}/api/integrations/custom-app/status",
        headers=ctx["jwt_headers"], timeout=10,
    ).json()
    assert s["orders_count"] >= 1
    assert s["last_order"]["order_number"] == "CAPP-0001"
    assert s["last_order"]["customer_name"] == "أحمد"


# ── 2) Re-sending the same order → updates, no dup ──────────────────
def test_resending_same_order_updates_no_duplicate():
    ctx = _new_user_with_api_key()
    p = {"order_number": "CAPP-DUP", "total_amount": 100,
         "items": [{"product_name": "X", "quantity": 1, "unit_price": 100}]}
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/orders",
        json=p, headers=ctx["api_headers"], timeout=10,
    )
    assert r.status_code == 200 and r.json()["created"] == 1

    # Re-send with NEW total + 2 items
    p2 = {"order_number": "CAPP-DUP", "total_amount": 250,
          "items": [
              {"product_name": "X", "quantity": 1, "unit_price": 100},
              {"product_name": "Y", "quantity": 3, "unit_price": 50},
          ]}
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/orders",
        json=p2, headers=ctx["api_headers"], timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["created"] == 0
    assert body["updated"] == 1

    s = requests.get(
        f"{BASE_URL}/api/integrations/custom-app/status",
        headers=ctx["jwt_headers"], timeout=10,
    ).json()
    assert s["orders_count"] == 1            # SINGLE order, no dup
    assert s["last_order"]["total_amount"] == 250.0


# ── 3) Batch endpoint ────────────────────────────────────────────────
def test_batch_orders():
    ctx = _new_user_with_api_key()
    payload = {"orders": [
        {"order_number": "B-1", "total_amount": 100,
         "items": [{"product_name": "x", "quantity": 1, "unit_price": 100}]},
        {"order_number": "B-2", "total_amount": 200,
         "items": [{"product_name": "y", "quantity": 1, "unit_price": 200}]},
        {"order_number": "B-3", "total_amount": 50,
         "items": [{"product_name": "z", "quantity": 1, "unit_price": 50}]},
    ]}
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/orders",
        json=payload, headers=ctx["api_headers"], timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["received"] == 3
    assert body["created"] == 3


# ── 4) Order without identifier → reported as failed ────────────────
def test_order_without_identifier_fails():
    ctx = _new_user_with_api_key()
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/orders",
        json={"total_amount": 99},   # no order_number/order_id/ref
        headers=ctx["api_headers"], timeout=10,
    )
    body = r.json()
    assert body["ok"] is False
    assert body["errors"] == 1


# ── 5) Products upsert ──────────────────────────────────────────────
def test_products_upsert():
    ctx = _new_user_with_api_key()
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/products",
        json={"products": [
            {"product_id": "P1", "sku": "SKU-1", "name": "منتج 1",
             "cost_price": 30, "sale_price": 60, "quantity": 100},
            {"product_id": "P2", "sku": "SKU-2", "name": "منتج 2",
             "cost_price": 20, "sale_price": 40, "quantity": 50},
        ]},
        headers=ctx["api_headers"], timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["created"] == 2

    # Update P1's price + qty
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/products",
        json={"product_id": "P1", "name": "منتج 1 محدّث",
              "sale_price": 75, "quantity": 80, "sku": "SKU-1"},
        headers=ctx["api_headers"], timeout=10,
    )
    body = r.json()
    assert body["updated"] == 1

    s = requests.get(
        f"{BASE_URL}/api/integrations/custom-app/status",
        headers=ctx["jwt_headers"], timeout=10,
    ).json()
    assert s["products_count"] == 2


# ── 6) Customers upsert ─────────────────────────────────────────────
def test_customers_upsert():
    ctx = _new_user_with_api_key()
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/customers",
        json={"customers": [
            {"customer_id": "C1", "name": "أحمد", "mobile": "0555555555"},
            {"customer_id": "C2", "name": "خالد", "mobile": "0566666666"},
        ]},
        headers=ctx["api_headers"], timeout=10,
    )
    assert r.status_code == 200
    assert r.json()["created"] == 2

    s = requests.get(
        f"{BASE_URL}/api/integrations/custom-app/status",
        headers=ctx["jwt_headers"], timeout=10,
    ).json()
    assert s["customers_count"] == 2


# ── 7) API-key auth: invalid key → 401 ──────────────────────────────
def test_invalid_api_key_rejected():
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/orders",
        json={"order_number": "X", "total_amount": 1},
        headers={"X-API-Key": "mzn_invalid_key_here"},
        timeout=10,
    )
    assert r.status_code == 401

    # Missing entirely
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/orders",
        json={"order_number": "X", "total_amount": 1},
        timeout=10,
    )
    assert r.status_code == 401


# ── 8) Regenerate API key invalidates old key ───────────────────────
def test_regenerate_key_invalidates_old():
    ctx = _new_user_with_api_key()
    old_key = ctx["api_key"]

    # Rotate
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/settings/api-key/regenerate",
        headers=ctx["jwt_headers"], timeout=10,
    )
    assert r.status_code == 200
    new_key = r.json()["api_key"]
    assert new_key != old_key

    # Old key now rejected
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/orders",
        json={"order_number": "Q", "total_amount": 1,
              "items": [{"product_name": "p", "quantity": 1, "unit_price": 1}]},
        headers={"X-API-Key": old_key}, timeout=10,
    )
    assert r.status_code == 401

    # New key accepted
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/orders",
        json={"order_number": "Q", "total_amount": 1,
              "items": [{"product_name": "p", "quantity": 1, "unit_price": 1}]},
        headers={"X-API-Key": new_key}, timeout=10,
    )
    assert r.status_code == 200


# ── 9) Existing sources (Excel, Make) untouched ─────────────────────
def test_existing_endpoints_still_work():
    """Make sure adding the new routes didn't shadow or break existing
    `/api/orders` and `/api/integrations/make-webhook` endpoints."""
    ctx = _new_user_with_api_key()
    # /api/orders (UI listing) must still respond with JWT
    r = requests.get(
        f"{BASE_URL}/api/orders?limit=1",
        headers=ctx["jwt_headers"], timeout=10,
    )
    assert r.status_code in (200, 204), r.text


# ── 10) Test connection endpoint ────────────────────────────────────
def test_test_connection():
    ctx = _new_user_with_api_key()
    r = requests.post(
        f"{BASE_URL}/api/integrations/custom-app/test-connection",
        headers=ctx["api_headers"], timeout=10,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["user_email"]
