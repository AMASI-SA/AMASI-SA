"""Exercise real canonical/item/cost helpers using synthetic merchant records."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from mezan_special_orders.canonical_adapter import LocalOrderReader, to_canonical_order
from mezan_special_orders.catalog_adapter import ExistingCatalogAdapter, product_from_catalog, selected_values_from_item
from mezan_special_orders.contracts import OptionValue
from mezan_special_orders.domain import DomainError, validated_options
from mezan_special_orders.tests.test_core import Harness, OWNER, run, request_data


def catalog_row():
    return {"user_id": "test-store", "salla_product_id": "p-demo", "name": "منتج تجريبي", "sku": "TEST-P",
        "archived": False, "source_revision": "catalog-test-2", "options_count": 3,
        "main_image": "https://example.invalid/catalog-test.png", "raw_salla": {
            "options": [
                {"id": 10, "name": "المقاس", "required": True, "values": [{"id": 101, "name": "56"}, {"id": 102, "name": "58"}]},
                {"id": 20, "name": "الاسم", "display_type": "text", "required": True},
                {"id": 30, "name": "الإضافات", "display_type": "checkbox", "required": False, "values": [{"id": 301, "name": "تغليف"}, {"id": 302, "name": "بطاقة"}]},
            ], "images": [], "variants": [{"id": "v-56", "sku": "TEST-56", "values": [{"id": 101}]}, {"id": "v-58", "sku": "TEST-58", "values": [{"id": 102}]}]}}


def options(size="58", multi=False):
    result = [OptionValue(key="option:10", value=size), OptionValue(key="option:20", value="اسم اختباري")]
    if multi:
        result.append(OptionValue(key="option:30", value=("تغليف", "بطاقة")))
    return tuple(result)


class ReadCollection:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.queries = []
    async def find_one(self, query, projection):
        self.queries.append(deepcopy(query))
        for row in self.rows:
            matches = True
            for key, value in query.items():
                actual = row.get(key)
                if isinstance(value, dict):
                    matches &= (actual != value["$ne"]) if "$ne" in value else actual in value["$in"]
                else:
                    matches &= actual == value
            if matches:
                return deepcopy(row)
        return None


class CatalogDB:
    def __init__(self, rows):
        self.collection = ReadCollection(rows)
    def __getitem__(self, name):
        assert name == "mezan_products_v2", "Adapter must not mutate or silently read legacy sources"
        return self.collection


def test_catalog_reads_actual_v2_collection_and_never_copies_catalog_price_as_charge():
    async def scenario():
        row = catalog_row()
        row.update(price=500, cost_price_from_salla=70)
        db = CatalogDB([row])
        adapter = ExistingCatalogAdapter(db)
        product = await adapter.product("test-store", "p-demo", "v-58")
        assert product.variant_id == "v-58" and product.sku == "TEST-58"
        assert "price" not in product.model_dump() and "cost_price_from_salla" not in product.model_dump()
        assert db.collection.queries == [{"user_id": "test-store", "salla_product_id": "p-demo", "archived": {"$ne": True}}]
        assert product.option_rules[0].source_option_id == "10"
        assert product.variant_selections[0].value == "58"
    run(scenario())


@pytest.mark.parametrize("field,value,error", [("user_id", "another-store", "catalog_scope_or_identity_mismatch"), ("salla_product_id", "different", "catalog_scope_or_identity_mismatch"), ("archived", True, "catalog_product_archived"), ("options_count", "three", "catalog_option_count_invalid")])
def test_catalog_tenant_identity_and_metadata_validation(field, value, error):
    row = catalog_row(); row[field] = value
    with pytest.raises(DomainError, match=error):
        product_from_catalog(row, "test-store", "p-demo")


def test_unknown_product_is_not_resolved_from_another_merchant():
    async def scenario():
        row = catalog_row(); row["user_id"] = "another-store"
        with pytest.raises(DomainError, match="product_not_found"):
            await ExistingCatalogAdapter(CatalogDB([row])).product("test-store", "p-demo")
    run(scenario())


def test_variant_cannot_silently_use_wrong_size_or_another_product_sku():
    row = catalog_row()
    product = product_from_catalog(row, "test-store", "p-demo", "v-58")
    assert validated_options(product, options())[0]["value_id"] == "102"
    with pytest.raises(DomainError, match="selected_options_do_not_match_variant"):
        validated_options(product, options("56"))
    with pytest.raises(DomainError, match="catalog_variant_not_found"):
        product_from_catalog(row, "test-store", "p-demo", "foreign-variant")


@pytest.mark.parametrize("variant", [{"id": "v-58"}, {"id": "v-58", "values": [{"id": "unknown"}]}, {"id": "v-58", "values": [{"id": 102}, {"id": 101}]}])
def test_variant_missing_or_ambiguous_selection_fails_closed(variant):
    row = catalog_row(); row["raw_salla"]["variants"] = [variant]
    with pytest.raises(DomainError):
        product_from_catalog(row, "test-store", "p-demo", "v-58")


def test_variant_named_option_shape_is_supported():
    row = catalog_row(); row["raw_salla"]["variants"] = [{"id": "v-58", "options": [{"name": "المقاس", "value": "58"}]}]
    product = product_from_catalog(row, "test-store", "p-demo", "v-58")
    assert product.variant_selections[0].value == "58"


@pytest.mark.parametrize("change", ["missing_schema", "ambiguous_name", "file_option", "bad_images", "duplicate_choice"])
def test_missing_or_corrupt_options_are_not_silently_accepted(change):
    row = catalog_row()
    if change == "missing_schema": row["raw_salla"]["options"] = []
    elif change == "ambiguous_name": row["raw_salla"]["options"][1]["name"] = "المقاس"
    elif change == "file_option": row["raw_salla"]["options"][1]["display_type"] = "file_upload"
    elif change == "bad_images": row["raw_salla"]["images"] = "https://example.invalid/not-a-list"
    elif change == "duplicate_choice": row["raw_salla"]["options"][0]["values"].append({"id": 999, "name": "58"})
    with pytest.raises((DomainError, ValidationError)):
        product_from_catalog(row, "test-store", "p-demo")


def test_multi_choice_option_snapshot_preserves_ids_and_free_text():
    product = product_from_catalog(catalog_row(), "test-store", "p-demo")
    values = selected_values_from_item({"options_raw": [
        {"option_id": "10", "value": {"id": "102"}},
        {"option_id": "20", "value": "نص خاص بالعميل"},
        {"option_id": "30", "values": [{"id": 301}, {"id": 302}]},
    ]}, product)
    rows = validated_options(product, values)
    assert rows[0]["value_id"] == "102"
    assert rows[1]["value"] == "نص خاص بالعميل"
    assert rows[2]["value"] == ["تغليف", "بطاقة"]
    assert [v["id"] for v in rows[2]["values"]] == ["301", "302"]


def test_catalog_mutation_does_not_rewrite_an_existing_snapshot():
    row = catalog_row()
    product = product_from_catalog(row, "test-store", "p-demo")
    row["name"] = "اسم جديد"; row["raw_salla"]["options"][0]["values"][0]["name"] = "52"
    assert product.name == "منتج تجريبي" and product.option_rules[0].choices == ("56", "58")


def test_canonical_uses_real_order_and_item_contracts_without_faking_salla_identity():
    from order_engine.models import OrderDTO
    from order_item_engine.mapper import map_order_item_identities
    async def scenario():
        h = Harness(); order = await h.create()
        doc = await h.doc(order)
        dto = to_canonical_order(doc, tenant_id="test-store")
        assert isinstance(dto, OrderDTO)
        assert dto.source.provider == "mezan" and dto.source.source_order_id is None
        assert dto.order_purpose == "replacement" and dto.original_order_number == "TEST-ORIGINAL"
        identities = map_order_item_identities(dto)
        assert identities[0].source.provider == "mezan"
        assert identities[0].order_item_id == doc["items"][0]["order_item_id"]
        assert identities[0].options[0].value == "56"
        assert identities[0].total == 0
    run(scenario())


def test_partial_bank_receipt_in_canonical_dto_does_not_count_until_verified():
    async def scenario():
        h = Harness(); o = await h.receipt(await h.create(partial=True))
        before = to_canonical_order(await h.doc(o), tenant_id="test-store")
        assert before.payment.paid_amount == 0 and before.payment.remaining_amount == 60
        assert before.payment.method == "cod" and before.payment.receipt_url is None
        o = await h.bank(o)
        dto = to_canonical_order(await h.doc(o), tenant_id="test-store")
        assert dto.payment.paid_amount == 40 and dto.payment.remaining_amount == 20
        assert dto.totals.total == 60 and dto.totals.shipping == 20
    run(scenario())


def test_current_multi_options_reach_existing_option_cost_binding_and_identity_helpers():
    from order_item_engine.mapper import map_order_item_identities
    from order_option_cost_snapshot_routes import binding_matches, selected_option_tokens
    async def scenario():
        h = Harness()
        h.ports.product_value = product_from_catalog(catalog_row(), "test-store", "p-demo", "v-58")
        data = request_data("creator")
        data["items"][0].update(variant_id="v-58", options=[v.model_dump(mode="json") for v in options(multi=True)])
        order = await h.create(data=data)
        dto = to_canonical_order(await h.doc(order), tenant_id="test-store")
        tokens = selected_option_tokens(dto.items[0])
        assert binding_matches({"option_id": "10", "value_id": "102"}, tokens)
        assert binding_matches({"option_id": "30", "value_id": "301"}, tokens)
        assert binding_matches({"option_id": "30", "value_id": "302"}, tokens)
        assert not binding_matches({"option_id": "10", "value_id": "101"}, tokens)
        identity = map_order_item_identities(dto)[0]
        assert [(v.name, v.value) for v in identity.options] == [("المقاس", "58"), ("الاسم", "اسم اختباري"), ("الإضافات", "تغليف / بطاقة")]
        assert identity.sku == "TEST-58" and identity.source.source_variant_id == "v-58"
        from product_inventory_rules import order_item_specifications
        # Existing helper normalizes Arabic keys; compare its two real DTO inputs.
        assert order_item_specifications(dto.items[0]) == order_item_specifications(identity)
        assert len(order_item_specifications(identity)) == 3
        assert any("تغليف" in v and "بطاقة" in v for v in order_item_specifications(identity).values())
    run(scenario())


def test_original_adapter_uses_selected_recipient_not_buyer_and_copies_options():
    from order_engine.models import OrderDTO
    async def scenario():
        db = CatalogDB([catalog_row()]); calls = []
        dto = OrderDTO.model_validate({"order_id": "original-test", "order_number": "ORIGINAL-TEST", "created_at": "2026-09-20T12:00:00Z", "source": {},
            "customer": {"name": "المشتري", "mobile": "0511111111"},
            "shipping": {"recipient": {"name": "المستلم", "mobile": "0500000000"}, "address": {"city": "مدينة اختبار", "district": "حي اختبار", "formatted": "عنوان اختبار مستقل"}, "tracking_number": "OLD-TRACKING"},
            "items": [{"order_item_id": "old-line", "product_id": "p-demo", "variant_id": "v-56", "name": "قديم", "quantity": 1, "options_raw": [{"option_id": "10", "value": {"id": "101"}}, {"option_id": "20", "value": "اسم الطلب القديم"}]}]})
        before = dto.model_dump(mode="json")
        async def reader(tenant, number):
            calls.append((tenant, number)); return dto
        original = await ExistingCatalogAdapter(db, reader).original("test-store", "ORIGINAL-TEST")
        assert calls == [("test-store", "ORIGINAL-TEST")]
        assert original.recipient.name == "المستلم" and original.tracking_number == "OLD-TRACKING"
        assert original.items[0].options[0].value == "56"
        assert dto.model_dump(mode="json") == before
    run(scenario())


@pytest.mark.parametrize("mutation", ["tenant", "options", "purpose", "policy", "identity"])
def test_canonical_reader_rejects_tampered_source_before_it_can_be_prepared(mutation):
    async def scenario():
        h = Harness(); o = await h.create(); doc = await h.doc(o)
        if mutation == "tenant": doc["tenant_id"] = "another-store"
        elif mutation == "options": doc["items"][0]["options"][0]["value"] = "58"
        elif mutation == "purpose": doc["purpose"] = "gift"
        elif mutation == "policy": doc["policy"]["counts_in_sales_orders"] = True
        elif mutation == "identity": doc["order_number"] = "123456"
        with pytest.raises(DomainError): to_canonical_order(doc, tenant_id="test-store")
    run(scenario())


def test_repository_default_does_not_access_new_collection():
    from order_engine.repository import MongoOrderRepository
    class DB:
        unified_orders = object()
        def __getitem__(self, key): raise AssertionError("local reads must be explicitly enabled")
    repo = MongoOrderRepository(DB())
    assert repo.supports_mezan_orders is False
    assert run(repo.get_mezan_order(user_id="test-store", order_number="MZ-" + "A" * 32)) is None


def test_ordinary_salla_wire_payload_does_not_gain_local_metadata_fields():
    from order_engine.models import OrderDTO
    dto = OrderDTO.model_validate({"order_id": "ordinary", "order_number": "1001",
        "created_at": "2026-09-20T12:00:00Z", "source": {"provider": "salla"}, "is_gift": True})
    wire = dto.model_dump(mode="json")
    assert wire["is_gift"] is True and dto.order_purpose == "sale"
    assert not {"order_purpose", "original_order_number", "special_order_id"}.intersection(wire)
