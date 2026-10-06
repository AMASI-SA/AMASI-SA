"""Generation-specific QR identities preserve every generation-zero barcode."""
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5
import pytest
from preparation_piece_barcode import preparation_piece_id, preparation_piece_identity_key, parse_preparation_piece_barcode
from preparation_piece_operations import build_piece_documents
from reviewed_preparation_batches import _line_from_batch_storage

IDENTITY = dict(user_id="merchant", order_number="order", order_item_id="item", unit_index=1)

def test_generation_zero_keeps_exact_original_seed_and_uuid():
    seed = "mezan-piece-v2:merchant:order:item:1"
    assert preparation_piece_identity_key(**IDENTITY) == seed
    assert preparation_piece_identity_key(**IDENTITY, generation=0) == seed
    assert preparation_piece_id(**IDENTITY, generation=0) == uuid5(NAMESPACE_URL, seed).hex
    assert preparation_piece_id(**IDENTITY) == preparation_piece_id(**IDENTITY, generation=0)

def test_generations_have_distinct_stable_ids_across_files():
    ids = [preparation_piece_id(**IDENTITY, generation=n) for n in range(4)]
    assert len(set(ids)) == 4
    for generation in range(4):
        assert ids[generation] == preparation_piece_id(**IDENTITY, batch_id="different", generation=generation)

@pytest.mark.parametrize("generation", [-1, True, "1", 1.5])
def test_invalid_generation_rejected(generation):
    with pytest.raises(ValueError, match="invalid_preparation_piece_generation"):
        preparation_piece_id(**IDENTITY, generation=generation)

@pytest.mark.parametrize("unit_generations,generation", [({}, 2), ({"1": 3, "2": 4}, 0), ({}, 0)])
def test_rendered_qr_matches_materialized_unit_identity(unit_generations, generation):
    line = dict(order_number="order", order_item_id="item", product_id="product", product_name="Product",
                unit_indices=[1, 2], quantity=2, generation=generation, unit_generations=unit_generations)
    batch = {"id": "batch", "user_id": "merchant", "lines": [line]}
    registry = {"responsible_employee_id": "employee", "file_number": "file"}
    pieces = build_piece_documents(user_id="merchant", registry=registry, batch=batch,
                                  services_by_product={}, assigned_at=datetime.now(timezone.utc))
    assert len(pieces) == 2
    for piece in pieces:
        index = piece["unit_index"]
        card = _line_from_batch_storage({**line, "unit_index": index}, batch)
        assert parse_preparation_piece_barcode(card.barcode_payload) == piece["piece_id"]
        assert piece.get("generation", 0) == unit_generations.get(str(index), generation)
    # Multi-piece printed card anchors to its first unit exactly as before.
    assert parse_preparation_piece_barcode(_line_from_batch_storage(line, batch).barcode_payload) == pieces[0]["piece_id"]
