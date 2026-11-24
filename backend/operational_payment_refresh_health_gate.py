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


def require_salla_current(row):
    """Do not mistake a successful aggregate for a healthy Salla source."""
    if (type(row) is not dict or row.get("key") != "salla"
            or row.get("state") != "current" or row.get("code") != "salla_source_current"):
        raise RefreshHealthHold("payment_salla_source_unverified")
    return True


async def require_current_refresh_health(db, owner, state, *, at, event_proof_bound=False):
    """Run the same owner-scoped read at preparation and immediately before CAS."""
    from operational_balance_freshness import HEALTH
    from operational_balance_store import fail
    try:
        health = await db[HEALTH].find_one(
            {"_id": owner, "owner_id": owner},
            {"_id": 0, "owner_id": 1, "last_success_at": 1, "last_attempt": 1},
        )
        require_healthy_source(state, health, at=at)
        # Source freshness is separate from aggregate completion. The actual
        # owner Salla scheduled reconciliation can fail even after a successful
        # 74-second operational engine refresh. Read only this bounded source.
        from operational_source_freshness import _salla, _safe
        source_now = _stamp(at)
        salla = await _safe("salla", lambda: _salla(db, owner, source_now))
        if salla.get("state") == "current" and salla.get("code") == "salla_source_current":
            require_salla_current(salla)
        else:
            # Global daily modified-history coverage is structurally partial
            # in Salla's list API. An independently verified, signed NEW
            # payment may use a separate, explicit opt-in gate. Technical
            # errors, bad source clocks and historical orders stay blocked.
            from operational_salla_event_source_gate import (
                EventSourceHold, require_verified_event_source,
            )
            if event_proof_bound is not True:
                raise RefreshHealthHold("payment_salla_source_unverified")
            from operational_salla_schedule_health_routes import FIELDS
            import asyncio
            import pymongo
            try:
                with pymongo.timeout(1.5):
                    async with asyncio.timeout(2):
                        row = await db["salla_scheduled_reconciliation"].find_one(
                            {"_id": owner}, FIELDS, max_time_ms=1500)
                require_verified_event_source(
                    row, now=source_now, event_proof_bound=event_proof_bound)
            except EventSourceHold as exc:
                raise RefreshHealthHold(str(exc)) from None
            except (TimeoutError, ValueError, TypeError):
                raise RefreshHealthHold("payment_salla_event_source_unavailable") from None
    except RefreshHealthHold as exc:
        fail(str(exc), "نتيجة تحديث المصادر غير سليمة؛ أوقف الترحيل المالي حتى نجاح التحديث", 409)
