"""Operational refresh health admission for direct financial event posting.

A recently saved aggregate is NOT sufficient when a newer full refresh has
timed out or failed. This pure gate checks the independently persisted,
owner-scoped health receipt. It fails closed on absence/unknown metadata,
active attempts and stale or failed source acquisition. No writes performed.
"""
from __future__ import annotations
from datetime import datetime, timezone


class RefreshHealthHold(ValueError):
    pass


def _stamp(value):
    if not isinstance(value, str):
        raise RefreshHealthHold("payment_refresh_health_timestamp_invalid")
    try:
        out = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if out.tzinfo is None or out.utcoffset() is None:
            raise ValueError()
        return out.astimezone(timezone.utc)
    except ValueError:
        raise RefreshHealthHold("payment_refresh_health_timestamp_invalid") from None


def require_healthy_source(state, health, *, at):
    """Return True only when health confirms the SAME saved source snapshot."""
    if not isinstance(health, dict) or not isinstance(state, dict):
        raise RefreshHealthHold("payment_refresh_health_missing")
    if health.get("owner_id") != state.get("owner_id"):
        raise RefreshHealthHold("payment_refresh_health_owner_conflict")
    source = (state.get("engine") or {}).get("as_of")
    current = _stamp(at)
    saved = _stamp(source)
    if saved > current:
        raise RefreshHealthHold("payment_refresh_future_snapshot")
    success = _stamp(health.get("last_success_at"))
    if success != saved:
        raise RefreshHealthHold("payment_refresh_success_receipt_mismatch")
    attempt = health.get("last_attempt")
    if not isinstance(attempt, dict):
        raise RefreshHealthHold("payment_refresh_attempt_unknown")
    outcome = attempt.get("outcome")
    started = _stamp(attempt.get("started_at"))
    if started > current:
        raise RefreshHealthHold("payment_refresh_attempt_future")
    if outcome == "running":
        raise RefreshHealthHold("payment_refresh_attempt_running")
    if outcome not in {"success", "failed", "unknown"}:
        raise RefreshHealthHold("payment_refresh_attempt_unknown")
    if outcome in {"failed", "unknown"} and started >= saved:
        raise RefreshHealthHold("payment_refresh_newer_attempt_failed")
    if outcome == "success" and started > saved:
        # A success event later than the claimed canonical as_of is not a
        # stable posted snapshot. Require the definitive aggregate receipt.
        raise RefreshHealthHold("payment_refresh_success_receipt_mismatch")
    return True
