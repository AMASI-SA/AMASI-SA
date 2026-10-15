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


def stable_ready_item_id(line: dict[str, Any]) -> str:
    """Return a deterministic id for one reviewed, file-ready order line."""
    payload = {
        "order_number": _text(line.get("order_number")),
        "order_item_id": _text(line.get("order_item_id")),
        "line_index": int(line.get("line_index") or 0),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"ready-item:{hashlib.sha256(encoded).hexdigest()[:32]}"


def stable_ready_unit_id(line: dict[str, Any], unit_index: int) -> str:
    """Return the immutable identity of one physical reviewed piece."""
    index = int(unit_index)
    if index <= 0:
        raise ValueError("invalid_ready_unit_index")
    payload = {
        "ready_item_id": stable_ready_item_id(line),
        "unit_index": index,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"ready-unit:{hashlib.sha256(encoded).hexdigest()[:32]}"


def stable_reviewed_line_revision(line: dict[str, Any]) -> str:
    """Hash only frozen commercial/operational identity facts.

    Display/catalog enrichment must not invalidate an already-open selection.
    Recovery lines therefore use their immutable review snapshot identity
    instead of the Product V2-enriched projection. Live lines still use the
    current canonical identity because they have no frozen recovery identity.
    """
    frozen_identity = (
        line.get("review_snapshot_identity")
        if isinstance(line.get("review_snapshot_identity"), dict)
        else line.get("ready_item_identity")
    )
    identity = frozen_identity if isinstance(frozen_identity, dict) else line
    payload = {
        "order_number": _text(line.get("order_number")),
        "order_item_id": _text(identity.get("order_item_id") or line.get("order_item_id")),
        "source_item_id": _text(identity.get("source_item_id")),
        "product_id": _text(identity.get("product_id")),
        "parent_product_id": _text(identity.get("parent_product_id")),
        "variant_id": _text(identity.get("variant_id")),
        "sku": _text(identity.get("sku")).upper(),
        "barcode": _text(identity.get("barcode")),
        "quantity": int(identity.get("quantity") or line.get("quantity") or 0),
        "options": (
            identity.get("options")
            if "options" in identity
            else (line.get("options_normalized") or {})
        ),
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
    lines.sort(key=lambda row: (
        row["line_revision"],
        row["available_unit_indices"],
        row["remaining_quantity"],
    ))
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
    "stable_ready_item_id",
    "stable_ready_unit_id",
    "stable_reviewed_line_revision",
    "stable_reviewed_product_revision",
]
