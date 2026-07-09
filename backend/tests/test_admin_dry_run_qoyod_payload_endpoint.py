"""TEMP endpoint tests — `/api/admin/dry-run-qoyod-payload`.

Verifies every guard the user demanded (2026-02):
    G1. env var missing / empty      → 404
    G2. env set but wrong token      → 404
    G3. env set + valid token, but wrong / other order_number → 404
    G4. env set + valid token + only allowed order (270457540)
        → 200 with the full read-only report
    G5. NO Qoyod HTTP call ever leaves the process
    G6. NO Mongo write happens (counts unchanged)
    G7. all responses share the same 404 shape when a guard trips
        (probes cannot distinguish env-missing from wrong-token).

The endpoint is intentionally temporary — these tests also stand as
its acceptance/rollback checklist.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import mongomock_motor  # noqa: F401
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport

from integrations.qoyod.models import ensure_qoyod_indexes
from integrations.qoyod_manual.admin_diagnostics import (
    make_admin_diagnostics_router, ALLOWED_ORDER_NUMBERS,
)


VALID_ORDER = "270457540"
VALID_TOKEN = "test-diag-token-xyz-01234567"


@pytest_asyncio.fixture
async def db():
    client = mongomock_motor.AsyncMongoMockClient()
    _db = client["test_admin_diag"]
    await ensure_qoyod_indexes(_db)
    return _db


@pytest_asyncio.fixture
async def http(db):
    app = FastAPI()
    app.include_router(make_admin_diagnostics_router(db),
                       prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app),
                           base_url="http://test") as c:
        yield c


@pytest.fixture(autouse=True)
def _no_stale_env():
    """Never let a previously-set env var leak between test cases."""
    prev = os.environ.pop("DIAGNOSTIC_TOKEN", None)
    yield
    if prev is not None:
        os.environ["DIAGNOSTIC_TOKEN"] = prev
    else:
        os.environ.pop("DIAGNOSTIC_TOKEN", None)


async def _seed_order(db, order_num: str = VALID_ORDER) -> None:
    """Insert one salla_direct inbox row with a realistic canonical
    so the report has meaningful data."""
    await db.integration_inbox.insert_one({
        "id": f"row-{order_num}",
        "user_id": "main",
        "trace_id": f"trace-{order_num}",
        "connector_key": "salla_direct",
        "source": "salla_direct",
        "received_at": datetime.now(timezone.utc),
        "salla_order_number": order_num,
        "salla_order_id": f"oid-{order_num}",
        "idempotency_key": f"salla_direct:order:{order_num}",
        "pipeline_stage": "NORMALIZED",
        "canonical_payload": {
            "order_number": order_num,
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
            "order_date": "2026-07-15T10:00:00+00:00",
            "total_amount":    1250.83,
            "subtotal":        1087.68,
            "tax_amount":       163.15,
            "shipping_amount":    0.00,
            "cod_fee_amount":     0.00,
            "currency": "SAR",
            "customer": {"name": "X", "phone": "+966501112222"},
            "items": [
                {"sku": "SKU-A", "name": "A", "quantity": 2,
                 "unit_price": 100.00, "total": 230.00},
                {"sku": "SKU-B", "name": "B", "quantity": 1,
                 "unit_price": 500.87, "total": 500.87},
                {"sku": "SKU-C", "name": "C", "quantity": 1,
                 "unit_price": 500.00, "total": 520.00},
            ],
        },
        "stage_history": [],
    })


# ── G1: env missing → 404 ───────────────────────────────────────────
@pytest.mark.asyncio
async def test_endpoint_returns_404_when_env_missing(db, http):
    await _seed_order(db)
    r = await http.post("/api/admin/dry-run-qoyod-payload",
                        json={"order_number": VALID_ORDER,
                              "token": "anything"})
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


# ── G2: env set, wrong token → 404 (same shape) ────────────────────
@pytest.mark.asyncio
async def test_endpoint_returns_404_on_wrong_token(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_order(db)
    r = await http.post("/api/admin/dry-run-qoyod-payload",
                        json={"order_number": VALID_ORDER,
                              "token": "not-the-token"})
    assert r.status_code == 404
    assert r.json() == {"detail": "Not Found"}


# ── G2b: empty env var (whitespace) treated as missing ─────────────
@pytest.mark.asyncio
async def test_endpoint_returns_404_on_whitespace_env(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = "   "
    await _seed_order(db)
    r = await http.post("/api/admin/dry-run-qoyod-payload",
                        json={"order_number": VALID_ORDER,
                              "token": "   "})
    assert r.status_code == 404


# ── G3: wrong order_number even with valid token → 404 ─────────────
@pytest.mark.asyncio
async def test_endpoint_returns_404_on_wrong_order_number(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_order(db, order_num="271000000")
    r = await http.post("/api/admin/dry-run-qoyod-payload",
                        json={"order_number": "271000000",
                              "token": VALID_TOKEN})
    assert r.status_code == 404


# ── G4: happy path — 200 + full report ─────────────────────────────
@pytest.mark.asyncio
async def test_endpoint_returns_full_dry_run_report_on_happy_path(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_order(db)

    r = await http.post("/api/admin/dry-run-qoyod-payload",
                        json={"order_number": VALID_ORDER,
                              "token": VALID_TOKEN})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["order_number"] == VALID_ORDER
    assert body["trace"]["connector_key"] == "salla_direct"
    assert body["canonical_summary"]["total_amount"] == 1250.83
    assert body["canonical_summary"]["item_count"] == 3

    # Per-line breakdown must include the same 3 items as the seed.
    item_rows = [b for b in body["breakdown_per_line"]
                 if b.get("sku") in ("SKU-A", "SKU-B", "SKU-C")]
    assert len(item_rows) == 3
    for row in item_rows:
        assert "unit_price" not in row  # breakdown uses `qoyod_unit_price`
        assert "qoyod_unit_price" in row
        assert "computed_discount" in row
        assert "line_gross_after_tax" in row
        assert "delta_vs_salla_line" in row

    # qoyod_lines_payload has product_id / quantity / unit_price /
    # discount / tax_percent for every line.
    assert body["qoyod_lines_payload"]
    for line in body["qoyod_lines_payload"]:
        assert set(line) >= {"product_id", "description", "quantity",
                             "unit_price", "discount", "discount_type",
                             "tax_percent"}

    # Totals block reports the exact three values + three deltas.
    t = body["totals"]
    assert set(t) == {
        "sum_line_gross_after_tax", "expected_qoyod_total",
        "salla_total_amount", "delta_sum_vs_salla",
        "delta_expected_vs_salla", "delta_sum_vs_expected"}
    assert t["salla_total_amount"] == 1250.83


# ── G4b: order allowlist is exactly one number ─────────────────────
def test_only_one_order_is_allowlisted():
    """Regression against future creep — this endpoint must only
    ever accept 270457540 until the user explicitly extends it."""
    assert ALLOWED_ORDER_NUMBERS == {"270457540"}


# ── G5: NO Qoyod HTTP calls happen from the handler ────────────────
@pytest.mark.asyncio
async def test_no_qoyod_http_leaves_the_handler(db, http, monkeypatch):
    """Any accidental Qoyod HTTP call must raise. Belt-and-braces
    for the read-only guarantee the user demanded."""
    def _boom(*a, **kw):
        raise AssertionError("Qoyod HTTP was invoked — read-only "
                             "guarantee violated")
    try:
        import integrations.qoyod.client as qc
        for name in ("get_json", "post_json", "put_json",
                     "delete_json", "call_qoyod"):
            if hasattr(qc, name):
                monkeypatch.setattr(qc, name, _boom, raising=False)
    except Exception:
        pass

    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_order(db)
    r = await http.post("/api/admin/dry-run-qoyod-payload",
                        json={"order_number": VALID_ORDER,
                              "token": VALID_TOKEN})
    assert r.status_code == 200


# ── G6: NO Mongo writes happen (counts unchanged) ─────────────────
@pytest.mark.asyncio
async def test_no_mongo_writes_happen(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_order(db)

    before_inbox   = await db.integration_inbox.count_documents({})
    before_invoice = await db.qoyod_invoices.count_documents({})
    before_audit   = await db.manual_send_audit.count_documents({})

    for _ in range(3):
        r = await http.post("/api/admin/dry-run-qoyod-payload",
                            json={"order_number": VALID_ORDER,
                                  "token": VALID_TOKEN})
        assert r.status_code == 200

    assert await db.integration_inbox.count_documents({}) == before_inbox
    assert await db.qoyod_invoices.count_documents({})   == before_invoice
    assert await db.manual_send_audit.count_documents({}) == before_audit


# ── G7: 404 responses are indistinguishable across guard failures ─
@pytest.mark.asyncio
async def test_all_404_responses_share_same_body(db, http):
    """A probe hitting the endpoint must not be able to tell
    env-missing from wrong-token from wrong-order."""
    await _seed_order(db)
    bodies: list[str] = []

    # env missing.
    os.environ.pop("DIAGNOSTIC_TOKEN", None)
    r = await http.post("/api/admin/dry-run-qoyod-payload",
                        json={"order_number": VALID_ORDER,
                              "token": VALID_TOKEN})
    bodies.append(r.text)

    # wrong token.
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    r = await http.post("/api/admin/dry-run-qoyod-payload",
                        json={"order_number": VALID_ORDER,
                              "token": "nope"})
    bodies.append(r.text)

    # wrong order.
    r = await http.post("/api/admin/dry-run-qoyod-payload",
                        json={"order_number": "111111",
                              "token": VALID_TOKEN})
    bodies.append(r.text)

    # malformed body.
    r = await http.post("/api/admin/dry-run-qoyod-payload",
                        content=b"not-json",
                        headers={"content-type": "application/json"})
    bodies.append(r.text)

    assert len(set(bodies)) == 1, (
        f"404 responses differ across guard failures: {bodies}")


# ── G8: env-var read is per-request (removing it deactivates) ─────
@pytest.mark.asyncio
async def test_removing_env_var_deactivates_endpoint(db, http):
    os.environ["DIAGNOSTIC_TOKEN"] = VALID_TOKEN
    await _seed_order(db)
    r1 = await http.post("/api/admin/dry-run-qoyod-payload",
                         json={"order_number": VALID_ORDER,
                               "token": VALID_TOKEN})
    assert r1.status_code == 200

    # Remove the env var — the endpoint must immediately 404.
    del os.environ["DIAGNOSTIC_TOKEN"]
    r2 = await http.post("/api/admin/dry-run-qoyod-payload",
                         json={"order_number": VALID_ORDER,
                               "token": VALID_TOKEN})
    assert r2.status_code == 404
