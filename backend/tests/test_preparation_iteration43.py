"""Iter-43 — per-card editing of customer_name/size/color/note tests.

Backend contract under test:
    PATCH /api/preparation/line/{upload_id}/{idx}
        Body: { customer_name?, size?, color?, note?, scope: "line"|"product" }
        Updates the editable fields on one line OR all sibling cards
        sharing the same product (product_id → SKU → name_norm).

    POST  /api/preparation/line/{upload_id}/{idx}/reset
        Body: { scope: "line"|"product" }
        Reverts the four editable fields to their original parsed values
        captured at first storage.

The preview response surfaces `edited_fields` per card so the UI can
render a "✏️ مُعدّل" indicator without recomputing client-side.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
API = f"{BASE_URL}/api"
SAMPLE_PDF = Path("/tmp/compare/original_salla.pdf")


def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register() -> tuple[str, str]:
    r = requests.post(
        f"{API}/auth/register",
        json={"name": "iter43 Tester",
              "email": f"iter43-{uuid.uuid4().hex[:10]}@example.com",
              "password": "Test1234!"},
        timeout=10,
    )
    r.raise_for_status()
    body = r.json()
    return body["access_token"], body["id"]


def _cleanup(uid: str) -> None:
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _do():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        try:
            db = client[os.environ["DB_NAME"]]
            for coll in (
                "preparation_uploads", "exported_orders", "exported_items",
                "product_image_catalog", "unified_orders",
                "users", "settings",
            ):
                await db[coll].delete_many(
                    {"$or": [{"user_id": uid}, {"id": uid}, {"_id": uid}]},
                )
        finally:
            client.close()

    asyncio.run(_do())


def _upload(token: str, filename: str = "orders.pdf") -> dict:
    if not SAMPLE_PDF.exists():
        pytest.skip(f"Fixture {SAMPLE_PDF} missing")
    with SAMPLE_PDF.open("rb") as f:
        r = requests.post(
            f"{API}/preparation/upload",
            headers=_hdr(token),
            files={"file": (filename, f, "application/pdf")},
            timeout=60,
        )
    r.raise_for_status()
    return r.json()


def _all_lines(token: str, upload_id: str) -> list[dict]:
    r = requests.get(f"{API}/preparation/preview/{upload_id}",
                     headers=_hdr(token), timeout=20)
    r.raise_for_status()
    body = r.json()
    out = []
    for g in body.get("groups", []):
        out.extend(g.get("preview_lines", []))
    return out


def _find_line_by_idx(lines: list[dict], idx: int) -> dict:
    for ln in lines:
        if ln["idx"] == idx:
            return ln
    raise AssertionError(f"idx={idx} not found in preview lines")


# ── 1. Edit a single card — only that card changes ─────────────────────
def test_patch_single_line_updates_only_target():
    token, uid = _register()
    try:
        first = _upload(token)
        upload_id = first["upload_id"]
        lines = _all_lines(token, upload_id)
        target = lines[0]
        target_idx = target["idx"]
        original_customer = target.get("customer_name")

        r = requests.patch(
            f"{API}/preparation/line/{upload_id}/{target_idx}",
            headers=_hdr(token),
            json={"customer_name": "أبو محمد المعدّل",
                  "note": "ملاحظة جديدة من التاجر",
                  "scope": "line"},
            timeout=15,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["ok"] is True
        assert body["applied_count"] == 1
        assert body["scope"] == "line"
        assert sorted(body["fields_updated"]) == ["customer_name", "note"]

        # Reload preview — target line is updated, peers untouched.
        new_lines = _all_lines(token, upload_id)
        updated = _find_line_by_idx(new_lines, target_idx)
        assert updated["customer_name"] == "أبو محمد المعدّل"
        assert updated["note"] == "ملاحظة جديدة من التاجر"
        assert sorted(updated["edited_fields"]) == ["customer_name", "note"]

        # Peers — at least one different idx — keep their customer_name.
        peers = [ln for ln in new_lines if ln["idx"] != target_idx]
        assert peers, "Sample fixture should contain >1 line"
        # No peer should have customer_name equal to our edited string.
        assert all(p["customer_name"] != "أبو محمد المعدّل" for p in peers)
    finally:
        _cleanup(uid)


# ── 2. Product-scoped edit applies to all siblings ─────────────────────
def test_patch_product_scope_applies_to_all_siblings():
    token, uid = _register()
    try:
        first = _upload(token)
        upload_id = first["upload_id"]
        lines = _all_lines(token, upload_id)

        # Pick the largest product group (highest count) to maximise the
        # number of siblings the edit will hit.
        groups = first["groups"]
        biggest = max(groups, key=lambda g: g["count"])
        assert biggest["count"] >= 2, "Sample fixture should have a repeating product"
        first_line_idx = biggest["preview_lines"][0]["idx"]
        sibling_idxs = {ln["idx"] for ln in biggest["preview_lines"]}

        r = requests.patch(
            f"{API}/preparation/line/{upload_id}/{first_line_idx}",
            headers=_hdr(token),
            json={"color": "ذهبي مطلي خاص", "scope": "product"},
            timeout=15,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        # All siblings updated → applied_count matches sibling group size.
        assert body["applied_count"] == len(sibling_idxs)
        assert set(body["applied_to_indices"]) == sibling_idxs
        assert body["scope"] in ("product_id", "sku", "name")
        assert body["fields_updated"] == ["color"]

        new_lines = _all_lines(token, upload_id)
        for sib_idx in sibling_idxs:
            sib = _find_line_by_idx(new_lines, sib_idx)
            assert sib["color"] == "ذهبي مطلي خاص"
            assert "color" in sib["edited_fields"]
    finally:
        _cleanup(uid)


# ── 3. Reset restores original parsed values ───────────────────────────
def test_reset_line_restores_original_values():
    token, uid = _register()
    try:
        first = _upload(token)
        upload_id = first["upload_id"]
        lines = _all_lines(token, upload_id)
        target = lines[0]
        target_idx = target["idx"]
        original_customer = target.get("customer_name")
        original_note = target.get("note")

        # Edit
        requests.patch(
            f"{API}/preparation/line/{upload_id}/{target_idx}",
            headers=_hdr(token),
            json={"customer_name": "TEMP-CHANGE",
                  "note": "TEMP-NOTE",
                  "size": "XL-TEST",
                  "color": "أزرق-اختبار",
                  "scope": "line"},
            timeout=15,
        ).raise_for_status()

        # Confirm edit took effect
        edited_line = _find_line_by_idx(_all_lines(token, upload_id), target_idx)
        assert edited_line["customer_name"] == "TEMP-CHANGE"
        assert edited_line["note"] == "TEMP-NOTE"
        assert set(edited_line["edited_fields"]) >= {"customer_name", "note"}

        # Reset
        r = requests.post(
            f"{API}/preparation/line/{upload_id}/{target_idx}/reset",
            headers=_hdr(token),
            json={"scope": "line"},
            timeout=15,
        )
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert body["ok"] is True
        assert body["applied_count"] == 1

        restored = _find_line_by_idx(_all_lines(token, upload_id), target_idx)
        assert restored["customer_name"] == original_customer
        assert restored["note"] == original_note
        # Nothing diverges from original anymore → no edit badge.
        assert restored["edited_fields"] == []
    finally:
        _cleanup(uid)


# ── 4. Edits survive PDF generation (used in the printable output) ─────
def test_edited_fields_propagate_to_generated_pdf():
    token, uid = _register()
    try:
        first = _upload(token)
        upload_id = first["upload_id"]
        lines = _all_lines(token, upload_id)
        target = lines[0]
        target_idx = target["idx"]

        # Apply a distinctive edit to the customer name.
        # Use an ASCII-only marker so PyMuPDF's BiDi extraction doesn't
        # re-shuffle the run order during the post-generation read-back.
        unique_marker = f"CUSTOMERMARK{uuid.uuid4().hex[:8].upper()}"
        requests.patch(
            f"{API}/preparation/line/{upload_id}/{target_idx}",
            headers=_hdr(token),
            json={"customer_name": unique_marker, "scope": "line"},
            timeout=15,
        ).raise_for_status()

        # Generate the PDF with just this one card.
        r = requests.post(
            f"{API}/preparation/generate/{upload_id}",
            headers=_hdr(token),
            json={"selected_indices": [target_idx]},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:300]
        pdf_bytes = r.content
        assert len(pdf_bytes) > 5000, "PDF should be non-trivial"

        # Extract text from the generated PDF — the ASCII marker MUST be
        # present (no BiDi reshuffling for pure-Latin runs).
        try:
            import fitz
        except ImportError:
            pytest.skip("PyMuPDF not available for text extraction check")

        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        text = ""
        for page in doc:
            text += page.get_text("text")
        doc.close()

        assert unique_marker in text, (
            f"Unique marker '{unique_marker}' not found in generated PDF text"
        )
    finally:
        _cleanup(uid)


# ── 5. Validation: empty body → 400 ────────────────────────────────────
def test_patch_with_no_editable_fields_returns_400():
    token, uid = _register()
    try:
        first = _upload(token)
        upload_id = first["upload_id"]
        lines = _all_lines(token, upload_id)
        target_idx = lines[0]["idx"]

        r = requests.patch(
            f"{API}/preparation/line/{upload_id}/{target_idx}",
            headers=_hdr(token),
            json={"scope": "line"},  # no editable fields
            timeout=15,
        )
        assert r.status_code == 400, r.text[:300]
    finally:
        _cleanup(uid)


# ── 6. Validation: out-of-range idx → 404 ──────────────────────────────
def test_patch_out_of_range_idx_returns_404():
    token, uid = _register()
    try:
        first = _upload(token)
        upload_id = first["upload_id"]

        r = requests.patch(
            f"{API}/preparation/line/{upload_id}/9999",
            headers=_hdr(token),
            json={"customer_name": "x"},
            timeout=15,
        )
        assert r.status_code == 404, r.text[:300]
    finally:
        _cleanup(uid)


# ── 7. Auth required for both endpoints ────────────────────────────────
def test_edit_endpoints_require_auth():
    r1 = requests.patch(f"{API}/preparation/line/anything/0",
                        json={"customer_name": "x"}, timeout=10)
    assert r1.status_code in (401, 403), r1.text[:200]

    r2 = requests.post(f"{API}/preparation/line/anything/0/reset",
                       json={"scope": "line"}, timeout=10)
    assert r2.status_code in (401, 403), r2.text[:200]


# ── 8. Cross-user isolation ────────────────────────────────────────────
def test_edit_cross_user_returns_404():
    token_a, uid_a = _register()
    token_b, uid_b = _register()
    try:
        first = _upload(token_a)
        upload_id = first["upload_id"]
        lines = _all_lines(token_a, upload_id)
        target_idx = lines[0]["idx"]

        # User B tries to edit user A's upload.
        r = requests.patch(
            f"{API}/preparation/line/{upload_id}/{target_idx}",
            headers=_hdr(token_b),
            json={"customer_name": "intruder"},
            timeout=15,
        )
        assert r.status_code == 404, r.text[:300]

        r2 = requests.post(
            f"{API}/preparation/line/{upload_id}/{target_idx}/reset",
            headers=_hdr(token_b),
            json={"scope": "line"},
            timeout=15,
        )
        assert r2.status_code == 404, r2.text[:300]
    finally:
        _cleanup(uid_a)
        _cleanup(uid_b)


# ── 9. None values clear the field (e.g. user removes the note) ────────
def test_patch_with_none_clears_the_field():
    token, uid = _register()
    try:
        first = _upload(token)
        upload_id = first["upload_id"]
        lines = _all_lines(token, upload_id)
        # Find a line that has a non-empty note to start with.
        with_note = next((ln for ln in lines if ln.get("note")), None)
        if not with_note:
            pytest.skip("No card with a note in fixture")
        target_idx = with_note["idx"]

        r = requests.patch(
            f"{API}/preparation/line/{upload_id}/{target_idx}",
            headers=_hdr(token),
            json={"note": None, "scope": "line"},
            timeout=15,
        )
        assert r.status_code == 200, r.text[:300]

        cleared = _find_line_by_idx(_all_lines(token, upload_id), target_idx)
        assert cleared["note"] in (None, "")
        assert "note" in cleared["edited_fields"]
    finally:
        _cleanup(uid)
