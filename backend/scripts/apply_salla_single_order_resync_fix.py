#!/usr/bin/env python3
"""Apply the Salla single-order resync fix safely.

This script only edits source/test files. It never calls Salla, Qoyod, MongoDB,
or any production endpoint.
"""
from __future__ import annotations

from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
SYNC = ROOT / "backend" / "salla_integration" / "sync.py"
ROUTES = ROOT / "backend" / "orders_explorer_routes.py"
TEST = ROOT / "backend" / "tests" / "test_salla_single_order_status_resync.py"


def fail(message: str) -> None:
    raise SystemExit(f"PATCH_ABORTED: {message}")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        fail(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


def patch_sync() -> None:
    text = SYNC.read_text(encoding="utf-8")

    text = replace_once(
        text,
        "import asyncio\nimport copy\nimport uuid\n",
        "import asyncio\nimport logging\nimport uuid\n",
        "sync imports",
    )
    text = replace_once(
        text,
        "from .service import SallaError, call_salla\n",
        "from pymongo.errors import DuplicateKeyError\n\nfrom .service import SallaError, call_salla\n",
        "DuplicateKeyError import",
    )
    text = replace_once(
        text,
        "from orders_db import upsert_order\n\n\n# Salla's /orders endpoint",
        "from orders_db import upsert_order\n\n\nlogger = logging.getLogger(__name__)\n\n\n# Salla's /orders endpoint",
        "logger declaration",
    )

    start = text.find("async def _refresh_plan_b_status_snapshot(")
    end = text.find("\n\n# ── Salla order → unified_orders document shape", start)
    if start < 0 or end < 0:
        fail("could not locate _refresh_plan_b_status_snapshot block")

    new_snapshot = '''async def _refresh_plan_b_status_snapshot(
    db,
    user_id: str,
    order_number: str,
    order_doc: dict,
) -> dict:
    """Upsert a read-only current-status snapshot for Plan B.

    This snapshot uses its own connector and is explicitly ineligible for
    Qoyod processing. Repeating the same status updates the same snapshot
    instead of violating the inbox idempotency index.
    """
    order_number = str(order_number or "").strip()
    status_slug = str(
        order_doc.get("order_status_slug")
        or order_doc.get("order_status")
        or ""
    ).strip().lower()
    status_native = str(order_doc.get("order_status") or status_slug).strip()

    if not order_number or not status_slug:
        return {
            "created": False,
            "updated": False,
            "reason": "missing_order_or_status",
        }

    latest = await db.integration_inbox.find_one(
        {
            "user_id": {"$in": [user_id, "main"]},
            "$or": [
                {"salla_order_number": order_number},
                {"canonical_payload.order_number": order_number},
                {"canonical_payload.order_id": order_number},
            ],
        },
        sort=[("received_at", -1)],
    )

    canonical = dict((latest or {}).get("canonical_payload") or {})
    previous_slug = str(canonical.get("order_status") or "").strip().lower()
    previous_native = str(canonical.get("order_status_native") or "").strip()
    snapshot_user_id = str((latest or {}).get("user_id") or user_id).strip()

    now = _now()
    metadata = dict(canonical.get("metadata") or {})
    metadata.update({
        "source_event": "order.updated",
        "status_source": "salla_order_details",
        "resynced_at": now,
    })
    canonical.update({
        "order_number": order_number,
        "order_status": status_slug,
        "order_status_native": status_native,
        "metadata": metadata,
    })

    connector_key = "salla_direct_status_resync"
    idempotency_key = (
        f"salla:order:{order_number}:order.updated:{status_slug}"
    )
    trace_id = uuid.uuid4().hex

    snapshot = {
        "trace_id": trace_id,
        "user_id": snapshot_user_id,
        "connector_key": connector_key,
        "idempotency_key": idempotency_key,
        "salla_order_number": order_number,
        "source": connector_key,
        "received_at": now,
        "updated_at": now,
        "canonical_payload": canonical,
        "pipeline_stage": "STATUS_SNAPSHOT",
        "no_qoyod_send": True,
        "eligibility_only": True,
        "manual_send_allowed": False,
        "auto_send_allowed": False,
        "salla_direct_status_resync": {
            "at": now,
            "source_endpoint": "GET /orders/{id}",
            "previous_status_slug": previous_slug or None,
            "previous_status_native": previous_native or None,
            "new_status_slug": status_slug,
            "new_status_native": status_native,
        },
    }

    selector = {
        "user_id": snapshot_user_id,
        "connector_key": connector_key,
        "idempotency_key": idempotency_key,
    }
    try:
        result = await db.integration_inbox.update_one(
            selector,
            {
                "$set": snapshot,
                "$setOnInsert": {
                    "id": uuid.uuid4().hex,
                    "created_at": now,
                },
            },
            upsert=True,
        )
    except DuplicateKeyError:
        await db.integration_inbox.update_one(selector, {"$set": snapshot})
        return {
            "created": False,
            "updated": True,
            "reason": "concurrent_duplicate_snapshot_updated",
            "trace_id": trace_id,
            "status_slug": status_slug,
            "status_native": status_native,
            "no_qoyod_send": True,
        }

    return {
        "created": result.upserted_id is not None,
        "updated": result.upserted_id is None,
        "trace_id": trace_id,
        "connector_key": connector_key,
        "idempotency_key": idempotency_key,
        "previous_status": previous_slug or None,
        "new_status": status_slug,
        "status_slug": status_slug,
        "status_native": status_native,
        "no_qoyod_send": True,
    }
'''
    text = text[:start] + new_snapshot + text[end:]

    marker = "\n\nasync def resync_single_order(db, user_id: str, order_number: str) -> dict:\n"
    if marker not in text:
        fail("could not locate resync_single_order")
    helper = '''\n\nasync def _fetch_salla_order_details(
    db,
    user_id: str,
    order_number: str,
) -> dict | None:
    """Resolve the internal Salla id, then fetch authoritative details."""
    search_resp = await call_salla(
        db,
        user_id,
        "GET",
        "/orders",
        params={
            "keyword": order_number,
            "format": "light",
            "per_page": 10,
        },
    )
    rows = search_resp.get("data") if isinstance(search_resp, dict) else None
    if not isinstance(rows, list):
        rows = []

    match = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        reference_id = str(row.get("reference_id") or "").strip()
        row_id = str(row.get("id") or "").strip()
        if reference_id == order_number or row_id == order_number:
            match = row
            break

    if match is None and len(rows) == 1 and isinstance(rows[0], dict):
        match = rows[0]
    if match is None:
        return None

    internal_id = str(match.get("id") or "").strip()
    if not internal_id:
        raise RuntimeError(
            f"Salla search result missing internal id: {order_number}"
        )

    details_resp = await call_salla(
        db,
        user_id,
        "GET",
        f"/orders/{internal_id}",
    )
    details = details_resp.get("data") if isinstance(details_resp, dict) else None
    if not isinstance(details, dict):
        raise RuntimeError(
            f"Salla Order Details returned invalid payload: {order_number}"
        )

    actual_reference = str(
        details.get("reference_id") or details.get("order_number") or ""
    ).strip()
    if actual_reference and actual_reference != order_number:
        raise RuntimeError(
            "Salla Order Details reference mismatch: "
            f"expected={order_number} actual={actual_reference}"
        )
    return details
'''
    text = text.replace(marker, helper + marker, 1)

    old_fetch = '''    # Salla supports keyword search on reference_id
    try:
        resp = await call_salla(
            db, user_id, "GET", "/orders",
            params={"keyword": order_number, "format": "light", "per_page": 10},
        )
    except SallaError as e:
        return {"ok": False, "found": False, "error": str(e),
                "needs_reauth": e.needs_reauth}

    data = resp.get("data") or []
    # Find exact match (keyword can return partials)
    raw = None
    for o in data:
        if str(o.get("reference_id") or o.get("id")) == order_number:
            raw = o
            break
    if raw is None and data:
        # Some Salla tenants only return id-based matches when reference_id
        # is searched; accept the single result if it's a unique hit.
        if len(data) == 1:
            raw = data[0]

    if raw is None:
        return {"ok": True, "found": False, "before": before,
                "error": "not_found_in_salla"}
'''
    new_fetch = '''    try:
        raw = await _fetch_salla_order_details(
            db, user_id, order_number
        )
    except SallaError as e:
        return {"ok": False, "found": False, "error": str(e),
                "needs_reauth": e.needs_reauth}

    if raw is None:
        return {"ok": True, "found": False, "before": before,
                "error": "not_found_in_salla"}
'''
    text = replace_once(text, old_fetch, new_fetch, "single-order fetch block")

    text = replace_once(
        text,
        '''    doc = _salla_order_to_doc(raw)
    if not doc.get("order_number"):
        return {"ok": False, "found": False, "error": "order_number missing in payload"}
''',
        '''    doc = _salla_order_to_doc(raw)
    if not doc.get("order_number"):
        return {"ok": False, "found": False, "error": "order_number missing in payload"}
    current_slug = str(
        doc.get("order_status_slug") or doc.get("order_status") or ""
    ).strip().lower()
    if not current_slug:
        raise RuntimeError(
            f"Salla Order Details missing status: {order_number}"
        )
''',
        "status validation",
    )

    SYNC.write_text(text, encoding="utf-8")


def patch_routes() -> None:
    text = ROUTES.read_text(encoding="utf-8")
    text = replace_once(
        text,
        "from io import BytesIO\nfrom typing import Optional\n",
        "from io import BytesIO\nimport logging\nfrom typing import Optional\nimport uuid\n",
        "routes imports",
    )
    text = replace_once(
        text,
        "from order_status_policy import default_category_for, get_policy_map, resolve_category\n\n\ndef attach_orders_explorer_routes",
        "from order_status_policy import default_category_for, get_policy_map, resolve_category\n\n\nlogger = logging.getLogger(__name__)\n\n\ndef attach_orders_explorer_routes",
        "routes logger",
    )

    old = '''    @router.post("/{order_number}/resync")
    async def resync_order(order_number: str, user: dict = Depends(current_user)):
        """Iter-87 — Manual re-fetch from Salla for a single order. Picks
        up missed `order.updated` events from Make.com / Salla webhooks
        (e.g. order paid after being created with pending_payment)."""
        from salla_integration.sync import resync_single_order
        from salla_integration.service import SallaError
        try:
            result = await resync_single_order(db, user["id"], order_number)
        except SallaError as e:
            raise HTTPException(
                status_code=e.status_code if e.status_code != 200 else 400,
                detail={"message": str(e), "needs_reauth": e.needs_reauth},
            )
        # Attach the resolved policy category for convenience
        if result.get("after"):
            overrides = await get_policy_map(db, user["id"])
            result["after"]["category"] = resolve_category(
                result["after"].get("order_status"), overrides
            )
        return result
'''
    new = '''    @router.post("/{order_number}/resync")
    async def resync_order(order_number: str, user: dict = Depends(current_user)):
        """Re-fetch one order from Salla without invoking Qoyod writes."""
        from salla_integration.sync import resync_single_order
        from salla_integration.service import SallaError
        try:
            result = await resync_single_order(db, user["id"], order_number)
            if result.get("after"):
                overrides = await get_policy_map(db, user["id"])
                result["after"]["category"] = resolve_category(
                    result["after"].get("order_status"), overrides
                )
            return result
        except SallaError as e:
            raise HTTPException(
                status_code=e.status_code if e.status_code != 200 else 400,
                detail={"message": str(e), "needs_reauth": e.needs_reauth},
            )
        except Exception:
            error_reference = uuid.uuid4().hex
            logger.exception(
                "Unexpected single-order Salla resync failure "
                "error_reference=%s user_id=%s order_number=%s",
                error_reference,
                user.get("id"),
                order_number,
            )
            raise HTTPException(
                status_code=500,
                detail={
                    "message": "تعذر إعادة فحص الطلب من سلة",
                    "error_reference": error_reference,
                    "order_number": order_number,
                },
            )
'''
    text = replace_once(text, old, new, "resync endpoint")
    ROUTES.write_text(text, encoding="utf-8")


def write_tests() -> None:
    TEST.write_text('''"""Regression tests for single-order Salla status resync."""
from unittest.mock import AsyncMock, patch

import pytest

from salla_integration.sync import (
    _fetch_salla_order_details,
    _refresh_plan_b_status_snapshot,
)


@pytest.mark.asyncio
async def test_fetch_uses_order_details_as_authority():
    calls = []

    async def fake_call(db, user_id, method, path, params=None):
        calls.append((method, path, params))
        if path == "/orders":
            return {"data": [{
                "id": 987654,
                "reference_id": "271887616",
                "status": {"slug": "completed", "name": "تم التنفيذ"},
            }]}
        assert path == "/orders/987654"
        return {"data": {
            "id": 987654,
            "reference_id": "271887616",
            "status": {"slug": "under_review", "name": "تم المراجعة"},
        }}

    with patch("salla_integration.sync.call_salla", new=fake_call):
        details = await _fetch_salla_order_details(
            object(), "main", "271887616"
        )

    assert details["status"]["slug"] == "under_review"
    assert calls[0][1] == "/orders"
    assert calls[1][1] == "/orders/987654"


class _Result:
    def __init__(self, upserted_id=None):
        self.upserted_id = upserted_id


class _Inbox:
    def __init__(self):
        self.rows = []

    async def find_one(self, query, sort=None):
        if not self.rows:
            return {
                "user_id": "main",
                "canonical_payload": {
                    "order_number": "271887616",
                    "order_status": "completed",
                    "order_status_native": "تم التنفيذ",
                },
            }
        return self.rows[-1]

    async def update_one(self, selector, update, upsert=False):
        row = dict(update.get("$set") or {})
        existing = next((r for r in self.rows if all(
            r.get(k) == v for k, v in selector.items()
        )), None)
        if existing:
            existing.update(row)
            return _Result(None)
        row.update(update.get("$setOnInsert") or {})
        self.rows.append(row)
        return _Result("inserted")


class _DB:
    def __init__(self):
        self.integration_inbox = _Inbox()


@pytest.mark.asyncio
async def test_snapshot_is_status_aware_idempotent_and_never_sendable():
    db = _DB()
    doc = {
        "order_status_slug": "under_review",
        "order_status": "تم المراجعة",
    }

    first = await _refresh_plan_b_status_snapshot(
        db, "main", "271887616", doc
    )
    second = await _refresh_plan_b_status_snapshot(
        db, "main", "271887616", doc
    )

    assert first["created"] is True
    assert second["updated"] is True
    assert len(db.integration_inbox.rows) == 1
    row = db.integration_inbox.rows[0]
    assert row["idempotency_key"] == (
        "salla:order:271887616:order.updated:under_review"
    )
    assert row["canonical_payload"]["order_status"] == "under_review"
    assert row["canonical_payload"]["order_status_native"] == "تم المراجعة"
    assert row["connector_key"] == "salla_direct_status_resync"
    assert row["pipeline_stage"] == "STATUS_SNAPSHOT"
    assert row["no_qoyod_send"] is True
    assert row["manual_send_allowed"] is False
    assert row["auto_send_allowed"] is False
''', encoding="utf-8")


def main() -> None:
    for path in (SYNC, ROUTES):
        if not path.exists():
            fail(f"missing file: {path}")
    patch_sync()
    patch_routes()
    write_tests()
    print("PATCH_APPLIED")
    print(f"updated: {SYNC.relative_to(ROOT)}")
    print(f"updated: {ROUTES.relative_to(ROOT)}")
    print(f"created: {TEST.relative_to(ROOT)}")
    print("No Salla/Qoyod/database calls were made.")


if __name__ == "__main__":
    main()
