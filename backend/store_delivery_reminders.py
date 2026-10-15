"""Pure reminder rules for Amasi Delivery commitments.

No scheduler or notification transport is coupled here. The scheduler asks this
module what reminder is due, making the timing policy deterministic and testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from store_delivery_domain import normalize_text


@dataclass(frozen=True)
class ReminderDecision:
    code: str
    due: bool
    overdue: bool
    minutes_to_deadline: int | None
    message: str


def _deadline(instruction: dict[str, Any], *, tzinfo) -> datetime | None:
    date_text = normalize_text(instruction.get("delivery_date"))
    time_text = normalize_text(instruction.get("delivery_time"))
    if not date_text:
        return None
    if not time_text:
        time_text = "23:59"
    try:
        local = datetime.strptime(f"{date_text} {time_text}", "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    return local.replace(tzinfo=tzinfo)


def reminder_decision(
    instruction: dict[str, Any],
    *,
    now: datetime,
    delivery_status: str,
    last_reminder_code: str | None = None,
) -> ReminderDecision:
    """Return at most one actionable reminder for an active instruction."""
    if normalize_text(instruction.get("status")) not in {"", "active"}:
        return ReminderDecision("none", False, False, None, "")
    if normalize_text(delivery_status) == "delivered":
        return ReminderDecision("none", False, False, None, "")

    deadline = _deadline(instruction, tzinfo=now.tzinfo)
    order_id = normalize_text(instruction.get("order_id"))
    if deadline is None:
        if normalize_text(instruction.get("priority")) == "urgent" and last_reminder_code != "urgent_open":
            return ReminderDecision(
                "urgent_open",
                True,
                False,
                None,
                f"لديك تعليمات عاجلة للطلب {order_id}. افتح الطلب وراجع تعليمات خدمة العملاء.",
            )
        return ReminderDecision("none", False, False, None, "")

    seconds = (deadline - now).total_seconds()
    minutes = int(seconds // 60)
    if seconds < 0:
        code = "overdue"
        if last_reminder_code == code:
            return ReminderDecision(code, False, True, minutes, "")
        return ReminderDecision(
            code,
            True,
            True,
            minutes,
            f"تأخر موعد توصيل الطلب {order_id}. كان الموعد {deadline.strftime('%H:%M')}",
        )

    windows = (
        (10, "due_10m"),
        (30, "due_30m"),
        (60, "due_1h"),
        (120, "due_2h"),
    )
    for threshold, code in windows:
        if 0 <= minutes <= threshold:
            if last_reminder_code == code:
                return ReminderDecision(code, False, False, minutes, "")
            return ReminderDecision(
                code,
                True,
                False,
                minutes,
                f"الطلب {order_id} يجب توصيله خلال {max(minutes, 0)} دقيقة، قبل {deadline.strftime('%H:%M')}.",
            )

    # Previous-evening reminder for an early scheduled delivery.
    tomorrow = (now + timedelta(days=1)).date()
    if deadline.date() == tomorrow and deadline.hour < 12 and now.hour >= 18:
        code = "tomorrow_morning"
        if last_reminder_code != code:
            return ReminderDecision(
                code,
                True,
                False,
                minutes,
                f"غدًا لديك الطلب {order_id} الساعة {deadline.strftime('%H:%M')}.",
            )

    return ReminderDecision("none", False, False, minutes, "")


__all__ = ["ReminderDecision", "reminder_decision"]
