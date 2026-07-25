import pytest
from pydantic import ValidationError

from warehouse_location_routes import CabinetGenerate, WarehouseCreate, generate_location_rows, location_code


def test_location_code_is_stable_and_padded():
    assert location_code("a05", 1, 3, 4) == "A05-C01-R03-B04"


def test_generate_locations_for_cabinet_structure():
    payload = CabinetGenerate(
        warehouse_id="warehouse-1",
        cabinet_code="A05",
        columns=2,
        rows=3,
        bins_per_row=4,
        purpose="temporary_staging",
    )

    rows = generate_location_rows(payload)

    assert len(rows) == 24
    assert rows[0] == {
        "code": "A05-C01-R01-B01",
        "column": 1,
        "row": 1,
        "bin": 1,
        "purpose": "temporary_staging",
        "state": "empty",
        "max_items": None,
    }
    assert rows[-1]["code"] == "A05-C02-R03-B04"
    assert len({row["code"] for row in rows}) == 24


def test_generated_locations_preserve_capacity_and_purpose():
    payload = CabinetGenerate(
        warehouse_id="warehouse-1",
        cabinet_code="RET-01",
        columns=1,
        rows=2,
        bins_per_row=2,
        purpose="returns",
        max_items_per_bin=15,
    )

    rows = generate_location_rows(payload)

    assert {row["purpose"] for row in rows} == {"returns"}
    assert {row["max_items"] for row in rows} == {15}


def test_structure_above_safety_limit_is_rejected():
    payload = CabinetGenerate(
        warehouse_id="warehouse-1",
        cabinet_code="BIG",
        columns=50,
        rows=50,
        bins_per_row=3,
    )

    with pytest.raises(ValueError, match="cabinet_location_limit_exceeded"):
        generate_location_rows(payload)


def test_codes_are_normalized_and_invalid_codes_rejected():
    warehouse = WarehouseCreate(name="المستودع الرئيسي", code=" main 01 ")
    assert warehouse.code == "MAIN-01"

    with pytest.raises(ValidationError):
        WarehouseCreate(name="Bad", code="A/01")
