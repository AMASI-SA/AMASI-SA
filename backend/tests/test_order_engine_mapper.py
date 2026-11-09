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


def test_uncollected_cod_with_null_salla_remaining_uses_order_total(salla_order_payload):
    salla_order_payload.update({
        "status": {"slug": "under_review", "name": "بانتظار المراجعة"},
        "payment_method": "cod",
        "payment": {},
        "payment_actions": {
            "remaining_action": {
                "has_remaining_amount": False,
                "paid_amount": {"amount": 0, "currency": "SAR"},
                "remaining_amount": None,
            }
        },
        "remaining_amount": 0,
    })

    payment = map_salla_order(salla_order_payload).payment

    assert payment.paid_amount == 0
    assert payment.remaining_amount == 135
    assert payment.has_remaining_amount is True
    assert payment.collection_status == "unpaid"


@pytest.mark.parametrize("override", [
    {"payment_actions": {"remaining_action": {
        "has_remaining_amount": False, "remaining_amount": {"amount": 0, "currency": "SAR"},
    }}},
    {"payment": {"status": "paid"}},
    {"status": {"slug": "delivered"}},
    {"payment_method": "mada"},
    {"paid_amount": 40},
])
def test_cod_missing_balance_does_not_override_collected_or_explicit_data(salla_order_payload, override):
    salla_order_payload.update({
        "status": {"slug": "under_review"},
        "payment_method": "cod",
        "payment": {},
        "payment_actions": {"remaining_action": {
            "has_remaining_amount": False, "paid_amount": {"amount": 0, "currency": "SAR"},
            "remaining_amount": None,
        }},
    })
    salla_order_payload.update(override)

    assert map_salla_order(salla_order_payload).payment.remaining_amount == 0


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


def test_settlement_clears_stale_root_balance(salla_order_payload):
    salla_order_payload.update(payment_method="mada", paid_amount=80,
        remaining_amount=20, has_remaining_amount=True, payment_collection_status="partial")
    salla_order_payload["payment_actions"] = {"remaining_action": {
        "has_remaining_amount": False, "remaining_amount": None,
        "paid_amount": {"amount": 100, "currency": "SAR"}}}
    order = map_salla_order(salla_order_payload)
    assert order.payment.paid_amount == 100
    assert order.payment.remaining_amount == 0
    assert order.payment.has_remaining_amount is False
    assert order.payment.collection_status == "paid"


def test_missing_collection_flag_does_not_clear_balance(salla_order_payload):
    salla_order_payload.update(paid_amount=80, remaining_amount=20,
        has_remaining_amount=True, payment_collection_status="partial")
    salla_order_payload["payment_actions"] = {"remaining_action": {"remaining_amount": None}}
    order = map_salla_order(salla_order_payload)
    assert order.payment.remaining_amount == 20
    assert order.payment.collection_status == "partial"


@pytest.mark.parametrize("payment_fields, expected", [
    ({"payment_actions": {"remaining_action": {"paid_amount": 135, "has_remaining_amount": False,
             "remaining_amount": None}}, "remaining_amount": 85}, ("135.00", "0.00")),
    ({"payment_actions": {"remaining_action": {"paid_amount": 135, "has_remaining_amount": False,
             "remaining_amount": 44, "checkout_url":"https://example.test/stale"}}}, ("135.00", "0.00")),
    ({"payment_actions": {"remaining_action": {"paid_amount": 135, "has_remaining_amount": False,
             "remaining_amount": 44}}, "payment":{"remaining_amount":44}}, None),
    ({"payment_actions": {"refund_action": {"paid_amount": {"currency": "SAR", "amount": 35}},
                         "remaining_action": {"remaining_amount": 100, "has_remaining_amount": True}}}, ("35.00", "100.00")),
    ({"remaining_amount": 135}, ("0.00", "135.00")),
    ({"payment": {"status": "paid"}}, None),
    ({"paid_amount":135, "payment_methods":[{"payment_method":"mada","amount":135,"currency":"QAR"}]}, None),
    ({"paid_amount":135, "payment_methods":[{"payment_method":"mada","amount":135,"currency":"SAR"}]}, ("135.00", "0.00")),
    ({"payment": {"status": "paid"}, "remaining_amount": {"currency": "SAR", "amount": 0}}, ("135.00", "0.00")),
    ({"payment": {"status": "captured"}, "payment_actions":{"remaining_action":{"remaining_amount":0}}}, ("135.00", "0.00")),
    ({"payment_collection_status":"paid", "remaining_amount":0, "has_remaining_amount":False}, ("135.00", "0.00")),
    ({"payment": {"status": "paid"}, "remaining_amount":0, "refunded_amount":5}, None),
    ({"payment": {"status": "authorized"}, "remaining_amount":0}, None),
    ({"payment": {"status": "paid"}, "remaining_amount":0, "is_pending_payment":True}, None),
    ({"remaining_amount":0}, None),
    ({"payment_status":"paid", "paid_amount":0, "remaining_amount":0}, None),
    ({"payment": {"status": "unpaid"}, "paid_amount": 135}, None),
    ({"paid_amount": 35, "remaining_amount": 95}, None),
    ({"paid_amount": 135, "payment": {"paid_amount": 110}, "remaining_amount": 0}, None),
    ({"paid_amount": {"currency":"QAR","amount":135}}, None),
    ({"payment":{"paid_amount":135, "currency":"QAR"}}, None),
    ({"payment_actions":{"remaining_action":{"paid_amount":135,"currency":"QAR","has_remaining_amount":False}}}, None),
    ({"payment_actions":{"refund_action":{"paid_amount":135,"currency":"QAR"}}}, None),
    ({"paid_amount": 135, "total": {"currency": "QAR", "amount": 135}}, None),
    ({"paid_amount": 135, "total_amount": 130}, None),
    ({"paid_amount": 135, "status": {"slug": "awaiting_payment"}}, None),
    ({"paid_amount": 135, "payment_status": "pending_payment"}, None),
    ({"paid_amount": 135, "payment_methods": [{"payment_method":"tabby","amount":135}]}, None),
    ({"paid_amount": 135, "payment_methods": [{"payment_method":"mada","amount":70}]}, None),
    ({"paid_amount": 135, "payment_method":{"code":"mada","name":"مدى"}}, ("135.00", "0.00")),
    ({"paid_amount": 135, "payment_method":"cod"}, None),
    ({"paid_amount": 135, "payment_method":"bank"}, None),
    ({"paid_amount": 135, "refunded_amount": 3}, None),
    ({"paid_amount": 135, "remaining_amount": False}, None),
])
def test_page_uses_original_reconciled_paid_and_due(salla_order_payload, payment_fields, expected):
    salla_order_payload.update(payment_method="mada", payment={})
    salla_order_payload.update(payment_fields)
    balance = map_salla_order(salla_order_payload).payment.source_balance
    assert ((balance.paid, balance.remaining) if balance else None) == expected


@pytest.mark.parametrize("watermark", [{}, {"requires_authoritative_refresh":True}, {"source_updated_at":"2026-08-10T00:00:00Z"}])
def test_promoted_stale_display_fields_do_not_prove_collection(salla_order_payload, watermark):
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import _map_row

    raw = dict(salla_order_payload, payment_method="mada", payment={})
    doc = {"order_number":str(raw["reference_id"]), "order_date":str(raw["date"]), "paid_amount":135,
           "remaining_amount":0, "raw_by_source":{"salla_direct":raw}, "g47_salla_snapshot":watermark}
    if watermark:
        raw["paid_amount"] = 135
    row = MongoOrderRepository._to_discovery_row(doc)
    dto = _map_row(row.salla_raw, native_payment_source=row.native_payment_source)
    assert dto.payment.paid_amount == 135 and dto.payment.source_balance is None


def test_native_zero_due_is_required_when_paid_amount_is_absent(salla_order_payload):
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import _map_row

    raw = dict(salla_order_payload, payment_method="mada", payment={"status": "paid"})
    doc = {"order_number": str(raw["reference_id"]), "order_date": str(raw["date"]), "remaining_amount": 0,
           "raw_by_source": {"salla_direct": raw}}
    row = MongoOrderRepository._to_discovery_row(doc)
    assert "items" not in row.native_payment_source and "customer" not in row.native_payment_source
    assert _map_row(row.salla_raw, native_payment_source=row.native_payment_source).payment.source_balance is None
    raw["remaining_amount"] = {"amount": 0, "currency": "SAR"}
    row = MongoOrderRepository._to_discovery_row(doc)
    result = _map_row(row.salla_raw, native_payment_source=row.native_payment_source)
    assert result.payment.source_balance.paid == "135.00"


def test_review_approval_survives_internal_component_acknowledgement(salla_order_payload):
    from copy import deepcopy
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import _map_row
    from order_review_business_snapshot import build_snapshot, compare_snapshots, REVIEW_SCOPE_VERSION

    raw = dict(salla_order_payload, payment_method="mada", payment={}, paid_amount=135, remaining_amount=0)
    doc = {"order_number": str(raw["reference_id"]), "order_date": str(raw["date"]),
           "raw_by_source": {"salla_direct": raw}, "g47_salla_snapshot": {"component_pending": True}}

    def snapshot(document):
        row = MongoOrderRepository._to_discovery_row(document)
        order = _map_row(row.salla_raw, native_payment_source=row.native_payment_source)
        return order, build_snapshot(document, order, {}, identity={"operation_id": "same-operation"},
                                     schema_version=REVIEW_SCOPE_VERSION)

    before_order, before = snapshot(doc)
    acknowledged = deepcopy(doc)
    acknowledged["g47_salla_snapshot"]["component_pending"] = False
    after_order, after = snapshot(acknowledged)
    assert before_order.payment.source_balance.paid == "135.00"
    assert after_order.payment.source_balance.paid == "135.00"
    assert before_order.payment.paid_amount == after_order.payment.paid_amount
    comparison = compare_snapshots(before, after)
    assert comparison["equal"], comparison


@pytest.mark.parametrize("method", ["mada", "tabby_installment", "tamara_installment"])
@pytest.mark.parametrize("paid,due", [(135, 0), (0, 135), (60, 75)])
def test_component_wait_does_not_change_native_payment_balance(salla_order_payload, method, paid, due):
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import _map_row
    from salla_payment_collection import order_page_source_current

    stamp = "2026-07-13T13:12:00Z"
    raw = dict(salla_order_payload, payment_method=method, payment={},
               paid_amount=paid, remaining_amount=due, updated_at=stamp)
    doc = {"order_number": str(raw["reference_id"]), "order_date": str(raw["date"]),
           "raw_by_source": {"salla_direct": raw},
           "g47_salla_snapshot": {"source_updated_at": stamp, "component_pending": True}}
    unchanged = deepcopy(doc)
    for pending in (True, False, True):
        doc["g47_salla_snapshot"]["component_pending"] = pending
        row = MongoOrderRepository._to_discovery_row(doc)
        balance = _map_row(row.salla_raw, native_payment_source=row.native_payment_source).payment.source_balance
        assert balance is not None
        assert (balance.paid, balance.remaining) == (f"{paid:.2f}", f"{due:.2f}")
    assert doc == unchanged
    # The financial-posting guard remains independent and unchanged.
    assert not order_page_source_current(doc, raw)


@pytest.mark.parametrize("invalid", ["identity", "stale", "newer_status", "cancelled", "refresh", "amount", "currency", "missing"])
def test_component_wait_does_not_bypass_payment_validation(salla_order_payload, invalid):
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import _map_row

    stamp = "2026-07-13T13:12:00Z"
    raw = dict(salla_order_payload, payment_method="tabby_installment", payment={},
               paid_amount=135, remaining_amount=0, updated_at=stamp)
    doc = {"order_number": str(raw["reference_id"]), "order_date": str(raw["date"]),
           "raw_by_source": {"salla_direct": raw},
           "g47_salla_snapshot": {"source_updated_at": stamp, "component_pending": True}}
    if invalid == "identity": doc["salla_order_id"] = "wrong-order"
    if invalid == "stale": doc["g47_salla_snapshot"]["source_updated_at"] = "2026-07-14T13:12:00Z"
    if invalid == "newer_status": doc["last_salla_direct_status_source_updated_at"] = "2026-07-14T13:12:00Z"
    if invalid == "cancelled": doc["g47_salla_snapshot"]["cancelled"] = True
    if invalid == "refresh": doc["g47_salla_snapshot"]["requires_authoritative_refresh"] = True
    if invalid == "amount": raw["payment"] = {"paid_amount": 20}
    if invalid == "currency": raw["payment"] = {"currency": "USD"}
    if invalid == "missing": del raw["paid_amount"]
    row = MongoOrderRepository._to_discovery_row(doc)
    assert _map_row(row.salla_raw, native_payment_source=row.native_payment_source).payment.source_balance is None


def test_native_tabby_actions_remain_visible_during_component_wait(salla_order_payload):
    from order_engine.repository import MongoOrderRepository
    from order_engine.service import _map_row

    stamp = "2026-07-13T13:12:00Z"
    raw = dict(salla_order_payload, payment_method="tabby_installment", payment={}, updated_at=stamp,
               is_pending_payment=False,
               payment_methods=[{"payment_method": "tabby_installment", "amount": "135.00",
                                 "transaction_reference": "synthetic-tabby-reference"}],
               payment_actions={"remaining_action": {"paid_amount": {"amount": 135, "currency": "SAR"},
                                                     "remaining_amount": None, "has_remaining_amount": False}})
    doc = {"order_number": str(raw["reference_id"]), "order_date": str(raw["date"]),
           "raw_by_source": {"salla_direct": raw},
           "g47_salla_snapshot": {"component_pending": True, "source_updated_at": stamp}}
    row = MongoOrderRepository._to_discovery_row(doc)
    balance = _map_row(row.salla_raw, native_payment_source=row.native_payment_source).payment.source_balance
    assert (balance.paid, balance.remaining) == ("135.00", "0.00")
    assert doc["g47_salla_snapshot"]["component_pending"] is True
