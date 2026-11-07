"""Explicit Sold Products cohort; absent means the executive default is unchanged."""
from fastapi import HTTPException
from sold_products_report_v2 import SELECTABLE_STATUSES


def validate_report_statuses(value: str) -> str:
    selected = [part.strip() for part in value.split(",")]
    if (not selected or any(not part for part in selected)
            or ("default" in selected and selected != ["default"])
            or any(part != "default" and part not in SELECTABLE_STATUSES for part in selected)):
        raise HTTPException(status_code=422, detail="حالة طلب غير مدعومة")
    return ",".join(selected)
