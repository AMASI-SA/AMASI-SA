import pytest
from pydantic import ValidationError

from warehouse_location_routes import (
    CabinetGenerate,
    WarehouseCreate,
    generate_location_rows,
    location_code,
)


def warehouse_payload(**overrides):
    values = {
        "name": "المستودع الرئيسي",
        "code": "WH01",
        "country": "السعودية",
        "city": "الرياض",
        "district": "العليا",
        "street": "شارع 12",
        "warehouse_number": "01",
    }
    values.update(overrides)
    return WarehouseCreate(**values)


def test_location_code_is_compact_and_padded():
    assert location_code("wh01", "a05", 4) == "WH01-A05-004"


def test_generate_length_by_width_grid():
    payload = CabinetGenerate(
        warehouse_id="warehouse-1",
        cabinet_code="A05",
        length=4,
        width=6,
        purpose="temporary_staging",
    )

    rows = generate_location_rows(payload, warehouse_code="WH01")

    assert len(rows) == 24
    assert rows[0] == {
        "code": "WH01-A05-001",
        "number": 1,
        "row": 1,
        "column": 1,
        "grid_y": 1,
        "grid_x": 1,
        "purpose": "temporary_staging",
        "state": "empty",
        "max_items": None,
    }
    assert rows[5]["code"] == "WH01-A05-006"
    assert rows[5]["row"] == 1
    assert rows[5]["column"] == 6
    assert rows[6]["code"] == "WH01-A05-007"
    assert rows[6]["row"] == 2
    assert rows[6]["column"] == 1
    assert rows[-1]["code"] == "WH01-A05-024"
    assert len({row["code"] for row in rows}) == 24


def test_multiple_cabinets_have_independent_numbering():
    first = CabinetGenerate(
        warehouse_id="warehouse-1",
        cabinet_code="A01",
        length=2,
        width=3,
    )
    second = CabinetGenerate(
        warehouse_id="warehouse-1",
        cabinet_code="A02",
        length=3,
        width=2,
    )

    first_rows = generate_location_rows(first, warehouse_code="WH01")
    second_rows = generate_location_rows(second, warehouse_code="WH01")

    assert first_rows[0]["code"] == "WH01-A01-001"
    assert first_rows[-1]["code"] == "WH01-A01-006"
    assert second_rows[0]["code"] == "WH01-A02-001"
    assert second_rows[-1]["code"] == "WH01-A02-006"
    assert {row["code"] for row in first_rows}.isdisjoint(
        {row["code"] for row in second_rows}
    )


def test_generated_locations_preserve_capacity_and_purpose():
    payload = CabinetGenerate(
        warehouse_id="warehouse-1",
        cabinet_code="RET-01",
        length=2,
        width=2,
        purpose="returns",
        max_items_per_location=15,
    )

    rows = generate_location_rows(payload, warehouse_code="WH01")

    assert {row["purpose"] for row in rows} == {"returns"}
    assert {row["max_items"] for row in rows} == {15}


def test_structure_above_safety_limit_is_rejected():
    payload = CabinetGenerate(
        warehouse_id="warehouse-1",
        cabinet_code="BIG",
        length=100,
        width=100,
    )

    with pytest.raises(ValueError, match="cabinet_location_limit_exceeded"):
        generate_location_rows(payload, warehouse_code="WH01")


def test_warehouse_address_is_required_and_code_is_normalized():
    warehouse = warehouse_payload(code=" main 01 ")
    assert warehouse.code == "MAIN-01"
    assert warehouse.city == "الرياض"

    with pytest.raises(ValidationError):
        warehouse_payload(code="A/01")

    with pytest.raises(ValidationError):
        WarehouseCreate(name="ناقص", code="WH02")
