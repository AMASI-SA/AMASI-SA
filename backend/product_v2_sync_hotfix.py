"""Production-safe pagination hotfix for Mezan Product V2 sync.

Salla's products endpoint returns only 15 rows when ``format=light`` is sent in
this store.  The legacy sync that successfully fetched 1,200 products does not
send that parameter.  This service mirrors the proven request shape and never
archives products unless the remote catalogue was traversed completely.
"""
from __future__ import annotations

import uuid
from typing import Any

from pymongo import ReturnDocument

from product_v2_routes import (
    CHANGE_LOG,
    MAX_PRODUCT_PAGES,
    PRODUCTS,
    PRODUCTS_PER_PAGE,
    SYNC_RUNS,
    _now,
    _text,
    ensure_product_v2_indexes,
    normalize_salla_product,
)
from salla_integration.service import SallaError, call_salla


def _pagination(response: dict[str, Any]) -> dict[str, Any]:
    direct = response.get("pagination")
    if isinstance(direct, dict):
        return direct
    meta = response.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("pagination"), dict):
        return meta["pagination"]
    return {}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def next_product_page(*, requested_page: int, row_count: int, pagination: dict[str, Any]) -> int | None:
    """Resolve the next Salla page without treating the default 15 rows as EOF."""
    if row_count <= 0:
        return None
    current = _as_int(
        pagination.get("currentPage")
        or pagination.get("current_page")
        or pagination.get("page"),
        requested_page,
    )
    total = _as_int(
        pagination.get("totalPages")
        or pagination.get("total_pages")
        or pagination.get("last_page")
        or pagination.get("lastPage"),
        0,
    )
    if total and current >= total:
        return None
    return requested_page + 1


async def run_product_v2_sync_fixed(db: Any, user_id: str) -> dict[str, Any]:
    await ensure_product_v2_indexes(db)
    user_id = str(user_id)
    run_id = uuid.uuid4().hex
    started_at = _now()
    await db[SYNC_RUNS].insert_one({
        "id": run_id,
        "user_id": user_id,
        "status": "running",
        "started_at": started_at,
        "ended_at": None,
        "created": 0,
        "updated": 0,
        "unchanged": 0,
        "errors_count": 0,
        "pages_fetched": 0,
        "collection": PRODUCTS,
        "pagination_strategy": "full_products_without_format_light",
    })

    created = updated = unchanged = errors_count = pages_fetched = 0
    seen_ids: set[str] = set()
    error_sample: list[dict[str, Any]] = []
    previous_page_signature: tuple[str, ...] | None = None
    traversal_complete = False

    try:
        page = 1
        while page <= MAX_PRODUCT_PAGES:
            response = await call_salla(
                db,
                user_id,
                "GET",
                "/products",
                params={"page": page, "per_page": PRODUCTS_PER_PAGE},
            )
            rows = response.get("data") if isinstance(response, dict) else None
            pages_fetched += 1
            if not isinstance(rows, list) or not rows:
                traversal_complete = True
                break

            signature = tuple(_text(row.get("id")) for row in rows if isinstance(row, dict))
            if signature and signature == previous_page_signature:
                errors_count += 1
                error_sample.append({
                    "page": page,
                    "error": "repeated_salla_product_page",
                    "detail": "Salla returned the same product IDs for two consecutive pages.",
                })
                break
            previous_page_signature = signature

            synced_at = _now()
            for raw in rows:
                if not isinstance(raw, dict):
                    errors_count += 1
                    continue
                try:
                    doc = normalize_salla_product(raw, user_id=user_id, synced_at=synced_at)
                    seen_ids.add(doc["salla_product_id"])
                    selector = {"user_id": user_id, "salla_product_id": doc["salla_product_id"]}
                    existing = await db[PRODUCTS].find_one(
                        selector,
                        {"_id": 0, "source_revision": 1, "mezan_product_id": 1},
                    )
                    if existing and existing.get("source_revision") == doc["source_revision"]:
                        unchanged += 1
                        await db[PRODUCTS].update_one(
                            selector,
                            {"$set": {"last_synced_at": synced_at, "archived": False}},
                        )
                        continue

                    now = _now()
                    previous = await db[PRODUCTS].find_one_and_update(
                        selector,
                        {
                            "$set": {**doc, "updated_at": now},
                            "$setOnInsert": {"id": uuid.uuid4().hex, "created_at": now},
                        },
                        upsert=True,
                        return_document=ReturnDocument.BEFORE,
                    )
                    if previous is None:
                        created += 1
                    else:
                        updated += 1
                        await db[CHANGE_LOG].insert_one({
                            "id": uuid.uuid4().hex,
                            "user_id": user_id,
                            "mezan_product_id": doc["mezan_product_id"],
                            "salla_product_id": doc["salla_product_id"],
                            "event_type": "salla_product_updated",
                            "previous_revision": previous.get("source_revision"),
                            "next_revision": doc["source_revision"],
                            "occurred_at": now,
                            "sync_run_id": run_id,
                        })
                except Exception as exc:  # isolate malformed products
                    errors_count += 1
                    if len(error_sample) < 20:
                        error_sample.append({
                            "product_id": _text(raw.get("id")),
                            "error": str(exc)[:300],
                        })

            next_page = next_product_page(
                requested_page=page,
                row_count=len(rows),
                pagination=_pagination(response),
            )
            if next_page is None:
                traversal_complete = True
                break
            page = next_page
        else:
            errors_count += 1
            error_sample.append({
                "page": MAX_PRODUCT_PAGES,
                "error": "product_page_limit_reached",
            })

        # Never archive from a partial traversal.
        if traversal_complete and seen_ids:
            await db[PRODUCTS].update_many(
                {
                    "user_id": user_id,
                    "salla_product_id": {"$nin": list(seen_ids)},
                    "archived": {"$ne": True},
                },
                {"$set": {"archived": True, "archived_at": _now(), "updated_at": _now()}},
            )

        final_status = "completed" if errors_count == 0 and traversal_complete else "completed_with_errors"
        ended_at = _now()
        await db[SYNC_RUNS].update_one(
            {"id": run_id, "user_id": user_id},
            {"$set": {
                "status": final_status,
                "ended_at": ended_at,
                "created": created,
                "updated": updated,
                "unchanged": unchanged,
                "errors_count": errors_count,
                "errors_sample": error_sample,
                "pages_fetched": pages_fetched,
                "seen_products": len(seen_ids),
                "traversal_complete": traversal_complete,
            }},
        )
        return {
            "run_id": run_id,
            "status": final_status,
            "created": created,
            "updated": updated,
            "unchanged": unchanged,
            "errors_count": errors_count,
            "pages_fetched": pages_fetched,
            "seen_products": len(seen_ids),
            "traversal_complete": traversal_complete,
            "collection": PRODUCTS,
        }
    except Exception as exc:
        await db[SYNC_RUNS].update_one(
            {"id": run_id, "user_id": user_id},
            {"$set": {
                "status": "failed",
                "ended_at": _now(),
                "last_error": str(exc)[:500],
                "created": created,
                "updated": updated,
                "unchanged": unchanged,
                "errors_count": errors_count + 1,
                "pages_fetched": pages_fetched,
                "seen_products": len(seen_ids),
                "traversal_complete": False,
            }},
        )
        raise
