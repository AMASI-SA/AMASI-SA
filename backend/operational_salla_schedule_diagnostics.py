"""Sanitized owner-only inspection of scheduled Salla reconciliation.

The financial aggregate and the Salla daily scheduler have *different*
completion contracts. This read-only model diagnoses the scheduler's last
attempt, without treating a successful operational aggregate as Salla success.

No invoice, order, customer, financial amounts, sync-log IDs, retry tokens,
exception messages or raw source rows are ever returned.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

RIYADH = ZoneInfo("Asia/Riyadh")
VERSION = "daily_10_riyadh_update_v1"
STATUSES = frozenset({
    "scheduled", "running", "failed", "partial", "completed",
    "interrupted", "incomplete",
})
COVERAGE_REASON = "provider_modified_since_filter_unavailable"
DISCOVERY_MODE = "recent_creations_and_verified_local_source_activity"
ERROR_CLASSES = frozenset({
    "TimeoutError", "RuntimeError", "CancelledError", "ValueError",
    "ConnectionError", "HTTPException", "AutoReconnect",
    "ServerSelectionTimeoutError", "NetworkTimeout", "OperationFailure",
})
MAX_COUNT = 1000000
SCHEDULE_PROJECTION = {
    "_id": 0, "schedule_version": 1, "status": 1,
    "started_at": 1, "last_success_at": 1, "completed_slot": 1,
    "attempts": 1, "last_error_type": 1,
    "last_result.errors_count": 1,
    "last_result.incomplete_count": 1,
    "last_result.truncated": 1,
    "last_result.coverage_complete": 1,
    "last_result.coverage_reason": 1,
    "last_result.discovery": 1,
}



def _stamp(value):
    if isinstance(value, datetime):
        return (value.replace(tzinfo=timezone.utc) if value.tzinfo is None
                else value.astimezone(timezone.utc)).isoformat()
    if not isinstance(value, str) or not 0 < len(value) <= 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).isoformat() if parsed.tzinfo else None
    except (ValueError, OverflowError):
        return None


def _counter(value):
    return value if type(value) is int and 0 <= value <= MAX_COUNT else None


def _day(value):
    if type(value) is not str or len(value) != 10:
        return None
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None
    return value if dt.date().isoformat() == value else None


def diagnose_salla_daily_cycle(row, *, now):
    """Single immutable, bounded, no-money scheduler health view."""
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ValueError("salla_schedule_diagnostic_clock_invalid")
    now = now.astimezone(timezone.utc)
    if not isinstance(row, dict) or not row:
        return {"state": "unknown", "reason": "schedule_record_missing"}
    version = row.get("schedule_version")
    status = row.get("status")
    if version != VERSION or status not in STATUSES:
        return {"state": "unknown", "reason": "schedule_version_or_status_invalid"}
    result = row.get("last_result")
    result = result if isinstance(result, dict) else {}
    errors = _counter(result.get("errors_count"))
    incomplete = _counter(result.get("incomplete_count"))
    truncated = result.get("truncated")
    coverage = result.get("coverage_complete")
    last = _stamp(row.get("last_success_at"))
    started = _stamp(row.get("started_at"))
    completed = _day(row.get("completed_slot"))
    attempts = _counter(row.get("attempts"))
    error_class = row.get("last_error_type")
    error_class = error_class if type(error_class) is str and error_class in ERROR_CLASSES else None
    counters_complete = (errors is not None and incomplete is not None
                         and type(truncated) is bool and type(coverage) is bool)
    successful = (status == "completed" and counters_complete
                  and errors == incomplete == 0 and truncated is False
                  and coverage is True and completed is not None and last is not None)
    unprovable_history = (
        result.get("coverage_complete") is False
        and result.get("coverage_reason") == COVERAGE_REASON
        and result.get("discovery") == DISCOVERY_MODE
    )
    if status == "partial" and unprovable_history:
        # The bounded native Salla API discovery did not prove a complete
        # modified-order cohort. Retrying is not evidence of full coverage.
        state, reason = "partial_coverage", "salla_modified_history_coverage_unprovable"
    elif status in {"failed", "partial", "interrupted", "incomplete"}:
        state, reason = "failed", "scheduled_salla_reconciliation_failed"
    elif status == "running":
        state, reason = "running", "scheduled_salla_reconciliation_running"
    elif successful:
        state, reason = "completed", "scheduled_salla_reconciliation_complete"
    else:
        state, reason = "unknown", "scheduled_salla_evidence_incomplete"
    return {
        "state": state, "reason": reason, "schedule_version": VERSION,
        "status": status, "started_at": started, "last_success_at": last,
        "completed_slot": completed, "attempts": attempts,
        "last_error_class": error_class,
        "coverage_limitation": ("provider_modified_since_filter_unavailable"
                                if unprovable_history else None),
        "coverage": {
            "errors_count": errors, "incomplete_count": incomplete,
            "truncated": truncated if type(truncated) is bool else None,
            "coverage_complete": coverage if type(coverage) is bool else None,
        },
        "eligible_for_financial_posting": False,
    }
