"""Date anchors for preparation navigation, independent of supplier custody."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo


DateBasis = Literal["file_created_at", "employee_assigned_at"]


def _timestamp(value: Any) -> datetime | None:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            str(value or "").strip().replace("Z", "+00:00")
        )
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def file_creation_at(registry: dict[str, Any]) -> Any:
    """Older files without a creation timestamp retain their registration date."""
    for value in (registry.get("created_at"), registry.get("registered_at")):
        if _timestamp(value) is not None:
            return value
    return None


def employee_assignment_at(piece: dict[str, Any], registry: dict[str, Any]) -> Any:
    """Return a work assignment, never a dispatch or supplier-receipt transfer.

    Rejection/reassignment writes an explicit ``assign_*`` history row. The
    generic reassigned_at field is also written by supplier receipt, so it is
    deliberately not a fallback. Legacy pieces retain their initial assigned
    timestamp or, when missing, the registry's registration timestamp.
    """
    explicit = piece.get("employee_assigned_at")
    if _timestamp(explicit) is not None:
        return explicit

    employee_id = str(piece.get("responsible_employee_id") or "").strip()
    assignments: list[tuple[datetime, Any]] = []
    for event in piece.get("assignment_history") or []:
        if not isinstance(event, dict):
            continue
        if not str(event.get("assignment_id") or "").startswith("assign_"):
            continue
        if (event.get("reason") == "supplier_receipt"
                or event.get("source") == "supplier_receiving_invoice_approval"):
            continue
        recipient = str(event.get("responsible_employee_id") or "").strip()
        if employee_id and recipient and recipient != employee_id:
            continue
        assigned = _timestamp(event.get("assigned_at"))
        if assigned is not None:
            assignments.append((assigned, event["assigned_at"]))
    if assignments:
        return max(assignments, key=lambda item: item[0])[1]
    for value in (piece.get("assigned_at"), registry.get("registered_at")):
        if _timestamp(value) is not None:
            return value
    return None


def progress_day(value: Any) -> str:
    parsed = _timestamp(value)
    return parsed.astimezone(ZoneInfo("Asia/Riyadh")).strftime("%Y-%m-%d") if parsed else "__unknown__"


def progress_timestamp(value: Any) -> str | None:
    """Keep API dates unambiguous on devices in any timezone, including Mongo's naive UTC."""
    parsed = _timestamp(value)
    return parsed.isoformat() if parsed else None


def piece_progress_day(piece: dict[str, Any], registry: dict[str, Any], date_basis: DateBasis) -> str:
    if date_basis == "file_created_at":
        return progress_day(file_creation_at(registry))
    if date_basis == "employee_assigned_at":
        return progress_day(employee_assignment_at(piece, registry))
    raise ValueError("unsupported_preparation_date_basis")
