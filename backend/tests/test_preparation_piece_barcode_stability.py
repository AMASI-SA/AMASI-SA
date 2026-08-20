from preparation_piece_barcode import (
    parse_preparation_piece_barcode,
    preparation_piece_barcode,
    preparation_piece_id,
    preparation_piece_identity_key,
)


def test_piece_identity_is_stable_across_different_batches():
    common = dict(
        user_id="merchant-1",
        order_number="12345",
        order_item_id="98765",
        unit_index=1,
    )
    first = preparation_piece_id(batch_id="batch-a", **common)
    second = preparation_piece_id(batch_id="batch-b", **common)
    assert first == second
    assert parse_preparation_piece_barcode(preparation_piece_barcode(batch_id="batch-a", **common)) == first


def test_piece_identity_changes_only_when_physical_order_unit_changes():
    base = dict(user_id="merchant-1", order_number="12345", order_item_id="98765")
    first = preparation_piece_id(batch_id="a", unit_index=1, **base)
    second_unit = preparation_piece_id(batch_id="a", unit_index=2, **base)
    other_item = preparation_piece_id(
        batch_id="a",
        user_id="merchant-1",
        order_number="12345",
        order_item_id="98766",
        unit_index=1,
    )
    assert first != second_unit
    assert first != other_item


def test_identity_key_uses_order_item_and_unit_not_batch():
    key = preparation_piece_identity_key(
        user_id="merchant-1",
        order_number="12345",
        order_item_id="98765",
        unit_index=3,
    )
    assert key == "mezan-piece-v2:merchant-1:12345:98765:3"
    assert "batch" not in key
