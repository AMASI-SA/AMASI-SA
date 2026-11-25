"""Narrow per-event source admission despite unprovable daily modified history.

A verified NEW Salla paid event can be independently attributable even though
Salla's daily existing-only *historical* discovery is structurally partial.
This module never declares historical/full source coverage, never overrides
aggregate health, and never authorizes a missing/ambiguous native payment.
Feature flag defaults OFF; activation requires Preview financial UAT.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import os

from operational_salla_schedule_diagnostics import (
    VERSION, COVERAGE_REASON, DISCOVERY_MODE,
)

FLAG = "OPERATIONAL_PAYMENT_EVENT_SCOPED_SOURCE_GATE_ENABLED"
RIYADH = ZoneInfo("Asia/Riyadh")


class EventSourceHold(ValueError):
    pass


def enabled():
    return os.environ.get(FLAG) == "1"


def _date(value):
    if type(value) is not str or len(value) != 10:
        return None
    try:
        date = datetime.fromisoformat(value).date()
        return date if date.isoformat() == value else None
    except ValueError:
        return None


def _expected_daily_slot(now):
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise EventSourceHold("payment_event_source_clock_invalid")
    day = now.astimezone(RIYADH)
    # The scheduled cycle can continue/retry until 11:00 Riyadh.
    return day.date() if day.hour >= 11 else (day - timedelta(days=1)).date()


def require_verified_event_source(row, *, now, event_proof_bound):
    """Return a tightly constrained historical-coverage exception, not 'current'.

    The callsite must have independently verified a signed tenant-bound Salla
    event and the matching current native capture/order-page proof. A partially
    verified daily job may be tolerated ONLY if *its sole* missing guarantee is
    historical modified-order coverage, with zero errors, incomplete items or
    truncation. All timestamp, payout/cutover and idempotency checks still live
    in the existing CAS writer.
    """
    if not enabled():
        raise EventSourceHold("payment_event_source_gate_disabled")
    if event_proof_bound is not True:
        raise EventSourceHold("payment_event_source_proof_unverified")
    if type(row) is not dict or row.get("schedule_version") != VERSION or row.get("status") != "partial":
        raise EventSourceHold("payment_salla_daily_status_unverified")
    result = row.get("last_result")
    if (type(result) is not dict
            or result.get("coverage_complete") is not False
            or result.get("coverage_reason") != COVERAGE_REASON
            or result.get("discovery") != DISCOVERY_MODE
            or type(result.get("errors_count")) is not int or result["errors_count"] != 0
            or type(result.get("incomplete_count")) is not int or result["incomplete_count"] != 0
            or result.get("truncated") is not False):
        raise EventSourceHold("payment_salla_daily_technical_incomplete")
    if _date(row.get("completed_slot")) != _expected_daily_slot(now):
        raise EventSourceHold("payment_salla_daily_slot_unverified")
    return {"basis": "new_verified_salla_event_only",
            "historical_coverage_complete": False,
            "permission": "first_payment_canonical_cas_only"}
