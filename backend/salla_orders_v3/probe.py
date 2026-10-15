"""Explicit owner-requested, bounded provider samples; never an operational writer.

A probe is separate from parity evidence. It cannot attest Qoyod, attribution,
consumer regressions or a cutover. Raw provider/customer payloads are not stored.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import uuid
from copy import deepcopy
from datetime import timedelta
from typing import Any

import httpx
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from salla_integration.service import SALLA_API_BASE, _decrypt_access

from . import diagnostics as diagnostic
from .config import (
    LEASES_COLLECTION, PARITY_EVIDENCE_COLLECTION, PARITY_RUNS_COLLECTION,
    PROBE_COOLDOWN_SECONDS, PROBE_MAX_RESPONSE_BYTES, PROBE_TIMEOUT_SECONDS,
    shadow_collection, validate_provider_read_request,
)
from .gateway import SallaOrdersGateway
from .parity import compare_fulfillment_parity
from .shadow import SallaOrdersShadowEngine


class ProbeError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _owner(actor: str, owner: str, authorized: bool) -> str:
    if authorized is not True or type(actor) is not str or not actor.strip() or actor != owner:
        raise PermissionError("Salla V3 probes are owner-only")
    return actor


def _identifier(value: Any) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", value):
        raise ProbeError("invalid_identity")
    return value


def _json(value: Any) -> bytes:
    return json.dumps(diagnostic._canonical_artifact_value(value), ensure_ascii=False,
                      sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _http_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=30, follow_redirects=False)


async def _integration(db: Any, owner: str, store: str) -> dict:
    # The provider client is owner-scoped. Reject ambiguous/rebound integrations.
    rows = await db.salla_integrations.find({"user_id": owner}).limit(2).to_list(length=2)
    if len(rows) != 1:
        raise ProbeError("integration_unavailable")
    row = rows[0]
    scope = diagnostic.scope_diagnostic(row)
    expiry = diagnostic._persisted_timestamp(row.get("expires_at"))
    if (str(row.get("store_id")) != store or not scope["connected"]
            or not scope["required_scope_present"] or not row.get("token_revision")
            or expiry is None or expiry <= diagnostic._utcnow()):
        raise ProbeError("integration_unavailable")
    return row


def _products_only(order: dict) -> dict:
    products = order.get("products")
    if products is None:
        products = []
    if not isinstance(products, list) or any(not isinstance(p, dict) for p in products):
        raise ProbeError("invalid_products")
    keys = ("order_item_id", "product_id", "parent_product_id", "variant_id", "sku",
            "quantity", "options", "custom_fields")
    result = {"products": [{k: deepcopy(p[k]) for k in keys if k in p} for p in products]}
    for product in result["products"]:
        for key in ("options", "custom_fields"):
            value = product.get(key)
            if isinstance(value, list):
                # Provenance/raw provider rows may carry unrelated personal data.
                # Keep only the semantic fields actually consumed by parity.
                if any(not isinstance(v, dict) or "name" not in v or "value" not in v for v in value):
                    raise ProbeError("invalid_products")
                product[key] = [{"name": deepcopy(v["name"]), "value": deepcopy(v["value"])} for v in value]
    for key in ("items_authoritative", "items_payload_valid", "needs_items_enrichment",
                "signal_revision", "items_success_signal_revision"):
        if key in order:
            result[key] = order[key]
    return result


async def _legacy(db: Any, owner: str, store: str, number: str) -> dict:
    rows = await db.unified_orders.find(
        {"user_id": owner, "order_number": number},
        {"_id": 0, "order_id": 1, "store_id": 1, "products": 1},
    ).limit(2).to_list(length=2)
    if len(rows) != 1 or not rows[0].get("order_id"):
        raise ProbeError("local_order_unavailable")
    row = rows[0]
    if row.get("store_id") is not None and str(row["store_id"]) != store:
        raise ProbeError("local_order_unavailable")
    return {"order_id": str(row["order_id"]), **_products_only(row)}


async def _claim_probe(db: Any, owner: str, store: str) -> None:
    now = diagnostic._bson_utc_datetime(diagnostic._utcnow())
    token = uuid.uuid4().hex
    collection = shadow_collection(db, LEASES_COLLECTION)
    try:
        row = await collection.find_one_and_update(
            {"_id": f"order-probe:{owner}:{store}",
             "$or": [{"expires_at": {"$lte": now}}, {"expires_at": {"$exists": False}}]},
            {"$set": {"token": token, "owner_id": owner, "store_id": store,
                      "expires_at": now + timedelta(seconds=PROBE_COOLDOWN_SECONDS)}},
            upsert=True, return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        raise ProbeError("probe_cooldown") from None
    if not row or row.get("token") != token:
        raise ProbeError("probe_cooldown")
    # Deliberately retain the cooldown after success, failure and cancellation.


async def collect_order_probe(db: Any, *, authenticated_user_id: str, user_id: str,
                              store_id: str, order_number: str, owner_authorized: bool) -> dict:
    """Collect one List/Details/Items triplet with no refresh, retries or order writes."""
    owner = _owner(authenticated_user_id, user_id, owner_authorized)
    store, number = _identifier(store_id), _identifier(order_number)
    try:
        async with asyncio.timeout(PROBE_TIMEOUT_SECONDS):
            return await _collect(db, owner, store, number)
    except ProbeError:
        raise
    except TimeoutError:
        raise ProbeError("probe_timeout") from None
    except Exception:
        # Provider/DB exception strings may contain customer data or credentials.
        raise ProbeError("probe_unavailable") from None


async def _collect(db: Any, owner: str, store: str, number: str) -> dict:
    head = diagnostic._trusted_candidate_head_sha()
    integration = await _integration(db, owner, store)
    before = await _legacy(db, owner, store, number)
    token = _decrypt_access(integration)
    if not token:
        raise ProbeError("integration_unavailable")
    await _claim_probe(db, owner, store)
    # These indexes are also needed when the background Shadow worker is disabled.
    for name, index in ((PARITY_RUNS_COLLECTION, "salla_orders_v3_parity_run_ttl"),
                        (PARITY_EVIDENCE_COLLECTION, "salla_orders_v3_parity_evidence_ttl")):
        await shadow_collection(db, name).create_index("expires_at", expireAfterSeconds=0, name=index)
    run = await diagnostic.create_parity_run_context(
        db, authenticated_user_id=owner, user_id=owner, owner_authorized=True,
    )
    if head != run.candidate_head_sha:
        raise ProbeError("runtime_changed")
    samples = []

    async def check_context():
        current = await _integration(db, owner, store)
        if any(current.get(k) != integration.get(k) for k in
               ("token_revision", "access_token_encrypted", "store_id")):
            raise ProbeError("integration_changed")
        if diagnostic._trusted_candidate_head_sha() != head:
            raise ProbeError("runtime_changed")

    async def get_sample(_db, actor, method, path, *, params=None):
        validate_provider_read_request(method, path)
        if actor != owner or len(samples) >= 3:
            raise ProbeError("provider_budget_exceeded")
        await check_context()
        # Pin the existing access token. A 401 fails; no OAuth rotation or probe to
        # another endpoint is possible, including through redirects.
        async with _http_client() as client:
            async with client.stream("GET", SALLA_API_BASE + path,
                                     headers={"Authorization": "Bearer " + token}, params=params) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    raise ProbeError("provider_read_failed")
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > PROBE_MAX_RESPONSE_BYTES:
                        raise ProbeError("provider_response_too_large")
        value = json.loads(body)
        if not isinstance(value, dict) or value.get("success") is False:
            raise ProbeError("invalid_provider_response")
        if path == "/orders/" + before["order_id"]:
            data = value.get("data")
            if (not isinstance(data, dict) or str(data.get("id")) != before["order_id"]
                    or str(data.get("reference_id")) != number):
                raise ProbeError("provider_identity_mismatch")
        await check_context()
        samples.append({"path": path, "params": params, "response_sha256": hashlib.sha256(_json(value)).hexdigest(),
                        "observed_at": diagnostic._utcnow().isoformat()})
        return value

    gateway = SallaOrdersGateway(db, call_provider=get_sample, max_attempts=1)
    light = await gateway.resolve_light_order(owner, number)
    if (not light or str(light.get("reference_id")) != number
            or str(light.get("id")) != before["order_id"]):
        raise ProbeError("provider_identity_mismatch")
    outcome = await SallaOrdersShadowEngine(db, gateway=gateway).prepare_order_snapshot(
        user_id=owner, store_id=store, light_order=light,
    )
    if outcome.get("items_payload_valid") is not True or len(samples) != 3:
        raise ProbeError("invalid_items_sample")
    after = await _legacy(db, owner, store, number)
    if _json(before) != _json(after):
        raise ProbeError("local_order_changed")
    await check_context()
    # Sample revision 1 belongs only to this triplet, never to the durable queue.
    identity = {"user_id": owner, "store_id": store, "order_number": number}
    artifact = {
        "order_identity": identity, "internal_order_id": before["order_id"], "samples": samples,
        "legacy_order": _products_only(before),
        "v3_order": _products_only(outcome["compatibility_order"]),
    }
    if len(_json(artifact)) > PROBE_MAX_RESPONSE_BYTES:
        raise ProbeError("probe_artifact_too_large")
    # Validate both product/option shapes before storing a usable observation.
    compare_fulfillment_parity(artifact["legacy_order"], artifact["v3_order"])
    observed_at = diagnostic._bson_utc_datetime(diagnostic._utcnow())
    if not diagnostic._parity_run_valid(run, evaluated_at=observed_at):
        raise ProbeError("probe_expired")
    probe_id = uuid.uuid4().hex
    row = {
        "_id": "order-probe:" + probe_id, "probe_id": probe_id,
        "record_type": "salla_orders_v3_order_probe", "schema_version": 1,
        "status": "sealed", "owner_id": owner, "created_by": owner,
        "candidate_head_sha": head, "run_id": run.run_id,
        "observed_at": observed_at, "expires_at": run.expires_at,
        "artifact": artifact,
        "artifact_digest": diagnostic._artifact_digest(artifact, candidate_head_sha=head,
                              run_id=run.run_id, owner_id=owner, observed_at=observed_at),
    }
    await shadow_collection(db, PARITY_EVIDENCE_COLLECTION).insert_one(row)
    return await read_order_probe(db, authenticated_user_id=owner, user_id=owner,
                                 owner_authorized=True, probe_id=probe_id)


async def read_order_probe(db: Any, *, authenticated_user_id: str, user_id: str,
                           owner_authorized: bool, probe_id: str) -> dict:
    owner = _owner(authenticated_user_id, user_id, owner_authorized)
    if type(probe_id) is not str or not re.fullmatch(r"[0-9a-f]{32}", probe_id):
        raise ProbeError("invalid_probe")
    row = await shadow_collection(db, PARITY_EVIDENCE_COLLECTION).find_one(
        {"_id": "order-probe:" + probe_id, "owner_id": owner}, {"_id": 0},
    )
    if (not row or row.get("record_type") != "salla_orders_v3_order_probe"
            or row.get("schema_version") != 1 or row.get("status") != "sealed"
            or row.get("created_by") != owner or row.get("probe_id") != probe_id):
        raise ProbeError("invalid_probe")
    try:
        run = await diagnostic.read_parity_run_context(
            db, authenticated_user_id=owner, user_id=owner, owner_authorized=True, run_id=row["run_id"],
        )
        now = diagnostic._utcnow()
        observed = diagnostic._persisted_timestamp(row["observed_at"])
        expires = diagnostic._persisted_timestamp(row["expires_at"])
        artifact = row["artifact"]
        digest = diagnostic._artifact_digest(artifact, candidate_head_sha=run.candidate_head_sha,
                      run_id=run.run_id, owner_id=owner, observed_at=observed)
        if (row["candidate_head_sha"] != run.candidate_head_sha or row["artifact_digest"] != digest
                or not run.created_at <= observed <= now < expires
                or expires != run.expires_at or artifact["order_identity"]["user_id"] != owner):
            raise ProbeError("invalid_probe")
        comparison = compare_fulfillment_parity(artifact["legacy_order"], artifact["v3_order"])
    except Exception:
        raise ProbeError("invalid_probe") from None
    return {
        "probe_id": probe_id, "run_id": run.run_id, "candidate_head_sha": run.candidate_head_sha,
        "observed_at": observed.isoformat(), "expires_at": expires.isoformat(),
        "artifact_digest": digest, "order_identity": artifact["order_identity"],
        "provider_sample_valid": True, "provider_atomic_snapshot": False,
        "fulfillment_parity": comparison, "provider_request_count": len(artifact["samples"]),
        "operational_write_reached": False, "provider_write_reached": False,
        "parity_ready": False, "cutover_allowed": False,
        "pending_gates": ["qoyod", "attribution", "consumer_regressions", "operational_adapter"],
    }
