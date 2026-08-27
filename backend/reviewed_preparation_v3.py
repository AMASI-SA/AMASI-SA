"""Stable, bounded primitives for the reviewed-preparation V3 path.

The V3 contract keeps product identity/revision deterministic and ensures that
Mongo/image I/O cannot grow linearly into the proxy timeout window.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, TypeVar

T = TypeVar("T")
R = TypeVar("R")


def _text(value: Any) -> str:
    return str(value or "").strip()


def stable_reviewed_line_revision(line: dict[str, Any]) -> str:
    """Hash only frozen commercial/operational identity facts.

    Display names, catalog images and category labels are deliberately excluded:
    enriching those fields must not invalidate an already-open selection.
    """
    payload = {
        "order_number": _text(line.get("order_number")),
        "order_item_id": _text(line.get("order_item_id")),
        "source_item_id": _text(line.get("source_item_id")),
        "product_id": _text(line.get("product_id")),
        "parent_product_id": _text(line.get("parent_product_id")),
        "variant_id": _text(line.get("variant_id")),
        "sku": _text(line.get("sku")).upper(),
        "barcode": _text(line.get("barcode")),
        "quantity": int(line.get("quantity") or 0),
        "options": line.get("options_normalized") or {},
        "review_snapshot_identity": line.get("review_snapshot_identity") or {},
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def stable_reviewed_product_revision(product: dict[str, Any]) -> str:
    """Revision for the exact currently available units of a product card."""
    lines = []
    for line in product.get("source_lines") or []:
        lines.append({
            "line_revision": line.get("line_revision")
            or stable_reviewed_line_revision(line),
            "available_unit_indices": sorted(
                int(value)
                for value in (line.get("available_unit_indices") or [])
                if int(value) > 0
            ),
            "remaining_quantity": int(
                line.get("remaining_quantity")
                if line.get("remaining_quantity") is not None
                else line.get("quantity")
                or 0
            ),
        })
    payload = {
        "group_key": _text(product.get("group_key")),
        "remaining_quantity": int(
            product.get("remaining_quantity")
            if product.get("remaining_quantity") is not None
            else product.get("quantity")
            or 0
        ),
        "lines": lines,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def bounded_map_ordered(
    values: Iterable[T],
    worker: Callable[[T], Awaitable[R]],
    *,
    concurrency: int,
) -> list[R]:
    """Run independent I/O concurrently while preserving input order."""
    items = list(values)
    if not items:
        return []
    semaphore = asyncio.Semaphore(max(1, int(concurrency)))

    async def run(value: T) -> R:
        async with semaphore:
            return await worker(value)

    return list(await asyncio.gather(*(run(value) for value in items)))


__all__ = [
    "bounded_map_ordered",
    "stable_reviewed_line_revision",
    "stable_reviewed_product_revision",
]
