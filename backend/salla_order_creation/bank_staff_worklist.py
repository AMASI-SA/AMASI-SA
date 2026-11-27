"""Read-only, redacted assisted-bank review worklist for authorized staff.

Every document is selected by the configured Salla merchant OWNER and the
unpaid/pending staff-review status. The bot does not see this endpoint.
Returns message/attachment references to the *existing* private Chatwoot
thread; the original proof stays there. No IBAN, payer identity, transfer
reference, attachment URL or payment/GL mutation.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

_INTENT = re.compile(r"[a-zA-Z0-9_-]{8,100}\Z", re.ASCII)


class BankWorklistDenied(ValueError):
    pass


def may_read_bank_review(user: dict | None, staff_permitted: Callable | None) -> bool:
    """The server supplies its canonical effective orders.manage RBAC check.

    This function does not trust a caller-supplied permissions JSON field.
    The owner is accepted only when backed by the authenticated dependency.
    """
    if not isinstance(user, dict) or not user.get("id"):
        return False
    if str(user.get("role") or "").lower() == "owner":
        return True
    if not callable(staff_permitted):
        return False
    try:
        return staff_permitted(user) is True
    except Exception:
        return False


def _positive(value: Any) -> bool:
    return type(value) is int and 0 < value < 2**63


def _source(row: dict) -> dict:
    if type(row) is not dict:
        raise BankWorklistDenied("invalid_bank_review_record")
    intent = row.get("intent_id")
    order_id = row.get("salla_order_id")
    kind = row.get("source_kind", "image")
    attachment = row.get("source_attachment_id")
    message = row.get("source_message_id")
    conversation = row.get("conversation_id")
    inbox = row.get("inbox_id")
    if (type(intent) is not str or not _INTENT.fullmatch(intent)
            or not _positive(order_id)
            or kind not in ("image", "file", "text")
            or type(attachment) is not int
            or (kind == "text" and attachment != 0)
            or (kind != "text" and not _positive(attachment))
            or not _positive(message)
            or not str(conversation or "").isdigit()
            or not str(inbox or "").isdigit()
            or row.get("review_state") != "awaiting_staff_bank_verification"
            or row.get("bank_credit_confirmed") is not False
            or row.get("paid") is not False
            or row.get("fulfillment_eligible") is not False):
        raise BankWorklistDenied("bank_review_record_not_pending_or_untrusted")
    try:
        amount = Decimal(str(row["receipt_amount_sar"]))
    except (InvalidOperation, KeyError, ValueError, TypeError):
        raise BankWorklistDenied("bank_review_amount_untrusted") from None
    if not (amount.is_finite() and 0 < amount <= 100_000_000
            and amount.as_tuple().exponent >= -2):
        raise BankWorklistDenied("bank_review_amount_untrusted")
    reference = row.get("salla_reference_id")
    if reference is not None and not _positive(reference):
        raise BankWorklistDenied("bank_review_reference_untrusted")
    created_at = row.get("created_at")
    if not isinstance(created_at, datetime):
        raise BankWorklistDenied("bank_review_created_at_untrusted")
    # Motor's default tz_aware=False returns Mongo UTC BSON datetimes naive.
    # Mongo dates are UTC by contract; restore UTC only for presentation.
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=timezone.utc)
    return {
        "intent_id": intent,
        "salla_order_id": order_id,
        "salla_reference_id": reference,
        "receipt_amount_sar": f"{amount:.2f}",
        "source_kind": kind,
        "source_message_id": message,
        "source_attachment_id": attachment,
        "conversation_id": str(conversation),
        "inbox_id": str(inbox),
        "created_at": created_at.isoformat(),
        "review_state": "awaiting_staff_bank_verification",
        "staff_review_required": True,
        "bank_credit_confirmed": False,
        "paid": False,
        "fulfillment_eligible": False,
    }


async def list_pending_bank_reviews(
    db: Any, *, owner_id: str, limit: int = 20, offset: int = 0,
) -> dict:
    if (type(owner_id) is not str or not owner_id
            or type(limit) is not int or not 1 <= limit <= 25
            or type(offset) is not int or not 0 <= offset <= 1000):
        raise BankWorklistDenied("invalid_bank_worklist_scope_or_page")
    query = {
        "user_id": owner_id,
        "review_state": "awaiting_staff_bank_verification",
        "bank_credit_confirmed": False,
        "paid": False,
        "fulfillment_eligible": False,
    }
    # Bounded and paginated, no raw receipt, bank identifiers or secrets.
    cursor = (
        db.salla_ai_bank_staff_reviews.find(query)
        .sort([("created_at", -1), ("_id", -1)])
        .skip(offset)
        .limit(limit)
    )
    values = await cursor.to_list(length=limit)
    if not isinstance(values, list) or len(values) > limit:
        raise BankWorklistDenied("bank_worklist_read_unavailable")
    return {
        "items": [_source(row) for row in values],
        "offset": offset,
        "limit": limit,
        "next_offset": offset + len(values) if len(values) == limit else None,
        "read_only": True,
        "review_owner": "customer_service",
    }
