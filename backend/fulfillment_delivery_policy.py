"""The canonical order's delivered status stops assembly, not its history."""
from fastapi import HTTPException


DELIVERED_BLOCKER = "assembly_order_delivered"


def order_is_delivered(status):
    return str(status or "").strip().casefold() == "delivered"


def require_assembly_order_open(order):
    if order_is_delivered(order.status if order else None):
        raise HTTPException(409, detail={"code": DELIVERED_BLOCKER,
            "message": "تم توصيل الطلب في سلة؛ متاح للعرض فقط"})
