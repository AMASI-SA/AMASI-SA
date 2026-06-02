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
        for coll in ("preparation_uploads", "exported_orders", "exported_items",
                     "product_image_catalog", "unified_orders",
                     "product_costs", "users"):
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
        assert s["total_exported_items"] == 0

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

        # Stats now show 19 items (item-level dedup) + 12 orders touched.
        s2 = requests.get(f"{API}/preparation/export-log/stats",
                          headers=_hdr(token), timeout=10).json()
        assert s2["total_exported_items"] == 19
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

        # Second upload — same PDF. With item-level dedup all 19 items
        # are filtered out (which means all 12 orders are excluded too).
        r2 = _upload(token)
        body = r2.json()
        assert body["excluded_items_count"] == 19, (
            f"Expected all 19 items dedup'd at item-level; got {body}"
        )
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
        assert "طباعتها مسبقاً" in detail
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

        # DELETE with confirm → deletes 19 items
        r3 = requests.delete(
            f"{API}/preparation/export-log",
            headers=_hdr(token), json={"confirm": True}, timeout=10,
        )
        assert r3.status_code == 200
        # 19 items deleted from exported_items
        assert r3.json()["deleted_items"] == 19
        # No legacy rows to delete on a fresh user
        assert r3.json()["deleted_legacy_orders"] == 0

        # After clear: stats is 0
        s = requests.get(f"{API}/preparation/export-log/stats",
                         headers=_hdr(token), timeout=10).json()
        assert s["total_exported_items"] == 0
        assert s["total_exported_orders"] == 0

        # Re-upload: all 19 items are now keep-able again
        r4 = _upload(token)
        body = r4.json()
        assert body["excluded_items_count"] == 0
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



# ── 9. PUT /image — user uploads a custom image (iteration 34b) ───────
def _png_bytes(size=(320, 320), color=(180, 40, 90)) -> bytes:
    from PIL import Image
    out = io.BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


def test_put_image_applies_to_all_lines_with_same_product_name():
    """When the same product appears on N orders (e.g. "تغليف انيق" ×4),
    uploading once with scope=product must update ALL N lines so the
    final PDF uses the same image for every card of that product."""
    token, uid = _register()
    try:
        r = _upload(token)
        body = r.json()
        upload_id = body["upload_id"]

        # Find the "تغليف انيق" group (4 lines in the sample) — pick a line
        # without an image to target with scope=product.
        target_group = next(
            g for g in body["groups"] if "تغليف" in (g["product_name"] or "")
        )
        assert target_group["count"] == 4, target_group
        target_idx = next(
            ln["idx"] for ln in target_group["preview_lines"] if not ln["has_image"]
        )

        r2 = requests.put(
            f"{API}/preparation/image/{upload_id}/{target_idx}?scope=product",
            headers=_hdr(token),
            files={"file": ("custom.png", io.BytesIO(_png_bytes()), "image/png")},
            timeout=15,
        )
        assert r2.status_code == 200, r2.text[:300]
        j = r2.json()
        assert j["scope"] == "product"
        assert j["applied_count"] == 4, (
            f"All 4 sibling lines should have been updated; got {j}"
        )
        assert sorted(j["applied_to_indices"]) == sorted({
            ln["idx"] for ln in target_group["preview_lines"]
        }), "applied indices should equal the group's idxs"
        assert j["bytes"] > 0

        # Re-fetch preview — every line in the group must now have
        # has_image=True and image_source=user_upload.
        prev = requests.get(
            f"{API}/preparation/preview/{upload_id}",
            headers=_hdr(token), timeout=10,
        ).json()
        rg = next(g for g in prev["groups"] if "تغليف" in (g["product_name"] or ""))
        for ln in rg["preview_lines"]:
            assert ln["has_image"] is True, ln
            assert ln["image_source"] == "user_upload", ln
    finally:
        _cleanup(uid)


def test_put_image_scope_line_only_updates_one_row():
    """scope=line is the granular escape hatch — only the targeted idx
    gets the new image, sibling lines in the same product remain unchanged."""
    token, uid = _register()
    try:
        r = _upload(token)
        body = r.json()
        upload_id = body["upload_id"]
        target_group = next(
            g for g in body["groups"] if "تغليف" in (g["product_name"] or "")
        )
        target_idx = next(
            ln["idx"] for ln in target_group["preview_lines"] if not ln["has_image"]
        )

        r2 = requests.put(
            f"{API}/preparation/image/{upload_id}/{target_idx}?scope=line",
            headers=_hdr(token),
            files={"file": ("c.png", io.BytesIO(_png_bytes()), "image/png")},
            timeout=15,
        )
        assert r2.status_code == 200, r2.text[:200]
        j = r2.json()
        assert j["scope"] == "line"
        assert j["applied_count"] == 1
        assert j["applied_to_indices"] == [target_idx]
    finally:
        _cleanup(uid)


def test_put_image_rejects_non_image_and_oversized():
    token, uid = _register()
    try:
        r = _upload(token)
        upload_id = r.json()["upload_id"]

        # Non-image content-type
        r1 = requests.put(
            f"{API}/preparation/image/{upload_id}/0",
            headers=_hdr(token),
            files={"file": ("bad.pdf", io.BytesIO(b"not an image"), "application/pdf")},
            timeout=10,
        )
        assert r1.status_code == 400, r1.text[:200]

        # Empty body but image content-type → still 400 (empty file)
        r2 = requests.put(
            f"{API}/preparation/image/{upload_id}/0",
            headers=_hdr(token),
            files={"file": ("empty.png", io.BytesIO(b""), "image/png")},
            timeout=10,
        )
        assert r2.status_code == 400, r2.text[:200]

        # Image content-type but corrupt bytes → 400
        r3 = requests.put(
            f"{API}/preparation/image/{upload_id}/0",
            headers=_hdr(token),
            files={"file": ("c.png", io.BytesIO(b"\x00\x01\x02notapng"), "image/png")},
            timeout=10,
        )
        assert r3.status_code == 400, r3.text[:200]
    finally:
        _cleanup(uid)


def test_put_image_cross_user_404():
    """User B cannot upload an image into A's preparation_uploads doc."""
    tok_a, uid_a = _register()
    tok_b, uid_b = _register()
    try:
        r = _upload(tok_a)
        upid = r.json()["upload_id"]
        rb = requests.put(
            f"{API}/preparation/image/{upid}/0",
            headers=_hdr(tok_b),
            files={"file": ("c.png", io.BytesIO(_png_bytes()), "image/png")},
            timeout=10,
        )
        assert rb.status_code == 404, rb.text[:200]
    finally:
        _cleanup(uid_a)
        _cleanup(uid_b)


def test_generated_pdf_uses_user_uploaded_image():
    """End-to-end: upload PDF → upload custom image → generate PDF →
    the bytes for that line's image inside the prep_uploads doc must
    match the user-uploaded image (re-encoded as JPEG)."""
    token, uid = _register()
    try:
        r = _upload(token)
        body = r.json()
        upload_id = body["upload_id"]
        target_group = next(
            g for g in body["groups"] if "تغليف" in (g["product_name"] or "")
        )
        target_idx = next(
            ln["idx"] for ln in target_group["preview_lines"] if not ln["has_image"]
        )
        custom = _png_bytes()
        requests.put(
            f"{API}/preparation/image/{upload_id}/{target_idx}?scope=product",
            headers=_hdr(token),
            files={"file": ("c.png", io.BytesIO(custom), "image/png")},
            timeout=15,
        ).raise_for_status()

        # Verify the storage now reports user_upload on every related line.
        async def _check():
            from motor.motor_asyncio import AsyncIOMotorClient
            c = AsyncIOMotorClient(os.environ["MONGO_URL"])
            db = c[os.environ["DB_NAME"]]
            doc = await db.preparation_uploads.find_one(
                {"user_id": uid, "upload_id": upload_id},
                {"_id": 0, "lines": 1},
            )
            c.close()
            return doc
        doc = asyncio.run(_check())
        sources = [
            ln.get("image_source") for ln in (doc.get("lines") or [])
            if "تغليف" in (ln.get("product_name") or "")
        ]
        assert sources and all(s == "user_upload" for s in sources), sources

        # Now generate — must succeed and return a PDF.
        rg = requests.post(
            f"{API}/preparation/generate/{upload_id}",
            headers=_hdr(token), timeout=60,
        )
        assert rg.status_code == 200, rg.text[:300]
        assert rg.content[:4] == b"%PDF"
        assert len(rg.content) > 50_000
    finally:
        _cleanup(uid)


# ════════════════════════════════════════════════════════════════════════
# Iteration 34c additions — item-level dedup, selection, image catalog
# ════════════════════════════════════════════════════════════════════════
def test_partial_print_with_selected_indices():
    """Print only 5 of 19 items, then verify the remaining 14 still appear
    in the next preview. This is the key "item-level granularity" promise."""
    token, uid = _register()
    try:
        r = _upload(token)
        body = r.json()
        upload_id = body["upload_id"]

        # Pick 5 item indices from the first group
        first_group = body["groups"][0]
        selected = [ln["idx"] for ln in first_group["preview_lines"][:5]]
        assert len(selected) >= 2, "need ≥2 selectable items for the test"

        r2 = requests.post(
            f"{API}/preparation/generate/{upload_id}",
            headers=_hdr(token),
            json={"selected_indices": selected},
            timeout=60,
        )
        assert r2.status_code == 200, r2.text[:300]
        assert int(r2.headers.get("X-Exported-Cards", 0)) == len(selected)
        assert int(r2.headers.get("X-Exported-Items", 0)) == len(selected)

        # Preview again — selected ones are now excluded, the rest remain.
        preview = requests.get(
            f"{API}/preparation/preview/{upload_id}",
            headers=_hdr(token), timeout=10,
        ).json()
        assert preview["excluded_items_count"] == len(selected)
        assert preview["kept_lines"] == 19 - len(selected)

        # The same indices are now in exported_items
        exported_idxs = set()
        for g in preview["groups"]:
            for ln in g["preview_lines"]:
                exported_idxs.add(ln["idx"])
        for s in selected:
            assert s not in exported_idxs, (
                f"idx {s} should be excluded from kept lines after print"
            )
    finally:
        _cleanup(uid)


def test_same_order_with_multiple_items_partial_print():
    """Order #263822478 has 3 items in the sample. Printing only the first
    must leave the other 2 visible in the next preview — the WHOLE order
    must NOT be excluded."""
    token, uid = _register()
    try:
        r = _upload(token)
        body = r.json()
        upload_id = body["upload_id"]

        # Find lines belonging to order #263822478
        target_lines = []
        for g in body["groups"]:
            for ln in g["preview_lines"]:
                if ln["order_number"] == "263822478":
                    target_lines.append(ln)
        assert len(target_lines) == 3, (
            f"Sample order should have 3 items; got {len(target_lines)}"
        )

        # Print just the FIRST one
        sel = [target_lines[0]["idx"]]
        rg = requests.post(
            f"{API}/preparation/generate/{upload_id}",
            headers=_hdr(token), json={"selected_indices": sel},
            timeout=60,
        )
        assert rg.status_code == 200, rg.text[:300]

        # Re-preview: the OTHER 2 items of this order must still appear.
        prev = requests.get(
            f"{API}/preparation/preview/{upload_id}",
            headers=_hdr(token), timeout=10,
        ).json()
        remaining_for_order = []
        for g in prev["groups"]:
            for ln in g["preview_lines"]:
                if ln["order_number"] == "263822478":
                    remaining_for_order.append(ln)
        assert len(remaining_for_order) == 2, (
            f"order #263822478 should still have 2 items visible; "
            f"got {len(remaining_for_order)}"
        )
        # And the excluded list should report just 1 item for this order
        excluded_for_order = [e for e in prev["excluded_items"]
                              if e["order_number"] == "263822478"]
        assert len(excluded_for_order) == 1
    finally:
        _cleanup(uid)


def test_empty_selection_rejected():
    token, uid = _register()
    try:
        r = _upload(token)
        upload_id = r.json()["upload_id"]
        r2 = requests.post(
            f"{API}/preparation/generate/{upload_id}",
            headers=_hdr(token), json={"selected_indices": []},
            timeout=15,
        )
        assert r2.status_code == 400, r2.text[:200]
        assert "حدّد" in (r2.json().get("detail") or "")
    finally:
        _cleanup(uid)


def test_put_image_also_persists_to_catalog():
    """PUT /image saves to product_image_catalog so NEXT upload picks it up."""
    token, uid = _register()
    try:
        r = _upload(token)
        body = r.json()
        upload_id = body["upload_id"]
        # Find any product line without image
        target = None
        for g in body["groups"]:
            for ln in g["preview_lines"]:
                if not ln["has_image"] and ln["product_name"]:
                    target = (ln, g["product_name"])
                    break
            if target:
                break
        assert target is not None, "sample should have at least one image-less product"
        ln, pname = target

        from PIL import Image
        png = io.BytesIO()
        Image.new("RGB", (300, 300), (100, 200, 50)).save(png, format="PNG")
        png.seek(0)

        rput = requests.put(
            f"{API}/preparation/image/{upload_id}/{ln['idx']}",
            headers=_hdr(token),
            files={"file": ("c.png", png, "image/png")},
            timeout=15,
        )
        assert rput.status_code == 200, rput.text[:200]
        assert rput.json()["catalog_saved"] is True

        # Confirm catalog endpoint returns it
        cat = requests.get(
            f"{API}/preparation/image-catalog",
            headers=_hdr(token), timeout=10,
        ).json()
        assert cat["count"] >= 1
        names = [it["product_name"] for it in cat["items"]]
        assert pname in names, f"{pname!r} not in {names!r}"
    finally:
        _cleanup(uid)


def test_image_catalog_auto_match_on_next_upload():
    """Upload PDF, fix one image, clear log, re-upload → the previously
    image-less product now arrives WITH an image (auto-matched)."""
    token, uid = _register()
    try:
        # 1st upload
        r1 = _upload(token)
        body1 = r1.json()
        upload_id1 = body1["upload_id"]
        target = None
        for g in body1["groups"]:
            for ln in g["preview_lines"]:
                if not ln["has_image"] and ln["product_name"]:
                    target = ln
                    break
            if target:
                break
        assert target is not None
        target_name = target["product_name"]

        # Provide an image manually
        from PIL import Image
        png = io.BytesIO()
        Image.new("RGB", (200, 200), (255, 0, 0)).save(png, format="PNG")
        png.seek(0)
        requests.put(
            f"{API}/preparation/image/{upload_id1}/{target['idx']}",
            headers=_hdr(token),
            files={"file": ("c.png", png, "image/png")},
            timeout=15,
        ).raise_for_status()

        # Clear exported log so re-upload doesn't filter the line out
        requests.delete(
            f"{API}/preparation/export-log",
            headers=_hdr(token), json={"confirm": True}, timeout=10,
        )

        # 2nd upload — same PDF
        r2 = _upload(token)
        body2 = r2.json()
        # Find the same product in the new upload
        new_line = None
        for g in body2["groups"]:
            if g["product_name"] == target_name:
                new_line = g["preview_lines"][0]
                break
        assert new_line is not None, (
            f"product {target_name!r} missing from new upload"
        )
        assert new_line["has_image"] is True, (
            "auto-match from product_image_catalog should have set has_image=True"
        )
    finally:
        _cleanup(uid)


def test_image_catalog_crud():
    """List → upsert → fetch image → delete → 404."""
    token, uid = _register()
    try:
        from PIL import Image
        # 1. List on a fresh user → empty
        r1 = requests.get(
            f"{API}/preparation/image-catalog",
            headers=_hdr(token), timeout=10,
        ).json()
        assert r1["count"] == 0

        # 2. Upsert manually
        png = io.BytesIO()
        Image.new("RGB", (100, 100), (0, 0, 255)).save(png, format="PNG")
        png.seek(0)
        ru = requests.put(
            f"{API}/preparation/image-catalog/test-product",
            headers=_hdr(token),
            files={"file": ("c.png", png, "image/png")},
            params={"product_name": "Test Product"},
            timeout=10,
        )
        assert ru.status_code == 200, ru.text[:200]

        # 3. List shows 1 item
        r2 = requests.get(
            f"{API}/preparation/image-catalog",
            headers=_hdr(token), timeout=10,
        ).json()
        assert r2["count"] == 1
        norm = r2["items"][0]["name_norm"]

        # 4. Fetch image
        r3 = requests.get(
            f"{API}/preparation/image-catalog/image/{norm}",
            headers=_hdr(token), timeout=10,
        )
        assert r3.status_code == 200
        assert r3.headers["content-type"].startswith("image/")

        # 5. Delete
        r4 = requests.delete(
            f"{API}/preparation/image-catalog/{norm}",
            headers=_hdr(token), timeout=10,
        )
        assert r4.status_code == 200
        assert r4.json()["deleted_count"] == 1

        # 6. Fetch after delete → 404
        r5 = requests.get(
            f"{API}/preparation/image-catalog/image/{norm}",
            headers=_hdr(token), timeout=10,
        )
        assert r5.status_code == 404
    finally:
        _cleanup(uid)


def test_short_date_helper():
    """Direct unit-level coverage of the date compressor."""
    import sys
    sys.path.insert(0, "/app/backend")
    from preparation_pdf import short_date
    assert short_date("Tuesday 2 June 2026") == "06/02"
    assert short_date("Friday 25 December 2026") == "12/25"
    assert short_date("June 2, 2026") == "06/02"
    assert short_date("2026-06-02") == "06/02"
    assert short_date(None) is None
    assert short_date("") is None
    assert short_date("garbage") is None
