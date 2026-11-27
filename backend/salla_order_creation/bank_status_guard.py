"""Safely target the merchant's exact custom BANK review status.

Multiple Amasi statuses share the generic 'under_review' slug, including
'تم المراجعة'. Sending the slug blindly could select a processed/incorrect
custom status. Discover only the unique, ACTIVE status named exactly
'بإنتظار المراجعة' under Salla's canonical review classification.

Status change is a separate POST /orders/{id}/status, NEVER another
POST /orders and NEVER a paid/settled action. It is OFF by default.
Unknown outcome is fenced with a unique Mongo transition intent and must
be manually reconciled, not retried blindly.
"""

from __future__ import annotations

import hashlib
from typing import Any, Awaitable, Callable

from .readers import SourceUnavailable

READ = "بإنتظار المراجعة"
CANONICAL = "بإنتظار المراجعة"
REVIEW_SLUG = "under_review"
PENDING_PAYMENT_NAME = "بإنتظار الدفع"
PENDING_PAYMENT_SLUG = "payment_pending"
# No generic under_review status is eligible for a transition: other Amasi
# merchant statuses such as تم المراجعة share exactly that provider slug.

Request = Callable[..., Awaitable[dict]]


class BankStatusDenied(ValueError):
    pass


def _exact_active_status(statuses: Any, *, name: str, slug: str) -> int:
    if not isinstance(statuses, list) or len(statuses) > 250:
        raise BankStatusDenied("salla_bank_review_status_list_invalid")
    matches = []
    for row in statuses:
        if not isinstance(row, dict):
            raise BankStatusDenied("salla_bank_status_row_invalid")
        original = row.get("original")
        if (row.get("name") == name
                and row.get("slug") == slug
                and row.get("is_active") is not False
                and isinstance(original, dict)
                and original.get("name") == name):
            status_id = row.get("id")
            if type(status_id) is not int or status_id < 1:
                raise BankStatusDenied("salla_bank_review_status_id_invalid")
            matches.append(status_id)
    if len(matches) != 1:
        raise BankStatusDenied("unique_active_bank_review_status_required")
    return matches[0]


def exact_active_review_status(statuses: Any) -> int:
    return _exact_active_status(statuses, name=READ, slug=REVIEW_SLUG)


def exact_active_pending_payment_status(statuses: Any) -> int:
    return _exact_active_status(
        statuses, name=PENDING_PAYMENT_NAME, slug=PENDING_PAYMENT_SLUG,
    )


def _data(response: Any) -> dict:
    if not isinstance(response, dict) or response.get("success") is False:
        raise BankStatusDenied("salla_order_read_unavailable")
    data = response.get("data")
    if not isinstance(data, dict):
        raise BankStatusDenied("salla_order_read_invalid")
    return data


def _customized_id(data: dict) -> int | None:
    status = data.get("status")
    if not isinstance(status, dict):
        return None
    customized = status.get("customized")
    if not isinstance(customized, dict):
        return None
    number = customized.get("id")
    return number if type(number) is int and number > 0 else None


async def ensure_bank_review_custom_status(
    db: Any, *, user_id: str, intent_id: str,
    salla_order_id: int, request: Request,
    enabled: bool = False,
) -> dict:
    """Read back exact merchant status; perform <=1 Salla STATUS update.

    The calling service must also have already created a signed customer-
    confirmed bank-only Salla order and staff-review case.
    """
    if not enabled:
        raise BankStatusDenied("bank_review_status_update_disabled")
    if (not user_id or not intent_id
            or type(salla_order_id) is not int or salla_order_id < 1):
        raise BankStatusDenied("invalid_bank_review_order_scope")
    if not callable(request):
        raise BankStatusDenied("trusted_salla_client_required")

    scope = await db.salla_ai_order_intents.find_one({
        "user_id": user_id, "intent_id": intent_id,
        "salla_order_id": salla_order_id,
        "pricing_mode": "salla_bank_review",
    })
    if (not isinstance(scope, dict)
            or scope.get("state") not in (
                "created_bank_review_pending",
                "created_bank_review_status_unverified",
            )):
        raise BankStatusDenied("verified_unpaid_bank_order_required")
    case = await db.salla_ai_bank_staff_reviews.find_one({
        "user_id": user_id, "intent_id": intent_id,
        "salla_order_id": salla_order_id,
    })
    if (not isinstance(case, dict)
            or case.get("review_state") != "awaiting_staff_bank_verification"
            or case.get("bank_credit_confirmed") is not False):
        raise BankStatusDenied("verified_staff_bank_case_required")

    # Only the exact original Salla order can be inspected/updated.
    order = _data(await request(
        db, user_id, "GET", f"/orders/{salla_order_id}",
    ))
    if (type(order.get("id")) is not int
            or order["id"] != salla_order_id
            or order.get("payment_method") != "bank"):
        raise BankStatusDenied("salla_bank_order_identity_unverified")
    current = order.get("status")
    if not isinstance(current, dict):
        raise BankStatusDenied("salla_order_status_missing")
    current_slug = current.get("slug")
    if current_slug not in (REVIEW_SLUG, PENDING_PAYMENT_SLUG):
        raise BankStatusDenied("bank_order_not_in_pre_review_status")
    paid = order.get("paid_amount")
    # Missing/unknown payment is NOT proof of zero received.
    from decimal import Decimal, InvalidOperation
    try:
        received = Decimal(str(paid))
    except (InvalidOperation, ValueError, TypeError):
        raise BankStatusDenied("bank_order_payment_amount_unverified") from None
    if (type(paid) is bool or not received.is_finite()
            or received != Decimal("0")):
        raise BankStatusDenied("bank_order_must_be_unpaid")

    response = await request(
        db, user_id, "GET", "/orders/statuses",
    )
    if (not isinstance(response, dict)
            or response.get("success") is False
            or not isinstance(response.get("data"), list)):
        raise BankStatusDenied("store_review_statuses_unavailable")
    exact_id = exact_active_review_status(response["data"])
    current_id = _customized_id(order)
    if current_slug == REVIEW_SLUG and current_id != exact_id:
        # Never move a merchant-customized "تم المراجعة" or other shared
        # under_review status BACKWARDS to the pending review queue.
        raise BankStatusDenied("bank_review_custom_state_conflict")
    if current_id == exact_id and current_slug == REVIEW_SLUG:
        return {
            "state": "confirmed_under_review",
            "salla_order_id": salla_order_id,
            "review_status_id": exact_id,
            "status_post_performed": False,
            "bank_credit_confirmed": False,
        }

    if (current_slug != PENDING_PAYMENT_SLUG or
            current_id != exact_active_pending_payment_status(response["data"])):
        # Only exact original payment-pending custom status can advance,
        # never a generic provider slug, unknown ID, or a completed order.
        raise BankStatusDenied("exact_bank_pending_payment_status_required")

    # Unique Mongo ledger, pre-write. Duplicate/recovery will only read.
    fence_id = hashlib.sha256(
        f"assisted-bank-status:{user_id}:{intent_id}".encode("utf-8")
    ).hexdigest()
    try:
        await db.salla_ai_bank_status_intents.insert_one({
            "_id": fence_id, "user_id": user_id,
            "intent_id": intent_id, "salla_order_id": salla_order_id,
            "review_status_id": exact_id, "state": "sending",
        })
    except Exception:
        prior = await db.salla_ai_bank_status_intents.find_one({
            "_id": fence_id,
        })
        if (not isinstance(prior, dict)
                or prior.get("user_id") != user_id
                or prior.get("intent_id") != intent_id
                or prior.get("salla_order_id") != salla_order_id
                or prior.get("review_status_id") != exact_id):
            raise BankStatusDenied("bank_status_intent_conflict")
        # Never retry an ambiguous status-changing POST.
        return {
            "state": "status_update_previously_attempted",
            "salla_order_id": salla_order_id,
            "status_post_performed": False,
            "bank_credit_confirmed": False,
        }

    try:
        result = await request(
            db, user_id, "POST", f"/orders/{salla_order_id}/status",
            json={"status_id": exact_id, "send_status_sms": False},
        )
        if not isinstance(result, dict) or result.get("success") is not True:
            raise ValueError("unconfirmed Salla status update")
    except Exception:
        return {
            "state": "status_update_needs_reconciliation",
            "salla_order_id": salla_order_id,
            "status_post_performed": True,
            "bank_credit_confirmed": False,
        }

    # The write response alone is not proof: verify exact customized ID.
    try:
        later = _data(await request(
            db, user_id, "GET", f"/orders/{salla_order_id}",
        ))
    except Exception:
        return {
            "state": "status_update_needs_reconciliation",
            "salla_order_id": salla_order_id,
            "status_post_performed": True,
            "bank_credit_confirmed": False,
        }
    if (later.get("id") != salla_order_id
            or _customized_id(later) != exact_id
            or later.get("payment_method") != "bank"):
        return {
            "state": "status_update_needs_reconciliation",
            "salla_order_id": salla_order_id,
            "status_post_performed": True,
            "bank_credit_confirmed": False,
        }
    return {
        "state": "confirmed_under_review",
        "salla_order_id": salla_order_id,
        "review_status_id": exact_id,
        "status_post_performed": True,
        "bank_credit_confirmed": False,
    }
