"""Gift-only review prerequisite from the authoritative Salla order snapshot."""
from fastapi import HTTPException


def gift_address_review(source):
    raw = (source.get("raw_by_source") or {}).get("salla_direct") or {}
    # Use provider order source, never tags, product names, notes or buyer address.
    provider_source = raw.get("source") or source.get("source_native") or source.get("source")
    is_gift = provider_source == "buy_as_gift"
    # Observed GET /orders/{id} field. Missing evidence is not confirmation.
    complete = raw.get("address_incomplete") is False
    blocked = is_gift and not complete
    return {
        "is_gift": is_gift,
        "blocked": blocked,
        "code": "gift_recipient_address_incomplete" if blocked else None,
        "message": (
            "لم تؤكد سلة اكتمال عنوان مستلم الهدية. أكمل عنوان المستلم في سلة ثم حدّث الطلب قبل اعتماد المراجعة."
            if blocked else None
        ),
    }


def require_gift_address(source):
    evidence = gift_address_review(source)
    if evidence["blocked"]:
        raise HTTPException(409, detail={
            **evidence, "refresh_required": True, "retry_post": False,
        })
