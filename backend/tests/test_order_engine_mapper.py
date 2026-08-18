"""Pure mapper tests: Salla raw payload → canonical OrderDTO."""
from copy import deepcopy
from datetime import timezone

import pytest

from order_engine.mapper import OrderMappingError, map_salla_order


@pytest.fixture
def salla_order_payload():
    return {
        "id": 1065072654,
        "reference_id": "272139435",
        "date": {
            "date": "2026-07-13 16:11:37.000000",
            "timezone": "Asia/Riyadh",
        },
        "status": {
            "slug": "in_progress",
            "name": "قيد التنفيذ",
        },
        "customer": {
            "id": 9001,
            "full_name": "عميل اختبار",
            "mobile": "0500000000",
            "email": "customer@example.test",
            "is_guest": False,
        },
        "payment_method": {
            "code": "bank",
            "name": "بنك الإنماء",
        },
        "payment": {
            "status": "paid",
            "reference": "TX-100",
            "paid_at": "2026-07-13T16:12:00+03:00",
        },
        "amounts": {
            "sub_total": {"amount": 100, "currency": "SAR"},
            "shipping_cost": {"amount": 25, "currency": "SAR"},
            "discounts": {"amount": 5, "currency": "SAR"},
            "tax": {"amount": 15, "currency": "SAR"},
            "total": {"amount": 135, "currency": "SAR"},
        },
        "shipments": [
            {
                "courier": {
                    "name": "مندوب الرياض",
                    "code": "riyadh_delegate",
                },
                "tracking_number": "SHIP-100",
                "status": "created",
                "shipping_address": {
                    "country": {
                        "name": "السعودية",
                        "code": "SA",
                    },
                    "city": {"name": "الرياض"},
                    "district": "العليا",
                    "street": "شارع الاختبار",
                    "postal_code": "12345",
                },
            }
        ],
        "items": [
            {
                "id": 50001,
                "quantity": 1,
                "product": {
                    "id": 11912,
                    "name": "اسوارة الفراشة",
                    "sku": "AMS11912",
                    "main_image": "https://example.test/product.jpg",
                },
                "variant": {
                    "id": 70001,
                    "sku": "AMS11912-GOLD-18",
                },
                "options": [
                    {"name": "اللون", "value": "ذهبي"},
                    {"name": "المقاس", "value": "18"},
                ],
                "custom_fields": [
                    {"name": "النقش", "value": "سارة"},
                ],
                "amounts": {
                    "price_without_tax": {
                        "amount": 100,
                        "currency": "SAR",
                    },
                    "total_discount": {
                        "amount": 5,
                        "currency": "SAR",
                    },
                    "tax": {
                        "amount": 15,
                        "currency": "SAR",
                    },
                    "total": {
                        "amount": 110,
                        "currency": "SAR",
                    },
                },
            }
        ],
        "tags": [{"name": "هدية"}],
        "customer_notes": "تغليف هدية",
    }


def test_maps_realistic_salla_order(salla_order_payload):
    order = map_salla_order(salla_order_payload)

    assert order.order_id == "1065072654"
    assert order.order_number == "272139435"
    assert order.status == "in_progress"
    assert order.status_native == "قيد التنفيذ"
    assert order.created_at.year == 2026

    assert order.customer.name == "عميل اختبار"
    assert order.customer.shipping_address.city == "الرياض"

    assert order.payment.method == "bank"
    assert order.payment.receiving_bank_code == "bank_inma"
    assert order.payment.receiving_bank_name == "مصرف الإنماء"

    assert order.shipping.company == "مندوب الرياض"
    assert order.shipping.tracking_number == "SHIP-100"

    assert order.totals.total == 135
    assert order.totals.shipping == 25

    assert len(order.items) == 1
    item = order.items[0]

    assert item.order_item_id == "salla:272139435:50001"
    assert item.product_id == "11912"
    assert item.variant_id == "70001"
    assert item.sku == "AMS11912-GOLD-18"
    assert item.color == "ذهبي"
    assert item.size == "18"
    assert item.image_url == "https://example.test/product.jpg"
    assert item.custom_fields[0]["value"] == "سارة"


def test_salla_riyadh_wall_clock_is_normalized_to_utc(salla_order_payload):
    order = map_salla_order(salla_order_payload)

    assert order.created_at.tzinfo == timezone.utc
    assert order.created_at.isoformat() == "2026-07-13T13:11:37+00:00"


def test_naive_salla_timestamp_defaults_to_riyadh_then_utc(salla_order_payload):
    salla_order_payload["date"] = "2026-07-15 19:23:00"

    order = map_salla_order(salla_order_payload)

    assert order.created_at.isoformat() == "2026-07-15T16:23:00+00:00"


def test_explicit_utc_timestamp_remains_authoritative(salla_order_payload):
    salla_order_payload["date"] = "2026-07-15T16:23:00Z"

    order = map_salla_order(salla_order_payload)

    assert order.created_at.isoformat() == "2026-07-15T16:23:00+00:00"


def test_explicit_offset_is_normalized_to_utc(salla_order_payload):
    salla_order_payload["date"] = "2026-07-15T19:23:00+03:00"

    order = map_salla_order(salla_order_payload)

    assert order.created_at.isoformat() == "2026-07-15T16:23:00+00:00"


def test_unknown_source_timezone_falls_back_to_riyadh(salla_order_payload):
    salla_order_payload["date"] = {
        "date": "2026-07-15 19:23:00",
        "timezone": "Invalid/Timezone",
    }

    order = map_salla_order(salla_order_payload)

    assert order.created_at.isoformat() == "2026-07-15T16:23:00+00:00"


def test_mapper_does_not_mutate_raw_payload(salla_order_payload):
    original = deepcopy(salla_order_payload)

    map_salla_order(salla_order_payload)

    assert salla_order_payload == original


def test_stable_generated_order_item_id_without_source_item_id(
    salla_order_payload,
):
    del salla_order_payload["items"][0]["id"]

    first = map_salla_order(salla_order_payload)
    second = map_salla_order(deepcopy(salla_order_payload))

    assert first.items[0].order_item_id == second.items[0].order_item_id
    assert first.items[0].order_item_id.startswith(
        "salla:272139435:generated:"
    )


def test_duplicate_identical_lines_receive_different_ids(
    salla_order_payload,
):
    item = deepcopy(salla_order_payload["items"][0])
    item.pop("id")
    salla_order_payload["items"] = [deepcopy(item), deepcopy(item)]

    order = map_salla_order(salla_order_payload)

    assert len(order.items) == 2
    assert order.items[0].order_item_id != order.items[1].order_item_id


@pytest.mark.parametrize(
    ("bank_name", "expected"),
    [
        ("مصرف الراجحي", "bank_rajhi"),
        ("بنك الإنماء", "bank_inma"),
        ("البنك الأهلي السعودي", "bank_ahli"),
        ("SNB", "bank_ahli"),
    ],
)
def test_receiving_bank_mapping(
    salla_order_payload,
    bank_name,
    expected,
):
    salla_order_payload["payment_method"] = {
        "code": "bank",
        "name": bank_name,
    }

    order = map_salla_order(salla_order_payload)

    assert order.payment.receiving_bank_code == expected


def test_maps_printable_salla_shipment_label(
    salla_order_payload,
):
    salla_order_payload["shipments"][0]["label"] = [
        {
            "url": "https://cdn.salla.sa/example/shipping-label.pdf",
        }
    ]

    order = map_salla_order(salla_order_payload)

    assert order.shipping.label_url == (
        "https://cdn.salla.sa/example/shipping-label.pdf"
    )


def test_maps_salla_bank_account_and_receipt_evidence(
    salla_order_payload,
):
    salla_order_payload["payment_method"] = "bank"
    salla_order_payload["bank"] = {
        "bank_name": "مصرف الراجحي",
    }
    salla_order_payload["receipt_image"] = (
        "https://cdn.salla.sa/example/transfer-receipt.jpg"
    )

    order = map_salla_order(salla_order_payload)

    assert order.payment.receiving_bank_code == "bank_rajhi"
    assert order.payment.receiving_bank_name == "مصرف الراجحي"
    assert order.payment.receipt_url == (
        "https://cdn.salla.sa/example/transfer-receipt.jpg"
    )


def test_maps_partial_collection_without_marking_fully_paid(
    salla_order_payload,
):
    salla_order_payload["payment_method"] = "mada"
    salla_order_payload["payment_actions"] = {
        "refund_action": {
            "paid_amount": {"amount": 129.60, "currency": "SAR"},
        },
        "remaining_action": {
            "has_remaining_amount": True,
            "paid_amount": {"amount": 129.60, "currency": "SAR"},
            "remaining_amount": {"amount": 1.08, "currency": "SAR"},
            "checkout_url": "https://example.test/pay-remaining",
        },
    }
    salla_order_payload["amounts"]["total"] = {
        "amount": 130.68,
        "currency": "SAR",
    }

    order = map_salla_order(salla_order_payload)

    assert order.payment.paid_amount == pytest.approx(129.60)
    assert order.payment.remaining_amount == pytest.approx(1.08)
    assert order.payment.has_remaining_amount is True
    assert order.payment.collection_status == "partial"
    assert order.payment.checkout_url == "https://example.test/pay-remaining"
    assert order.payment.paid_amount + order.payment.remaining_amount == pytest.approx(
        order.totals.total
    )


def test_missing_creation_date_is_rejected(salla_order_payload):
    salla_order_payload.pop("date")

    with pytest.raises(OrderMappingError, match="creation date"):
        map_salla_order(salla_order_payload)


def test_missing_order_number_is_rejected(salla_order_payload):
    salla_order_payload.pop("id")
    salla_order_payload.pop("reference_id")

    with pytest.raises(OrderMappingError, match="order number"):
        map_salla_order(salla_order_payload)


def test_mapper_has_no_database_or_http_dependency():
    import order_engine.mapper as mapper

    names = set(mapper.__dict__)

    forbidden = {
        "db",
        "motor",
        "pymongo",
        "requests",
        "httpx",
        "FastAPI",
        "APIRouter",
    }

    assert names.isdisjoint(forbidden)



def test_maps_nested_transfer_receipt_and_files_custom_field(salla_order_payload):
    salla_order_payload["receipt_image"] = {
        "url": "https://cdn.salla.sa/example/receipt-274724433.jpg",
    }
    salla_order_payload["items"][0]["files"] = [
        {
            "name": "هل تريد إضافة كرت اهداء",
            "value": {"name": "لا"},
        }
    ]

    order = map_salla_order(salla_order_payload)

    assert order.payment.receipt_url == (
        "https://cdn.salla.sa/example/receipt-274724433.jpg"
    )
    assert order.items[0].custom_fields[-1] == {
        "name": "هل تريد إضافة كرت اهداء",
        "value": {"name": "لا"},
    }


def test_salla_customer_choices_are_preserved_for_order_details(salla_order_payload):
    item = salla_order_payload["items"][0]
    item.pop("options", None)
    item["customer_options"] = [
        {"title": "لون الفستان", "answer": {"name": "أخضر"}},
        {"question": "هل تريد إضافة اسم على الفستان", "option_value": "لا"},
        {"label": "مقاس الطفل بالعمر", "selected": {"value": "5 سنة"}},
    ]

    order = map_salla_order(salla_order_payload)
    mapped = order.items[0]

    assert mapped.options_normalized["لون الفستان"] == "أخضر"
    assert mapped.options_normalized["هل تريد إضافة اسم على الفستان"] == "لا"
    assert mapped.options_normalized["مقاس الطفل بالعمر"] == "5 سنة"
    assert len(mapped.options_raw) == 3
