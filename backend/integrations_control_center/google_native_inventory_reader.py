"""Bounded native observations for explicitly chosen category products only."""
from datetime import datetime, timezone
import re

import httpx
from salla_integration.service import SallaError, call_salla
from .google_live_category_proof import canonical_id, checked_page, checked_response, digest, safe_customer_url
from .google_live_category_reader import PUBLIC_INTEGRATION, CategoryProofUnavailable, preview_live_category, _store
from .google_native_inventory_proof import product_observations


async def _get(db, owner, path, params, caller):
    if not (path in ("/store/info", "/categories") or re.fullmatch(r"/categories/[1-9][0-9]{0,29}(/products)?", path)
            or re.fullmatch(r"/products/[1-9][0-9]{0,29}(/variants)?", path)):
        raise CategoryProofUnavailable("unsafe_native_path")
    try:
        return checked_response(await caller(db, owner, "GET", path, params=params))
    except SallaError as exc:
        raise CategoryProofUnavailable("permission_unproved" if exc.status_code == 403 else "native_read_unavailable") from None
    except httpx.RequestError:
        raise CategoryProofUnavailable("native_read_unavailable") from None


def _product(response, product_id, domain):
    if response.get("success") is not True or response.get("status") != 200 or not isinstance(response.get("data"), dict):
        raise ValueError("native_product_unproved")
    raw = response["data"]
    if canonical_id(raw.get("id")) != product_id:
        raise ValueError("native_product_identity_changed")
    urls = raw.get("urls")
    if not isinstance(urls, dict):
        raise ValueError("native_product_customer_url_unproved")
    safe_customer_url(urls.get("customer"), domain)
    return raw


async def selected_inventory_preview(db, *, owner_id, fields, category_id, selected_ids, caller=call_salla):
    if not isinstance(selected_ids, list) or not 1 <= len(selected_ids) <= 5:
        raise ValueError("selected_product_bound")
    selected_ids = [canonical_id(v) for v in selected_ids]
    if len(set(selected_ids)) != len(selected_ids):
        raise ValueError("duplicate_selected_product")
    excluded = {canonical_id(v) for v in fields.get("excluded_product_ids", [])}
    if excluded & set(selected_ids):
        raise CategoryProofUnavailable("selected_product_excluded")
    started = datetime.now(timezone.utc).isoformat()
    before = await db.salla_integrations.find_one({"user_id": owner_id}, PUBLIC_INTEGRATION)
    if not before:
        raise CategoryProofUnavailable("owned_store_identity_unproved")
    async def fixed(db, owner, method, path, *, params):
        if method != "GET":
            raise ValueError("native_write_forbidden")
        return await _get(db, owner, path, params, caller)
    category = await preview_live_category(db, owner_id=owner_id, fields=fields, category_id=category_id, caller=fixed)
    if category["store_id"] != canonical_id(before.get("store_id")):
        raise CategoryProofUnavailable("store_identity_changed")
    members = {r["salla_product_id"] for r in category["products"]}
    if not set(selected_ids) <= members:
        raise CategoryProofUnavailable("selected_product_membership_unproved")
    products = []
    for product_id in selected_ids:
        first = _product(await _get(db, owner_id, "/products/" + product_id, None, caller), product_id, before["store_domain"])
        variants, hashes, marker = [], [], None
        complete = False
        for page in (1, 2):
            response = await _get(db, owner_id, "/products/" + product_id + "/variants", {"page": page, "per_page": 50}, caller)
            rows, shape = checked_page(response, page)
            if shape[1] > 60:
                raise ValueError("native_variant_page_size_unproved")
            if marker is not None and shape != marker:
                raise ValueError("variant_pagination_changed")
            marker = shape
            if len(variants) + len(rows) > 120:
                break
            variants.extend(rows)
            hashes.append(digest(response))
            if page == shape[0]:
                complete = True
                break
            if shape[0] > 2 or shape[2] > 120:
                break
        last = _product(await _get(db, owner_id, "/products/" + product_id, None, caller), product_id, before["store_domain"])
        observed = product_observations(first, last, variants, complete)
        observed.update(customer_url=first["urls"]["customer"], native_variant_page_digests=hashes,
                        observed_at=datetime.now(timezone.utc).isoformat(), provider_revision=None, remote_snapshot_atomic=False)
        if category["category_status"] != "active":
            observed["blocking_reasons"].append("category_hidden_or_visibility_unproved")
        products.append(observed)
    final_store = _store(await _get(db, owner_id, "/store/info", None, caller), before)
    after = await db.salla_integrations.find_one({"user_id": owner_id}, PUBLIC_INTEGRATION)
    if before != after or final_store["id"] != category["store_id"]:
        raise CategoryProofUnavailable("store_connection_changed")
    return {"store_id": category["store_id"], "category_id": category["category_id"],
        "category_response_digest": category["response_digest"], "category_traversal_complete": category["pagination_traversal_complete"],
        "selected_product_ids": selected_ids, "products": products, "observed_started_at": started,
        "observed_finished_at": datetime.now(timezone.utc).isoformat(), "provider_revision": None, "remote_snapshot_atomic": False,
        "globally_ready": False, "size_coverage_verified": False, "preparation_time": None,
        "merchant_mapping_verified": False, "execution_authorized": False}
