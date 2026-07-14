"""Pure mapper tests for OrderDTO → OrderItemIdentityDTO[]."""

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from order_engine.models import (
    CustomerDTO,
    MoneyTotalsDTO,
    OrderDTO,
    OrderItemDTO,
    OrderSourceDTO,
    PaymentDTO,
    ShippingDTO,
)
from order_item_engine.mapper import (
    OrderItemMappingError,
    map_order_item_identities,
    map_order_item_identity,
)


def make_item(
    *,
    order_item_id: str,
    source_item_id: str,
    name: str = "اسوارة الفراشة",
    sku: str = "AMS-001",
) -> OrderItemDTO:
    return OrderItemDTO(
        order_item_id=order_item_id,
        source_item_id=source_item_id,
        product_id="product-1",
        parent_product_id="parent-1",
        variant_id="variant-1",
        sku=sku,
        barcode="123456789",
        name=name,
        quantity=1,
        image_url="https://example.test/main.jpg",
        image_urls=[
            "https://example.test/main.jpg",
            "https://example.test/second.jpg",
        ],
        product_url="https://example.test/product",
        unit_price=100,
        discount=5,
        tax_reported_by_source=15,
        total=110,
        options_raw=[
            {"name": "اللون", "value": "ذهبي"},
            {"name": "المقاس", "value": "18"},
        ],
        options_normalized={
            "اللون": "ذهبي",
            "المقاس": "18",
        },
        color="ذهبي",
        size="18",
        material="فضة",
        custom_fields=[
            {"name": "النقش", "value": "سارة"},
        ],
        # These temporary Order Engine placeholders must not leak into
        # the immutable Order Item identity.
        preparation_status="pending",
        availability_status="unknown",
        fulfillment_source="supplier",
    )


def make_order(items=None) -> OrderDTO:
    return OrderDTO(
        order_id="source-order-1",
        order_number="272008653",
        created_at=datetime(
            2026,
            7,
            14,
            10,
            30,
            tzinfo=timezone.utc,
        ),
        source=OrderSourceDTO(
            source_order_id="source-order-1",
            source_reference="272008653",
        ),
        customer=CustomerDTO(),
        payment=PaymentDTO(),
        shipping=ShippingDTO(),
        totals=MoneyTotalsDTO(
            currency="SAR",
            total=220,
        ),
        items=items
        if items is not None
        else [
            make_item(
                order_item_id="salla:272008653:item-1",
                source_item_id="item-1",
            )
        ],
    )


def test_maps_one_order_item_identity():
    order = make_order()

    result = map_order_item_identities(order)

    assert len(result) == 1

    identity = result[0]

    assert identity.order_item_id == "salla:272008653:item-1"
    assert identity.order_id == "source-order-1"
    assert identity.order_number == "272008653"
    assert identity.line_index == 0

    assert identity.source.provider == "salla"
    assert identity.source.source_order_item_id == "item-1"
    assert identity.source.source_product_id == "product-1"
    assert identity.source.source_variant_id == "variant-1"

    assert identity.name == "اسوارة الفراشة"
    assert identity.sku == "AMS-001"
    assert identity.color == "ذهبي"
    assert identity.size == "18"
    assert identity.material == "فضة"

    assert identity.currency == "SAR"
    assert identity.unit_price == 100
    assert identity.discount == 5
    assert identity.total == 110


def test_each_order_line_receives_its_own_index_and_identity():
    order = make_order([
        make_item(
            order_item_id="salla:272008653:item-1",
            source_item_id="item-1",
        ),
        make_item(
            order_item_id="salla:272008653:item-2",
            source_item_id="item-2",
        ),
    ])

    result = map_order_item_identities(order)

    assert [item.line_index for item in result] == [0, 1]
    assert [item.order_item_id for item in result] == [
        "salla:272008653:item-1",
        "salla:272008653:item-2",
    ]


def test_same_product_in_two_lines_remains_two_identities():
    first = make_item(
        order_item_id="salla:272008653:item-1",
        source_item_id="item-1",
        sku="SAME-SKU",
    )
    second = make_item(
        order_item_id="salla:272008653:item-2",
        source_item_id="item-2",
        sku="SAME-SKU",
    )

    order = make_order([first, second])
    result = map_order_item_identities(order)

    assert result[0].product_id == result[1].product_id
    assert result[0].sku == result[1].sku
    assert result[0].order_item_id != result[1].order_item_id


def test_duplicate_order_item_identity_is_rejected():
    order = make_order([
        make_item(
            order_item_id="duplicate-id",
            source_item_id="item-1",
        ),
        make_item(
            order_item_id="duplicate-id",
            source_item_id="item-2",
        ),
    ])

    with pytest.raises(
        OrderItemMappingError,
        match="duplicate order_item_id",
    ):
        map_order_item_identities(order)


def test_options_are_preserved_without_duplicate_normalized_entries():
    identity = map_order_item_identities(make_order())[0]

    assert [
        (option.name, option.value)
        for option in identity.options
    ] == [
        ("اللون", "ذهبي"),
        ("المقاس", "18"),
    ]


def test_normalized_options_are_used_when_raw_options_are_missing():
    item = make_item(
        order_item_id="item-1",
        source_item_id="source-item-1",
    )
    item.options_raw = []
    item.options_normalized = {
        "اللون": "فضي",
        "المقاس": "20",
    }

    identity = map_order_item_identities(
        make_order([item])
    )[0]

    assert [
        (option.name, option.value)
        for option in identity.options
    ] == [
        ("اللون", "فضي"),
        ("المقاس", "20"),
    ]


def test_mapper_does_not_mutate_order_dto():
    order = make_order()
    before = order.model_dump(mode="python")

    map_order_item_identities(order)

    assert order.model_dump(mode="python") == before


def test_mutable_identity_fields_are_deep_copied():
    order = make_order()
    identity = map_order_item_identities(order)[0]

    identity.image_urls.append(
        "https://example.test/new.jpg"
    )
    identity.custom_fields[0]["value"] = "معدّل"

    assert (
        "https://example.test/new.jpg"
        not in order.items[0].image_urls
    )
    assert order.items[0].custom_fields[0]["value"] == "سارة"


def test_operational_placeholders_do_not_leak_into_identity():
    identity = map_order_item_identities(make_order())[0]
    payload = identity.model_dump()

    assert "preparation_status" not in payload
    assert "availability_status" not in payload
    assert "fulfillment_source" not in payload
    assert "supplier_id" not in payload
    assert "receiving_employee_id" not in payload


def test_empty_order_returns_empty_identity_list():
    assert map_order_item_identities(
        make_order(items=[])
    ) == []


def test_direct_mapper_rejects_negative_line_index():
    order = make_order()

    with pytest.raises(
        OrderItemMappingError,
        match="line_index",
    ):
        map_order_item_identity(
            order=order,
            item=order.items[0],
            line_index=-1,
        )


def test_non_order_dto_is_rejected():
    with pytest.raises(
        OrderItemMappingError,
        match="OrderDTO",
    ):
        map_order_item_identities({})


def test_mapper_has_no_io_or_framework_dependencies():
    import ast
    import inspect
    import order_item_engine.mapper as mapper

    tree = ast.parse(inspect.getsource(mapper))

    imported_modules = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.update(
                alias.name for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported_modules.add(node.module)

    forbidden = {
        "motor",
        "pymongo",
        "requests",
        "httpx",
        "fastapi",
        "server",
    }

    assert not any(
        module == blocked
        or module.startswith(f"{blocked}.")
        for module in imported_modules
        for blocked in forbidden
    )
