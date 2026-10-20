"""Read-only provider precondition for a new local review approval."""
import asyncio
import re

from fastapi import HTTPException


PENDING = frozenset({
    "under review", "waiting review", "pending review",
    "بإنتظار المراجعة", "بانتظار المراجعة", "انتظار المراجعة",
})


def _text(value):
    return str(value or "").strip()


def _normalize(value):
    return " ".join(_text(value).casefold().replace("_", " ").split())


def require_pending_status(status):
    if not isinstance(status, dict):
        status = {"name": status}
    custom = status.get("customized") or {}
    if not isinstance(custom, dict):
        custom = {"name": custom}
    values = [status.get("slug"), status.get("name"), custom.get("slug"), custom.get("name")]
    normalized = {_normalize(value) for value in values if _text(value)}
    name = _text(custom.get("name") or status.get("name") or status.get("slug")) or "غير معروفة"
    if not normalized or not normalized.issubset(PENDING):
        raise HTTPException(409, detail={
            "code": "review_salla_status_ineligible", "salla_status": name,
            "message": f"حالة الطلب في سلة: {name}. هذه الحالة غير مؤهلة لاعتماد تمت المراجعة.",
        })


async def verify_salla_pending(db, *, user_id, order_number, source, call_salla):
    """Never refresh canonical data, enqueue work, or write a Salla status."""
    raw = ((source or {}).get("raw_by_source") or {}).get("salla_direct") or {}
    identifier = _text(raw.get("id"))
    unavailable = {
        "code": "review_salla_status_unavailable",
        "message": "تعذّر التحقق من حالة الطلب في سلة. لم يتم اعتماد المراجعة؛ أعد المحاولة.",
    }
    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
        raise HTTPException(503, detail=unavailable)
    try:
        response = await asyncio.wait_for(
            call_salla(db, user_id, "GET", f"/orders/{identifier}"), timeout=20,
        )
    except Exception:
        raise HTTPException(503, detail=unavailable) from None
    details = response.get("data") if isinstance(response, dict) else None
    if (not isinstance(details, dict) or response.get("success") is False
            or _text(details.get("id")) != identifier
            or _text(details.get("reference_id")) != _text(order_number)):
        raise HTTPException(503, detail=unavailable)
    require_pending_status(details.get("status"))
