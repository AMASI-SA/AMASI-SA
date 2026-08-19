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


def replace_span(text: str, start: str, end: str, replacement: str, label: str) -> str:
    start_index = text.find(start)
    if start_index < 0:
        raise RuntimeError(f"{label}: start anchor not found")
    end_index = text.find(end, start_index)
    if end_index < 0:
        raise RuntimeError(f"{label}: end anchor not found")
    return text[:start_index] + replacement + text[end_index:]


supplier = SUPPLIER.read_text(encoding="utf-8")

supplier = replace_once(
    supplier,
    '''        if not service_id or service_id not in supplier_links:
            continue
''',
    '''        if not service_id:
            continue
''',
    "do not require supplier-service assignment",
)

supplier = replace_span(
    supplier,
    '''async def _supplier_service_catalog(
''',
    '''async def _supplier_product_reference_price(
''',
    '''async def _supplier_service_catalog(
    db: Any,
    *,
    user_id: str,
    session: dict[str, Any],
    mongo_session: Any = None,
) -> dict[str, dict[str, Any]]:
    """Return the active merchant service catalogue.

    Supplier-to-service assignment is operational context only. It must not
    hide a customer-selected service or prevent an authorised employee from
    adding a new permanent service to the product.
    """
    del session
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    rows = await db[RESOURCES].find(
        {
            "user_id": user_id,
            "kind": "service",
            "track_inventory": {"$ne": True},
            "status": {"$ne": "inactive"},
        },
        {
            "_id": 0,
            "id": 1,
            "name": 1,
            "code": 1,
            "unit": 1,
            "unit_cost": 1,
        },
        **kwargs,
    ).sort("name", 1).to_list(5000)
    return {
        _text(row.get("id")): dict(row)
        for row in rows
        if _text(row.get("id"))
    }


''',
    "global service catalogue",
)

supplier = replace_once(
    supplier,
    '''        # A generic product link is only an available service. If the customer
        # did not select it and it was not made permanent from supplier
        # receiving, it must neither remain pending nor block receipt.
        if not _service_is_invoice_eligible(row) and not _service_is_complete(row):
            continue
''',
    "",
    "preserve non-invoiced operational services",
)

supplier = replace_once(
    supplier,
    '''        if _text(row.get("service_id"))
        and _service_is_invoice_eligible(row)
        and not _service_is_complete(row)
''',
    '''        if _text(row.get("service_id")) and not _service_is_complete(row)
''',
    "preserve pending service grouping",
)

selector_helper = '''def supplier_uninvoiced_piece_selector(
    *,
    merchant_id: str,
    product_identifiers: list[str],
    service_id: str,
) -> dict[str, Any]:
    """Target only mutable pieces; issued supplier invoices remain immutable."""
    return {
        "user_id": merchant_id,
        "product_id": {"$in": product_identifiers},
        "status": {"$nin": [PIECE_STATUS_CANCELLED, PIECE_STATUS_RECEIVED]},
        "$and": [
            {
                "$or": [
                    {"supplier_receiving_history": {"$exists": False}},
                    {"supplier_receiving_history": None},
                    {"supplier_receiving_history": []},
                ]
            },
            {
                "services": {
                    "$not": {"$elemMatch": {"service_id": service_id}}
                }
            },
        ],
    }


'''
supplier = replace_once(
    supplier,
    '''def _permanent_supplier_service_snapshot(
''',
    selector_helper + '''def _permanent_supplier_service_snapshot(
''',
    "uninvoiced piece selector helper",
)

supplier = replace_span(
    supplier,
    '''    uninvoiced_piece_query = {
''',
    '''    piece_result = await db[PIECES].update_many(
''',
    '''    uninvoiced_piece_query = supplier_uninvoiced_piece_selector(
        merchant_id=merchant_id,
        product_identifiers=identifiers,
        service_id=normalized_service_id,
    )
''',
    "use immutable-history selector",
)

SUPPLIER.write_text(supplier, encoding="utf-8")

pieces = PIECES.read_text(encoding="utf-8")
pieces = replace_once(
    pieces,
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
    "preserve product preparation services",
)
PIECES.write_text(pieces, encoding="utf-8")

test = TEST.read_text(encoding="utf-8")
test = replace_once(
    test,
    '''from __future__ import annotations

from preparation_piece_operations import inherit_required_services
''',
    '''from __future__ import annotations

from datetime import datetime, timezone

from preparation_piece_operations import inherit_required_services
''',
    "test datetime import",
)
test = replace_once(
    test,
    '''    _permanent_supplier_service_snapshot,
    supplier_piece_invoice_services,
    supplier_piece_service_blocker,
''',
    '''    _permanent_supplier_service_snapshot,
    supplier_piece_invoice_services,
    supplier_piece_service_blocker,
    supplier_service_completion_update,
    supplier_uninvoiced_piece_selector,
''',
    "test helper imports",
)
test = replace_once(
    test,
    '''    assert "ordinary" not in by_id
    assert by_id["permanent"]["source"] == "supplier_receiving_permanent"
''',
    '''    assert by_id["ordinary"]["source"] == "product"
    assert by_id["ordinary"]["supplier_invoice_required"] is False
    assert by_id["permanent"]["source"] == "supplier_receiving_permanent"
''',
    "future product service assertion",
)

test += r'''


def test_customer_selected_service_does_not_require_supplier_assignment() -> None:
    rows = supplier_piece_invoice_services(
        {
            "services": [{
                "service_id": "option",
                "service_name": "خيار العميل",
                "source": "option",
                "customer_selected": True,
                "status": "pending",
                "required_quantity": 1,
            }]
        },
        {"supplier_snapshot": {"id": "supplier-1", "service_links": []}},
        {
            "option": {
                "id": "option",
                "name": "خيار العميل",
                "unit": "job",
                "unit_cost": 10,
            }
        },
    )
    assert [row["service_id"] for row in rows] == ["option"]
    assert rows[0]["reference_unit_price_halalas"] == 1000


def test_hidden_product_service_remains_pending_when_not_invoiced() -> None:
    completed_at = datetime.now(timezone.utc)
    update = supplier_service_completion_update(
        piece={
            "services": [{
                "service_id": "ordinary",
                "service_name": "خدمة تشغيلية",
                "source": "product",
                "status": "pending",
                "required_quantity": 1,
                "completed_quantity": 0,
            }]
        },
        invoice_line={"services": []},
        session={
            "id": "session-1",
            "reference": "SR-1",
            "supplier_snapshot": {
                "id": "supplier-1",
                "company_name": "مورد",
            },
        },
        actor={"id": "employee-1", "name": "موظف"},
        invoice_id="invoice-1",
        completed_at=completed_at,
    )
    assert [row["service_id"] for row in update["$set"]["services"]] == [
        "ordinary"
    ]
    assert update["$set"]["remaining_service_count"] == 1
    assert update["$set"]["status"] == "in_progress"
    assert update["$set"]["execution_status"] == "awaiting_remaining_services"


def test_permanent_service_selector_excludes_invoiced_and_received_pieces() -> None:
    selector = supplier_uninvoiced_piece_selector(
        merchant_id="merchant-1",
        product_identifiers=["product-1"],
        service_id="service-1",
    )
    assert selector["status"]["$nin"] == ["cancelled", "received"]
    history_clause = selector["$and"][0]["$or"]
    assert {"supplier_receiving_history": {"$exists": False}} in history_clause
    assert {"supplier_receiving_history": []} in history_clause
    assert selector["$and"][1]["services"]["$not"]["$elemMatch"] == {
        "service_id": "service-1"
    }
'''
TEST.write_text(test, encoding="utf-8")

print("Finalized supplier invoice service policy.")
