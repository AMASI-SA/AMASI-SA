"""Iter-41 — multi-file accumulative upload + custom filename tests.

Tests for the merchant-requested ability to add multiple Salla orders
PDFs to a single preparation session ("اماكنية إضافة ملفات متعدّدة ثم
اختيار المنتجات لرفعها").

Backend contract under test:
    POST /api/preparation/append/{upload_id}  — merges parsed lines from
    a second PDF into an existing upload, deduping by item_key (latest
    wins). Returns the merged groups + filenames list.
"""
from __future__ import annotations

import io
import os
import asyncio
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
        json={"name": "iter41 Tester",
              "email": f"iter41-{uuid.uuid4().hex[:10]}@example.com",
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


def _append(token: str, upload_id: str, filename: str = "orders2.pdf") -> requests.Response:
    if not SAMPLE_PDF.exists():
        pytest.skip(f"Fixture {SAMPLE_PDF} missing")
    with SAMPLE_PDF.open("rb") as f:
        return requests.post(
            f"{API}/preparation/append/{upload_id}",
            headers=_hdr(token),
            files={"file": (filename, f, "application/pdf")},
            timeout=60,
        )


# ── 1. Smoke: append works + returns merged preview ──────────────────
def test_append_returns_merged_preview():
    token, uid = _register()
    try:
        first = _upload(token, "fileA.pdf")
        upload_id = first["upload_id"]
        first_count = first["total_product_lines"]
        assert first.get("filenames") == ["fileA.pdf"]

        r = _append(token, upload_id, "fileB.pdf")
        assert r.status_code == 200, r.text[:300]
        merged = r.json()

        # Re-uploading the SAME content → every item_key collides →
        # replaced_count == first batch size, total still == first batch.
        assert merged["replaced_count"] == first_count, merged
        assert merged["total_product_lines"] == first_count, merged
        # Filenames list grew to 2 entries (in insert order).
        assert merged["filenames"] == ["fileA.pdf", "fileB.pdf"], merged
        # The "primary" filename is now the latest one.
        assert merged["filename"] == "fileB.pdf"
        # Same upload_id (NO new row)
        assert merged["upload_id"] == upload_id
    finally:
        _cleanup(uid)


# ── 2. Indices are re-numbered contiguously after append ─────────────
def test_append_reindexes_lines_contiguously():
    token, uid = _register()
    try:
        first = _upload(token)
        upload_id = first["upload_id"]
        merged = _append(token, upload_id, "second.pdf").json()
        # Walk every group's preview_lines and assert idx∈[0, total)
        # AND that no idx repeats.
        seen = set()
        total = merged["total_product_lines"]
        for g in merged["groups"]:
            for ln in g["preview_lines"]:
                assert 0 <= ln["idx"] < total, ln
                assert ln["idx"] not in seen, f"duplicate idx {ln['idx']}"
                seen.add(ln["idx"])
    finally:
        _cleanup(uid)


# ── 3. Append against non-existent upload_id → 404 ──────────────────
def test_append_against_invalid_upload_id_returns_404():
    token, uid = _register()
    try:
        r = _append(token, "ffffffffffffffffffffffffffffffff", "x.pdf")
        assert r.status_code == 404, r.text[:200]
    finally:
        _cleanup(uid)


# ── 4. Append validates content type + size ─────────────────────────
def test_append_rejects_non_pdf():
    token, uid = _register()
    try:
        first = _upload(token)
        upload_id = first["upload_id"]
        # Wrong extension
        r = requests.post(
            f"{API}/preparation/append/{upload_id}",
            headers=_hdr(token),
            files={"file": ("doc.docx", io.BytesIO(b"not a pdf"), "application/octet-stream")},
            timeout=15,
        )
        assert r.status_code == 400, r.text[:200]
        # Empty file
        r = requests.post(
            f"{API}/preparation/append/{upload_id}",
            headers=_hdr(token),
            files={"file": ("empty.pdf", io.BytesIO(b""), "application/pdf")},
            timeout=15,
        )
        assert r.status_code == 400, r.text[:200]
    finally:
        _cleanup(uid)


# ── 5. Preview endpoint now exposes the filenames list ──────────────
def test_preview_exposes_filenames_list():
    token, uid = _register()
    try:
        first = _upload(token, "alpha.pdf")
        upload_id = first["upload_id"]
        _append(token, upload_id, "beta.pdf")
        # GET preview → should contain BOTH names
        preview = requests.get(
            f"{API}/preparation/preview/{upload_id}",
            headers=_hdr(token),
            timeout=15,
        ).json()
        assert preview["filenames"] == ["alpha.pdf", "beta.pdf"], preview


        # Single-file uploads must still report filenames (backwards-compat
        # for /upload's response shape).
        second_token, _ = _register()
        single = _upload(second_token, "solo.pdf")
        assert single["filenames"] == ["solo.pdf"]
    finally:
        _cleanup(uid)


# ── 6. Generate after append works on the merged set ────────────────
def test_generate_after_append_with_selected_indices():
    token, uid = _register()
    try:
        first = _upload(token)
        upload_id = first["upload_id"]
        merged = _append(token, upload_id, "second.pdf").json()
        # Pick a handful of indices from the merged preview
        idxs = []
        for g in merged["groups"]:
            for ln in g["preview_lines"][:1]:
                idxs.append(ln["idx"])
        assert len(idxs) >= 2
        # POST /generate with those indices → PDF binary
        r = requests.post(
            f"{API}/preparation/generate/{upload_id}",
            headers=_hdr(token),
            json={"selected_indices": idxs},
            timeout=60,
        )
        assert r.status_code == 200, r.text[:300]
        assert r.headers.get("content-type", "").startswith("application/pdf")
        assert len(r.content) > 1000   # non-trivial PDF body
        # The X-Exported-* headers should reflect what we selected.
        ec = r.headers.get("x-exported-cards")
        assert ec and int(ec) == len(idxs), r.headers
    finally:
        _cleanup(uid)
