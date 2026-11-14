#!/usr/bin/env python3
"""Reproduce the stale-creation fence with synthetic, local-only evidence.

Run from any directory with the standard library only:
    python docs/operations/MZ-SALLA-MISSING-OPTIONS-20261114/reproduce_stale_creation.py

The script extracts the reviewed functions from the checkout with Python AST.
It does not import the application, read environment files, connect to Mongo,
call a provider, or write data. A minimal in-memory owner/DB seam isolates the
stale branch. This is a source-level mechanism reproduction, not a complete
webhook integration test or proof that a future repair is correct.
"""
from __future__ import annotations

import ast
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[3]
OWNER = "synthetic-owner"
ORDER = "synthetic-order"
EARLIER = "2026-01-01T00:00:00Z"
LATER = "2026-01-01T00:00:01Z"


def load_functions(path: Path, names: set[str]) -> tuple[dict[str, Any], str]:
    source = path.read_bytes()
    module = ast.parse(source.decode("utf-8"), filename=str(path))
    definitions = [
        node for node in module.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    assert {node.name for node in definitions} == names, "Reviewed source seam changed"
    namespace = {
        "Any": Any,
        "Optional": Optional,
        "deepcopy": deepcopy,
        "datetime": datetime,
        "timezone": timezone,
        "ZoneInfo": ZoneInfo,
        "ZoneInfoNotFoundError": ZoneInfoNotFoundError,
    }
    selected = ast.Module(body=definitions, type_ignores=[])
    exec(compile(selected, str(path), "exec"), namespace)
    return namespace, hashlib.sha256(source).hexdigest()


def synthetic_line(identity: str, price: int, choice: str | None) -> dict[str, Any]:
    line = {
        "id": identity,
        "product_id": "synthetic-product",
        "quantity": 1,
        "amounts": {
            "price_without_tax": {"amount": price, "currency": "SAR"},
            "total": {"amount": price, "currency": "SAR"},
        },
    }
    if choice is not None:
        line["options"] = [{
            "product_option_id": "synthetic-size-option",
            "name": "Synthetic size",
            "value": {"id": choice, "name": choice},
        }]
    return line


class InMemoryOrders:
    def __init__(self, document: dict[str, Any]):
        self.document = deepcopy(document)
        self.reads = 0

    async def find_one(self, selector: dict[str, Any]) -> dict[str, Any]:
        assert selector == {"user_id": OWNER, "order_number": ORDER}
        self.reads += 1
        return deepcopy(self.document)


class InMemoryDatabase:
    def __init__(self, document: dict[str, Any]):
        self.unified_orders = InMemoryOrders(document)


async def main() -> None:
    fence, fence_hash = load_functions(ROOT / "backend/fulfillment_v2_routes.py", {
        "_text", "component_source_time", "component_provider_version",
        "persist_component_source_snapshot",
    })
    merger, merger_hash = load_functions(ROOT / "backend/orders_db.py", {
        "_salla_item_identity", "_merge_salla_item_snapshot", "_merge_salla_order_items",
    })
    rich_lines = [
        synthetic_line("synthetic-line-a", 23, "synthetic-small"),
        synthetic_line("synthetic-line-b", 29, "synthetic-large"),
    ]
    sparse_lines = [
        synthetic_line("synthetic-line-a", 23, None),
        synthetic_line("synthetic-line-b", 29, None),
    ]
    newer_raw = {
        "id": "synthetic-native-order",
        "reference_id": ORDER,
        "updated_at": LATER,
        "status": {"slug": "processing"},
        "items": sparse_lines,
    }
    older_creation = {
        **deepcopy(newer_raw),
        "updated_at": EARLIER,
        "status": {"slug": "pending_review"},
        "items": rich_lines,
    }
    current = {
        "user_id": OWNER,
        "order_number": ORDER,
        "order_status_slug": "processing",
        "raw_by_source": {"salla_direct": deepcopy(newer_raw)},
        "g47_salla_snapshot": {
            "source_updated_at": fence["component_provider_version"](newer_raw),
            "revision": 1,
            "cancelled": False,
            "component_pending": True,
        },
    }
    db = InMemoryDatabase(current)
    persistence_calls: list[bool] = []
    owner_calls: list[str] = []

    async def local_owner(database, owner, callback):
        assert database is db and owner == OWNER
        owner_calls.append(owner)
        return await callback(database)

    async def persistence_callback(database):
        assert database is db
        persistence_calls.append(True)
        raise AssertionError("Stale full snapshot unexpectedly reached persistence")

    fence["operational_owner"] = local_owner
    result = await fence["persist_component_source_snapshot"](
        db, user_id=OWNER, order_number=ORDER, payload=older_creation,
        persist=persistence_callback, created_event=True,
    )
    assert result == {"stale": True, "created": False}
    assert owner_calls == [OWNER]
    assert db.unified_orders.reads == 1
    assert not persistence_calls
    assert db.unified_orders.document == current
    assert all("options" not in row for row in current["raw_by_source"]["salla_direct"]["items"])

    # The downstream merge can preserve already-known choices, but the stale
    # return above prevents it from seeing choices from the delayed creation.
    merge = merger["_merge_salla_order_items"]
    rich_then_sparse = merge(rich_lines, sparse_lines)
    assert [row["options"] for row in rich_then_sparse] == [row["options"] for row in rich_lines]
    assert all("options" not in row for row in merge([], sparse_lines))
    different_identity = synthetic_line("synthetic-unrelated-line", 23, None)
    assert "options" not in merge(rich_lines, [different_identity])[0]

    print(json.dumps({
        "result": "reproduced",
        "scope": "source_only_synthetic_no_network_no_database_writes",
        "stale_full_snapshot_rejected": True,
        "persistence_callback_calls": len(persistence_calls),
        "current_document_unchanged": True,
        "known_same_line_choices_preserved_by_downstream_merge": True,
        "new_line_does_not_inherit_other_line_choices": True,
        "source_sha256": {
            "backend/fulfillment_v2_routes.py": fence_hash,
            "backend/orders_db.py": merger_hash,
        },
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
