from __future__ import annotations

import asyncio

import pytest

from reviewed_preparation_v3 import (
    bounded_map_ordered,
    stable_ready_unit_id,
    stable_reviewed_line_revision,
    stable_reviewed_product_revision,
)
from reviewed_preparation_batches import plan_preparation_allocations


def _line(**patch):
    row = {
        "order_number": "1001",
        "order_item_id": "line-1",
        "source_item_id": "source-1",
        "product_id": "product-1",
        "parent_product_id": "parent-1",
        "variant_id": "variant-1",
        "sku": "AMS1001",
        "barcode": "123",
        "quantity": 2,
        "options_normalized": {"size": "8"},
        "available_unit_indices": [1, 2],
    }
    row.update(patch)
    return row


def test_line_revision_ignores_display_enrichment_but_tracks_identity():
    original = _line(name="قديم", image_url="https://old.example/image.jpg")
    enriched = _line(name="اسم الكتالوج", image_url="https://new.example/image.jpg")

    assert stable_reviewed_line_revision(original) == stable_reviewed_line_revision(enriched)
    assert stable_reviewed_line_revision(original) != stable_reviewed_line_revision(
        _line(sku="AMS-CHANGED")
    )
    assert stable_reviewed_line_revision(original) != stable_reviewed_line_revision(
        _line(options_normalized={"size": "10"})
    )


def test_product_revision_tracks_exact_available_units():
    line = _line()
    product = {
        "group_key": "product:product-1",
        "quantity": 2,
        "remaining_quantity": 2,
        "source_lines": [{**line, "line_revision": stable_reviewed_line_revision(line)}],
    }
    original = stable_reviewed_product_revision(product)
    changed = stable_reviewed_product_revision({
        **product,
        "remaining_quantity": 1,
        "source_lines": [{
            **product["source_lines"][0],
            "remaining_quantity": 1,
            "available_unit_indices": [2],
        }],
    })
    assert original != changed


def test_ready_unit_id_is_stable_and_changes_only_with_piece_index():
    line = _line(line_index=3)
    first = stable_ready_unit_id(line, 1)

    assert first == stable_ready_unit_id({**line, "name": "اسم عرض جديد"}, 1)
    assert first != stable_ready_unit_id(line, 2)
    assert first.startswith("ready-unit:")


def test_plan_rejects_stale_selection_revision_fail_closed():
    line = _line()
    product = {
        "group_key": "product:product-1",
        "name": "منتج",
        "quantity": 2,
        "remaining_quantity": 2,
        "revision": "a" * 64,
        "source_lines": [line],
    }
    with pytest.raises(ValueError, match="reviewed_selection_stale"):
        plan_preparation_allocations(
            [product],
            [{"group_key": product["group_key"], "quantity": 1, "revision": "b" * 64}],
        )

    planned = plan_preparation_allocations(
        [product],
        [{"group_key": product["group_key"], "quantity": 1, "revision": "a" * 64}],
    )
    assert planned[0]["unit_indices"] == [1]


@pytest.mark.asyncio
async def test_bounded_map_preserves_order_and_limits_parallelism():
    active = 0
    peak = 0

    async def worker(value: int) -> int:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return value * 10

    result = await bounded_map_ordered(range(12), worker, concurrency=3)

    assert result == [value * 10 for value in range(12)]
    assert peak == 3


def test_recovery_revision_uses_frozen_identity_not_catalog_enrichment():
    frozen = {
        "order_item_id": "review-snapshot:1001:0",
        "source_item_id": "",
        "product_id": "",
        "parent_product_id": "",
        "variant_id": "",
        "sku": "",
        "barcode": "",
        "quantity": 1,
    }
    sparse = _line(
        order_item_id=frozen["order_item_id"],
        source_item_id=None,
        product_id=None,
        parent_product_id=None,
        variant_id=None,
        sku=None,
        barcode=None,
        quantity=1,
        review_snapshot_identity=frozen,
    )
    enriched = {
        **sparse,
        "source_item_id": "catalog-source",
        "product_id": "catalog-product",
        "parent_product_id": "catalog-parent",
        "variant_id": "catalog-variant",
        "sku": "AMS11949",
        "barcode": "catalog-barcode",
    }

    assert stable_reviewed_line_revision(sparse) == stable_reviewed_line_revision(enriched)
    assert stable_reviewed_line_revision(sparse) != stable_reviewed_line_revision({
        **sparse,
        "options_normalized": {"size": "10"},
    })


def test_product_revision_ignores_source_line_order_only():
    first = _line(order_item_id="line-1", available_unit_indices=[1])
    second = _line(order_item_id="line-2", available_unit_indices=[1])
    product = {
        "group_key": "product:product-1",
        "remaining_quantity": 2,
        "source_lines": [first, second],
    }

    assert stable_reviewed_product_revision(product) == stable_reviewed_product_revision({
        **product,
        "source_lines": [second, first],
    })

def test_recovery_revision_uses_frozen_options_not_live_order_options():
    frozen = {
        "order_item_id": "review-snapshot:1001:0",
        "quantity": 1,
        "options": {"size": "10"},
    }
    line = _line(
        review_snapshot_identity=frozen,
        options_normalized={"size": "12"},
    )
    original = stable_reviewed_line_revision(line)
    assert original == stable_reviewed_line_revision(
        {**line, "options_normalized": {"size": "14"}}
    )
    assert original != stable_reviewed_line_revision(
        _line(
            review_snapshot_identity={**frozen, "options": {"size": "12"}},
            options_normalized={"size": "12"},
        )
    )
