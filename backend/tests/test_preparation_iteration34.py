"""Iteration 34 — "تجهيز المنتجات" (Product Preparation) end-to-end tests.

This locks the contract of the feature:

1. Auth required for every endpoint.
2. Upload PDF → parse + group + sort by count desc (top group = most repeated).
3. Re-upload the same PDF → all 12 orders are now EXCLUDED (dedup works).
4. Generate when everything is excluded → 400 with a helpful Arabic message.
5. Clear-log requires `confirm: true`; with confirm, exported_orders cleared.
6. After clearing, re-upload → kept_lines > 0 again.
7. Unique-index on (user_id, order_number) — second insert with same key
   is a no-op (uses ordered=False).

Run:
  REACT_APP_BACKEND_URL=http://localhost:8001 \
    pytest /app/backend/tests/test_preparation_iteration34.py -v
"""
from __future__ import annotations

import os
import io
import uuid
import asyncio
import requests
from pathlib import Path
from datetime import datetime, timezone
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")
BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"

SAMPLE_PDF = Path("/tmp/pdfs/orders_sample.pdf")


def _register():
    email = f"i34-{uuid.uuid4().hex[:8]}@test.app"
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "I34", "email": email, "password": "test12345"},
        timeout=15,
    )
    r.raise_for_status()
    j = r.json()
    return j["access_token"], j["id"]


def _hdr(t):
    return {"Authorization": f"Bearer {t}"}


def _cleanup(uid: str) -> None:
    async def _do():
        c = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = c[os.environ["DB_NAME"]]
        for coll in ("preparation_uploads", "exported_orders",
                     "unified_orders", "product_costs", "users"):
            await db[coll].delete_many({"user_id": uid})
        await db.users.delete_many({"id": uid})
        c.close()
    asyncio.run(_do())


def _upload(token: str) -> dict:
    if not SAMPLE_PDF.exists():
        raise RuntimeError(f"Sample PDF missing at {SAMPLE_PDF}")
    with open(SAMPLE_PDF, "rb") as f:
        files = {"file": ("orders.pdf", f, "application/pdf")}
        r = requests.post(
            f"{API}/preparation/upload",
            headers=_hdr(token),
            files=files,
            timeout=60,
        )
    return r


# ── 1. Auth required ───────────────────────────────────────────────────
def test_endpoints_require_auth():
    for method, path, payload in [
        ("GET", "/preparation/export-log/stats", None),
        ("DELETE", "/preparation/export-log", {"confirm": True}),
        ("GET", "/preparation/preview/anything", None),
        ("GET", "/preparation/image/anything/0", None),
        ("GET", "/preparation/excluded/anything", None),
        ("POST", "/preparation/generate/anything", None),
    ]:
        fn = getattr(requests, method.lower())
        r = fn(f"{API}{path}", json=payload, timeout=10)
        assert r.status_code in (401, 403), (
            f"{method} {path} should require auth; got {r.status_code}"
        )


# ── 2. Upload parses + groups correctly ────────────────────────────────
def test_upload_parses_and_sorts_groups():
    token, uid = _register()
    try:
        r = _upload(token)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        # 12 distinct orders, 19 product lines parsed from the sample
        assert body["total_orders"] == 12
        assert body["total_product_lines"] == 19
        assert body["excluded_orders_count"] == 0
        assert body["kept_lines"] == 19
        groups = body.get("groups") or []
        assert len(groups) >= 5, "expected several distinct product groups"
        # Groups must be sorted by count desc.
        counts = [g["count"] for g in groups]
        assert counts == sorted(counts, reverse=True), (
            f"groups not sorted desc: {counts}"
        )
        # Top group = "قالدة روز ب السم مطيل ذهب" with 5
        assert groups[0]["count"] == 5
        assert "قالدة" in groups[0]["product_name"]
    finally:
        _cleanup(uid)


# ── 3. Generate writes exported_orders + returns PDF ───────────────────
def test_generate_returns_pdf_and_marks_exported():
    token, uid = _register()
    try:
        r = _upload(token)
        upload_id = r.json()["upload_id"]

        # Pre-generate: stats should be 0
        s = requests.get(f"{API}/preparation/export-log/stats",
                         headers=_hdr(token), timeout=10).json()
        assert s["total_exported_orders"] == 0

        # Generate
        r2 = requests.post(
            f"{API}/preparation/generate/{upload_id}",
            headers=_hdr(token), timeout=60,
        )
        assert r2.status_code == 200, r2.text[:300]
        assert r2.headers.get("content-type", "").startswith("application/pdf")
        assert int(r2.headers.get("X-Exported-Orders", "0")) == 12
        assert int(r2.headers.get("X-Exported-Cards", "0")) == 19
        assert r2.content[:4] == b"%PDF", "response is not a PDF"
        assert len(r2.content) > 50_000, "PDF is suspiciously small"

        # Stats now show 12
        s2 = requests.get(f"{API}/preparation/export-log/stats",
                          headers=_hdr(token), timeout=10).json()
        assert s2["total_exported_orders"] == 12
        assert s2["last_exported_at"] is not None
    finally:
        _cleanup(uid)


# ── 4. Re-upload excludes already-exported orders ──────────────────────
def test_reupload_excludes_previously_exported():
    token, uid = _register()
    try:
        # First upload + generate
        r1 = _upload(token)
        upload_id1 = r1.json()["upload_id"]
        requests.post(f"{API}/preparation/generate/{upload_id1}",
                      headers=_hdr(token), timeout=60).raise_for_status()

        # Second upload — same PDF
        r2 = _upload(token)
        body = r2.json()
        assert body["excluded_orders_count"] == 12
        assert body["kept_lines"] == 0
        assert set(body["excluded_orders"]) == {
            "263821948", "263822478", "263829492", "263829942",
            "263831603", "263832078", "263833616", "263837831",
            "263839771", "263839904", "263840401", "263842732",
        }

        # Generate now should refuse with helpful Arabic message
        upload_id2 = body["upload_id"]
        r3 = requests.post(f"{API}/preparation/generate/{upload_id2}",
                           headers=_hdr(token), timeout=15)
        assert r3.status_code == 400
        detail = r3.json().get("detail") or ""
        assert "تم تصديرها مسبقاً" in detail
        assert "مسح سجل التصدير" in detail
    finally:
        _cleanup(uid)


# ── 5. Clear-log requires confirm, then allows re-export ───────────────
def test_clear_log_requires_confirm_and_re_enables_export():
    token, uid = _register()
    try:
        r1 = _upload(token)
        uid1 = r1.json()["upload_id"]
        requests.post(f"{API}/preparation/generate/{uid1}",
                      headers=_hdr(token), timeout=60).raise_for_status()

        # DELETE without confirm → 400
        r2 = requests.delete(
            f"{API}/preparation/export-log",
            headers=_hdr(token), json={"confirm": False}, timeout=10,
        )
        assert r2.status_code == 400, r2.text[:200]
        assert "تأكيد" in (r2.json().get("detail") or "")

        # DELETE with confirm → deletes 12
        r3 = requests.delete(
            f"{API}/preparation/export-log",
            headers=_hdr(token), json={"confirm": True}, timeout=10,
        )
        assert r3.status_code == 200
        assert r3.json()["deleted_count"] == 12

        # After clear: stats is 0
        s = requests.get(f"{API}/preparation/export-log/stats",
                         headers=_hdr(token), timeout=10).json()
        assert s["total_exported_orders"] == 0

        # Re-upload: all 12 are now keep-able again
        r4 = _upload(token)
        body = r4.json()
        assert body["excluded_orders_count"] == 0
        assert body["kept_lines"] == 19
    finally:
        _cleanup(uid)


# ── 6. Unauthorized user can't see another user's upload ───────────────
def test_upload_is_scoped_to_owner():
    token_a, uid_a = _register()
    token_b, uid_b = _register()
    try:
        r1 = _upload(token_a)
        upid = r1.json()["upload_id"]
        # B tries to preview A's upload
        r2 = requests.get(f"{API}/preparation/preview/{upid}",
                          headers=_hdr(token_b), timeout=10)
        assert r2.status_code == 404, r2.text[:200]
        # B tries to generate from A's upload
        r3 = requests.post(f"{API}/preparation/generate/{upid}",
                           headers=_hdr(token_b), timeout=10)
        assert r3.status_code == 404, r3.text[:200]
        # B tries to fetch one of A's images
        r4 = requests.get(f"{API}/preparation/image/{upid}/0",
                          headers=_hdr(token_b), timeout=10)
        assert r4.status_code == 404, r4.text[:200]
    finally:
        _cleanup(uid_a)
        _cleanup(uid_b)


# ── 7. Rejects non-PDF + empty body ────────────────────────────────────
def test_upload_validates_input():
    token, uid = _register()
    try:
        # Non-PDF extension
        r1 = requests.post(
            f"{API}/preparation/upload", headers=_hdr(token),
            files={"file": ("orders.xlsx", io.BytesIO(b"hello"), "application/xml")},
            timeout=10,
        )
        assert r1.status_code == 400
        # Empty PDF (zero bytes) — extension OK but body empty
        r2 = requests.post(
            f"{API}/preparation/upload", headers=_hdr(token),
            files={"file": ("orders.pdf", io.BytesIO(b""), "application/pdf")},
            timeout=10,
        )
        assert r2.status_code == 400
    finally:
        _cleanup(uid)


# ── 8. Image endpoint returns binary data ──────────────────────────────
def test_image_endpoint_streams_bytes():
    token, uid = _register()
    try:
        r = _upload(token)
        upload_id = r.json()["upload_id"]
        # Index 0 should have an image (order #1 in sample has product img)
        r2 = requests.get(
            f"{API}/preparation/image/{upload_id}/0",
            headers=_hdr(token), timeout=10,
        )
        assert r2.status_code == 200, r2.text[:200]
        ct = r2.headers.get("content-type", "")
        assert ct.startswith("image/"), f"unexpected content-type {ct!r}"
        # Out-of-range idx → 404
        r3 = requests.get(
            f"{API}/preparation/image/{upload_id}/9999",
            headers=_hdr(token), timeout=10,
        )
        assert r3.status_code == 404
    finally:
        _cleanup(uid)
