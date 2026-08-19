#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPPLIER = ROOT / "backend" / "supplier_receiving_routes.py"
PIECES = ROOT / "backend" / "preparation_piece_operations.py"
TEST = ROOT / "backend" / "tests" / "test_supplier_invoice_service_eligibility.py"


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected exactly one match, found {count}")
    return text.replace(old, new, 1)


supplier = SUPPLIER.read_text(encoding="utf-8")
supplier = replace_once(
    supplier,
    '''    for raw in piece.get("services") or []:
        row = dict(raw)
        service_id = _text(row.get("service_id"))
        selected_row = selected.get(service_id)
''',
    '''    for raw in piece.get("services") or []:
        row = dict(raw)
        # A generic product link is only an available service. If the customer
        # did not select it and it was not made permanent from supplier
        # receiving, it must neither remain pending nor block receipt.
        if not _service_is_invoice_eligible(row) and not _service_is_complete(row):
            continue
        service_id = _text(row.get("service_id"))
        selected_row = selected.get(service_id)
''',
    "drop hidden services when finalizing a piece",
)
supplier = replace_once(
    supplier,
    '''        for row in (piece.get("services") or [])
        if _text(row.get("service_id")) and not _service_is_complete(row)
''',
    '''        for row in (piece.get("services") or [])
        if _text(row.get("service_id"))
        and _service_is_invoice_eligible(row)
        and not _service_is_complete(row)
''',
    "group only invoice-eligible pending services",
)
SUPPLIER.write_text(supplier, encoding="utf-8")

pieces = PIECES.read_text(encoding="utf-8")
pieces = replace_once(
    pieces,
    '''    for link in product_links:
        permanent_invoice_service = (
            link.get("supplier_invoice_required") is True
        )
        add(
            link.get("resource_id"),
            link.get("quantity"),
            (
                "supplier_receiving_permanent"
                if permanent_invoice_service
                else "product"
            ),
            customer_selected=False,
            supplier_invoice_required=permanent_invoice_service,
        )
''',
    '''    for link in product_links:
        # Product links are availability/configuration only unless a receiving
        # employee explicitly promoted the service to a permanent supplier
        # invoice service. Customer-selected services come from option bindings.
        if link.get("supplier_invoice_required") is not True:
            continue
        add(
            link.get("resource_id"),
            link.get("quantity"),
            "supplier_receiving_permanent",
            customer_selected=False,
            supplier_invoice_required=True,
        )
''',
    "do not inherit generic product links into future pieces",
)
PIECES.write_text(pieces, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
test = replace_once(
    test,
    '''    assert by_id["ordinary"]["source"] == "product"
    assert by_id["ordinary"]["supplier_invoice_required"] is False
    assert by_id["permanent"]["source"] == "supplier_receiving_permanent"
''',
    '''    assert "ordinary" not in by_id
    assert by_id["permanent"]["source"] == "supplier_receiving_permanent"
''',
    "future piece assertion",
)
TEST.write_text(test, encoding="utf-8")

print("Refined supplier service lifecycle eligibility.")
