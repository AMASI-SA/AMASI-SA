"""Merchant-scoped manager choices for mobile supplier and assembly workflow.

Only explicit 'optional' enables a shortcut; missing, malformed, unavailable or
legacy settings always retain the existing mandatory processing gates. The
caller must independently enforce authenticated actor, merchant and stage.
"""
from __future__ import annotations

from typing import Any

REQUIRED = "required"
OPTIONAL = "optional"
FIELD_PREPARATION = "preparation_employee_receipt_mode"
FIELD_SUPPLIER = "supplier_assignment_mode"


def safe_mode(value: Any) -> str:
    return OPTIONAL if value == OPTIONAL else REQUIRED


async def read_modes(db: Any, merchant_id: str, *, mongo_session: Any = None) -> dict[str, str]:
    if not merchant_id:
        return {FIELD_PREPARATION: REQUIRED, FIELD_SUPPLIER: REQUIRED}
    options: dict[str, Any] = {"user_id": merchant_id}
    projection = {
        "operational_flow.preparation_employee_receipt_mode": 1,
        "operational_flow.supplier_assignment_mode": 1,
    }
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    collection = getattr(db, "settings", None)
    if collection is None:
        # Some legacy read-only projections do not expose a settings
        # collection. They may display results but must not allow shortcuts.
        return {FIELD_PREPARATION: REQUIRED, FIELD_SUPPLIER: REQUIRED}
    row = await collection.find_one(options, projection, **kwargs) or {}
    flow = row.get("operational_flow") if isinstance(row.get("operational_flow"), dict) else {}
    return {
        FIELD_PREPARATION: safe_mode(flow.get(FIELD_PREPARATION)),
        FIELD_SUPPLIER: safe_mode(flow.get(FIELD_SUPPLIER)),
    }


async def is_optional(
    db: Any, merchant_id: str, field: str, *, mongo_session: Any = None,
) -> bool:
    if field not in {FIELD_PREPARATION, FIELD_SUPPLIER}:
        raise ValueError("unknown_operational_flow_policy")
    return (await read_modes(db, merchant_id, mongo_session=mongo_session))[field] == OPTIONAL


__all__ = [
    "REQUIRED", "OPTIONAL", "FIELD_PREPARATION", "FIELD_SUPPLIER",
    "safe_mode", "read_modes", "is_optional",
]
