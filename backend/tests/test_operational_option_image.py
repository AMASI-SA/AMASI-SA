"""Operational images stay on the internal piece and are reused only by exact safe option key."""
from types import SimpleNamespace

from order_review_routes import operational_image_preference_key
from preparation_piece_operations import _workflow_assembly_pieces


def _item(product="product-one"):
    return SimpleNamespace(
        product_id=product, variant_id=None, parent_product_id=None,
        sku=None, order_item_id="item-a",
    )


def _spec(value="نعم", key="هل تريد تغليف ورد؟"):
    return [{"name": key, "key": key, "value": value}]


def test_operational_photo_key_is_stable_only_for_exact_product_option_and_name():
    first = operational_image_preference_key(_item(), _spec(), "باقة الورد")
    second = operational_image_preference_key(_item(), _spec(), "باقة الورد")
    assert first is not None and first == second
    assert first != operational_image_preference_key(_item(), _spec("لا"), "باقة الورد")
    assert first != operational_image_preference_key(_item("product-two"), _spec(), "باقة الورد")
    assert first != operational_image_preference_key(_item(), _spec(), "كرت إهداء")


def test_personalized_text_never_becomes_reusable_image_mapping():
    assert operational_image_preference_key(_item(), _spec("سارة", "الاسم"), "كرت") is None
    assert operational_image_preference_key(_item(), _spec("تهنئة", "نص الإهداء"), "كرت") is None
    assert operational_image_preference_key(_item(), [], "كرت") is None


def test_assembly_operational_piece_uses_frozen_photo_without_supplier_exports():
    url = "/api/order-reviews-v1/mezan-images/abc"
    workflow = {
        "operational_items": [{
            "operational_item_id": "op:123", "name": "باقة الورد",
            "source_order_item_id": "item-a",
            "linked_specs": _spec(), "selected_image_url": url,
            "preparation_status": "pending", "supplier_export": False,
        }],
        "items": [],
    }
    pieces = _workflow_assembly_pieces(workflow, order_number="296611617")
    assert len(pieces) == 1
    piece = pieces[0]
    assert piece["image_url"] == url
    assert piece["selected_image_url"] == url
    assert piece["virtual_kind"] == "operational"
    assert piece["supplier_export"] is False
    assert piece["inventory_item"] is False
    assert piece["financial_item"] is False
    assert piece["specifications_snapshot"] == _spec()


def test_existing_operational_cards_without_image_still_open_in_assembly():
    pieces = _workflow_assembly_pieces({
        "operational_items": [{
            "operational_item_id": "op:legacy", "name": "كرت",
            "linked_specs": _spec(),
            "preparation_status": "pending",
        }],
        "items": [],
    }, order_number="300")
    assert len(pieces) == 1
    assert pieces[0]["image_url"] is None
    assert pieces[0]["assembly_status"] == "pending"
