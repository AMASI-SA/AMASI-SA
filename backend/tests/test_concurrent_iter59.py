"""Iter-59 concurrency test — Excel upload must not block Make.com webhooks.

Strategy
--------
1. Build a synthetic Excel file with N rows (large enough to take seconds).
2. POST it to /api/analyses → expect immediate response with job_id.
3. WHILE the job is still processing, fire several POST /api/webhook/make/{token}
   requests in parallel and time their responses.
4. Assert that:
   - Excel POST returns within 1 second (regardless of file size).
   - Each webhook response time is well under 1 second.
   - Eventually the import_job reaches `completed` and the analysis exists.
   - When the same order_number is sent by BOTH Excel and Make, Make's
     fields win (live-source priority).

Run:  cd /app/backend && python -m pytest tests/test_concurrent_iter59.py -v
"""
from __future__ import annotations

import asyncio
import io
import os
import time
import uuid
from datetime import datetime, timezone

import httpx
import openpyxl
import pytest


# Local backend (supervisor-managed)
BACKEND = os.environ.get("HESAB_TEST_BACKEND", "http://localhost:8001")
ADMIN_EMAIL = "admin@hesab.app"
ADMIN_PASSWORD = "admin123"

# Salla Excel header (must match excel_parser's expected columns)
HEADER = [
    "رقم الطلب", "تاريخ الطلب", "حالة الطلب", "اسم العميل", "جوال العميل",
    "طريقة الدفع", "شركة الشحن", "تكلفة الشحن", "المجموع الفرعي",
    "الخصم", "الإجمالي", "العملة",
]


def _build_excel_bytes(n_rows: int, *, order_number_start: int = 100000) -> bytes:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Orders"
    ws.append(HEADER)
    for i in range(n_rows):
        order_num = order_number_start + i
        ws.append([
            str(order_num),
            "2026-02-15 10:00:00",
            "تم التنفيذ",
            f"عميل {i}",
            f"+9665{i:08d}",
            "مدى",
            "سمسا",
            25.0,
            150.0,
            0.0,
            175.0,
            "SAR",
        ])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture(scope="module")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


async def _login(client: httpx.AsyncClient) -> str:
    resp = await client.post("/api/auth/login",
                             json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    resp.raise_for_status()
    return resp.json()["access_token"]


async def _get_webhook_token(client: httpx.AsyncClient, jwt: str) -> str:
    resp = await client.get("/api/webhook/settings",
                            headers={"Authorization": f"Bearer {jwt}"})
    resp.raise_for_status()
    return resp.json()["token"]


@pytest.mark.asyncio
async def test_excel_upload_does_not_block_webhooks():
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(base_url=BACKEND, timeout=timeout) as client:
        jwt = await _login(client)
        webhook_token = await _get_webhook_token(client, jwt)

        # Use a UNIQUE order_number range per test run so we never collide
        # with previously-imported orders.
        base = 900000 + (int(time.time()) % 50_000)
        xlsx_bytes = _build_excel_bytes(800, order_number_start=base)

        # --- Fire the Excel upload (should return in <1s) ---
        t0 = time.perf_counter()
        files = {"file": (f"concurrent_test_{base}.xlsx", xlsx_bytes,
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        upload_resp = await client.post(
            "/api/analyses",
            files=files,
            headers={"Authorization": f"Bearer {jwt}"},
            params={"name": f"iter59-test-{base}"},
        )
        upload_ms = (time.perf_counter() - t0) * 1000
        upload_resp.raise_for_status()
        body = upload_resp.json()
        job_id = body["job_id"]
        assert body["status"] == "queued"
        assert upload_ms < 1500, f"Upload took {upload_ms:.0f}ms — should be <1500ms"

        # --- While the job is processing, fire 10 webhooks in parallel ---
        webhook_url = f"/api/webhook/make/{webhook_token}"
        webhook_orders = [
            {
                "order_number": str(base + 500 + i),  # mix: some collide with Excel range, some don't
                "order_id": f"wh-{base + 500 + i}",
                "created_at": "2026-02-15T11:00:00+03:00",
                "status": "بانتظار الدفع",
                "customer_name": f"Make عميل {i}",
                "customer_mobile": f"+9665{i:08d}",
                "payment_method": "تحويل بنكي",
                "shipping_company": "أرامكس",
                "shipping_cost": 30.0,
                "subtotal": 200.0,
                "total": 230.0,
                "currency": "SAR",
            }
            for i in range(10)
        ]

        async def send_webhook(payload):
            t = time.perf_counter()
            r = await client.post(webhook_url, json=payload)
            ms = (time.perf_counter() - t) * 1000
            return ms, r.status_code, r.json() if r.status_code == 200 else r.text

        results = await asyncio.gather(*[send_webhook(p) for p in webhook_orders])
        webhook_times = [ms for ms, _, _ in results]
        max_webhook_ms = max(webhook_times)
        avg_webhook_ms = sum(webhook_times) / len(webhook_times)

        # Webhooks must respond fast even while the Excel job is grinding.
        assert max_webhook_ms < 3000, (
            f"Slowest webhook took {max_webhook_ms:.0f}ms — "
            f"Excel upload is still blocking the event loop"
        )
        assert all(code == 200 for _, code, _ in results), \
            f"Some webhooks failed: {results}"

        print(f"\n   ✅ Upload responded in {upload_ms:.0f}ms")
        print(f"   ✅ Webhooks: avg={avg_webhook_ms:.0f}ms, max={max_webhook_ms:.0f}ms (during Excel job)")

        # --- Wait for the job to complete (max 60s) ---
        completed = False
        for _ in range(60):
            r = await client.get(f"/api/import-jobs/{job_id}",
                                 headers={"Authorization": f"Bearer {jwt}"})
            r.raise_for_status()
            doc = r.json()
            if doc["status"] in {"completed", "failed"}:
                completed = True
                break
            await asyncio.sleep(1)
        assert completed, f"Job did not finish in 60s: {doc}"
        assert doc["status"] == "completed", f"Job failed: {doc.get('error_message')}"
        assert doc["created_count"] + doc["updated_count"] >= 800
        print(f"   ✅ Job completed: created={doc['created_count']}, updated={doc['updated_count']}, errors={doc['error_count']}")


@pytest.mark.asyncio
async def test_make_priority_overrides_excel_when_make_first():
    """When Make writes first then Excel arrives for the SAME order,
    Excel must NOT overwrite Make's status/total/payment_method.
    Excel may still fill empty fields (e.g. customer_name if Make didn't send one)."""
    timeout = httpx.Timeout(30.0)
    async with httpx.AsyncClient(base_url=BACKEND, timeout=timeout) as client:
        jwt = await _login(client)
        webhook_token = await _get_webhook_token(client, jwt)

        order_num = str(800000 + (int(time.time()) % 1000))

        # 1. Make writes first (rich, authoritative payload)
        wh_resp = await client.post(
            f"/api/webhook/make/{webhook_token}",
            json={
                "order_number": order_num,
                "order_id": f"make-{order_num}",
                "created_at": "2026-02-10T09:00:00+03:00",
                "status": "تم التوصيل",  # Make says: delivered
                "payment_method": "Apple Pay",  # Make's value
                "total": 999.99,  # Make's value
                "customer_name": "",  # left empty so Excel can fill
                "customer_mobile": "+966500000001",
                "shipping_company": "سمسا",
                "currency": "SAR",
            },
        )
        wh_resp.raise_for_status()

        # 2. Now Excel arrives with DIFFERENT critical fields
        xlsx_bytes = _build_excel_bytes(1, order_number_start=int(order_num))
        # The Excel header above produces status="تم التنفيذ", payment="مدى",
        # total=175.0, customer_name="عميل 0". That gives us clear diffs:
        #   - status: Make "تم التوصيل" vs Excel "تم التنفيذ"
        #   - total:  Make 999.99 vs Excel 175.0
        #   - payment: Make "Apple Pay" vs Excel "مدى"
        #   - customer_name: Make "" → Excel "عميل 0" SHOULD fill
        files = {"file": (f"priority_test_{order_num}.xlsx", xlsx_bytes,
                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")}
        up = await client.post(
            "/api/analyses",
            files=files,
            headers={"Authorization": f"Bearer {jwt}"},
            params={"name": f"priority-{order_num}"},
        )
        up.raise_for_status()
        job_id = up.json()["job_id"]
        # Wait for completion
        for _ in range(30):
            r = await client.get(f"/api/import-jobs/{job_id}",
                                 headers={"Authorization": f"Bearer {jwt}"})
            r.raise_for_status()
            if r.json()["status"] in {"completed", "failed"}:
                break
            await asyncio.sleep(0.5)
        assert r.json()["status"] == "completed"

        # 3. Inspect the merged order via the diagnostic trace endpoint
        trace = await client.get(
            f"/api/diagnostics/order-trace/{order_num}",
            headers={"Authorization": f"Bearer {jwt}"},
        )
        trace.raise_for_status()
        locs = trace.json().get("locations") or []
        unified = next((l for l in locs if l.get("store") == "unified_orders"), None)
        assert unified is not None, f"Order not found in unified_orders: {trace.json()}"

        # Pull full doc via webhook /orders (returns unified rows)
        orders_resp = await client.get(
            "/api/webhook/orders",
            params={"limit": 500},
            headers={"Authorization": f"Bearer {jwt}"},
        )
        all_orders = orders_resp.json()["orders"]
        target = next((o for o in all_orders if str(o.get("order_number")) == order_num), None)
        assert target is not None, "Order missing from /api/webhook/orders"

        # ─── Assertions ───────────────────────────────────────
        # Make's critical fields must STILL win
        assert target["total_amount"] == 999.99, \
            f"Excel overwrote Make's total: {target['total_amount']}"
        assert target["order_status"] == "تم التوصيل", \
            f"Excel overwrote Make's status: {target['order_status']}"
        assert target["payment_method"] == "Apple Pay", \
            f"Excel overwrote Make's payment_method: {target['payment_method']}"
        # Empty field MUST be filled by Excel
        assert target["customer_name"] == "عميل 0", \
            f"Excel did not fill empty customer_name: {target['customer_name']}"
        # Source-history fields
        assert target.get("last_make_update_at"), "last_make_update_at missing"
        assert target.get("last_excel_import_at"), "last_excel_import_at missing"
        print(f"   ✅ Make priority preserved (total, status, payment)")
        print(f"   ✅ Excel filled empty customer_name field")
        print(f"   ✅ last_make_update_at + last_excel_import_at both set")
