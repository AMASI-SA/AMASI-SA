#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPLIER = ROOT / "backend" / "supplier_receiving_routes.py"
TEST = ROOT / "backend" / "tests" / "test_supplier_invoice_service_eligibility.py"


def replace_span(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start anchor not found")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{label}: end anchor not found")
    return text[:start_index] + replacement + text[end_index:]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


supplier = SUPPLIER.read_text(encoding="utf-8")
supplier = replace_span(
    supplier,
    '''            if is_addition:
''',
    '''            else:
                reference_row = eligible_maps[0][service_id]
''',
    '''            if is_addition:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "supplier_receiving_service_add_via_product_required",
                        "service_id": service_id,
                        "line_number": line_number,
                    },
                )
''',
    "disable invoice-close service insertion",
)
SUPPLIER.write_text(supplier, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
test = replace_once(
    test,
    '''from datetime import datetime, timezone

from preparation_piece_operations import inherit_required_services
''',
    '''from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from preparation_piece_operations import inherit_required_services
''',
    "test pytest imports",
)
test = replace_once(
    test,
    '''from supplier_receiving_routes import (
    _invoice_group_key,
''',
    '''from supplier_receiving_routes import (
    ADD_PRODUCT_SERVICE_PERMISSION,
    SupplierReceivingInvoiceLineRequest,
    SupplierReceivingInvoiceServiceRequest,
    _invoice_group_key,
    build_supplier_receiving_invoice,
''',
    "test invoice builder imports",
)
test += r'''


def test_invoice_close_cannot_bypass_permanent_service_endpoint() -> None:
    with pytest.raises(HTTPException) as captured:
        build_supplier_receiving_invoice(
            session={"reference": "SR-1", "supplier_snapshot": {"service_links": []}},
            scans=[{
                "piece_id": "piece-1",
                "product_id": "product-1",
                "product_name": "منتج",
                "sku": "SKU-1",
                "product_charge_eligible": True,
                "reference_product_unit_price_halalas": 5000,
                "invoice_services": [],
            }],
            requested_lines=[SupplierReceivingInvoiceLineRequest(
                piece_ids=["piece-1"],
                product_unit_price_halalas=5000,
                services=[SupplierReceivingInvoiceServiceRequest(
                    service_id="new-service",
                    unit_price_halalas=1000,
                    add_to_product=True,
                )],
            )],
            saved_at=datetime.now(timezone.utc),
            permissions={ADD_PRODUCT_SERVICE_PERMISSION},
            service_catalog={
                "new-service": {
                    "id": "new-service",
                    "name": "خدمة جديدة",
                    "unit": "job",
                    "unit_cost": 10,
                }
            },
        )
    assert captured.value.status_code == 409
    assert captured.value.detail["code"] == (
        "supplier_receiving_service_add_via_product_required"
    )
'''
TEST.write_text(test, encoding="utf-8")

print("Required dedicated permanent-service endpoint.")
