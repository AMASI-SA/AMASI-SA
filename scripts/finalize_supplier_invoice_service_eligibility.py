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
    '''    """Return pending product services that the selected supplier can perform.\n\n    Product groups are already expanded into individual services on the piece,\n    so the invoice never stores a group as one opaque charge.\n    """\n''',
    '''    """Return pending services eligible for this supplier invoice.\n\n    A supplier catalogue link is only a pricing fallback. It is never an\n    eligibility requirement: customer-selected services and explicitly\n    permanent supplier-invoice services remain available even when the supplier\n    has no service links. Ordinary product links that the customer did not\n    select stay hidden.\n    """\n''',
    "supplier invoice service docstring",
)

supplier = replace_once(
    supplier,
    '''        for row in (piece.get("services") or [])\n        if _text(row.get("service_id")) and not _service_is_complete(row)\n''',
    '''        for row in (piece.get("services") or [])\n        if (\n            _text(row.get("service_id"))\n            and _service_is_invoice_eligible(row)\n            and not _service_is_complete(row)\n        )\n''',
    "pending service signature eligibility",
)

supplier = replace_once(
    supplier,
    '''    for raw in piece.get("services") or []:\n        row = dict(raw)\n        service_id = _text(row.get("service_id"))\n        selected_row = selected.get(service_id)\n''',
    '''    for raw in piece.get("services") or []:\n        row = dict(raw)\n        # Legacy pieces may contain an ordinary product-level service that the\n        # customer did not select. Remove it from the durable piece plan when\n        # closing this invoice so it cannot keep the piece in progress.\n        if not _service_is_invoice_eligible(row):\n            continue\n        service_id = _text(row.get("service_id"))\n        selected_row = selected.get(service_id)\n''',
    "drop hidden legacy services on invoice close",
)

SUPPLIER.write_text(supplier, encoding="utf-8")

pieces = PIECES.read_text(encoding="utf-8")
pieces = replace_once(
    pieces,
    '''    for link in product_links:\n        permanent_invoice_service = (\n            link.get("supplier_invoice_required") is True\n        )\n        add(\n            link.get("resource_id"),\n            link.get("quantity"),\n            (\n                "supplier_receiving_permanent"\n                if permanent_invoice_service\n                else "product"\n            ),\n            customer_selected=False,\n            supplier_invoice_required=permanent_invoice_service,\n        )\n''',
    '''    for link in product_links:\n        # A product-level service is only inherited when an authorised\n        # supplier-receiving employee explicitly made it permanent. Ordinary\n        # product links describe what can be configured; they are not proof the\n        # customer selected the service for this order.\n        if link.get("supplier_invoice_required") is not True:\n            continue\n        add(\n            link.get("resource_id"),\n            link.get("quantity"),\n            "supplier_receiving_permanent",\n            customer_selected=False,\n            supplier_invoice_required=True,\n        )\n''',
    "future piece permanent product services only",
)
PIECES.write_text(pieces, encoding="utf-8")

TEST.write_text(
    '''from __future__ import annotations\n\nfrom datetime import datetime, timezone\n\nfrom preparation_piece_operations import inherit_required_services\nfrom supplier_receiving_routes import (\n    _invoice_group_key,\n    _pending_service_signature,\n    _permanent_supplier_service_snapshot,\n    supplier_piece_invoice_services,\n    supplier_piece_service_blocker,\n    supplier_service_completion_update,\n)\n\n\ndef _session() -> dict:\n    return {\n        "id": "session-1",\n        "reference": "SR-1",\n        "supplier_snapshot": {\n            "id": "supplier-1",\n            "company_name": "مورد",\n            "service_links": [\n                {"service_id": "ordinary", "service_name": "عادية"},\n                {"service_id": "option", "service_name": "خيار العميل"},\n                {"service_id": "permanent", "service_name": "دائمة"},\n            ],\n        },\n    }\n\n\ndef test_product_link_not_selected_by_customer_is_hidden_from_invoice() -> None:\n    rows = supplier_piece_invoice_services(\n        {\n            "services": [\n                {\n                    "service_id": "ordinary",\n                    "service_name": "عادية",\n                    "source": "product",\n                    "status": "pending",\n                    "required_quantity": 1,\n                },\n                {\n                    "service_id": "option",\n                    "service_name": "خيار العميل",\n                    "source": "option",\n                    "status": "pending",\n                    "required_quantity": 1,\n                },\n                {\n                    "service_id": "permanent",\n                    "service_name": "دائمة",\n                    "source": "supplier_receiving_permanent",\n                    "supplier_invoice_required": True,\n                    "status": "pending",\n                    "required_quantity": 1,\n                },\n            ]\n        },\n        _session(),\n        {},\n    )\n\n    assert [row["service_id"] for row in rows] == ["option", "permanent"]\n    assert rows[0]["customer_selected"] is True\n    assert rows[1]["supplier_invoice_required"] is True\n\n\ndef test_invoice_service_does_not_require_supplier_service_link() -> None:\n    session = {"supplier_snapshot": {"id": "supplier-1", "service_links": []}}\n    rows = supplier_piece_invoice_services(\n        {\n            "services": [\n                {\n                    "service_id": "option",\n                    "service_name": "خيار العميل",\n                    "source": "option",\n                    "status": "pending",\n                    "required_quantity": 1,\n                    "reference_unit_cost": 7.5,\n                }\n            ]\n        },\n        session,\n        {},\n    )\n    assert [row["service_id"] for row in rows] == ["option"]\n    assert rows[0]["reference_unit_price_halalas"] == 750\n\n\ndef test_explicit_empty_invoice_services_never_falls_back_to_product_services() -> None:\n    key = _invoice_group_key(\n        {\n            "product_id": "p1",\n            "sku": "sku",\n            "product_name": "منتج",\n            "invoice_services": [],\n            "services": [{"service_id": "ordinary", "required_quantity": 1}],\n        }\n    )\n    assert key[-1] == ()\n\n\ndef test_hidden_product_service_does_not_split_quantity_group() -> None:\n    signature = _pending_service_signature(\n        {\n            "services": [\n                {\n                    "service_id": "ordinary",\n                    "source": "product",\n                    "status": "pending",\n                    "required_quantity": 1,\n                },\n                {\n                    "service_id": "option",\n                    "source": "option",\n                    "status": "pending",\n                    "required_quantity": 2,\n                },\n            ]\n        }\n    )\n    assert signature == (("option", "2"),)\n\n\ndef test_service_selection_is_optional_for_supplier_receipt() -> None:\n    assert supplier_piece_service_blocker({}, _session()) is None\n\n\ndef test_future_piece_inherits_only_permanent_and_customer_selected_services() -> None:\n    resources = {\n        "ordinary": {\n            "id": "ordinary",\n            "name": "عادية",\n            "kind": "service",\n            "unit_cost": 10,\n        },\n        "option": {\n            "id": "option",\n            "name": "خيار العميل",\n            "kind": "service",\n            "unit_cost": 12,\n        },\n        "permanent": {\n            "id": "permanent",\n            "name": "دائمة",\n            "kind": "service",\n            "unit_cost": 15,\n        },\n    }\n    rows = inherit_required_services(\n        line={"product_options": {"إضافة": "نعم"}},\n        product_links=[\n            {"resource_id": "ordinary", "quantity": 1},\n            {\n                "resource_id": "permanent",\n                "quantity": 1,\n                "supplier_invoice_required": True,\n            },\n        ],\n        option_bindings=[\n            {\n                "mode": "resource",\n                "resource_id": "option",\n                "quantity": 1,\n                "option_id": "o1",\n                "option_name": "إضافة",\n                "value_id": "v1",\n                "value_name": "نعم",\n            }\n        ],\n        resources_by_id=resources,\n    )\n    by_id = {row["service_id"]: row for row in rows}\n    assert set(by_id) == {"option", "permanent"}\n    assert by_id["option"]["source"] == "option"\n    assert by_id["option"]["customer_selected"] is True\n    assert by_id["permanent"]["source"] == "supplier_receiving_permanent"\n    assert by_id["permanent"]["supplier_invoice_required"] is True\n\n\ndef test_hidden_legacy_product_service_cannot_keep_piece_in_progress() -> None:\n    update = supplier_service_completion_update(\n        piece={\n            "services": [\n                {\n                    "service_id": "ordinary",\n                    "service_name": "عادية",\n                    "source": "product",\n                    "status": "pending",\n                    "required_quantity": 1,\n                }\n            ]\n        },\n        invoice_line={"services": []},\n        session=_session(),\n        actor={"id": "employee-1", "name": "موظف"},\n        invoice_id="invoice-1",\n        completed_at=datetime(2026, 8, 19, tzinfo=timezone.utc),\n    )\n    assert update["$set"]["services"] == []\n    assert update["$set"]["remaining_service_count"] == 0\n    assert update["$set"]["status"] == "received"\n    assert update["$set"]["execution_status"] == "received_from_supplier"\n\n\ndef test_permanent_snapshot_is_pending_and_invoice_required() -> None:\n    row = _permanent_supplier_service_snapshot(\n        {\n            "id": "service-1",\n            "name": "تطريز",\n            "kind": "service",\n            "unit_cost": 12.5,\n        }\n    )\n    assert row["source"] == "supplier_receiving_permanent"\n    assert row["supplier_invoice_required"] is True\n    assert row["customer_selected"] is False\n    assert row["status"] == "pending"\n''',
    encoding="utf-8",
)

print("Finalized supplier invoice eligibility semantics.")
