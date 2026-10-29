"""Owner-triggered fixed Salla GET observations. Never syncs catalog or stock."""
from datetime import datetime, timezone
import re

from salla_integration.service import SallaError, call_salla
from .google_live_category_proof import canonical_id, checked_page, checked_response, digest, safe_customer_url

PUBLIC_INTEGRATION = {"_id": 0, "store_id": 1, "store_domain": 1, "status": 1, "scope": 1, "token_revision": 1}


class CategoryProofUnavailable(ValueError):
    pass


async def _native(db, owner, path, params, caller):
    if not (path in ("/store/info", "/categories") or re.fullmatch(r"/categories/[1-9][0-9]{0,29}(/products)?", path)):
        raise ValueError("unsafe_provider_path")
    try:
        return checked_response(await caller(db, owner, "GET", path, params=params))
    except SallaError as exc:
        raise CategoryProofUnavailable("permission_unproved" if exc.status_code == 403 else "native_read_unavailable") from None


async def _pages(db, owner, path, caller, maximum_pages, maximum_rows):
    result, expected, pages_read, hashes = [], None, 0, []
    for page in range(1, maximum_pages + 1):
        response = await _native(db, owner, path, {"page": page, "per_page": 50}, caller)
        rows, marker = checked_page(response, page)
        if expected is not None and marker != expected:
            raise CategoryProofUnavailable("pagination_changed")
        expected = marker
        if len(result) + len(rows) > maximum_rows:
            return result, False, pages_read, hashes
        ids = [canonical_id(r.get("id")) for r in rows]
        if len(set(ids)) != len(ids) or set(ids) & {canonical_id(r["id"]) for r in result}:
            raise CategoryProofUnavailable("duplicate_source_identity")
        result.extend(rows)
        pages_read += 1
        hashes.append(digest(response))
        if page == marker[0]:
            return result, True, pages_read, hashes
        # Do not expand a response once its declared traversal exceeds bounds.
        if marker[0] > maximum_pages or marker[2] > maximum_rows:
            return result, False, pages_read, hashes
    return result, False, pages_read, hashes


def _store(response, integration):
    if not isinstance(response, dict) or response.get("success") is not True or response.get("status") != 200:
        raise CategoryProofUnavailable("store_identity_unproved")
    data = response.get("data")
    if not isinstance(data, dict) or canonical_id(data.get("id")) != canonical_id(integration.get("store_id")):
        raise CategoryProofUnavailable("store_identity_changed")
    if not isinstance(data.get("domain"), str) or not data["domain"]:
        raise CategoryProofUnavailable("store_domain_unproved")
    # Exact provider store domain consistency; never probe this domain as URL.
    if data["domain"].rstrip("/") != str(integration.get("store_domain") or "").rstrip("/"):
        raise CategoryProofUnavailable("store_identity_changed")
    return {"id": canonical_id(data["id"]), "domain": data["domain"]}


async def preview_live_category(db, *, owner_id, fields, category_id=None, caller=call_salla):
    started = datetime.now(timezone.utc).isoformat()
    before = await db.salla_integrations.find_one({"user_id": owner_id}, PUBLIC_INTEGRATION)
    if not before or not before.get("store_id") or not before.get("store_domain") or not before.get("token_revision"):
        raise CategoryProofUnavailable("owned_store_identity_unproved")
    ids = [canonical_id(v) for v in fields.get("category_ids", [])]
    if category_id is None and len(ids) == 1:
        category_id = ids[0]
    if category_id is not None:
        category_id = canonical_id(category_id)
        if category_id not in ids:
            raise CategoryProofUnavailable("category_not_in_saved_plan")
    url = fields.get("category_url")
    if url is not None:
        safe_customer_url(url, before["store_domain"])
    initial_store = _store(await _native(db, owner_id, "/store/info", None, caller), before)
    category_hashes = []
    if category_id is not None:
        category_id = canonical_id(category_id)
        if category_id not in ids:
            raise CategoryProofUnavailable("category_not_in_saved_plan")
        response = await _native(db, owner_id, "/categories/" + category_id, None, caller)
        if not isinstance(response, dict) or response.get("success") is not True or response.get("status") != 200 or not isinstance(response.get("data"), dict):
            raise CategoryProofUnavailable("category_identity_unproved")
        category = response["data"]
        if canonical_id(category.get("id")) != category_id:
            raise CategoryProofUnavailable("category_identity_changed")
        category_hashes = [digest(response)]
    elif url:
        categories, complete, _, category_hashes = await _pages(db, owner_id, "/categories", caller, 10, 500)
        selected = [r for r in categories if isinstance(r.get("urls"), dict) and r["urls"].get("customer") == url]
        if not complete or len(selected) != 1:
            raise CategoryProofUnavailable("category_url_resolution_unproved")
        category = selected[0]
        category_id = canonical_id(category["id"])
    else:
        raise CategoryProofUnavailable("select_one_saved_category")
    customer = (category.get("urls") or {}).get("customer") if isinstance(category.get("urls"), dict) else None
    if url is not None and customer != url:
        raise CategoryProofUnavailable("category_url_mismatch")
    if customer is not None:
        safe_customer_url(customer, initial_store["domain"])
    rows, complete, pages, hashes = await _pages(db, owner_id, "/categories/" + category_id + "/products", caller, 4, 200)
    final_store = _store(await _native(db, owner_id, "/store/info", None, caller), before)
    after = await db.salla_integrations.find_one({"user_id": owner_id}, PUBLIC_INTEGRATION)
    if after != before or final_store != initial_store:
        raise CategoryProofUnavailable("store_connection_changed")
    excluded = set(fields.get("excluded_product_ids") or [])
    category_status = category.get("status") if category.get("status") in {"active", "hidden"} else "unknown"
    return {"store_id": initial_store["id"], "category_id": category_id, "category_url": customer,
        "category_status": category_status, "observed_started_at": started, "observed_finished_at": datetime.now(timezone.utc).isoformat(),
        "source": "salla_owned_category_products_fixed_get", "provider_revision": None, "remote_snapshot_atomic": False,
        "pagination_traversal_complete": complete, "pages_read": pages, "products_observed": len(rows),
        "response_digest": digest(category_hashes + hashes), "stored_scope_authority": "declared_or_unknown",
        "unresolved_exclusion_ids": sorted(v for v in excluded if not isinstance(v, str) or not re.fullmatch(r"[1-9][0-9]{0,29}", v)),
        "mezan_product_mapping_verified": False,
        "inventory_verified": False, "execution_authorized": False,
        "products": [{"salla_product_id": canonical_id(r["id"]), "membership": "observed_category_endpoint",
            "excluded": canonical_id(r["id"]) in excluded, "category_hidden": None if category_status == "unknown" else category_status == "hidden",
            "product_status": r.get("status") if r.get("status") in {"sale", "out", "hidden"} else "unknown",
            "inventory": "unproved", "eligible_for_ad_selection": False} for r in rows],
        "limitations": ["Membership observation only; inventory, sizes, preparation time and catalog offer mapping unproved",
            "Completed page traversal is not an atomic category snapshot or campaign readiness",
            "No automatic selection, ranking, profit inference or funding authority"]}
