from datetime import datetime
from zoneinfo import ZoneInfo

from store_delivery_reminders import reminder_decision

RIYADH = ZoneInfo("Asia/Riyadh")


def test_due_within_hour():
    now = datetime(2026, 8, 20, 19, 0, tzinfo=RIYADH)
    decision = reminder_decision(
        {
            "status": "active",
            "order_id": "2339",
            "delivery_date": "2026-08-20",
            "delivery_time": "20:00",
        },
        now=now,
        delivery_status="out_for_delivery",
    )
    assert decision.due is True
    assert decision.code == "due_1h"
    assert decision.overdue is False


def test_overdue_once():
    now = datetime(2026, 8, 20, 20, 15, tzinfo=RIYADH)
    instruction = {
        "status": "active",
        "order_id": "2339",
        "delivery_date": "2026-08-20",
        "delivery_time": "20:00",
    }
    first = reminder_decision(instruction, now=now, delivery_status="out_for_delivery")
    again = reminder_decision(
        instruction,
        now=now,
        delivery_status="out_for_delivery",
        last_reminder_code="overdue",
    )
    assert first.code == "overdue" and first.due is True and first.overdue is True
    assert again.due is False


def test_tomorrow_early_delivery_previous_evening():
    now = datetime(2026, 8, 19, 19, 0, tzinfo=RIYADH)
    decision = reminder_decision(
        {
            "status": "active",
            "order_id": "9001",
            "delivery_date": "2026-08-20",
            "delivery_time": "07:00",
        },
        now=now,
        delivery_status="assigned",
    )
    assert decision.code == "tomorrow_morning"
    assert decision.due is True


def test_delivered_suppresses_reminders():
    now = datetime(2026, 8, 20, 19, 55, tzinfo=RIYADH)
    decision = reminder_decision(
        {
            "status": "active",
            "order_id": "2339",
            "delivery_date": "2026-08-20",
            "delivery_time": "20:00",
        },
        now=now,
        delivery_status="delivered",
    )
    assert decision.due is False
    assert decision.code == "none"
