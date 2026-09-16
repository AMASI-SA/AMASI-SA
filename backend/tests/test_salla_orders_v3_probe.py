"""Probe contracts use real gateway/normalizer with an isolated HTTP transport."""
import asyncio
import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

import salla_orders_v3.diagnostics as diagnostics
import salla_orders_v3.probe as probe


HEAD = "a" * 40
NOW = datetime(2026, 9, 16, 18, tzinfo=timezone.utc)
IDENTITY = {"user_id": "owner-1", "store_id": "50", "order_number": "3001"}
LIGHT = {"id": 901, "reference_id": "3001"}
ITEMS = [{"id": 123, "product_id": 45, "name": "Synthetic product", "sku": "SKU",
          "quantity": 2, "options": [{"name": "Size", "value": "XL", "private_note": "PRIVATE"},
                                      {"name": "Count", "value": 0},
                                      {"name": "Gift", "value": False}]}]


async def setup_probe(monkeypatch, handler=None, db=None, now=NOW):
    db = db if db is not None else AsyncMongoMockClient(tz_aware=True).db
    await db.salla_integrations.insert_one({
        "user_id": "owner-1", "store_id": 50, "status": "connected",
        "scope": "orders.read_write", "token_revision": "r1",
        "access_token_encrypted": "synthetic-encrypted", "expires_at": now + timedelta(hours=1),
    })
    await db.unified_orders.insert_one({
        **IDENTITY, "order_id": "901", "products": [], "customer_name": "PRIVATE",
    })
    calls = []

    async def transport(request):
        calls.append(request)
        if handler:
            response = await handler(request, db)
            if response is not None:
                return response
        path = request.url.path
        if path.endswith("/orders/items"):
            data = deepcopy(ITEMS)
        elif path.endswith("/orders/901"):
            data = {**LIGHT, "customer": {"name": "PRIVATE"}, "total": 20}
        else:
            data = [deepcopy(LIGHT)]
        return httpx.Response(200, json={"success": True, "data": data})

    monkeypatch.setattr(diagnostics, "_trusted_candidate_head_sha", lambda: HEAD)
    monkeypatch.setattr(diagnostics, "_utcnow", lambda: now)
    monkeypatch.setattr(probe, "_decrypt_access", lambda row: "synthetic-token")
    monkeypatch.setattr(probe, "_http_client", lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(transport), timeout=30, follow_redirects=False,
    ))
    return db, calls


async def collect(db, **overrides):
    return await probe.collect_order_probe(db, authenticated_user_id="owner-1",
                                         owner_authorized=True, **IDENTITY, **overrides)


@pytest.mark.asyncio
async def test_probe_collects_one_triplet_and_preserves_operational_data(monkeypatch):
    db, calls = await setup_probe(monkeypatch)
    before = await db.unified_orders.find_one({})
    result = await collect(db)
    assert [r.method for r in calls] == ["GET"] * 3
    assert [r.url.path.rsplit("/", 1)[-1] for r in calls] == ["orders", "901", "items"]
    assert dict(calls[0].url.params) == {"reference_id": "3001", "per_page": "30", "format": "light"}
    assert dict(calls[1].url.params) == {"format": "light"}
    assert dict(calls[2].url.params) == {"order_id": "901"}
    assert result["provider_sample_valid"] is True
    assert result["fulfillment_parity"]["passed"] is False
    assert result["fulfillment_parity"]["v3_product_count"] == 1
    assert result["parity_ready"] is False and result["cutover_allowed"] is False
    assert result["pending_gates"] == ["qoyod", "attribution", "consumer_regressions", "operational_adapter"]
    assert before == await db.unified_orders.find_one({})
    assert await db.integration_inbox.count_documents({}) == 0
    assert await db.salla_orders_v3_jobs.count_documents({}) == 0
    row = await db.salla_orders_v3_parity_evidence.find_one({})
    assert row["record_type"] == "salla_orders_v3_order_probe"
    encoded = json.dumps(row, default=str)
    assert "PRIVATE" not in encoded and "synthetic-token" not in encoded
    assert "synthetic-encrypted" not in encoded
    values = {o["name"]: o["value"] for o in row["artifact"]["v3_order"]["products"][0]["options"]}
    assert values == {"Size": "XL", "Count": 0, "Gift": False}
    loaded = await probe.read_order_probe(db, authenticated_user_id="owner-1", user_id="owner-1",
                                         owner_authorized=True, probe_id=result["probe_id"])
    assert loaded == result


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["foreign_store", "foreign_order", "scope", "expired_token", "owner"])
async def test_probe_rejects_invalid_context_before_network(monkeypatch, change):
    db, calls = await setup_probe(monkeypatch)
    if change == "foreign_store":
        await db.salla_integrations.update_one({}, {"$set": {"store_id": 51}})
    elif change == "foreign_order":
        await db.unified_orders.update_one({}, {"$set": {"user_id": "owner-2"}})
    elif change == "scope":
        await db.salla_integrations.update_one({}, {"$set": {"scope": "products.read"}})
    elif change == "expired_token":
        await db.salla_integrations.update_one({}, {"$set": {"expires_at": NOW}})
    with pytest.raises((probe.ProbeError, PermissionError)):
        if change == "owner":
            await probe.collect_order_probe(db, authenticated_user_id="employee", owner_authorized=True, **IDENTITY)
        else:
            await collect(db)
    assert calls == []
    assert await db.salla_orders_v3_parity_evidence.count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["list_identity", "details_identity", "missing_details_identity", "items_invalid", "expired_auth", "redirect", "local_changed", "integration_changed", "runtime_changed"])
async def test_probe_fails_closed_without_sealing_partial_samples(monkeypatch, fault):
    async def handler(request, db):
        path = request.url.path
        if fault == "list_identity" and path.endswith("/orders"):
            return httpx.Response(200, json={"data": [{"id": 901, "reference_id": "3002"}]})
        if path.endswith("/orders/901"):
            if fault == "details_identity":
                return httpx.Response(200, json={"data": {"id": 902, "reference_id": "3001"}})
            if fault == "missing_details_identity":
                return httpx.Response(200, json={"data": {"reference_id": "3001"}})
            if fault == "expired_auth":
                return httpx.Response(401, json={"message": "SECRET provider error"})
            if fault == "redirect":
                return httpx.Response(302, headers={"location": "https://untrusted.invalid"})
        if path.endswith("/orders/items"):
            if fault == "items_invalid":
                return httpx.Response(200, json={"data": [{}]})
            if fault == "local_changed":
                await db.unified_orders.update_one({}, {"$set": {"products": [{"sku": "new"}]}})
            if fault == "integration_changed":
                await db.salla_integrations.update_one({}, {"$set": {"token_revision": "r2"}})
            if fault == "runtime_changed":
                monkeypatch.setattr(diagnostics, "_trusted_candidate_head_sha", lambda: "b" * 40)
    db, calls = await setup_probe(monkeypatch, handler)
    with pytest.raises(probe.ProbeError) as exc:
        await collect(db)
    assert "SECRET" not in str(exc.value)
    assert len(calls) <= 3
    assert await db.salla_orders_v3_parity_evidence.count_documents({}) == 0


@pytest.mark.asyncio
async def test_probe_cooldown_survives_completion_and_allows_only_one_concurrent_probe(monkeypatch):
    db, calls = await setup_probe(monkeypatch)
    results = await asyncio.gather(collect(db), collect(db), return_exceptions=True)
    assert sum(isinstance(r, dict) for r in results) == 1
    assert sum(isinstance(r, probe.ProbeError) and r.code == "probe_cooldown" for r in results) == 1
    with pytest.raises(probe.ProbeError, match="probe_cooldown"):
        await collect(db)
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_probe_read_rejects_tampered_stale_or_other_owner_evidence(monkeypatch):
    db, calls = await setup_probe(monkeypatch)
    result = await collect(db)
    with pytest.raises(probe.ProbeError):
        await probe.read_order_probe(db, authenticated_user_id="owner-2", user_id="owner-2", owner_authorized=True, probe_id=result["probe_id"])
    await db.salla_orders_v3_parity_evidence.update_one({}, {"$set": {"artifact.v3_order.products": []}})
    with pytest.raises(probe.ProbeError):
        await probe.read_order_probe(db, authenticated_user_id="owner-1", user_id="owner-1", owner_authorized=True, probe_id=result["probe_id"])
    assert len(calls) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["expired", "head_changed"])
async def test_probe_evidence_expires_and_cannot_cross_releases(monkeypatch, reason):
    db, _ = await setup_probe(monkeypatch)
    result = await collect(db)
    if reason == "expired":
        monkeypatch.setattr(diagnostics, "_utcnow", lambda: NOW + timedelta(hours=1))
    else:
        monkeypatch.setattr(diagnostics, "_trusted_candidate_head_sha", lambda: "b" * 40)
    with pytest.raises(probe.ProbeError, match="invalid_probe"):
        await probe.read_order_probe(db, authenticated_user_id="owner-1", user_id="owner-1",
                                    owner_authorized=True, probe_id=result["probe_id"])


@pytest.mark.asyncio
async def test_probe_is_never_accepted_as_full_parity_evidence(monkeypatch):
    db, _ = await setup_probe(monkeypatch)
    result = await collect(db)
    run = await diagnostics.read_parity_run_context(db, authenticated_user_id="owner-1", user_id="owner-1",
                                                   owner_authorized=True, run_id=result["run_id"])
    with pytest.raises(RuntimeError, match="evidence is invalid"):
        await diagnostics.read_persisted_parity_evidence(db, authenticated_user_id="owner-1", user_id="owner-1",
             owner_authorized=True, parity_run=run, evidence_id=result["probe_id"])


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["oversized", "timeout", "cancelled"])
async def test_probe_resource_limits_and_cancellation_leave_no_evidence(monkeypatch, fault):
    async def handler(request, db):
        if fault == "oversized":
            return httpx.Response(200, content=b"x" * (probe.PROBE_MAX_RESPONSE_BYTES + 1))
        if fault == "timeout":
            raise TimeoutError()
        raise asyncio.CancelledError()
    db, calls = await setup_probe(monkeypatch, handler)
    expected = asyncio.CancelledError if fault == "cancelled" else probe.ProbeError
    with pytest.raises(expected):
        await collect(db)
    assert len(calls) == 1
    assert await db.salla_orders_v3_parity_evidence.count_documents({}) == 0
    with pytest.raises(probe.ProbeError, match="probe_cooldown"):
        await collect(db)


@pytest.mark.asyncio
async def test_matching_fulfillment_still_cannot_claim_cutover(monkeypatch):
    db, _ = await setup_probe(monkeypatch)
    product = {"order_item_id": "123", "product_id": "45", "parent_product_id": "",
               "variant_id": "", "sku": "SKU", "quantity": 2.0, "custom_fields": [],
               "options": [{"name": "Size", "value": "XL"}, {"name": "Count", "value": 0},
                           {"name": "Gift", "value": False}]}
    await db.unified_orders.update_one({}, {"$set": {"products": [product]}})
    result = await collect(db)
    assert result["fulfillment_parity"]["passed"] is True
    assert result["parity_ready"] is False and result["cutover_allowed"] is False


@pytest.mark.asyncio
async def test_probe_routes_derive_owner_and_reject_payload_context(monkeypatch):
    from salla_orders_v3.probe_routes import make_salla_orders_v3_probe_router
    db, calls = await setup_probe(monkeypatch)
    actor = {"id": "owner-1", "role": "employee"}
    app = FastAPI()
    app.include_router(make_salla_orders_v3_probe_router(db, lambda: actor), prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        url = "/api/salla/orders-v3/probes"
        payload = {"store_id": "50", "order_number": "3001"}
        assert (await client.post(url, json=payload)).status_code == 403
        actor["role"] = "owner"
        assert (await client.post(url, json={**payload, "user_id": "owner-2"})).status_code == 422
        response = await client.post(url, json=payload)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["cutover_allowed"] is False
        read = await client.get(url + "/" + response.json()["probe_id"])
        assert read.json() == response.json()
        assert read.headers["cache-control"] == "no-store"
    assert len(calls) == 3
