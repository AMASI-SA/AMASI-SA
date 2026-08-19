"""Supplier receiving sessions for customer-order preparation pieces.

An authorised receiver opens a temporary supplier-scoped session and scans
physical preparation pieces. Scanning only reserves the piece in the draft;
the supplier link, completed services, accounting invoice and payable are
committed together when the employee approves the invoice. Salla and Qoyod
writes remain deliberately disabled.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Callable
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from component_edit_policy import component_cost_metadata
from fulfillment_v2_routes import _actor_context, _require_permission
from mezan_supplier_management_routes import MEZAN_SUPPLIERS_V2
from order_option_cost_snapshot_routes import resolve_base_unit_cost
from preparation_piece_barcode import parse_preparation_piece_barcode
from preparation_piece_operations import (
    PIECES,
    PIECE_EVENTS,
    PIECE_STATUS_ASSIGNED,
    PIECE_STATUS_BLOCKED,
    PIECE_STATUS_CANCELLED,
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_RECEIPT,
    PIECE_STATUS_RECEIVED,
)
from preparation_supplier_dispatch import (
    DISPATCH_STATUS_PARTIAL,
    DISPATCH_STATUS_RECEIVED,
    supplier_receiving_dispatch_blocker,
)
from product_cost_revision import bump_product_cost_revision
from product_fulfillment_rules import PRODUCT_RESOURCE_BINDINGS
from product_option_cost_routes import AUDIT, BINDINGS, RESOURCES
from product_v2_details_routes import COST_PROFILES
from product_v2_routes import PRODUCTS
from supplier_invoice_pdf import generate_supplier_invoice_pdf
from tz_utils import riyadh_now_aware

SUPPLIERS = MEZAN_SUPPLIERS_V2
SESSIONS = "mezan_supplier_receiving_sessions_v1"
RECEIVING_EVENTS = "mezan_supplier_receiving_events_v1"
SUPPLIER_INVOICES = "mezan_supplier_invoices_v2"
SUPPLIER_INVOICE_SHARE_EVIDENCE = "mezan_supplier_invoice_share_evidence_v1"
RECEIVE_PERMISSION = "inventory.preparation.receive"
EDIT_PRODUCT_PRICE_PERMISSION = "supplier_receiving.product_price.edit"
EDIT_SERVICE_PRICE_PERMISSION = "supplier_receiving.service_price.edit"
ADD_PRODUCT_SERVICE_PERMISSION = "supplier_receiving.service.add"
PERMANENT_SUPPLIER_SERVICE_SOURCE = "supplier_receiving_permanent"
MAX_SESSION_SCANS = 5000
SCAN_LOCK_SECONDS = 120
MAX_SHARE_EVIDENCE_BYTES = 5 * 1024 * 1024
ALLOWED_SHARE_EVIDENCE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}
ELIGIBLE_PIECE_STATUSES = {
    PIECE_STATUS_IN_PROGRESS,
    PIECE_STATUS_READY_FOR_RECEIPT,
}
RECEIPT_PIECE_FIELDS = (
    "status",
    "execution_status",
    "received_at",
    "received_by_id",
    "received_by_name",
    "supplier_receiving_session_id",
    "supplier_receiving_reference",
    "supplier_id",
    "supplier_name",
    "supplier_service_ids",
    "supplier_service_link_status",
    "supplier_reassigned_from_id",
    "supplier_reassigned_from_name",
    "supplier_reassigned_at",
    "supplier_reassigned_by_id",
    "supplier_reassigned_by_name",
    "supplier_reassignment_session_id",
    "supplier_receiving_scanned_barcode",
    "receipt_event_id",
    "updated_at",
    "mezan_only",
    "salla_updated",
    "qoyod_updated",
)


class SupplierReceivingSessionCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: str = Field(min_length=8, max_length=160)
    supplier_id: str = Field(min_length=1, max_length=160)
    note: str | None = Field(default=None, max_length=1000)


class SupplierPieceScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    barcode: str = Field(min_length=1, max_length=500)
    quantity: int | None = Field(default=None, ge=1, le=5000)
    confirm_supplier_reassignment: bool = False


class SupplierReceivingInvoiceServiceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: str = Field(min_length=1, max_length=160)
    unit_price_halalas: int = Field(gt=0, le=100_000_000_000)
    add_to_product: bool = False


class SupplierReceivingInvoiceLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    piece_ids: list[str] = Field(min_length=1, max_length=5000)
    product_unit_price_halalas: int = Field(ge=0, le=100_000_000_000)
    services: list[SupplierReceivingInvoiceServiceRequest] = Field(
        max_length=200,
    )


class SupplierReceivingSessionCloseRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)
    invoice_lines: list[SupplierReceivingInvoiceLineRequest] = Field(
        default_factory=list,
        max_length=5000,
    )


class SupplierReceivingSessionCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=1000)


class SupplierInvoiceShareConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _actor_name(user: dict[str, Any]) -> str:
    return _text(user.get("name") or user.get("email"))


def _share_evidence_signature_matches(content_type: str, content: bytes) -> bool:
    if content_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/webp":
        return (
            len(content) >= 12
            and content.startswith(b"RIFF")
            and content[8:12] == b"WEBP"
        )
    return False


def _supplier_invoice_filename(invoice: dict[str, Any]) -> str:
    supplier = _text(
        (invoice.get("supplier_snapshot") or {}).get("company_name")
    ) or "مورد"
    number = _text(invoice.get("invoice_number") or invoice.get("id")) or "فاتورة"
    raw = f"فاتورة-{supplier}-{number}.pdf"
    return "".join(
        "-" if character in {'/', '\\', ':', '*', '?', '"', '<', '>', '|', '\r', '\n'} else character
        for character in raw
    )


def supplier_piece_product_charge_eligible(piece: dict[str, Any]) -> bool:
    """Charge the physical product once, even when services span suppliers."""
    if piece.get("supplier_receiving_history"):
        return False
    return not any(
        _service_is_complete(row) and _text(row.get("supplier_invoice_id"))
        for row in (piece.get("services") or [])
    )


def _halalas(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _positive_quantity(value: Any) -> Decimal:
    try:
        quantity = Decimal(str(value or 1))
    except (InvalidOperation, TypeError, ValueError):
        quantity = Decimal("1")
    if not quantity.is_finite() or quantity <= 0:
        return Decimal("1")
    return quantity


def _service_is_complete(service: dict[str, Any]) -> bool:
    if _text(service.get("status")).casefold() == "completed":
        return True
    required = _positive_quantity(service.get("required_quantity"))
    try:
        completed = Decimal(str(service.get("completed_quantity") or 0))
    except (InvalidOperation, TypeError, ValueError):
        completed = Decimal("0")
    return completed.is_finite() and completed >= required


def _service_is_invoice_eligible(service: dict[str, Any]) -> bool:
    """Expose only customer-selected or explicitly permanent invoice services."""
    source = _text(service.get("source")).casefold()
    return bool(
        service.get("customer_selected") is True
        or service.get("supplier_invoice_required") is True
        or source == "option"
        or source in {
            PERMANENT_SUPPLIER_SERVICE_SOURCE,
            "supplier_receiving_addition",
        }
    )


def supplier_piece_invoice_services(
    piece: dict[str, Any],
    session: dict[str, Any],
    service_catalog: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return pending product services that the selected supplier can perform.

    Product groups are already expanded into individual services on the piece,
    so the invoice never stores a group as one opaque charge.
    """
    service_catalog = service_catalog or {}
    supplier_links = {
        _text(row.get("service_id")): row
        for row in (
            (session.get("supplier_snapshot") or {}).get("service_links") or []
        )
        if _text(row.get("service_id"))
    }
    result: list[dict[str, Any]] = []
    for raw in piece.get("services") or []:
        service_id = _text(raw.get("service_id"))
        if not service_id or service_id not in supplier_links:
            continue
        if not _service_is_invoice_eligible(raw):
            continue
        if _service_is_complete(raw):
            continue
        catalog = service_catalog.get(service_id) or {}
        supplier_link = supplier_links.get(service_id) or {}
        reference_halalas = _halalas(
            catalog.get("unit_cost")
            if catalog.get("unit_cost") not in (None, "")
            else (
                raw.get("reference_unit_cost")
                if raw.get("reference_unit_cost") not in (None, "")
                else supplier_link.get("unit_cost")
            )
        )
        result.append({
            "service_id": service_id,
            "service_name": _text(
                catalog.get("name")
                or raw.get("service_name")
                or supplier_link.get("service_name")
            ) or service_id,
            "service_code": _text(
                catalog.get("code")
                or raw.get("service_code")
                or supplier_link.get("service_code")
            ) or None,
            "unit": _text(
                catalog.get("unit")
                or raw.get("unit")
                or supplier_link.get("unit")
            ) or "job",
            "required_quantity": float(_positive_quantity(
                raw.get("required_quantity")
            )),
            "reference_unit_price_halalas": reference_halalas,
            "reference_price_complete": reference_halalas is not None,
            "linked_to_product": True,
            "eligibility_source": _text(raw.get("source")) or "product",
            "eligibility_condition": dict(raw.get("condition") or {}) or None,
            "customer_selected": bool(
                raw.get("customer_selected") is True
                or _text(raw.get("source")).casefold() == "option"
            ),
            "supplier_invoice_required": bool(
                raw.get("supplier_invoice_required") is True
            ),
            "add_to_product": False,
        })
    return result


def supplier_piece_reference_price(piece: dict[str, Any]) -> dict[str, Any]:
    """Calculate one piece's suggested supplier price from its service plan."""
    total_halalas = 0
    missing_service_ids: list[str] = []
    services = list(piece.get("services") or [])
    for service in services:
        unit_cost_halalas = _halalas(service.get("reference_unit_cost"))
        service_id = _text(service.get("service_id"))
        try:
            quantity = Decimal(str(service.get("required_quantity") or 1))
        except (InvalidOperation, TypeError, ValueError):
            quantity = Decimal("1")
        if not quantity.is_finite() or quantity <= 0:
            quantity = Decimal("1")
        if unit_cost_halalas is None:
            missing_service_ids.append(service_id)
            continue
        total_halalas += int(
            (Decimal(unit_cost_halalas) * quantity).quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )
    return {
        "reference_unit_price_halalas": total_halalas,
        "reference_price_complete": bool(services) and not missing_service_ids,
        "missing_price_service_ids": missing_service_ids,
    }


def _invoice_group_key(scan: dict[str, Any]) -> tuple[Any, ...]:
    invoice_services = scan.get("invoice_services")
    scan_services = (
        invoice_services
        if isinstance(invoice_services, list)
        else scan.get("services")
    ) or []
    services = tuple(sorted(
        (
            _text(service.get("service_id")),
            str(service.get("required_quantity") or 1),
        )
        for service in scan_services
        if _text(service.get("service_id"))
    ))
    return (
        _text(scan.get("product_id")),
        _text(scan.get("sku")),
        _text(scan.get("product_name")).casefold(),
        bool(scan.get("product_charge_eligible", True)),
        services,
    )


def build_supplier_receiving_invoice(
    *,
    session: dict[str, Any],
    scans: list[dict[str, Any]],
    requested_lines: list[SupplierReceivingInvoiceLineRequest],
    saved_at: datetime,
    permissions: set[str] | None = None,
    service_catalog: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Validate the scanned set and build the one Mezan 2 supplier invoice."""
    permissions = permissions or set()
    service_catalog = service_catalog or {}
    scans_by_piece = {
        _text(scan.get("piece_id")): scan
        for scan in scans
        if _text(scan.get("piece_id"))
    }
    requested_piece_ids: list[str] = []
    public_lines: list[dict[str, Any]] = []
    price_changes: list[dict[str, Any]] = []
    added_product_services: list[dict[str, Any]] = []
    supplier_service_ids = {
        _text(row.get("service_id"))
        for row in (
            (session.get("supplier_snapshot") or {}).get("service_links") or []
        )
        if _text(row.get("service_id"))
    }
    for line_number, line in enumerate(requested_lines, start=1):
        piece_ids = [_text(piece_id) for piece_id in line.piece_ids if _text(piece_id)]
        if len(piece_ids) != len(line.piece_ids) or len(set(piece_ids)) != len(piece_ids):
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_invoice_duplicate_piece"},
            )
        line_scans = [scans_by_piece.get(piece_id) for piece_id in piece_ids]
        if any(scan is None for scan in line_scans):
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_invoice_piece_mismatch"},
            )
        group_keys = {_invoice_group_key(scan or {}) for scan in line_scans}
        if len(group_keys) != 1:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_invoice_group_mismatch"},
            )
        first = line_scans[0] or {}
        quantity = len(piece_ids)
        product_charge_eligible = bool(
            first.get("product_charge_eligible", True)
        )
        reference_product_halalas = int(
            first.get("reference_product_unit_price_halalas") or 0
        )
        requested_product_halalas = int(line.product_unit_price_halalas)
        if not product_charge_eligible and requested_product_halalas != 0:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "supplier_receiving_product_already_charged",
                    "line_number": line_number,
                },
            )
        if (
            requested_product_halalas != reference_product_halalas
            and EDIT_PRODUCT_PRICE_PERMISSION not in permissions
        ):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "supplier_receiving_price_permission_required",
                    "permission": EDIT_PRODUCT_PRICE_PERMISSION,
                    "line_number": line_number,
                },
            )
        if requested_product_halalas != reference_product_halalas:
            price_changes.append({
                "change_type": "product_price",
                "line_number": line_number,
                "product_id": _text(first.get("product_id")) or None,
                "product_name": _text(first.get("product_name")) or "منتج",
                "variant_id": _text(first.get("variant_id")) or None,
                "sku": _text(first.get("sku")) or None,
                "before_halalas": reference_product_halalas,
                "after_halalas": requested_product_halalas,
            })

        eligible_maps: list[dict[str, dict[str, Any]]] = []
        for scan in line_scans:
            rows = list((scan or {}).get("invoice_services") or [])
            if not rows:
                rows = supplier_piece_invoice_services(
                    scan or {},
                    session,
                    service_catalog,
                )
            eligible_maps.append({
                _text(row.get("service_id")): row
                for row in rows
                if _text(row.get("service_id"))
            })
        common_service_ids = (
            set.intersection(*(set(row) for row in eligible_maps))
            if eligible_maps
            else set()
        )
        requested_service_ids = [_text(row.service_id) for row in line.services]
        if (
            any(not service_id for service_id in requested_service_ids)
            or len(requested_service_ids) != len(set(requested_service_ids))
        ):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "supplier_receiving_invoice_duplicate_service",
                    "line_number": line_number,
                },
            )

        service_lines: list[dict[str, Any]] = []
        services_total_halalas = 0
        for requested_service in line.services:
            service_id = _text(requested_service.service_id)
            existing = service_id in common_service_ids
            is_addition = not existing
            if is_addition:
                if not requested_service.add_to_product:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_service_not_on_product",
                            "service_id": service_id,
                            "line_number": line_number,
                        },
                    )
                if ADD_PRODUCT_SERVICE_PERMISSION not in permissions:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "supplier_receiving_service_add_permission_required",
                            "permission": ADD_PRODUCT_SERVICE_PERMISSION,
                            "service_id": service_id,
                        },
                    )
                if service_id not in supplier_service_ids or service_id not in service_catalog:
                    raise HTTPException(
                        status_code=422,
                        detail={
                            "code": "supplier_receiving_service_not_available",
                            "service_id": service_id,
                        },
                    )
                reference_row = service_catalog[service_id]
                required_quantity = Decimal("1")
                added_product_services.append({
                    "line_number": line_number,
                    "product_id": _text(first.get("product_id")),
                    "product_name": _text(first.get("product_name")) or "منتج",
                    "service_id": service_id,
                    "service_name": _text(reference_row.get("name")) or service_id,
                    "quantity": 1.0,
                })
            else:
                reference_row = eligible_maps[0][service_id]
                required_quantity = _positive_quantity(
                    reference_row.get("required_quantity")
                )

            reference_service_halalas = reference_row.get(
                "reference_unit_price_halalas"
            )
            if reference_service_halalas is None:
                reference_service_halalas = _halalas(reference_row.get("unit_cost"))
            reference_service_halalas = int(reference_service_halalas or 0)
            requested_service_halalas = int(requested_service.unit_price_halalas)
            if (
                requested_service_halalas != reference_service_halalas
                and EDIT_SERVICE_PRICE_PERMISSION not in permissions
            ):
                raise HTTPException(
                    status_code=403,
                    detail={
                        "code": "supplier_receiving_price_permission_required",
                        "permission": EDIT_SERVICE_PRICE_PERMISSION,
                        "service_id": service_id,
                        "line_number": line_number,
                    },
                )
            if requested_service_halalas != reference_service_halalas:
                price_changes.append({
                    "change_type": "service_price",
                    "line_number": line_number,
                    "product_id": _text(first.get("product_id")) or None,
                    "service_id": service_id,
                    "service_name": _text(
                        reference_row.get("service_name")
                        or reference_row.get("name")
                    ) or service_id,
                    "before_halalas": reference_service_halalas,
                    "after_halalas": requested_service_halalas,
                })
            service_total = int(
                (
                    Decimal(requested_service_halalas)
                    * required_quantity
                    * Decimal(quantity)
                ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
            )
            services_total_halalas += service_total
            service_lines.append({
                "service_id": service_id,
                "service_name": _text(
                    reference_row.get("service_name")
                    or reference_row.get("name")
                ) or service_id,
                "service_code": _text(
                    reference_row.get("service_code")
                    or reference_row.get("code")
                ) or None,
                "unit": _text(reference_row.get("unit")) or "job",
                "quantity_per_piece": float(required_quantity),
                "total_quantity": float(required_quantity * Decimal(quantity)),
                "reference_unit_price_halalas": reference_service_halalas,
                "unit_price_halalas": requested_service_halalas,
                "total_halalas": service_total,
                "added_to_product": is_addition,
            })

        product_total_halalas = quantity * requested_product_halalas
        total_halalas = product_total_halalas + services_total_halalas
        public_lines.append({
            "line_number": line_number,
            "product_id": _text(first.get("product_id")) or None,
            "product_name": _text(first.get("product_name")) or "منتج",
            "sku": _text(first.get("sku")) or None,
            "variant_id": _text(first.get("variant_id")) or None,
            "product_charge_eligible": product_charge_eligible,
            "quantity": quantity,
            "reference_product_unit_price_halalas": reference_product_halalas,
            "product_unit_price_halalas": requested_product_halalas,
            "product_total_halalas": product_total_halalas,
            "services_total_halalas": services_total_halalas,
            "total_halalas": total_halalas,
            "piece_ids": piece_ids,
            "services": service_lines,
        })
        requested_piece_ids.extend(piece_ids)

    expected_piece_ids = set(scans_by_piece)
    if (
        len(requested_piece_ids) != len(set(requested_piece_ids))
        or set(requested_piece_ids) != expected_piece_ids
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "supplier_receiving_invoice_piece_mismatch"},
        )

    subtotal_halalas = sum(line["total_halalas"] for line in public_lines)
    if subtotal_halalas <= 0:
        raise HTTPException(
            status_code=422,
            detail={"code": "supplier_receiving_invoice_total_required"},
        )
    unique_additions: list[dict[str, Any]] = []
    seen_additions: set[tuple[str, str]] = set()
    for addition in added_product_services:
        key = (
            _text(addition.get("product_id")),
            _text(addition.get("service_id")),
        )
        if key in seen_additions:
            continue
        seen_additions.add(key)
        unique_additions.append(addition)
    return {
        "reference": _text(session.get("reference")),
        "status": "draft",
        "currency": "SAR",
        "line_count": len(public_lines),
        "piece_count": len(expected_piece_ids),
        "subtotal_halalas": subtotal_halalas,
        "total_halalas": subtotal_halalas,
        "lines": public_lines,
        "saved_at": saved_at,
        "price_changes": price_changes,
        "added_product_services": unique_additions,
        "financial_invoice_created": False,
        "liability_created": False,
        "mezan_only": True,
    }


def _session_reference(now: datetime, session_id: str) -> str:
    local_date = now.astimezone(riyadh_now_aware().tzinfo).strftime("%Y%m%d")
    return f"SR-{local_date}-{session_id[-6:].upper()}"


def _public_piece(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        key: value
        for key, value in row.items()
        if key not in {"_id", "user_id", "image_b64"}
    }


def _public_session(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    return {
        "id": _text(row.get("id")),
        "reference": _text(row.get("reference")),
        "status": _text(row.get("status")),
        "supplier": dict(row.get("supplier_snapshot") or {}),
        "supplier_context_only": False,
        "supplier_operational_linked": True,
        "supplier_service_link_status": _text(row.get("supplier_service_link_status"))
        or "catalog_linked",
        "opened_by": _text(row.get("opened_by")),
        "opened_by_name": _text(row.get("opened_by_name")),
        "opened_at": row.get("opened_at"),
        "closed_by": _text(row.get("closed_by")) or None,
        "closed_by_name": _text(row.get("closed_by_name")) or None,
        "closed_at": row.get("closed_at"),
        "note": _text(row.get("note")) or None,
        "close_note": _text(row.get("close_note")) or None,
        "cancelled_by": _text(row.get("cancelled_by")) or None,
        "cancelled_by_name": _text(row.get("cancelled_by_name")) or None,
        "cancelled_at": row.get("cancelled_at"),
        "cancel_note": _text(row.get("cancel_note")) or None,
        "cancelled_piece_count": int(row.get("cancelled_piece_count") or 0),
        "scan_count": int(row.get("scan_count") or 0),
        "order_numbers": list(row.get("order_numbers") or []),
        "file_numbers": list(row.get("file_numbers") or []),
        "preparation_employee_ids": list(row.get("preparation_employee_ids") or []),
        "last_scanned_at": row.get("last_scanned_at"),
        "operational_invoice": dict(row.get("operational_invoice") or {}) or None,
        "supplier_invoice": dict(row.get("supplier_invoice") or {}) or None,
        "financial_invoice_created": bool(row.get("financial_invoice_created")),
        "liability_created": bool(row.get("liability_created")),
        "experiment_mode": bool(row.get("experiment_mode")),
        "experiment_run_id": _text(row.get("experiment_run_id")) or None,
        "experiment_generation": int(row.get("experiment_generation") or 0) or None,
        "financial_writes_allowed": row.get("financial_writes_allowed") is not False,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }


def _public_supplier_invoice(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    public = {
        key: value
        for key, value in row.items()
        if key not in {"_id", "user_id", "ledger_entry_ids"}
    }
    evidence_id = _text(public.get("share_evidence_id"))
    public["share_evidence_url"] = (
        f"/api/supplier-receiving-v1/invoices/{_text(row.get('id'))}/share-evidence"
        if evidence_id
        else None
    )
    return public


async def _supplier_invoice_for_actor(
    db: Any,
    *,
    context: dict[str, Any],
    invoice_id: str,
    projection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "user_id": context["merchant_id"],
        "id": _text(invoice_id),
    }
    if not context["is_owner"]:
        query["supplier_approved_by"] = context["actor_id"]
    row = await db[SUPPLIER_INVOICES].find_one(
        query,
        projection or {"_id": 0},
    )
    if not row:
        raise HTTPException(
            status_code=404,
            detail={"code": "supplier_invoice_not_found"},
        )
    return row


def piece_scan_blocker(piece: dict[str, Any]) -> dict[str, Any] | None:
    """Return an explicit fail-closed reason for a non-receivable piece."""
    status = _text(piece.get("status")) or PIECE_STATUS_ASSIGNED
    if status == PIECE_STATUS_RECEIVED or piece.get("received_at"):
        return {
            "code": "supplier_piece_already_received",
            "message": "تم استلام هذه القطعة سابقًا؛ لم تُسجّل مرة ثانية.",
            "received_at": piece.get("received_at"),
            "received_by_name": piece.get("received_by_name"),
            "session_reference": piece.get("supplier_receiving_reference"),
        }
    if status in ELIGIBLE_PIECE_STATUSES:
        return None
    if status == PIECE_STATUS_BLOCKED:
        return {
            "code": "supplier_piece_blocked",
            "message": _text(piece.get("block_reason"))
            or "القطعة متوقفة ولا يمكن استلامها حتى معالجة سبب التوقف.",
            "reason": _text(piece.get("block_reason")) or None,
        }
    if status == PIECE_STATUS_CANCELLED:
        return {
            "code": "supplier_piece_cancelled",
            "message": _text(piece.get("cancellation_reason"))
            or "القطعة ملغاة ولا يمكن استلامها.",
            "reason": _text(piece.get("cancellation_reason")) or None,
        }
    if status == PIECE_STATUS_ASSIGNED:
        return {
            "code": "supplier_piece_not_started",
            "message": "ابدأ ملف التجهيز أولًا قبل استلام القطعة من المورد.",
        }
    return {
        "code": "supplier_piece_status_not_receivable",
        "message": "حالة القطعة الحالية لا تسمح بالاستلام.",
        "status": status,
    }


def supplier_invoice_experiment_run_id(
    scans: list[dict[str, Any]],
) -> str | None:
    """Return one experiment run id, rejecting mixed real/test invoices."""
    run_ids = {
        _text(row.get("experiment_run_id"))
        for row in scans
        if _text(row.get("experiment_run_id"))
    }
    experiment_markers = {
        bool(row.get("experiment_mode") or _text(row.get("experiment_run_id")))
        for row in scans
    }
    if len(run_ids) > 1 or len(experiment_markers) > 1:
        raise HTTPException(
            status_code=409,
            detail={"code": "supplier_receiving_experiment_mode_mismatch"},
        )
    if experiment_markers == {True} and not run_ids:
        raise HTTPException(
            status_code=409,
            detail={"code": "supplier_receiving_experiment_run_missing"},
        )
    return next(iter(run_ids), None)


def supplier_piece_service_blocker(
    piece: dict[str, Any],
    session: dict[str, Any],
    *,
    allow_service_addition: bool = False,
) -> dict[str, Any] | None:
    """Supplier services are optional; product receipt can be invoiced alone.

    Service eligibility is enforced only for a service the employee explicitly
    selects. A product-linked service that the customer did not choose must not
    block scanning or force itself into the supplier invoice.
    """
    del piece, session, allow_service_addition
    return None


def supplier_receipt_piece_patch(
    *,
    session: dict[str, Any],
    actor: dict[str, Any],
    piece_id: str,
    barcode: str,
    received_at: datetime,
) -> dict[str, Any]:
    """Reserve a piece in the draft without recording supplier completion."""
    return {
        "execution_status": "supplier_receiving_draft",
        "supplier_receiving_session_id": _text(session.get("id")),
        "supplier_receiving_reference": _text(session.get("reference")),
        "supplier_receiving_scanned_barcode": _text(barcode),
        "receipt_event_id": uuid.uuid5(
            uuid.NAMESPACE_URL,
            (
                f"supplier-receiving:{session.get('user_id')}:"
                f"{session.get('id')}:{_text(piece_id)}"
            ),
        ).hex,
        "updated_at": received_at,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }


def supplier_receipt_previous_piece_state(piece: dict[str, Any]) -> dict[str, Any]:
    """Capture only fields changed by a supplier scan so cancel can restore them."""
    present_fields = [field for field in RECEIPT_PIECE_FIELDS if field in piece]
    return {
        "previous_piece_state": {
            field: piece.get(field)
            for field in present_fields
        },
        "previous_piece_present_fields": present_fields,
    }


def supplier_receipt_piece_rollback_update(
    scan_event: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Build an exact inverse of the fields changed by one supplier scan."""
    state = scan_event.get("previous_piece_state")
    present_fields = scan_event.get("previous_piece_present_fields")
    if not isinstance(state, dict) or not isinstance(present_fields, list):
        raise ValueError("supplier_receiving_cancel_rollback_unavailable")
    allowed = set(RECEIPT_PIECE_FIELDS)
    present = {
        _text(field)
        for field in present_fields
        if _text(field) in allowed
    }
    update: dict[str, dict[str, Any]] = {
        "$set": {
            field: state.get(field)
            for field in present
        },
        "$unset": {
            field: ""
            for field in RECEIPT_PIECE_FIELDS
            if field not in present
        },
    }
    if not update["$set"]:
        update.pop("$set")
    if not update["$unset"]:
        update.pop("$unset")
    return update


async def _supplier_service_catalog(
    db: Any,
    *,
    user_id: str,
    session: dict[str, Any],
    mongo_session: Any = None,
) -> dict[str, dict[str, Any]]:
    service_ids = sorted({
        _text(row.get("service_id"))
        for row in (
            (session.get("supplier_snapshot") or {}).get("service_links") or []
        )
        if _text(row.get("service_id"))
    })
    if not service_ids:
        return {}
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    rows = await db[RESOURCES].find(
        {
            "user_id": user_id,
            "id": {"$in": service_ids},
            "kind": "service",
            "track_inventory": {"$ne": True},
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
    ).to_list(max(1, len(service_ids)))
    return {
        _text(row.get("id")): dict(row)
        for row in rows
        if _text(row.get("id"))
    }


async def _supplier_product_reference_price(
    db: Any,
    *,
    user_id: str,
    piece: dict[str, Any],
) -> dict[str, Any]:
    product_id = _text(piece.get("product_id"))
    product = await db[PRODUCTS].find_one(
        {
            "user_id": user_id,
            "$or": [
                {"id": product_id},
                {"mezan_product_id": product_id},
                {"salla_product_id": product_id},
            ],
        },
        {
            "_id": 0,
            "id": 1,
            "mezan_product_id": 1,
            "salla_product_id": 1,
            "cost_price_from_salla": 1,
            "variants": 1,
        },
    )
    if not product:
        return {
            "reference_product_unit_price_halalas": 0,
            "reference_product_price_complete": False,
            "reference_product_price_source": "missing",
        }
    salla_id = _text(product.get("salla_product_id")) or _text(
        product.get("mezan_product_id") or product.get("id")
    )
    profile = await db[COST_PROFILES].find_one(
        {"user_id": user_id, "salla_product_id": salla_id},
        {"_id": 0},
    ) or {}
    amount, source = resolve_base_unit_cost(
        {
            "variant_id": piece.get("variant_id") or piece.get("salla_variant_id"),
            "sku": piece.get("sku"),
        },
        profile,
        product,
    )
    amount_halalas = _halalas(amount)
    return {
        "reference_product_unit_price_halalas": int(amount_halalas or 0),
        "reference_product_price_complete": amount_halalas is not None,
        "reference_product_price_source": source,
    }


def supplier_service_completion_update(
    *,
    piece: dict[str, Any],
    invoice_line: dict[str, Any],
    session: dict[str, Any],
    actor: dict[str, Any],
    invoice_id: str,
    completed_at: datetime,
) -> dict[str, dict[str, Any]]:
    """Complete only the invoiced services and release partial work safely."""
    supplier = dict(session.get("supplier_snapshot") or {})
    selected = {
        _text(row.get("service_id")): row
        for row in invoice_line.get("services") or []
        if _text(row.get("service_id"))
    }
    services: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in piece.get("services") or []:
        row = dict(raw)
        # A generic product link is only an available service. If the customer
        # did not select it and it was not made permanent from supplier
        # receiving, it must neither remain pending nor block receipt.
        if not _service_is_invoice_eligible(row) and not _service_is_complete(row):
            continue
        service_id = _text(row.get("service_id"))
        selected_row = selected.get(service_id)
        if selected_row:
            required = _positive_quantity(row.get("required_quantity"))
            row.update({
                "status": "completed",
                "completed_quantity": float(required),
                "completed_at": completed_at,
                "completed_by_supplier_id": _text(supplier.get("id")),
                "completed_by_supplier_name": _text(supplier.get("company_name")),
                "supplier_invoice_id": invoice_id,
                "supplier_unit_price_halalas": int(
                    selected_row.get("unit_price_halalas") or 0
                ),
            })
            seen.add(service_id)
        services.append(row)
    for service_id, selected_row in selected.items():
        if service_id in seen:
            continue
        services.append({
            "service_id": service_id,
            "service_name": _text(selected_row.get("service_name")) or service_id,
            "service_code": _text(selected_row.get("service_code")) or None,
            "unit": _text(selected_row.get("unit")) or "job",
            "required_quantity": float(
                _positive_quantity(selected_row.get("quantity_per_piece"))
            ),
            "reference_unit_cost": (
                int(selected_row.get("reference_unit_price_halalas") or 0) / 100
            ),
            "source": "supplier_receiving_addition",
            "status": "completed",
            "completed_quantity": float(
                _positive_quantity(selected_row.get("quantity_per_piece"))
            ),
            "completed_at": completed_at,
            "completed_by_supplier_id": _text(supplier.get("id")),
            "completed_by_supplier_name": _text(supplier.get("company_name")),
            "supplier_invoice_id": invoice_id,
            "supplier_unit_price_halalas": int(
                selected_row.get("unit_price_halalas") or 0
            ),
        })
    remaining = sum(1 for row in services if not _service_is_complete(row))
    history_row = {
        "invoice_id": invoice_id,
        "session_id": _text(session.get("id")),
        "session_reference": _text(session.get("reference")),
        "supplier_id": _text(supplier.get("id")),
        "supplier_name": _text(supplier.get("company_name")),
        "service_ids": sorted(selected),
        "services": [dict(row) for row in selected.values()],
        "received_by_id": _text(actor.get("id")),
        "received_by_name": _actor_name(actor),
        "received_at": completed_at,
    }
    set_values: dict[str, Any] = {
        "services": services,
        "remaining_service_count": remaining,
        "service_plan_status": "completed" if remaining == 0 else "in_progress",
        "supplier_id": _text(supplier.get("id")),
        "supplier_name": _text(supplier.get("company_name")),
        "supplier_service_ids": sorted(selected),
        "supplier_service_link_status": "service_recorded",
        "received_by_id": _text(actor.get("id")),
        "received_by_name": _actor_name(actor),
        "updated_at": completed_at,
        "mezan_only": True,
        "salla_updated": False,
        "qoyod_updated": False,
    }
    update: dict[str, dict[str, Any]] = {
        "$set": set_values,
        "$push": {"supplier_receiving_history": history_row},
        "$unset": {
            "supplier_receiving_session_id": "",
            "supplier_receiving_reference": "",
            "supplier_receiving_scanned_barcode": "",
            "receipt_event_id": "",
        },
    }
    if remaining == 0:
        set_values.update({
            "status": PIECE_STATUS_RECEIVED,
            "execution_status": "received_from_supplier",
            "supplier_dispatch_status": DISPATCH_STATUS_RECEIVED,
            "received_at": completed_at,
        })
    else:
        set_values.update({
            "status": PIECE_STATUS_IN_PROGRESS,
            "execution_status": "awaiting_remaining_services",
            "supplier_dispatch_status": DISPATCH_STATUS_PARTIAL,
        })
        update["$unset"]["received_at"] = ""
    return update


async def _post_supplier_invoice_ledger(
    db: Any,
    *,
    user_id: str,
    actor: dict[str, Any],
    invoice: dict[str, Any],
    mongo_session: Any,
) -> dict[str, Any]:
    """Post the balanced payable legs inside the caller's Mongo transaction."""
    amount = round(int(invoice["total_halalas"]) / 100, 2)
    if amount <= 0:
        raise HTTPException(
            status_code=422,
            detail={"code": "supplier_receiving_invoice_total_required"},
        )
    user_max = await db.general_ledger.aggregate(
        [
            {"$match": {"user_id": user_id}},
            {"$group": {"_id": None, "mx": {"$max": "$entry_no"}}},
        ],
        session=mongo_session,
    ).to_list(1)
    first_entry_no = (
        int(user_max[0].get("mx") or 0) + 1 if user_max else 1
    )
    invoice_id = _text(invoice.get("id"))
    txn_group_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{invoice_id}:ledger"))
    now = completed_iso = invoice["approved_at"].isoformat()
    common = {
        "user_id": user_id,
        "txn_group_id": txn_group_id,
        "entry_type": "supplier_invoice",
        "amount": amount,
        "currency": "SAR",
        "status": "posted",
        "reverses_entry_id": None,
        "reversed_by_entry_id": None,
        "reason_code": None,
        "notes": f"فاتورة مورد ميزان 2 — {invoice.get('invoice_number')}",
        "posted_at": now,
        "posted_by": _text(actor.get("id")),
        "created_at": now,
        "updated_at": now,
        "metadata": {
            "txn_type": "supplier_invoice",
            "source": "supplier_receiving_v2",
            "supplier_invoice_v2_id": invoice_id,
            "supplier_receiving_session_id": invoice.get("session_id"),
            "supplier_id": invoice.get("supplier_id"),
            "mezan_supplier_v2": True,
            "qoyod_updated": False,
        },
    }
    entries = [
        {
            **common,
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{invoice_id}:expense")),
            "entry_no": first_entry_no,
            "entity_type": "expense",
            "entity_id": "inventory",
            "sub_account": None,
            "side": "debit",
        },
        {
            **common,
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{invoice_id}:payable")),
            "entry_no": first_entry_no + 1,
            "entity_type": "supplier",
            "entity_id": invoice.get("supplier_id"),
            "sub_account": "payable",
            "side": "credit",
        },
    ]
    await db.general_ledger.insert_many(entries, session=mongo_session)
    audits = []
    for entry in entries:
        audits.append({
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{entry['id']}:audit")),
            "user_id": user_id,
            "actor_id": _text(actor.get("id")),
            "actor_name": _actor_name(actor),
            "timestamp": completed_iso,
            "entity_type": entry["entity_type"],
            "entity_id": entry["entity_id"],
            "action": "create_supplier_invoice",
            "reason_code": None,
            "notes": common["notes"],
            "before_state": None,
            "after_state": {
                "entry_no": entry["entry_no"],
                "amount": amount,
                "side": entry["side"],
                "status": "posted",
                "sub_account": entry["sub_account"],
                "txn_group_id": txn_group_id,
                "supplier_invoice_v2_id": invoice_id,
            },
            "ledger_entry_id": entry["id"],
        })
    await db.accounting_audit_log.insert_many(
        audits,
        session=mongo_session,
    )
    return {
        "txn_group_id": txn_group_id,
        "entry_ids": [row["id"] for row in entries],
        "amount": amount,
    }


async def apply_supplier_invoice_price_changes(
    db: Any,
    *,
    user_id: str,
    actor: dict[str, Any],
    invoice_id: str,
    changes: list[dict[str, Any]],
    changed_at: datetime,
    mongo_session: Any,
) -> list[dict[str, Any]]:
    """Apply authorised invoice prices to Mezan defaults with impact evidence."""
    if not changes:
        return []
    service_targets: dict[str, int] = {}
    product_targets: dict[tuple[str, str], int] = {}
    for change in changes:
        change_type = _text(change.get("change_type"))
        target = int(change.get("after_halalas") or 0)
        if change_type == "service_price":
            service_id = _text(change.get("service_id"))
            previous = service_targets.get(service_id)
            if previous is not None and previous != target:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "supplier_receiving_conflicting_service_prices",
                        "service_id": service_id,
                    },
                )
            service_targets[service_id] = target
        elif change_type == "product_price":
            product_key = (
                _text(change.get("product_id")),
                _text(change.get("variant_id")),
            )
            previous = product_targets.get(product_key)
            if previous is not None and previous != target:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "supplier_receiving_conflicting_product_prices",
                        "product_id": product_key[0],
                        "variant_id": product_key[1] or None,
                    },
                )
            product_targets[product_key] = target

    enriched: list[dict[str, Any]] = []
    product_cache: dict[tuple[str, str], dict[str, Any]] = {}
    service_cache: dict[str, dict[str, Any]] = {}
    for index, raw_change in enumerate(changes):
        change = dict(raw_change)
        change_type = _text(change.get("change_type"))
        if change_type == "product_price":
            product_id = _text(change.get("product_id"))
            variant_id = _text(change.get("variant_id"))
            cache_key = (product_id, variant_id)
            applied = product_cache.get(cache_key)
            if applied is None:
                product = await db[PRODUCTS].find_one(
                    {
                        "user_id": user_id,
                        "$or": [
                            {"id": product_id},
                            {"mezan_product_id": product_id},
                            {"salla_product_id": product_id},
                        ],
                    },
                    {"_id": 0},
                    session=mongo_session,
                )
                if not product:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_price_product_not_found",
                            "product_id": product_id,
                        },
                    )
                salla_product_id = _text(product.get("salla_product_id")) or _text(
                    product.get("mezan_product_id") or product.get("id")
                )
                profile = await db[COST_PROFILES].find_one(
                    {"user_id": user_id, "salla_product_id": salla_product_id},
                    {"_id": 0},
                    session=mongo_session,
                ) or {}
                target_amount = int(change.get("after_halalas") or 0) / 100
                patch: dict[str, Any] = {
                    "user_id": user_id,
                    "salla_product_id": salla_product_id,
                    "updated_at": changed_at,
                    "updated_by": _text(actor.get("id")),
                    "last_supplier_invoice_id": invoice_id,
                }
                if variant_id:
                    variant_costs = dict(profile.get("variant_costs") or {})
                    variant_costs[variant_id] = target_amount
                    patch["variant_costs"] = variant_costs
                else:
                    patch["base_cost"] = target_amount
                await db[COST_PROFILES].update_one(
                    {"user_id": user_id, "salla_product_id": salla_product_id},
                    {
                        "$set": patch,
                        "$setOnInsert": {
                            "id": uuid.uuid4().hex,
                            "created_at": changed_at,
                        },
                    },
                    upsert=True,
                    session=mongo_session,
                )
                impacted_piece_count = await db[PIECES].count_documents(
                    {
                        "user_id": user_id,
                        "product_id": product_id,
                        "status": {"$ne": PIECE_STATUS_CANCELLED},
                    },
                    session=mongo_session,
                )
                applied = {
                    "salla_product_id": salla_product_id,
                    "impacted_product_count": 1,
                    "impacted_piece_count": int(impacted_piece_count or 0),
                    "applied_to": "variant_cost" if variant_id else "base_cost",
                }
                product_cache[cache_key] = applied
            change.update(applied)
        elif change_type == "service_price":
            service_id = _text(change.get("service_id"))
            applied = service_cache.get(service_id)
            if applied is None:
                resource = await db[RESOURCES].find_one(
                    {
                        "user_id": user_id,
                        "id": service_id,
                        "kind": "service",
                        "track_inventory": {"$ne": True},
                    },
                    {"_id": 0},
                    session=mongo_session,
                )
                if not resource:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_price_service_not_found",
                            "service_id": service_id,
                        },
                    )
                amount = int(change.get("after_halalas") or 0) / 100
                metadata = component_cost_metadata(
                    track_inventory=False,
                    amount=amount,
                )
                await db[RESOURCES].update_one(
                    {"user_id": user_id, "id": service_id},
                    {"$set": {
                        **metadata,
                        "updated_at": changed_at,
                        "updated_by": _text(actor.get("id")),
                        "last_supplier_invoice_id": invoice_id,
                    }},
                    session=mongo_session,
                )
                option_rows = await db[BINDINGS].find(
                    {
                        "user_id": user_id,
                        "resource_id": service_id,
                        "mode": "resource",
                    },
                    {"_id": 0, "salla_product_id": 1},
                    session=mongo_session,
                ).to_list(20000)
                product_rows = await db[PRODUCT_RESOURCE_BINDINGS].find(
                    {"user_id": user_id, "resource_id": service_id},
                    {"_id": 0, "salla_product_id": 1},
                    session=mongo_session,
                ).to_list(20000)
                impacted_products = {
                    _text(row.get("salla_product_id"))
                    for row in [*option_rows, *product_rows]
                    if _text(row.get("salla_product_id"))
                }
                applied = {
                    "impacted_product_count": len(impacted_products),
                    "impacted_option_binding_count": len(option_rows),
                    "impacted_product_binding_count": len(product_rows),
                    "applied_to": "shared_service_cost",
                }
                service_cache[service_id] = applied
            change.update(applied)
        change.update({
            "applied": True,
            "applied_at": changed_at,
            "changed_by": _text(actor.get("id")),
            "changed_by_name": _actor_name(actor),
        })
        enriched.append(change)
        await db[AUDIT].insert_one(
            {
                "id": str(uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"{invoice_id}:supplier-price:{index}",
                )),
                "user_id": user_id,
                "event_type": "supplier_invoice_price_applied",
                "supplier_invoice_id": invoice_id,
                "change": change,
                "actor_id": _text(actor.get("id")),
                "actor_name": _actor_name(actor),
                "created_at": changed_at,
            },
            session=mongo_session,
        )
    return enriched



async def _supplier_receiving_product(
    db: Any,
    *,
    user_id: str,
    product_id: str,
    mongo_session: Any = None,
) -> dict[str, Any]:
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    normalized = _text(product_id)
    product = await db[PRODUCTS].find_one(
        {
            "user_id": user_id,
            "$or": [
                {"id": normalized},
                {"mezan_product_id": normalized},
                {"salla_product_id": normalized},
            ],
        },
        {
            "_id": 0,
            "id": 1,
            "mezan_product_id": 1,
            "salla_product_id": 1,
            "name": 1,
            "sku": 1,
        },
        **kwargs,
    )
    if not product:
        raise HTTPException(
            status_code=404,
            detail={"code": "supplier_receiving_product_not_found"},
        )
    return product


def _supplier_receiving_product_identifiers(
    product: dict[str, Any],
    requested_id: str = "",
) -> list[str]:
    return sorted({
        value
        for value in (
            _text(requested_id),
            _text(product.get("id")),
            _text(product.get("mezan_product_id")),
            _text(product.get("salla_product_id")),
        )
        if value
    })


def _permanent_supplier_service_snapshot(
    resource: dict[str, Any],
    *,
    actor: dict[str, Any] | None = None,
    added_at: datetime | None = None,
) -> dict[str, Any]:
    timestamp = added_at or _now()
    return {
        "service_id": _text(resource.get("id")),
        "service_name": _text(resource.get("name"))
        or _text(resource.get("id")),
        "service_code": _text(resource.get("code")) or None,
        "required_quantity": 1.0,
        "unit": _text(resource.get("unit")) or "job",
        "reference_unit_cost": resource.get("unit_cost"),
        "source": PERMANENT_SUPPLIER_SERVICE_SOURCE,
        "condition": None,
        "customer_selected": False,
        "supplier_invoice_required": True,
        "supplier_invoice_added_at": timestamp,
        "supplier_invoice_added_by": _text((actor or {}).get("id")) or None,
        "status": "pending",
        "completed_quantity": 0.0,
    }


def _permanent_supplier_invoice_service_row(
    resource: dict[str, Any],
) -> dict[str, Any]:
    reference_halalas = _halalas(resource.get("unit_cost"))
    return {
        "service_id": _text(resource.get("id")),
        "service_name": _text(resource.get("name"))
        or _text(resource.get("id")),
        "service_code": _text(resource.get("code")) or None,
        "unit": _text(resource.get("unit")) or "job",
        "required_quantity": 1.0,
        "reference_unit_price_halalas": reference_halalas,
        "reference_price_complete": reference_halalas is not None,
        "linked_to_product": True,
        "eligibility_source": PERMANENT_SUPPLIER_SERVICE_SOURCE,
        "eligibility_condition": None,
        "customer_selected": False,
        "supplier_invoice_required": True,
        "add_to_product": False,
    }


async def _supplier_invoice_service_candidate_context(
    db: Any,
    *,
    user_id: str,
    session: dict[str, Any],
    product_id: str,
    mongo_session: Any = None,
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    set[str],
    set[str],
]:
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    product = await _supplier_receiving_product(
        db,
        user_id=user_id,
        product_id=product_id,
        mongo_session=mongo_session,
    )
    salla_product_id = _text(product.get("salla_product_id")) or _text(
        product.get("mezan_product_id") or product.get("id")
    )
    product_links = await db[PRODUCT_RESOURCE_BINDINGS].find(
        {"user_id": user_id, "salla_product_id": salla_product_id},
        {"_id": 0, "resource_id": 1},
        **kwargs,
    ).to_list(5000)
    option_links = await db[BINDINGS].find(
        {
            "user_id": user_id,
            "salla_product_id": salla_product_id,
            "mode": "resource",
            "resource_id": {"$nin": [None, ""]},
        },
        {"_id": 0, "resource_id": 1},
        **kwargs,
    ).to_list(5000)
    product_link_ids = {
        _text(row.get("resource_id"))
        for row in product_links
        if _text(row.get("resource_id"))
    }
    option_link_ids = {
        _text(row.get("resource_id"))
        for row in option_links
        if _text(row.get("resource_id"))
    }
    blocked = product_link_ids | option_link_ids
    catalog = await _supplier_service_catalog(
        db,
        user_id=user_id,
        session=session,
        mongo_session=mongo_session,
    )
    candidates = [
        dict(row)
        for candidate_service_id, row in catalog.items()
        if candidate_service_id not in blocked
    ]
    candidates.sort(key=lambda row: (
        _text(row.get("name")).casefold(),
        _text(row.get("id")),
    ))
    return product, candidates, product_link_ids, option_link_ids


async def _apply_permanent_supplier_invoice_service(
    db: Any,
    *,
    context: dict[str, Any],
    actor: dict[str, Any],
    session: dict[str, Any],
    product_id: str,
    service_id: str,
    mongo_session: Any,
) -> dict[str, Any]:
    merchant_id = context["merchant_id"]
    product, candidates, product_link_ids, option_link_ids = (
        await _supplier_invoice_service_candidate_context(
            db,
            user_id=merchant_id,
            session=session,
            product_id=product_id,
            mongo_session=mongo_session,
        )
    )
    normalized_service_id = _text(service_id)
    if normalized_service_id in product_link_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "supplier_receiving_service_already_linked_to_product",
                "service_id": normalized_service_id,
            },
        )
    if normalized_service_id in option_link_ids:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "supplier_receiving_service_already_linked_to_option",
                "service_id": normalized_service_id,
            },
        )
    resource = next(
        (
            row for row in candidates
            if _text(row.get("id")) == normalized_service_id
        ),
        None,
    )
    if not resource:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "supplier_receiving_service_not_available",
                "service_id": normalized_service_id,
            },
        )

    now = _now()
    salla_product_id = _text(product.get("salla_product_id")) or _text(
        product.get("mezan_product_id") or product.get("id")
    )
    selector = {
        "user_id": merchant_id,
        "salla_product_id": salla_product_id,
        "resource_id": normalized_service_id,
    }
    await db[PRODUCT_RESOURCE_BINDINGS].update_one(
        selector,
        {
            "$set": {
                **selector,
                "mezan_product_id": (
                    product.get("mezan_product_id") or product.get("id")
                ),
                "product_name": product.get("name"),
                "resource_name": resource.get("name"),
                "quantity": 1.0,
                "supplier_invoice_required": True,
                "supplier_invoice_source": "supplier_receiving",
                "supplier_invoice_added_at": now,
                "supplier_invoice_added_by": context["actor_id"],
                "supplier_invoice_added_by_name": _actor_name(actor),
                "updated_at": now,
            },
            "$setOnInsert": {
                "id": uuid.uuid4().hex,
                "created_at": now,
            },
        },
        upsert=True,
        session=mongo_session,
    )

    identifiers = _supplier_receiving_product_identifiers(product, product_id)
    snapshot = _permanent_supplier_service_snapshot(
        resource,
        actor=actor,
        added_at=now,
    )
    uninvoiced_piece_query = {
        "user_id": merchant_id,
        "product_id": {"$in": identifiers},
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
                    "$not": {
                        "$elemMatch": {"service_id": normalized_service_id}
                    }
                }
            },
        ],
    }
    piece_result = await db[PIECES].update_many(
        uninvoiced_piece_query,
        {
            "$push": {"services": snapshot},
            "$inc": {
                "service_count": 1,
                "remaining_service_count": 1,
            },
            "$set": {
                "service_plan_status": "pending",
                "service_plan_updated_at": now,
                "updated_at": now,
            },
        },
        session=mongo_session,
    )

    invoice_row = _permanent_supplier_invoice_service_row(resource)
    event_query = {
        "user_id": merchant_id,
        "session_id": _text(session.get("id")),
        "event_type": "supplier_piece_scanned",
        "product_id": {"$in": identifiers},
    }
    event_service_updates = 0
    event_invoice_updates = 0
    for collection_name in (RECEIVING_EVENTS, PIECE_EVENTS):
        service_result = await db[collection_name].update_many(
            {
                **event_query,
                "services": {
                    "$not": {
                        "$elemMatch": {"service_id": normalized_service_id}
                    }
                },
            },
            {
                "$push": {"services": snapshot},
                "$inc": {"remaining_service_count": 1},
                "$set": {"updated_at": now},
            },
            session=mongo_session,
        )
        invoice_result = await db[collection_name].update_many(
            {
                **event_query,
                "invoice_services": {
                    "$not": {
                        "$elemMatch": {"service_id": normalized_service_id}
                    }
                },
            },
            {
                "$push": {"invoice_services": invoice_row},
                "$set": {"updated_at": now},
            },
            session=mongo_session,
        )
        if collection_name == RECEIVING_EVENTS:
            event_service_updates = int(service_result.modified_count or 0)
            event_invoice_updates = int(invoice_result.modified_count or 0)

    audit = {
        "id": uuid.uuid4().hex,
        "user_id": merchant_id,
        "event_type": "supplier_receiving_permanent_service_added",
        "session_id": _text(session.get("id")),
        "session_reference": _text(session.get("reference")),
        "product_id": _text(product.get("mezan_product_id") or product.get("id")),
        "salla_product_id": salla_product_id,
        "product_name": _text(product.get("name")),
        "service_id": normalized_service_id,
        "service_name": _text(resource.get("name")),
        "impacted_uninvoiced_piece_count": int(
            piece_result.modified_count or 0
        ),
        "impacted_active_scan_count": event_invoice_updates,
        "historical_invoices_changed": False,
        "actor_id": context["actor_id"],
        "actor_name": _actor_name(actor),
        "created_at": now,
    }
    await db[AUDIT].insert_one(audit, session=mongo_session)
    return {
        "product": product,
        "service": resource,
        "service_snapshot": snapshot,
        "invoice_service": invoice_row,
        "impacted_uninvoiced_piece_count": int(
            piece_result.modified_count or 0
        ),
        "impacted_active_scan_count": max(
            event_service_updates,
            event_invoice_updates,
        ),
        "historical_invoices_changed": False,
    }


async def ensure_supplier_receiving_indexes(db: Any) -> None:
    await db[SESSIONS].create_index(
        [("user_id", ASCENDING), ("client_request_id", ASCENDING)],
        unique=True,
        name="uq_supplier_receiving_request_v1",
    )
    await db[SESSIONS].create_index(
        [("user_id", ASCENDING), ("opened_by", ASCENDING), ("status", ASCENDING)],
        unique=True,
        partialFilterExpression={"status": "open"},
        name="uq_supplier_receiving_open_actor_v1",
    )
    await db[SESSIONS].create_index(
        [("user_id", ASCENDING), ("opened_at", DESCENDING)],
        name="ix_supplier_receiving_history_v1",
    )
    await db[RECEIVING_EVENTS].create_index(
        [
            ("user_id", ASCENDING),
            ("session_id", ASCENDING),
            ("occurred_at", DESCENDING),
        ],
        name="ix_supplier_receiving_session_events_v1",
    )
    await db[RECEIVING_EVENTS].create_index(
        [("user_id", ASCENDING), ("piece_id", ASCENDING), ("event_type", ASCENDING)],
        unique=True,
        partialFilterExpression={"event_type": "supplier_piece_scanned"},
        name="uq_supplier_receiving_piece_scan_v1",
    )
    await db[SUPPLIER_INVOICES].create_index(
        [("user_id", ASCENDING), ("session_id", ASCENDING)],
        unique=True,
        name="uq_supplier_invoice_receiving_session_v2",
    )
    await db[SUPPLIER_INVOICES].create_index(
        [("user_id", ASCENDING), ("invoice_number", ASCENDING)],
        unique=True,
        name="uq_supplier_invoice_number_v2",
    )
    await db[SUPPLIER_INVOICES].create_index(
        [("user_id", ASCENDING), ("supplier_id", ASCENDING), ("approved_at", DESCENDING)],
        name="ix_supplier_invoice_supplier_v2",
    )
    await db[SUPPLIER_INVOICES].create_index(
        [("user_id", ASCENDING), ("supplier_approved_by", ASCENDING), ("approved_at", DESCENDING)],
        name="ix_supplier_invoice_employee_v2",
    )
    await db[SUPPLIER_INVOICES].create_index(
        [("user_id", ASCENDING), ("share_status", ASCENDING), ("approved_at", DESCENDING)],
        name="ix_supplier_invoice_share_status_v2",
    )
    await db[SUPPLIER_INVOICE_SHARE_EVIDENCE].create_index(
        [("user_id", ASCENDING), ("invoice_id", ASCENDING)],
        unique=True,
        name="uq_supplier_invoice_share_evidence_v1",
    )


async def _session_for_actor(
    db: Any,
    *,
    context: dict[str, Any],
    session_id: str,
) -> dict[str, Any]:
    session = await db[SESSIONS].find_one(
        {"user_id": context["merchant_id"], "id": session_id},
        {"_id": 0},
    )
    if not session:
        raise HTTPException(
            status_code=404,
            detail={"code": "supplier_receiving_session_not_found"},
        )
    if (
        not context["is_owner"]
        and _text(session.get("opened_by")) != context["actor_id"]
    ):
        raise HTTPException(
            status_code=403,
            detail={"code": "supplier_receiving_session_owner_required"},
        )
    return session


async def resolve_scanned_piece(
    db: Any,
    *,
    user_id: str,
    barcode: str,
) -> dict[str, Any]:
    """Resolve a unique Mezan piece QR, with a safe legacy order fallback."""
    raw = _text(barcode)
    piece_id = parse_preparation_piece_barcode(raw)
    if piece_id:
        piece = await db[PIECES].find_one(
            {
                "user_id": user_id,
                "piece_id": piece_id,
                "$or": [
                    {"experiment_archived_at": {"$exists": False}},
                    {"experiment_archived_at": None},
                ],
            },
            {"_id": 0},
        )
        if not piece:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_piece_barcode_not_found"},
            )
        return piece

    if not raw.isdigit():
        raise HTTPException(
            status_code=422,
            detail={"code": "supplier_piece_barcode_invalid"},
        )

    rows = (
        await db[PIECES]
        .find(
            {
                "user_id": user_id,
                "order_number": raw,
                "$or": [
                    {"experiment_archived_at": {"$exists": False}},
                    {"experiment_archived_at": None},
                ],
            },
            {"_id": 0},
        )
        .sort([("file_number", 1), ("order_item_id", 1), ("unit_index", 1)])
        .limit(25)
        .to_list(25)
    )
    if not rows:
        raise HTTPException(
            status_code=404,
            detail={"code": "supplier_piece_barcode_not_found"},
        )
    if len(rows) == 1:
        return rows[0]
    eligible = [row for row in rows if piece_scan_blocker(row) is None]
    raise HTTPException(
        status_code=409,
        detail={
            "code": "legacy_order_barcode_ambiguous",
            "message": (
                "باركود رقم الطلب القديم يطابق أكثر من قطعة. "
                "أعد تنزيل ملف التجهيز لطباعته بباركود القطعة الفريد."
            ),
            "order_number": raw,
            "candidate_count": len(rows),
            "eligible_candidate_count": len(eligible),
        },
    )


def _pending_service_signature(piece: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(
        (
            _text(row.get("service_id")),
            str(_positive_quantity(row.get("required_quantity"))),
        )
        for row in (piece.get("services") or [])
        if _text(row.get("service_id"))
        and _service_is_invoice_eligible(row)
        and not _service_is_complete(row)
    ))


async def supplier_scan_group_candidates(
    db: Any,
    *,
    user_id: str,
    scanned_piece: dict[str, Any],
    session: dict[str, Any],
    allow_service_addition: bool,
    allow_supplier_reassignment: bool = False,
) -> list[dict[str, Any]]:
    """Return interchangeable unreserved pieces for one quantity choice.

    Grouping is deliberately limited to the same materialised preparation
    group. Older rows without a group key remain single-piece scans.
    """
    group_key = _text(scanned_piece.get("group_key"))
    batch_id = _text(scanned_piece.get("batch_id"))
    order_item_id = _text(scanned_piece.get("order_item_id"))
    if not group_key or not batch_id or not order_item_id:
        return [scanned_piece]
    rows = (
        await db[PIECES]
        .find(
            {
                "user_id": user_id,
                "group_key": group_key,
                "batch_id": batch_id,
                "order_item_id": order_item_id,
                "status": {"$in": sorted(ELIGIBLE_PIECE_STATUSES)},
                "$and": [{
                    "$or": [
                        {"experiment_archived_at": {"$exists": False}},
                        {"experiment_archived_at": None},
                    ],
                }],
                "$or": [
                    {"supplier_receiving_session_id": {"$exists": False}},
                    {"supplier_receiving_session_id": None},
                    {"supplier_receiving_session_id": ""},
                ],
            },
            {"_id": 0},
        )
        .sort([("unit_index", 1), ("piece_id", 1)])
        .limit(MAX_SESSION_SCANS)
        .to_list(MAX_SESSION_SCANS)
    )
    supplier_id = (
        (session.get("supplier_snapshot") or {}).get("id")
        or session.get("supplier_id")
    )
    eligible_rows = []
    for row in rows:
        if piece_scan_blocker(row):
            continue
        dispatch_blocker = supplier_receiving_dispatch_blocker(row, supplier_id)
        if dispatch_blocker and not (
            allow_supplier_reassignment
            and _text(dispatch_blocker.get("code"))
            == "supplier_piece_dispatched_to_different_supplier"
        ):
            continue
        if supplier_piece_service_blocker(
            row,
            session,
            allow_service_addition=allow_service_addition,
        ):
            continue
        eligible_rows.append(row)

    scanned_id = _text(scanned_piece.get("piece_id"))
    eligible_rows.sort(key=lambda row: (
        0 if _text(row.get("piece_id")) == scanned_id else 1,
        int(row.get("unit_index") or 0),
        _text(row.get("piece_id")),
    ))
    if not eligible_rows:
        # Preserve the precise blocker from the scanned anchor (already
        # received, cancelled, wrong supplier, and so on).
        return [scanned_piece]

    seed = eligible_rows[0]
    expected_signature = _pending_service_signature(seed)
    product_charge_eligible = supplier_piece_product_charge_eligible(seed)
    candidates = [
        row for row in eligible_rows
        if _pending_service_signature(row) == expected_signature
        and supplier_piece_product_charge_eligible(row) == product_charge_eligible
    ]
    candidates.sort(key=lambda row: (
        0 if _text(row.get("piece_id")) == _text(seed.get("piece_id")) else 1,
        int(row.get("unit_index") or 0),
        _text(row.get("piece_id")),
    ))
    return candidates


async def _recent_session_events(
    db: Any,
    *,
    user_id: str,
    session_id: str,
    limit: int = 100,
    mongo_session: Any = None,
) -> list[dict[str, Any]]:
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    rows = (
        await db[RECEIVING_EVENTS]
        .find(
            {
                "user_id": user_id,
                "session_id": session_id,
                "event_type": "supplier_piece_scanned",
            },
            {
                "_id": 0,
                "user_id": 0,
                "previous_piece_state": 0,
                "previous_piece_present_fields": 0,
            },
            **kwargs,
        )
        .sort("occurred_at", -1)
        .limit(limit)
        .to_list(limit)
    )
    session = await db[SESSIONS].find_one(
        {"user_id": user_id, "id": session_id},
        {"_id": 0},
        **kwargs,
    )
    if session:
        service_catalog = await _supplier_service_catalog(
            db,
            user_id=user_id,
            session=session,
            mongo_session=mongo_session,
        )
        for row in rows:
            # Always rebuild this derived field. Older open sessions may still
            # contain product-level services that the customer did not choose.
            row["invoice_services"] = supplier_piece_invoice_services(
                row,
                session,
                service_catalog,
            )
    return rows


async def _cancellable_session_events(
    db: Any,
    *,
    user_id: str,
    session_id: str,
) -> list[dict[str, Any]]:
    """Return internal scan snapshots, including partially rolled-back retries."""
    return (
        await db[RECEIVING_EVENTS]
        .find(
            {
                "user_id": user_id,
                "session_id": session_id,
                "event_type": {
                    "$in": [
                        "supplier_piece_scanned",
                        "supplier_piece_scan_cancelled",
                    ]
                },
            },
            {"_id": 0},
        )
        .sort("occurred_at", -1)
        .limit(MAX_SESSION_SCANS)
        .to_list(MAX_SESSION_SCANS)
    )


def make_supplier_receiving_router(
    db: Any,
    current_user: Callable[..., Any],
) -> APIRouter:
    router = APIRouter(
        prefix="/supplier-receiving-v1",
        tags=["Supplier Receiving V1"],
    )

    @router.get("/catalog")
    async def catalog(
        limit: int = Query(default=50, ge=1, le=200),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        await ensure_supplier_receiving_indexes(db)
        merchant_id = context["merchant_id"]
        session_query: dict[str, Any] = {"user_id": merchant_id}
        if not context["is_owner"]:
            session_query["opened_by"] = context["actor_id"]
        sessions = (
            await db[SESSIONS]
            .find(
                session_query,
                {"_id": 0},
            )
            .sort("opened_at", -1)
            .limit(limit)
            .to_list(limit)
        )
        active = next(
            (
                row
                for row in sessions
                if _text(row.get("status")) in {"open", "cancelling"}
                and _text(row.get("opened_by")) == context["actor_id"]
            ),
            None,
        )
        suppliers = (
            await db[SUPPLIERS]
            .find(
                {
                    "user_id": merchant_id,
                    "status": {"$ne": "inactive"},
                },
                {
                    "_id": 0,
                    "id": 1,
                    "company_name": 1,
                    "contact_person": 1,
                    "phone": 1,
                    "email": 1,
                    "status": 1,
                    "service_ids": 1,
                    "service_links": 1,
                },
            )
            .sort("company_name", 1)
            .to_list(2000)
        )
        supplier_service_ids = sorted({
            _text(link.get("service_id"))
            for supplier in suppliers
            for link in (supplier.get("service_links") or [])
            if _text(link.get("service_id"))
        })
        service_rows = (
            await db[RESOURCES].find(
                {
                    "user_id": merchant_id,
                    "id": {"$in": supplier_service_ids},
                    "kind": "service",
                    "track_inventory": {"$ne": True},
                },
                {
                    "_id": 0,
                    "id": 1,
                    "name": 1,
                    "code": 1,
                    "unit": 1,
                    "unit_cost": 1,
                },
            ).sort("name", 1).to_list(max(1, len(supplier_service_ids)))
            if supplier_service_ids
            else []
        )
        eligible_count = await db[PIECES].count_documents(
            {
                "user_id": merchant_id,
                "status": {"$in": sorted(ELIGIBLE_PIECE_STATUSES)},
                "$and": [
                    {"$or": [
                        {"experiment_archived_at": {"$exists": False}},
                        {"experiment_archived_at": None},
                    ]},
                    {"$or": [
                        {"supplier_receiving_session_id": {"$exists": False}},
                        {"supplier_receiving_session_id": None},
                        {"supplier_receiving_session_id": ""},
                    ]},
                ],
            }
        )
        return {
            "ok": True,
            "suppliers": suppliers,
            "service_catalog": service_rows,
            "active_session": _public_session(active),
            "active_session_scans": (
                await _recent_session_events(
                    db,
                    user_id=merchant_id,
                    session_id=_text(active.get("id")),
                    limit=MAX_SESSION_SCANS,
                )
                if active
                else []
            ),
            "sessions": [_public_session(row) for row in sessions],
            "eligible_piece_count": eligible_count,
            "permissions": {
                "can_open": True,
                "can_scan": True,
                "can_close": True,
                "can_cancel": True,
                "can_edit_product_price": (
                    EDIT_PRODUCT_PRICE_PERMISSION in context["permissions"]
                ),
                "can_edit_service_price": (
                    EDIT_SERVICE_PRICE_PERMISSION in context["permissions"]
                ),
                "can_add_service": (
                    ADD_PRODUCT_SERVICE_PERMISSION in context["permissions"]
                ),
            },
            "barcode_mode": "unique_piece_qr",
            "legacy_order_barcode_requires_unique_piece": True,
            "financial_invoice_created_automatically": True,
            "liability_created_automatically": True,
            "supplier_source": "mezan_suppliers_v2",
            "legacy_supplier_data_used": False,
            "mezan_only": True,
            "qoyod_write_enabled": False,
        }


    @router.get(
        "/sessions/{session_id}/products/{product_id}/service-candidates"
    )
    async def supplier_invoice_service_candidates(
        session_id: str,
        product_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        _require_permission(context, ADD_PRODUCT_SERVICE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        if _text(session.get("status")) != "open":
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_closed"},
            )
        product, candidates, _product_links, _option_links = (
            await _supplier_invoice_service_candidate_context(
                db,
                user_id=context["merchant_id"],
                session=session,
                product_id=product_id,
            )
        )
        return {
            "ok": True,
            "product": {
                "id": _text(product.get("mezan_product_id") or product.get("id")),
                "salla_product_id": _text(product.get("salla_product_id")),
                "name": _text(product.get("name")) or "منتج",
                "sku": _text(product.get("sku")),
            },
            "services": [
                {
                    "id": _text(row.get("id")),
                    "name": _text(row.get("name")) or _text(row.get("id")),
                    "code": _text(row.get("code")),
                    "unit": _text(row.get("unit")) or "job",
                    "unit_cost": row.get("unit_cost"),
                    "unit_price_halalas": int(
                        _halalas(row.get("unit_cost")) or 0
                    ),
                }
                for row in candidates
            ],
            "existing_product_services_hidden": True,
            "existing_option_services_hidden": True,
            "historical_invoices_immutable": True,
        }

    @router.post(
        "/sessions/{session_id}/products/{product_id}/services/{service_id}",
        status_code=201,
    )
    async def add_permanent_supplier_invoice_service(
        session_id: str,
        product_id: str,
        service_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        _require_permission(context, ADD_PRODUCT_SERVICE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        if _text(session.get("status")) != "open":
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_closed"},
            )
        mongo_client = getattr(db, "client", None)
        if mongo_client is None or not hasattr(mongo_client, "start_session"):
            raise HTTPException(
                status_code=503,
                detail={"code": "supplier_receiving_atomic_transaction_required"},
            )
        async with await mongo_client.start_session() as mongo_session:
            async with mongo_session.start_transaction():
                fresh_session = await db[SESSIONS].find_one(
                    {
                        "user_id": context["merchant_id"],
                        "id": session_id,
                        "status": "open",
                        "opened_by": context["actor_id"],
                    },
                    {"_id": 0},
                    session=mongo_session,
                )
                if not fresh_session:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "supplier_receiving_session_closed"},
                    )
                result = await _apply_permanent_supplier_invoice_service(
                    db,
                    context=context,
                    actor=user,
                    session=fresh_session,
                    product_id=product_id,
                    service_id=service_id,
                    mongo_session=mongo_session,
                )
        return {
            "ok": True,
            "product": {
                "id": _text(
                    result["product"].get("mezan_product_id")
                    or result["product"].get("id")
                ),
                "salla_product_id": _text(
                    result["product"].get("salla_product_id")
                ),
                "name": _text(result["product"].get("name")) or "منتج",
                "sku": _text(result["product"].get("sku")),
            },
            "service": {
                **result["invoice_service"],
                "unit_cost": result["service"].get("unit_cost"),
            },
            "impacted_uninvoiced_piece_count": (
                result["impacted_uninvoiced_piece_count"]
            ),
            "impacted_active_scan_count": (
                result["impacted_active_scan_count"]
            ),
            "historical_invoices_changed": False,
            "permanent_for_future_orders": True,
        }

    @router.get("/invoices/{invoice_id}")
    async def get_supplier_invoice(
        invoice_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        invoice = await _supplier_invoice_for_actor(
            db,
            context=context,
            invoice_id=invoice_id,
        )
        return {"ok": True, "supplier_invoice": _public_supplier_invoice(invoice)}

    @router.get("/invoices/{invoice_id}/pdf")
    async def download_supplier_invoice_pdf(
        invoice_id: str,
        user: dict = Depends(current_user),
    ) -> Response:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        invoice = await _supplier_invoice_for_actor(
            db,
            context=context,
            invoice_id=invoice_id,
        )
        content = generate_supplier_invoice_pdf(invoice)
        filename = _supplier_invoice_filename(invoice)
        fallback = f"supplier-invoice-{_text(invoice.get('invoice_number')) or invoice_id}.pdf"
        return Response(
            content=content,
            media_type="application/pdf",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{fallback}"; '
                    f"filename*=UTF-8''{quote(filename)}"
                )
            },
        )

    @router.post("/invoices/{invoice_id}/share-evidence")
    async def upload_supplier_invoice_share_evidence(
        invoice_id: str,
        file: UploadFile = File(...),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        invoice = await _supplier_invoice_for_actor(
            db,
            context=context,
            invoice_id=invoice_id,
        )
        content_type = _text(file.content_type).casefold()
        if content_type not in ALLOWED_SHARE_EVIDENCE_TYPES:
            raise HTTPException(
                status_code=422,
                detail={"code": "supplier_invoice_share_evidence_image_required"},
            )
        try:
            content = await file.read(MAX_SHARE_EVIDENCE_BYTES + 1)
        finally:
            await file.close()
        if not content or len(content) > MAX_SHARE_EVIDENCE_BYTES:
            raise HTTPException(
                status_code=422,
                detail={"code": "supplier_invoice_share_evidence_size_invalid"},
            )
        if not _share_evidence_signature_matches(content_type, content):
            raise HTTPException(
                status_code=422,
                detail={"code": "supplier_invoice_share_evidence_image_invalid"},
            )
        now = _now()
        evidence_id = str(uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"supplier-invoice-share:{context['merchant_id']}:{invoice_id}",
        ))
        await db[SUPPLIER_INVOICE_SHARE_EVIDENCE].update_one(
            {"user_id": context["merchant_id"], "invoice_id": invoice_id},
            {
                "$set": {
                    "id": evidence_id,
                    "content": content,
                    "content_type": content_type,
                    "filename": _text(file.filename) or "supplier-share-evidence",
                    "size": len(content),
                    "uploaded_by": context["actor_id"],
                    "uploaded_by_name": _actor_name(user),
                    "uploaded_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
        updated = await db[SUPPLIER_INVOICES].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": invoice_id,
                "supplier_approved_by": invoice.get("supplier_approved_by"),
            },
            {"$set": {
                "share_status": "evidence_uploaded",
                "share_evidence_id": evidence_id,
                "share_evidence_uploaded_at": now,
                "share_evidence_uploaded_by": context["actor_id"],
                "share_confirmed": False,
                "updated_at": now,
            }},
            return_document=ReturnDocument.AFTER,
        )
        await db[SESSIONS].update_one(
            {"user_id": context["merchant_id"], "id": invoice.get("session_id")},
            {"$set": {
                "supplier_invoice.share_status": "evidence_uploaded",
                "supplier_invoice.share_confirmed": False,
                "supplier_invoice.share_evidence_uploaded_at": now,
                "updated_at": now,
            }},
        )
        await db[RECEIVING_EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "session_id": invoice.get("session_id"),
            "event_type": "supplier_invoice_share_evidence_uploaded",
            "supplier_invoice_id": invoice_id,
            "actor_id": context["actor_id"],
            "actor_name": _actor_name(user),
            "occurred_at": now,
            "mezan_only": True,
        })
        return {
            "ok": True,
            "supplier_invoice": _public_supplier_invoice(updated),
            "share_confirmation_required": True,
        }

    @router.get("/invoices/{invoice_id}/share-evidence")
    async def get_supplier_invoice_share_evidence(
        invoice_id: str,
        user: dict = Depends(current_user),
    ) -> Response:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        await _supplier_invoice_for_actor(
            db,
            context=context,
            invoice_id=invoice_id,
            projection={"_id": 0, "id": 1, "supplier_approved_by": 1},
        )
        evidence = await db[SUPPLIER_INVOICE_SHARE_EVIDENCE].find_one(
            {"user_id": context["merchant_id"], "invoice_id": invoice_id},
            {"_id": 0},
        )
        if not evidence:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_invoice_share_evidence_not_found"},
            )
        return Response(
            content=evidence.get("content") or b"",
            media_type=_text(evidence.get("content_type")) or "image/jpeg",
            headers={"Cache-Control": "private, no-store"},
        )

    @router.post("/invoices/{invoice_id}/confirm-share")
    async def confirm_supplier_invoice_share(
        invoice_id: str,
        payload: SupplierInvoiceShareConfirmRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        invoice = await _supplier_invoice_for_actor(
            db,
            context=context,
            invoice_id=invoice_id,
        )
        if _text(invoice.get("share_status")) == "confirmed":
            return {"ok": True, "supplier_invoice": _public_supplier_invoice(invoice)}
        evidence = await db[SUPPLIER_INVOICE_SHARE_EVIDENCE].find_one(
            {"user_id": context["merchant_id"], "invoice_id": invoice_id},
            {"_id": 0, "id": 1},
        )
        if not evidence:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_invoice_share_evidence_required"},
            )
        now = _now()
        updated = await db[SUPPLIER_INVOICES].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": invoice_id,
                "share_status": {"$ne": "confirmed"},
            },
            {"$set": {
                "share_status": "confirmed",
                "share_confirmed": True,
                "shared_at": now,
                "shared_by": context["actor_id"],
                "shared_by_name": _actor_name(user),
                "share_note": _text(payload.note) or None,
                "updated_at": now,
            }},
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            updated = await _supplier_invoice_for_actor(
                db,
                context=context,
                invoice_id=invoice_id,
            )
        await db[SESSIONS].update_one(
            {"user_id": context["merchant_id"], "id": invoice.get("session_id")},
            {"$set": {
                "supplier_invoice.share_status": "confirmed",
                "supplier_invoice.share_confirmed": True,
                "supplier_invoice.shared_at": now,
                "supplier_invoice.shared_by_name": _actor_name(user),
                "updated_at": now,
            }},
        )
        await db[RECEIVING_EVENTS].insert_one({
            "id": uuid.uuid4().hex,
            "user_id": context["merchant_id"],
            "session_id": invoice.get("session_id"),
            "event_type": "supplier_invoice_share_confirmed",
            "supplier_invoice_id": invoice_id,
            "supplier_id": invoice.get("supplier_id"),
            "actor_id": context["actor_id"],
            "actor_name": _actor_name(user),
            "occurred_at": now,
            "note": _text(payload.note) or None,
            "mezan_only": True,
        })
        return {"ok": True, "supplier_invoice": _public_supplier_invoice(updated)}

    @router.post("/sessions", status_code=201)
    async def open_session(
        payload: SupplierReceivingSessionCreateRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        await ensure_supplier_receiving_indexes(db)
        merchant_id = context["merchant_id"]
        existing_request = await db[SESSIONS].find_one(
            {
                "user_id": merchant_id,
                "client_request_id": _text(payload.client_request_id),
            },
            {"_id": 0},
        )
        if existing_request:
            if (
                _text(existing_request.get("supplier_id")) != _text(payload.supplier_id)
                or _text(existing_request.get("opened_by")) != context["actor_id"]
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_request_conflict"},
                )
            return {"ok": True, "session": _public_session(existing_request)}

        open_session_row = await db[SESSIONS].find_one(
            {
                "user_id": merchant_id,
                "opened_by": context["actor_id"],
                "status": {"$in": ["open", "cancelling"]},
            },
            {"_id": 0},
        )
        if open_session_row:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "supplier_receiving_open_session_exists",
                    "session": _public_session(open_session_row),
                },
            )
        supplier = await db[SUPPLIERS].find_one(
            {
                "user_id": merchant_id,
                "id": _text(payload.supplier_id),
                "status": {"$ne": "inactive"},
            },
            {
                "_id": 0,
                "id": 1,
                "company_name": 1,
                "contact_person": 1,
                "phone": 1,
                "email": 1,
                "status": 1,
                "service_ids": 1,
                "service_links": 1,
            },
        )
        if not supplier:
            raise HTTPException(
                status_code=404,
                detail={"code": "supplier_receiving_supplier_not_found"},
            )
        now = _now()
        session_id = f"supplier-receiving-{uuid.uuid4().hex}"
        row = {
            "id": session_id,
            "reference": _session_reference(now, session_id),
            "user_id": merchant_id,
            "client_request_id": _text(payload.client_request_id),
            "status": "open",
            "supplier_id": _text(supplier.get("id")),
            "supplier_snapshot": supplier,
            "supplier_context_only": False,
            "supplier_operational_linked": True,
            "supplier_service_link_status": (
                "catalog_linked"
                if list(supplier.get("service_links") or [])
                else "not_required"
            ),
            "opened_by": context["actor_id"],
            "opened_by_name": _actor_name(user),
            "opened_at": now,
            "note": _text(payload.note) or None,
            "scan_count": 0,
            "order_numbers": [],
            "file_numbers": [],
            "preparation_employee_ids": [],
            "created_at": now,
            "updated_at": now,
            "financial_invoice_created": False,
            "liability_created": False,
            "mezan_only": True,
            "salla_updated": False,
            "qoyod_updated": False,
        }
        try:
            await db[SESSIONS].insert_one(dict(row))
        except DuplicateKeyError as exc:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_open_session_exists"},
            ) from exc
        await db[RECEIVING_EVENTS].insert_one(
            {
                "id": uuid.uuid4().hex,
                "user_id": merchant_id,
                "session_id": session_id,
                "event_type": "supplier_receiving_session_opened",
                "supplier_context": supplier,
                "actor_id": context["actor_id"],
                "actor_name": _actor_name(user),
                "occurred_at": now,
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            }
        )
        return {"ok": True, "session": _public_session(row)}

    @router.get("/sessions/{session_id}")
    async def get_session(
        session_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        return {
            "ok": True,
            "session": _public_session(session),
            "scans": await _recent_session_events(
                db,
                user_id=context["merchant_id"],
                session_id=session_id,
                limit=MAX_SESSION_SCANS,
            ),
        }

    @router.post("/sessions/{session_id}/scan")
    async def scan_piece(
        session_id: str,
        payload: SupplierPieceScanRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        if _text(session.get("status")) != "open":
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_closed"},
            )
        if int(session.get("scan_count") or 0) >= MAX_SESSION_SCANS:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_scan_limit"},
            )
        lock_started_at = _now()
        lock_token = uuid.uuid4().hex
        session = await db[SESSIONS].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": session_id,
                "status": "open",
                "opened_by": context["actor_id"],
                "$or": [
                    {"scan_lock_token": {"$exists": False}},
                    {"scan_lock_token": None},
                    {"scan_lock_expires_at": {"$lte": lock_started_at}},
                ],
            },
            {
                "$set": {
                    "scan_lock_token": lock_token,
                    "scan_lock_started_at": lock_started_at,
                    "scan_lock_expires_at": lock_started_at
                    + timedelta(seconds=SCAN_LOCK_SECONDS),
                    "updated_at": lock_started_at,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if not session:
            latest = await db[SESSIONS].find_one(
                {"user_id": context["merchant_id"], "id": session_id},
                {"_id": 0, "status": 1},
            )
            code = (
                "supplier_receiving_session_closed"
                if _text((latest or {}).get("status")) != "open"
                else "supplier_receiving_scan_busy"
            )
            raise HTTPException(status_code=409, detail={"code": code})
        barcode = _text(payload.barcode)
        reserved_rows: list[tuple[dict[str, Any], dict[str, Any]]] = []
        inserted_event_ids: list[str] = []
        session_incremented = 0
        experiment_session_initialized = False
        try:
            scanned_piece = await resolve_scanned_piece(
                db,
                user_id=context["merchant_id"],
                barcode=barcode,
            )
            candidates = await supplier_scan_group_candidates(
                db,
                user_id=context["merchant_id"],
                scanned_piece=scanned_piece,
                session=session,
                allow_service_addition=(
                    ADD_PRODUCT_SERVICE_PERMISSION in context["permissions"]
                ),
                allow_supplier_reassignment=payload.confirm_supplier_reassignment,
            )
            # The card QR anchors to its first physical piece. If that piece
            # was already completed, the first still-eligible piece on the
            # same exact card becomes the scan target.
            piece = candidates[0]
            piece_experiment_run_id = _text(piece.get("experiment_run_id"))
            session_experiment_run_id = _text(session.get("experiment_run_id"))
            if (
                session_experiment_run_id
                and piece_experiment_run_id != session_experiment_run_id
            ) or (
                not session_experiment_run_id
                and session.get("experiment_mode") is True
                and not piece_experiment_run_id
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_experiment_mode_mismatch"},
                )
            if not piece_experiment_run_id and session_experiment_run_id:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_experiment_mode_mismatch"},
                )
            blocker = piece_scan_blocker(piece)
            if blocker:
                raise HTTPException(status_code=409, detail=blocker)
            dispatch_blocker = supplier_receiving_dispatch_blocker(
                piece,
                (session.get("supplier_snapshot") or {}).get("id")
                or session.get("supplier_id"),
            )
            if dispatch_blocker:
                is_supplier_mismatch = (
                    _text(dispatch_blocker.get("code"))
                    == "supplier_piece_dispatched_to_different_supplier"
                )
                if not (
                    is_supplier_mismatch
                    and payload.confirm_supplier_reassignment
                ):
                    supplier = dict(session.get("supplier_snapshot") or {})
                    detail = dict(dispatch_blocker)
                    if is_supplier_mismatch:
                        detail.update({
                            "requires_supplier_reassignment_confirmation": True,
                            "new_supplier_id": _text(supplier.get("id"))
                            or _text(session.get("supplier_id")),
                            "new_supplier_name": _text(supplier.get("company_name"))
                            or "المورد الجديد",
                        })
                    raise HTTPException(status_code=409, detail=detail)
            reserved_session_id = _text(piece.get("supplier_receiving_session_id"))
            if reserved_session_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "supplier_piece_already_in_receiving_session",
                        "session_id": reserved_session_id,
                        "same_session": reserved_session_id == session_id,
                    },
                )
            service_catalog = await _supplier_service_catalog(
                db,
                user_id=context["merchant_id"],
                session=session,
            )
            service_blocker = supplier_piece_service_blocker(
                piece,
                session,
                allow_service_addition=(
                    ADD_PRODUCT_SERVICE_PERMISSION in context["permissions"]
                ),
            )
            if service_blocker:
                raise HTTPException(status_code=409, detail=service_blocker)
            if payload.quantity is None and len(candidates) > 1:
                await db[SESSIONS].update_one(
                    {
                        "user_id": context["merchant_id"],
                        "id": session_id,
                        "scan_lock_token": lock_token,
                    },
                    {
                        "$set": {"updated_at": _now()},
                        "$unset": {
                            "scan_lock_token": "",
                            "scan_lock_started_at": "",
                            "scan_lock_expires_at": "",
                        },
                    },
                )
                return {
                    "ok": True,
                    "requires_quantity_selection": True,
                    "barcode": barcode,
                    "available_quantity": len(candidates),
                    "quantity_options": list(range(1, len(candidates) + 1)),
                    "supplier_reassignment_confirmed": (
                        payload.confirm_supplier_reassignment
                    ),
                    "piece": _public_piece(piece),
                    "product": {
                        "product_id": piece.get("product_id"),
                        "product_name": piece.get("product_name") or "منتج",
                        "sku": piece.get("sku"),
                        "selected_image_url": piece.get("selected_image_url"),
                    },
                }
            selected_quantity = int(payload.quantity or 1)
            if selected_quantity > len(candidates):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "supplier_receiving_quantity_exceeds_available",
                        "available_quantity": len(candidates),
                    },
                )
            if int(session.get("scan_count") or 0) + selected_quantity > MAX_SESSION_SCANS:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_session_scan_limit"},
                )

            selected_candidates = candidates[:selected_quantity]
            selected_run_ids = {
                _text(row.get("experiment_run_id")) for row in selected_candidates
            }
            if selected_run_ids != {piece_experiment_run_id}:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_experiment_mode_mismatch"},
                )
            if piece_experiment_run_id and not session_experiment_run_id:
                experiment_session_result = await db[SESSIONS].update_one(
                    {
                        "user_id": context["merchant_id"],
                        "id": session_id,
                        "status": "open",
                        "opened_by": context["actor_id"],
                        "scan_lock_token": lock_token,
                        "scan_count": 0,
                    },
                    {"$set": {
                        "experiment_mode": True,
                        "experiment_run_id": piece_experiment_run_id,
                        "experiment_generation": int(piece.get("experiment_generation") or 1),
                        "financial_writes_allowed": False,
                        "liability_created": False,
                        "updated_at": _now(),
                    }},
                )
                if not experiment_session_result.modified_count:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "supplier_receiving_experiment_mode_mismatch"},
                    )
                experiment_session_initialized = True
                session.update({
                    "experiment_mode": True,
                    "experiment_run_id": piece_experiment_run_id,
                    "experiment_generation": int(piece.get("experiment_generation") or 1),
                    "financial_writes_allowed": False,
                })

            now = _now()
            receiving_supplier = dict(session.get("supplier_snapshot") or {})
            receiving_supplier_id = (
                _text(receiving_supplier.get("id"))
                or _text(session.get("supplier_id"))
            )
            receiving_supplier_name = (
                _text(receiving_supplier.get("company_name"))
                or "المورد الجديد"
            )
            for original_piece in selected_candidates:
                piece_id = _text(original_piece.get("piece_id"))
                candidate_dispatch_blocker = supplier_receiving_dispatch_blocker(
                    original_piece,
                    receiving_supplier_id,
                )
                supplier_reassigned = bool(
                    candidate_dispatch_blocker
                    and _text(candidate_dispatch_blocker.get("code"))
                    == "supplier_piece_dispatched_to_different_supplier"
                    and payload.confirm_supplier_reassignment
                )
                patch = supplier_receipt_piece_patch(
                    session=session,
                    actor=user,
                    piece_id=piece_id,
                    barcode=barcode,
                    received_at=now,
                )
                if supplier_reassigned:
                    patch.update({
                        "supplier_id": receiving_supplier_id,
                        "supplier_name": receiving_supplier_name,
                        "supplier_reassigned_from_id": _text(
                            original_piece.get("supplier_id")
                        ) or None,
                        "supplier_reassigned_from_name": _text(
                            original_piece.get("supplier_name")
                        ) or None,
                        "supplier_reassigned_at": now,
                        "supplier_reassigned_by_id": context["actor_id"],
                        "supplier_reassigned_by_name": _actor_name(user),
                        "supplier_reassignment_session_id": session_id,
                    })
                updated_piece = await db[PIECES].find_one_and_update(
                    {
                        "user_id": context["merchant_id"],
                        "piece_id": piece_id,
                        "status": {"$in": sorted(ELIGIBLE_PIECE_STATUSES)},
                        "supplier_id": original_piece.get("supplier_id"),
                        "$or": [
                            {"supplier_receiving_session_id": {"$exists": False}},
                            {"supplier_receiving_session_id": None},
                            {"supplier_receiving_session_id": ""},
                        ],
                    },
                    {"$set": patch},
                    return_document=ReturnDocument.AFTER,
                )
                if not updated_piece:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_piece_scan_conflict",
                            "message": "تغيّرت إحدى قطع الكمية أثناء المسح؛ حاول مرة أخرى.",
                            "piece_id": piece_id,
                        },
                    )
                reserved_rows.append((original_piece, updated_piece))

            updated_pieces = [row for _before, row in reserved_rows]
            updated_session = await db[SESSIONS].find_one_and_update(
                {
                    "user_id": context["merchant_id"],
                    "id": session_id,
                    "status": "open",
                    "opened_by": context["actor_id"],
                    "scan_lock_token": lock_token,
                },
                {
                    "$inc": {"scan_count": selected_quantity},
                    "$addToSet": {
                        "order_numbers": {"$each": sorted({
                            _text(row.get("order_number")) for row in updated_pieces
                            if _text(row.get("order_number"))
                        })},
                        "file_numbers": {"$each": sorted({
                            _text(row.get("file_number")) for row in updated_pieces
                            if _text(row.get("file_number"))
                        })},
                        "preparation_employee_ids": {"$each": sorted({
                            _text(row.get("responsible_employee_id")) for row in updated_pieces
                            if _text(row.get("responsible_employee_id"))
                        })},
                    },
                    "$set": {"last_scanned_at": now, "updated_at": now},
                    "$unset": {
                        "scan_lock_token": "",
                        "scan_lock_started_at": "",
                        "scan_lock_expires_at": "",
                    },
                },
                return_document=ReturnDocument.AFTER,
            )
            if updated_session:
                session_incremented = selected_quantity
                session = updated_session
            else:
                # The received piece is authoritative. If the short-lived lock
                # expires during a slow call, closing repairs the session count
                # from the pieces linked to the session.
                latest_session = await db[SESSIONS].find_one(
                    {"user_id": context["merchant_id"], "id": session_id},
                    {"_id": 0},
                )
                if latest_session:
                    session = latest_session

            events: list[dict[str, Any]] = []
            for original_piece, updated_piece in reserved_rows:
                event = {
                    "id": _text(updated_piece.get("receipt_event_id")),
                    "user_id": context["merchant_id"],
                    "session_id": session_id,
                    "session_reference": _text(session.get("reference")),
                    "event_type": "supplier_piece_scanned",
                    "piece_id": _text(updated_piece.get("piece_id")),
                    "batch_id": _text(updated_piece.get("batch_id")),
                    "file_number": _text(updated_piece.get("file_number")),
                    "order_number": _text(updated_piece.get("order_number")),
                    "order_item_id": _text(updated_piece.get("order_item_id")),
                    "unit_index": updated_piece.get("unit_index"),
                    "product_id": updated_piece.get("product_id"),
                    "product_name": updated_piece.get("product_name"),
                    "sku": updated_piece.get("sku"),
                    "variant_id": updated_piece.get("variant_id")
                    or updated_piece.get("salla_variant_id"),
                    "selected_image_url": updated_piece.get("selected_image_url"),
                    "preparation_employee_id": _text(
                        updated_piece.get("responsible_employee_id")
                    ),
                    "preparation_employee_name": _text(
                        updated_piece.get("responsible_employee_name")
                    ),
                    "receiving_employee_id": context["actor_id"],
                    "receiving_employee_name": _actor_name(user),
                    "services": list(updated_piece.get("services") or []),
                    "invoice_services": supplier_piece_invoice_services(
                        updated_piece,
                        session,
                        service_catalog,
                    ),
                    "remaining_service_count": int(
                        updated_piece.get("remaining_service_count") or 0
                    ),
                    "supplier_context": dict(session.get("supplier_snapshot") or {}),
                    "supplier_service_link_status": "draft_not_recorded",
                    "supplier_reassigned": (
                        _text(original_piece.get("supplier_id"))
                        != _text(updated_piece.get("supplier_id"))
                    ),
                    "supplier_reassigned_from_id": (
                        _text(original_piece.get("supplier_id")) or None
                    ),
                    "supplier_reassigned_from_name": (
                        _text(original_piece.get("supplier_name")) or None
                    ),
                    "scanned_barcode": barcode,
                    "scanned_group_quantity": selected_quantity,
                    "occurred_at": now,
                    "financial_invoice_created": False,
                    "liability_created": False,
                    "mezan_only": True,
                    "salla_updated": False,
                    "qoyod_updated": False,
                }
                if _text(updated_piece.get("experiment_run_id")):
                    event.update({
                        "experiment_mode": True,
                        "experiment_run_id": _text(updated_piece.get("experiment_run_id")),
                        "experiment_generation": int(updated_piece.get("experiment_generation") or 1),
                        "financial_writes_allowed": False,
                    })
                product_charge_eligible = supplier_piece_product_charge_eligible(
                    original_piece
                )
                event["product_charge_eligible"] = product_charge_eligible
                event.update(supplier_piece_reference_price(updated_piece))
                product_reference = await _supplier_product_reference_price(
                    db,
                    user_id=context["merchant_id"],
                    piece=updated_piece,
                )
                if not product_charge_eligible:
                    product_reference.update({
                        "reference_product_unit_price_halalas": 0,
                        "reference_product_price_complete": True,
                        "reference_product_price_source": "previous_supplier_invoice",
                    })
                event.update(product_reference)
                event.update(supplier_receipt_previous_piece_state(original_piece))
                inserted_event_ids.append(event["id"])
                await db[RECEIVING_EVENTS].update_one(
                    {"id": event["id"]},
                    {"$setOnInsert": event},
                    upsert=True,
                )
                await db[PIECE_EVENTS].update_one(
                    {"id": event["id"]},
                    {"$setOnInsert": event},
                    upsert=True,
                )
                events.append(event)
        except Exception:
            for original_piece, updated_piece in reversed(reserved_rows):
                await db[PIECES].update_one(
                    {
                        "user_id": context["merchant_id"],
                        "piece_id": _text(updated_piece.get("piece_id")),
                        "supplier_receiving_session_id": session_id,
                        "receipt_event_id": _text(updated_piece.get("receipt_event_id")),
                    },
                    supplier_receipt_piece_rollback_update(
                        supplier_receipt_previous_piece_state(original_piece)
                    ),
                )
            if inserted_event_ids:
                await db[RECEIVING_EVENTS].delete_many({
                    "user_id": context["merchant_id"],
                    "id": {"$in": inserted_event_ids},
                    "event_type": "supplier_piece_scanned",
                })
                await db[PIECE_EVENTS].delete_many({
                    "user_id": context["merchant_id"],
                    "id": {"$in": inserted_event_ids},
                    "event_type": "supplier_piece_scanned",
                })
            session_update: dict[str, Any] = {
                "$set": {"updated_at": _now()},
                "$unset": {
                    "scan_lock_token": "",
                    "scan_lock_started_at": "",
                    "scan_lock_expires_at": "",
                },
            }
            if session_incremented:
                session_update["$inc"] = {"scan_count": -session_incremented}
            if experiment_session_initialized:
                session_update["$unset"].update({
                    "experiment_mode": "",
                    "experiment_run_id": "",
                    "experiment_generation": "",
                    "financial_writes_allowed": "",
                    "liability_created": "",
                })
            await db[SESSIONS].update_one(
                {
                    "user_id": context["merchant_id"],
                    "id": session_id,
                    "status": "open",
                    "opened_by": context["actor_id"],
                },
                session_update,
            )
            raise
        public_events = [{
            key: value
            for key, value in event.items()
            if key not in {
                "user_id",
                "previous_piece_state",
                "previous_piece_present_fields",
            }
        } for event in events]
        return {
            "ok": True,
            "piece": _public_piece(reserved_rows[0][1]),
            "pieces": [_public_piece(row) for _before, row in reserved_rows],
            "session": _public_session(session),
            "scan": public_events[0],
            "scans": public_events,
            "selected_quantity": len(public_events),
            "requires_quantity_selection": False,
            "supplier_service_link_applied": False,
            "draft_piece_reserved": True,
            "financial_invoice_created": False,
            "liability_created": False,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    @router.post("/sessions/{session_id}/cancel")
    async def cancel_session(
        session_id: str,
        payload: SupplierReceivingSessionCancelRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        status = _text(session.get("status"))
        if status == "cancelled":
            return {
                "ok": True,
                "session": _public_session(session),
                "cancelled": True,
            }
        if status == "closed":
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_closed"},
            )
        if status not in {"open", "cancelling"}:
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_not_open"},
            )

        scans = await _cancellable_session_events(
            db,
            user_id=context["merchant_id"],
            session_id=session_id,
        )
        unavailable = [
            scan
            for scan in scans
            if not scan.get("rolled_back")
            and (
                not isinstance(scan.get("previous_piece_state"), dict)
                or not isinstance(scan.get("previous_piece_present_fields"), list)
            )
        ]
        if unavailable:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "supplier_receiving_cancel_rollback_unavailable",
                    "piece_count": len(unavailable),
                },
            )

        now = _now()
        if status == "open":
            session = await db[SESSIONS].find_one_and_update(
                {
                    "user_id": context["merchant_id"],
                    "id": session_id,
                    "status": "open",
                    "opened_by": context["actor_id"],
                    "$or": [
                        {"scan_lock_token": {"$exists": False}},
                        {"scan_lock_token": None},
                        {"scan_lock_expires_at": {"$lte": now}},
                    ],
                },
                {
                    "$set": {
                        "status": "cancelling",
                        "cancellation_started_at": now,
                        "cancelled_by": context["actor_id"],
                        "cancelled_by_name": _actor_name(user),
                        "cancel_note": _text(payload.note) or None,
                        "updated_at": now,
                    }
                },
                return_document=ReturnDocument.AFTER,
            )
            if not session:
                latest = await db[SESSIONS].find_one(
                    {"user_id": context["merchant_id"], "id": session_id},
                    {"_id": 0, "status": 1},
                )
                if _text((latest or {}).get("status")) == "open":
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "supplier_receiving_scan_busy"},
                    )
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_session_cancel_conflict"},
                )

        rolled_back_count = 0
        for scan in scans:
            if scan.get("rolled_back") is True:
                rolled_back_count += 1
                continue
            try:
                rollback_update = supplier_receipt_piece_rollback_update(scan)
            except ValueError as exc:
                raise HTTPException(
                    status_code=409,
                    detail={"code": str(exc)},
                ) from exc
            result = await db[PIECES].update_one(
                {
                    "user_id": context["merchant_id"],
                    "piece_id": _text(scan.get("piece_id")),
                    "supplier_receiving_session_id": session_id,
                    "receipt_event_id": _text(scan.get("id")),
                },
                rollback_update,
            )
            if not result.modified_count:
                latest_piece = await db[PIECES].find_one(
                    {
                        "user_id": context["merchant_id"],
                        "piece_id": _text(scan.get("piece_id")),
                    },
                    {
                        "_id": 0,
                        "supplier_receiving_session_id": 1,
                        "receipt_event_id": 1,
                    },
                )
                if (
                    _text((latest_piece or {}).get("supplier_receiving_session_id"))
                    == session_id
                    or _text((latest_piece or {}).get("receipt_event_id"))
                    == _text(scan.get("id"))
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_cancel_piece_conflict",
                            "piece_id": _text(scan.get("piece_id")),
                        },
                    )
            rollback_event_patch = {
                "event_type": "supplier_piece_scan_cancelled",
                "original_event_type": "supplier_piece_scanned",
                "rolled_back": True,
                "rolled_back_at": now,
                "rolled_back_by": context["actor_id"],
                "rolled_back_by_name": _actor_name(user),
                "cancel_note": _text(payload.note) or None,
                "financial_invoice_created": False,
                "liability_created": False,
                "salla_updated": False,
                "qoyod_updated": False,
            }
            await db[RECEIVING_EVENTS].update_one(
                {
                    "user_id": context["merchant_id"],
                    "session_id": session_id,
                    "id": _text(scan.get("id")),
                },
                {"$set": rollback_event_patch},
            )
            await db[PIECE_EVENTS].update_one(
                {
                    "user_id": context["merchant_id"],
                    "id": _text(scan.get("id")),
                },
                {"$set": rollback_event_patch},
            )
            rolled_back_count += 1

        cancelled_at = _now()
        updated = await db[SESSIONS].find_one_and_update(
            {
                "user_id": context["merchant_id"],
                "id": session_id,
                "status": "cancelling",
                "opened_by": context["actor_id"],
            },
            {
                "$set": {
                    "status": "cancelled",
                    "cancelled_at": cancelled_at,
                    "cancelled_by": context["actor_id"],
                    "cancelled_by_name": _actor_name(user),
                    "cancel_note": _text(payload.note) or None,
                    "cancelled_piece_count": rolled_back_count,
                    "updated_at": cancelled_at,
                    "financial_invoice_created": False,
                    "liability_created": False,
                    "salla_updated": False,
                    "qoyod_updated": False,
                },
                "$unset": {
                    "scan_lock_token": "",
                    "scan_lock_started_at": "",
                    "scan_lock_expires_at": "",
                    "operational_invoice": "",
                },
            },
            return_document=ReturnDocument.AFTER,
        )
        if not updated:
            latest = await db[SESSIONS].find_one(
                {"user_id": context["merchant_id"], "id": session_id},
                {"_id": 0},
            )
            if _text((latest or {}).get("status")) == "cancelled":
                updated = latest
            else:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_session_cancel_conflict"},
                )

        event_id = uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"supplier-receiving-cancel:{context['merchant_id']}:{session_id}",
        ).hex
        await db[RECEIVING_EVENTS].update_one(
            {"id": event_id},
            {
                "$setOnInsert": {
                    "id": event_id,
                    "user_id": context["merchant_id"],
                    "session_id": session_id,
                    "session_reference": _text(updated.get("reference")),
                    "event_type": "supplier_receiving_session_cancelled",
                    "rolled_back_piece_count": rolled_back_count,
                    "actor_id": context["actor_id"],
                    "actor_name": _actor_name(user),
                    "note": _text(payload.note) or None,
                    "occurred_at": cancelled_at,
                    "financial_invoice_created": False,
                    "liability_created": False,
                    "mezan_only": True,
                    "salla_updated": False,
                    "qoyod_updated": False,
                }
            },
            upsert=True,
        )
        return {
            "ok": True,
            "session": _public_session(updated),
            "cancelled": True,
            "rolled_back_piece_count": rolled_back_count,
            "operational_invoice_created": False,
            "financial_invoice_created": False,
            "liability_created": False,
            "salla_updated": False,
            "qoyod_updated": False,
        }

    @router.post("/sessions/{session_id}/close")
    async def close_session(
        session_id: str,
        payload: SupplierReceivingSessionCloseRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _actor_context(db, user)
        _require_permission(context, RECEIVE_PERMISSION)
        await ensure_supplier_receiving_indexes(db)
        session = await _session_for_actor(
            db,
            context=context,
            session_id=session_id,
        )
        if _text(session.get("status")) == "closed":
            saved_invoice = await db[SUPPLIER_INVOICES].find_one(
                {
                    "user_id": context["merchant_id"],
                    "session_id": session_id,
                },
                {"_id": 0},
            )
            return {
                "ok": True,
                "session": _public_session(session),
                "supplier_invoice": _public_supplier_invoice(saved_invoice),
                "financial_invoice_created": bool(
                    (saved_invoice or {}).get("financial_invoice_created")
                ),
                "liability_created": bool(
                    (saved_invoice or {}).get("liability_created")
                ),
                "experiment_mode": bool((saved_invoice or {}).get("experiment_mode")),
                "experiment_run_id": _text(
                    (saved_invoice or {}).get("experiment_run_id")
                ) or None,
                "qoyod_updated": False,
            }
        if _text(session.get("status")) != "open":
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_receiving_session_not_open"},
            )
        mongo_client = getattr(db, "client", None)
        if mongo_client is None or not hasattr(mongo_client, "start_session"):
            raise HTTPException(
                status_code=503,
                detail={"code": "supplier_receiving_atomic_transaction_required"},
            )

        async def finalize(mongo_session: Any) -> dict[str, Any]:
            merchant_id = context["merchant_id"]
            fresh_session = await db[SESSIONS].find_one(
                {
                    "user_id": merchant_id,
                    "id": session_id,
                    "opened_by": context["actor_id"],
                },
                {"_id": 0},
                session=mongo_session,
            )
            if not fresh_session or _text(fresh_session.get("status")) != "open":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_session_close_conflict"},
                )
            now = _now()
            if (
                fresh_session.get("scan_lock_token")
                and fresh_session.get("scan_lock_expires_at")
                and fresh_session["scan_lock_expires_at"] > now
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_scan_busy"},
                )
            scans = await _recent_session_events(
                db,
                user_id=merchant_id,
                session_id=session_id,
                limit=MAX_SESSION_SCANS,
                mongo_session=mongo_session,
            )
            actual_count = len(scans)
            scanned_piece_ids = [
                _text(row.get("piece_id"))
                for row in scans
                if _text(row.get("piece_id"))
            ]
            current_pieces = await db[PIECES].find(
                {
                    "user_id": merchant_id,
                    "piece_id": {"$in": scanned_piece_ids},
                    "supplier_receiving_session_id": session_id,
                },
                {"_id": 0},
                session=mongo_session,
            ).to_list(MAX_SESSION_SCANS)
            current_by_id = {
                _text(row.get("piece_id")): row for row in current_pieces
            }
            if (
                len(scanned_piece_ids) != len(set(scanned_piece_ids))
                or set(current_by_id) != set(scanned_piece_ids)
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_invoice_piece_mismatch"},
                )
            for piece_id in scanned_piece_ids:
                piece = current_by_id[piece_id]
                blocker = piece_scan_blocker(piece)
                if blocker or _text(piece.get("active_hold_id")):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_piece_stopped_before_invoice",
                            "piece_id": piece_id,
                            "hold_id": _text(piece.get("active_hold_id")) or None,
                            "stop_type": _text(piece.get("hold_stop_type")) or None,
                            "message": (
                                _text(piece.get("hold_note"))
                                or _text((blocker or {}).get("message"))
                                or "توقفت القطعة بعد المسح؛ لم تُنشأ الفاتورة."
                            ),
                        },
                    )
            experiment_run_id = supplier_invoice_experiment_run_id(scans)
            current_run_ids = {
                _text(row.get("experiment_run_id"))
                for row in current_pieces
                if _text(row.get("experiment_run_id"))
            }
            if (
                (experiment_run_id and current_run_ids != {experiment_run_id})
                or (not experiment_run_id and current_run_ids)
                or _text(fresh_session.get("experiment_run_id"))
                not in {"", experiment_run_id or ""}
            ):
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_experiment_mode_mismatch"},
                )
            is_experiment = bool(experiment_run_id)
            service_catalog = await _supplier_service_catalog(
                db,
                user_id=merchant_id,
                session=fresh_session,
                mongo_session=mongo_session,
            )
            draft = build_supplier_receiving_invoice(
                session=fresh_session,
                scans=scans,
                requested_lines=payload.invoice_lines,
                saved_at=now,
                permissions=set(context["permissions"]),
                service_catalog=service_catalog,
            )
            invoice_id = f"msiv2_{uuid.uuid5(uuid.NAMESPACE_URL, f'{merchant_id}:{session_id}').hex}"
            invoice_number = _text(fresh_session.get("reference")).replace(
                "SR-", "SI-TEST-" if is_experiment else "SI-", 1
            )
            supplier = dict(fresh_session.get("supplier_snapshot") or {})
            invoice = {
                **draft,
                "id": invoice_id,
                "invoice_number": invoice_number,
                "reference": invoice_number,
                "user_id": merchant_id,
                "session_id": session_id,
                "session_reference": _text(fresh_session.get("reference")),
                "supplier_id": _text(supplier.get("id")),
                "supplier_snapshot": supplier,
                "status": "experiment_completed" if is_experiment else "payable_posted",
                "payment_status": "not_applicable" if is_experiment else "unpaid",
                "paid_halalas": 0,
                "outstanding_halalas": 0 if is_experiment else int(draft["total_halalas"]),
                "supplier_approved_at": now,
                "supplier_approved_by": context["actor_id"],
                "supplier_approved_by_name": _actor_name(user),
                "payable_posted_at": None if is_experiment else now,
                "approved_at": now,
                "created_at": now,
                "updated_at": now,
                "financial_invoice_created": not is_experiment,
                "liability_created": not is_experiment,
                "share_required": not is_experiment,
                "share_status": "not_required" if is_experiment else "pending",
                "share_confirmed": bool(is_experiment),
                "legacy_supplier_data_used": False,
                "qoyod_updated": False,
                "salla_updated": False,
                "experiment_mode": is_experiment,
                "experiment_run_id": experiment_run_id,
                "financial_writes_allowed": not is_experiment,
            }
            if is_experiment:
                invoice["price_changes"] = [
                    {
                        **change,
                        "applied": False,
                        "simulation_only": True,
                        "experiment_run_id": experiment_run_id,
                    }
                    for change in (invoice.get("price_changes") or [])
                ]
                invoice["price_updates_applied"] = False
                invoice["ledger_txn_group_id"] = None
                invoice["ledger_entry_ids"] = []
                ledger = None
            else:
                invoice["price_changes"] = await apply_supplier_invoice_price_changes(
                    db,
                    user_id=merchant_id,
                    actor=user,
                    invoice_id=invoice_id,
                    changes=list(invoice.get("price_changes") or []),
                    changed_at=now,
                    mongo_session=mongo_session,
                )
                invoice["price_updates_applied"] = True
                ledger = await _post_supplier_invoice_ledger(
                    db,
                    user_id=merchant_id,
                    actor=user,
                    invoice=invoice,
                    mongo_session=mongo_session,
                )
                invoice["ledger_txn_group_id"] = ledger["txn_group_id"]
                invoice["ledger_entry_ids"] = ledger["entry_ids"]
            if is_experiment:
                invoice["added_product_services"] = [
                    {
                        **addition,
                        "applied": False,
                        "simulation_only": True,
                        "experiment_run_id": experiment_run_id,
                    }
                    for addition in (invoice.get("added_product_services") or [])
                ]
            await db[SUPPLIER_INVOICES].insert_one(
                dict(invoice),
                session=mongo_session,
            )

            added_pairs: set[tuple[str, str]] = set()
            for addition in (
                [] if is_experiment else invoice.get("added_product_services") or []
            ):
                product_id = _text(addition.get("product_id"))
                service_id = _text(addition.get("service_id"))
                if not product_id or not service_id or (product_id, service_id) in added_pairs:
                    continue
                added_pairs.add((product_id, service_id))
                product = await db[PRODUCTS].find_one(
                    {
                        "user_id": merchant_id,
                        "$or": [
                            {"id": product_id},
                            {"mezan_product_id": product_id},
                            {"salla_product_id": product_id},
                        ],
                    },
                    {"_id": 0},
                    session=mongo_session,
                )
                resource = service_catalog.get(service_id)
                if not product or not resource:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_service_add_conflict",
                            "product_id": product_id,
                            "service_id": service_id,
                        },
                    )
                salla_product_id = _text(product.get("salla_product_id")) or _text(
                    product.get("mezan_product_id") or product.get("id")
                )
                option_conflict = await db[BINDINGS].find_one(
                    {
                        "user_id": merchant_id,
                        "salla_product_id": salla_product_id,
                        "mode": "resource",
                        "resource_id": service_id,
                    },
                    {
                        "_id": 0,
                        "option_id": 1,
                        "option_name": 1,
                        "value_id": 1,
                        "value_name": 1,
                    },
                    session=mongo_session,
                )
                if option_conflict:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_service_option_conflict",
                            "product_id": product_id,
                            "service_id": service_id,
                            "option": option_conflict,
                        },
                    )
                selector = {
                    "user_id": merchant_id,
                    "salla_product_id": salla_product_id,
                    "resource_id": service_id,
                }
                await db[PRODUCT_RESOURCE_BINDINGS].update_one(
                    selector,
                    {
                        "$set": {
                            "mezan_product_id": (
                                product.get("mezan_product_id") or product.get("id")
                            ),
                            "product_name": product.get("name"),
                            "resource_name": resource.get("name"),
                            "manual_link": True,
                            "updated_at": now,
                        },
                        "$setOnInsert": {
                            "id": uuid.uuid4().hex,
                            "quantity": 1.0,
                            "group_ids": [],
                            "created_at": now,
                        },
                    },
                    upsert=True,
                    session=mongo_session,
                )
                await db[AUDIT].insert_one(
                    {
                        "id": uuid.uuid4().hex,
                        "user_id": merchant_id,
                        "event_type": "supplier_receiving_service_added_to_product",
                        "supplier_invoice_id": invoice_id,
                        "session_id": session_id,
                        "salla_product_id": salla_product_id,
                        "resource_id": service_id,
                        "actor_id": context["actor_id"],
                        "actor_name": _actor_name(user),
                        "created_at": now,
                    },
                    session=mongo_session,
                )

            line_by_piece = {
                piece_id: line
                for line in invoice["lines"]
                for piece_id in line.get("piece_ids") or []
            }
            for scan in scans:
                piece_id = _text(scan.get("piece_id"))
                line = line_by_piece.get(piece_id)
                if not line:
                    raise HTTPException(
                        status_code=409,
                        detail={"code": "supplier_receiving_invoice_piece_mismatch"},
                    )
                piece = await db[PIECES].find_one(
                    {
                        "user_id": merchant_id,
                        "piece_id": piece_id,
                        "supplier_receiving_session_id": session_id,
                        "receipt_event_id": _text(scan.get("id")),
                    },
                    {"_id": 0},
                    session=mongo_session,
                )
                if not piece:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_invoice_piece_mismatch",
                            "piece_id": piece_id,
                        },
                    )
                result = await db[PIECES].update_one(
                    {
                        "user_id": merchant_id,
                        "piece_id": piece_id,
                        "supplier_receiving_session_id": session_id,
                        "receipt_event_id": _text(scan.get("id")),
                    },
                    supplier_service_completion_update(
                        piece=piece,
                        invoice_line=line,
                        session=fresh_session,
                        actor=user,
                        invoice_id=invoice_id,
                        completed_at=now,
                    ),
                    session=mongo_session,
                )
                if not result.modified_count:
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_invoice_piece_mismatch",
                            "piece_id": piece_id,
                        },
                    )
                event_patch = {
                    "event_type": (
                        "supplier_piece_service_simulated"
                        if is_experiment
                        else "supplier_piece_service_recorded"
                    ),
                    "supplier_invoice_id": invoice_id,
                    "supplier_invoice_number": invoice_number,
                    "recorded_services": list(line.get("services") or []),
                    "supplier_service_link_status": "service_recorded",
                    "financial_invoice_created": not is_experiment,
                    "liability_created": not is_experiment,
                    "experiment_mode": is_experiment,
                    "experiment_run_id": experiment_run_id,
                    "finalized_at": now,
                }
                receiving_event_result = await db[RECEIVING_EVENTS].update_one(
                    {
                        "id": _text(scan.get("id")),
                        "user_id": merchant_id,
                        "session_id": session_id,
                        "piece_id": piece_id,
                        "event_type": "supplier_piece_scanned",
                    },
                    {"$set": event_patch},
                    session=mongo_session,
                )
                piece_event_result = await db[PIECE_EVENTS].update_one(
                    {
                        "id": _text(scan.get("id")),
                        "user_id": merchant_id,
                        "piece_id": piece_id,
                    },
                    {"$set": event_patch},
                    session=mongo_session,
                )
                if (
                    not receiving_event_result.modified_count
                    or not piece_event_result.modified_count
                ):
                    raise HTTPException(
                        status_code=409,
                        detail={
                            "code": "supplier_receiving_invoice_event_conflict",
                            "piece_id": piece_id,
                        },
                    )

            invoice_summary = {
                "id": invoice_id,
                "invoice_number": invoice_number,
                "status": "experiment_completed" if is_experiment else "payable_posted",
                "currency": "SAR",
                "piece_count": invoice["piece_count"],
                "line_count": invoice["line_count"],
                "total_halalas": invoice["total_halalas"],
                "outstanding_halalas": invoice["outstanding_halalas"],
                "price_change_count": len(invoice.get("price_changes") or []),
                "approved_at": now,
                "ledger_txn_group_id": ledger["txn_group_id"] if ledger else None,
                "share_required": not is_experiment,
                "share_status": "not_required" if is_experiment else "pending",
                "share_confirmed": bool(is_experiment),
                "experiment_mode": is_experiment,
                "experiment_run_id": experiment_run_id,
            }
            updated = await db[SESSIONS].find_one_and_update(
                {
                    "user_id": merchant_id,
                    "id": session_id,
                    "status": "open",
                    "opened_by": context["actor_id"],
                },
                {
                    "$set": {
                        "status": "closed",
                        "scan_count": actual_count,
                        "closed_at": now,
                        "closed_by": context["actor_id"],
                        "closed_by_name": _actor_name(user),
                        "close_note": _text(payload.note) or None,
                        "supplier_service_link_status": (
                            "service_simulated" if is_experiment else "service_recorded"
                        ),
                        "supplier_invoice_id": invoice_id,
                        "supplier_invoice": invoice_summary,
                        "financial_invoice_created": not is_experiment,
                        "liability_created": not is_experiment,
                        "experiment_mode": is_experiment,
                        "experiment_run_id": experiment_run_id,
                        "updated_at": now,
                    },
                    "$unset": {
                        "operational_invoice": "",
                        "scan_lock_token": "",
                        "scan_lock_started_at": "",
                        "scan_lock_expires_at": "",
                    },
                },
                return_document=ReturnDocument.AFTER,
                session=mongo_session,
            )
            if not updated:
                raise HTTPException(
                    status_code=409,
                    detail={"code": "supplier_receiving_session_close_conflict"},
                )

            close_event = {
                "id": uuid.uuid5(
                    uuid.NAMESPACE_URL,
                    f"supplier-receiving-close:{merchant_id}:{session_id}",
                ).hex,
                "user_id": merchant_id,
                "session_id": session_id,
                "session_reference": _text(updated.get("reference")),
                "event_type": "supplier_receiving_session_closed",
                "scan_count": actual_count,
                "actor_id": context["actor_id"],
                "actor_name": _actor_name(user),
                "note": _text(payload.note) or None,
                "occurred_at": now,
                "supplier_service_link_status": (
                    "service_simulated" if is_experiment else "service_recorded"
                ),
                "supplier_invoice_id": invoice_id,
                "supplier_invoice": invoice_summary,
                "financial_invoice_created": not is_experiment,
                "liability_created": not is_experiment,
                "experiment_mode": is_experiment,
                "experiment_run_id": experiment_run_id,
                "mezan_only": True,
                "salla_updated": False,
                "qoyod_updated": False,
            }
            await db[RECEIVING_EVENTS].insert_one(
                close_event,
                session=mongo_session,
            )
            audit_events = []
            for index, change in enumerate(invoice.get("price_changes") or []):
                audit_events.append({
                    "id": str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{invoice_id}:price:{index}",
                    )),
                    "user_id": merchant_id,
                    "session_id": session_id,
                    "event_type": (
                        "supplier_receiving_price_change_simulated"
                        if is_experiment
                        else "supplier_receiving_price_changed"
                    ),
                    "supplier_invoice_id": invoice_id,
                    "actor_id": context["actor_id"],
                    "actor_name": _actor_name(user),
                    "before_halalas": change.get("before_halalas"),
                    "after_halalas": change.get("after_halalas"),
                    "change": change,
                    "occurred_at": now,
                    "mezan_only": True,
                    "experiment_mode": is_experiment,
                    "experiment_run_id": experiment_run_id,
                })
            for index, addition in enumerate(invoice.get("added_product_services") or []):
                audit_events.append({
                    "id": str(uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        f"{invoice_id}:service-add:{index}",
                    )),
                    "user_id": merchant_id,
                    "session_id": session_id,
                    "event_type": (
                        "supplier_receiving_service_addition_simulated"
                        if is_experiment
                        else "supplier_receiving_service_added_to_product"
                    ),
                    "supplier_invoice_id": invoice_id,
                    "actor_id": context["actor_id"],
                    "actor_name": _actor_name(user),
                    "addition": addition,
                    "occurred_at": now,
                    "mezan_only": True,
                    "experiment_mode": is_experiment,
                    "experiment_run_id": experiment_run_id,
                })
            if audit_events:
                await db[RECEIVING_EVENTS].insert_many(
                    audit_events,
                    session=mongo_session,
                )
            if not is_experiment and (
                invoice.get("price_changes") or added_pairs
            ):
                await bump_product_cost_revision(
                    db,
                    merchant_id,
                    session=mongo_session,
                )
            return {
                "ok": True,
                "session": _public_session(updated),
                "supplier_invoice": _public_supplier_invoice(invoice),
                "next_step": (
                    "experiment_completed_without_financial_writes"
                    if is_experiment
                    else "supplier_invoice_payable_posted"
                ),
                "share_next_step": (
                    None
                    if is_experiment
                    else "share_invoice_with_supplier_and_upload_evidence"
                ),
                "supplier_service_link_applied": not is_experiment,
                "financial_invoice_created": not is_experiment,
                "liability_created": not is_experiment,
                "experiment_mode": is_experiment,
                "experiment_run_id": experiment_run_id,
                "salla_updated": False,
                "qoyod_updated": False,
            }

        try:
            async with await mongo_client.start_session() as mongo_session:
                result = await mongo_session.with_transaction(finalize)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "supplier_receiving_accounting_transaction_failed",
                    "message": (
                        "تعذّر اعتماد فاتورة المورد محاسبيًا؛ بقيت الجلسة "
                        "مفتوحة ولم تُحفظ الفاتورة. حاول مرة أخرى."
                    ),
                },
            ) from exc
        return result

    return router


__all__ = [
    "ADD_PRODUCT_SERVICE_PERMISSION",
    "EDIT_PRODUCT_PRICE_PERMISSION",
    "EDIT_SERVICE_PRICE_PERMISSION",
    "ELIGIBLE_PIECE_STATUSES",
    "RECEIVING_EVENTS",
    "SESSIONS",
    "SupplierPieceScanRequest",
    "SupplierReceivingInvoiceLineRequest",
    "SupplierReceivingInvoiceServiceRequest",
    "SupplierReceivingSessionCancelRequest",
    "SupplierReceivingSessionCloseRequest",
    "SupplierReceivingSessionCreateRequest",
    "ensure_supplier_receiving_indexes",
    "make_supplier_receiving_router",
    "piece_scan_blocker",
    "build_supplier_receiving_invoice",
    "resolve_scanned_piece",
    "supplier_receipt_piece_patch",
    "supplier_receipt_piece_rollback_update",
    "supplier_receipt_previous_piece_state",
    "supplier_piece_reference_price",
    "supplier_invoice_experiment_run_id",
    "supplier_service_completion_update",
]
