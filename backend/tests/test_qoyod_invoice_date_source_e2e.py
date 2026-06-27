"""Regression — `issue_date` / `due_date` in Qoyod invoice payload MUST
honour `settings.invoice_date_source` (NOT be hardcoded to completed_at).

User directive 2026-02-27: verify the end-to-end chain
    settings.invoice_date_source
      → business_rules._resolve_invoice_date
        → invoice_builder.build_invoice_payload  (issue_date, due_date)

Today's settings page exposes 4 options:
  • "trigger_status_date"  (الإعداد الحالي — تاريخ انتقال الطلب للحالة المؤهلة)
  • "completed_at"
  • "paid_at"
  • "created_at"

Every option must produce the EXACT timestamp the operator selected,
not a silent default.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from integrations.qoyod.business_rules import evaluate as evaluate_rules
from integrations.qoyod.invoice_builder import build_invoice_payload
from integrations.qoyod.dto import SalesOrderDTO, CustomerDTO, LineItemDTO


_ORDER_CREATED = datetime(2026, 6, 1, 10, 0, tzinfo=timezone.utc)
_PAID         = datetime(2026, 6, 5, 12, 0, tzinfo=timezone.utc)
_COMPLETED    = datetime(2026, 6, 10, 18, 0, tzinfo=timezone.utc)


def _make_dto() -> SalesOrderDTO:
    return SalesOrderDTO(
        order_id="O-1", order_number="123",
        order_status="completed", order_status_native="completed",
        currency="SAR",
        order_date=_ORDER_CREATED,
        completed_at=_COMPLETED,
        paid_at=_PAID,
        customer=CustomerDTO(name="X", phone="+966500000000"),
        items=[LineItemDTO(sku="A", name="x", quantity=1, unit_price=10)],
    )


def _build_payload_for_source(source: str) -> dict:
    """Run the full rules → invoice builder chain for a given setting."""
    dto = _make_dto()
    settings = {
        "invoice_trigger_statuses": ["completed"],
        "trigger_once_only": True,
        "invoice_date_source": source,
    }
    decision = evaluate_rules(dto, settings)
    assert decision.eligible, f"DTO must be eligible for source={source!r}"
    payload = build_invoice_payload(
        dto_dict=dto.model_dump(),
        qoyod_customer_id="C-1",
        product_resolutions=[{"sku": "A", "qoyod_product_id": "P-1"}],
        invoice_date=decision.invoice_date,
        settings=settings,
    )
    return payload["invoice"]


@pytest.mark.parametrize("source,expected_date,note", [
    ("trigger_status_date", _COMPLETED.date().isoformat(),
     "current production setting — completed status → completed_at"),
    ("completed_at",        _COMPLETED.date().isoformat(),
     "explicit completed_at"),
    ("paid_at",             _PAID.date().isoformat(),
     "explicit paid_at"),
    ("created_at",          _ORDER_CREATED.date().isoformat(),
     "explicit Salla order_date"),
])
def test_invoice_payload_issue_and_due_date_follow_setting(
    source, expected_date, note,
):
    """For each `invoice_date_source` setting, BOTH issue_date and
    due_date in the Qoyod payload must equal the expected timestamp's
    date string. No silent fallback to completed_at."""
    inv = _build_payload_for_source(source)
    assert inv["issue_date"] == expected_date, (
        f"source={source!r} ({note}): issue_date={inv['issue_date']!r} "
        f"expected={expected_date!r}")
    assert inv["due_date"] == expected_date, (
        f"source={source!r} ({note}): due_date={inv['due_date']!r} "
        f"expected={expected_date!r}")


def test_created_at_does_NOT_use_completed_at():
    """The user's exact concern: switching the setting to 'created_at'
    must produce Salla's order_date, NOT the completion timestamp."""
    inv = _build_payload_for_source("created_at")
    assert inv["issue_date"] == _ORDER_CREATED.date().isoformat()
    assert inv["issue_date"] != _COMPLETED.date().isoformat()
