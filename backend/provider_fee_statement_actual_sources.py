"""Read accepted per-order operational fees without refreshing or posting.

An indexed existence check keeps average-only imports out of the larger
financial snapshot read. When accepted events exist, both the input and the
completed calculation must belong to the cache's exact owner revision.
"""
from datetime import datetime, timezone

from fastapi import HTTPException
from pymongo.errors import PyMongoError

from dashboard_operational_statement_fees import MAX_COHORT_ORDERS, build_operational_statement_fee_index
from operational_balance_store import STATES
from operational_financial_activity_runtime import prepare_in_thread
from operational_financial_position_state import read_financial_position_state, require_current_snapshot

HEADER_PROJECTION = {
    "_id": 1, "owner_id": 1, "revision": 1, "status": 1, "started_at": 1, "engine.as_of": 1,
}


def _header_valid(state, owner):
    return (isinstance(state, dict) and state.get("_id") == owner and state.get("owner_id") == owner
            and type(state.get("revision")) is int and state["revision"] >= 0
            and isinstance(state.get("engine", {}), dict))


def _binding(state):
    if not state:
        return None
    return tuple(state.get(key) for key in ("_id", "owner_id", "revision", "status", "started_at")) + (
        (state.get("engine") or {}).get("as_of"),)


async def load_operational_statement_fee_index(db, owner, order_numbers, *, source_state=None,
                                               accepted_as_of=None):
    """Return accepted full fees or explicit unavailable entries for a cohort.

    A missing key means no accepted per-order proof. Known read/validation/race
    failures return an unavailable key, so they cannot silently become estimates.
    An optional projected state binds this read to the executive cache revision.
    """
    numbers = sorted({str(value) for value in order_numbers if value is not None})
    if not numbers:
        return {}

    def rejected(reason):
        return {number: {"available": False, "reason_code": reason, "plan_id": None} for number in numbers}

    if len(numbers) > MAX_COHORT_ORDERS:
        return rejected("provider_statement_fee_scope_limit")
    if not isinstance(owner, str) or not owner.strip() or owner != owner.strip():
        return rejected("provider_statement_fee_source_invalid")
    accepted_as_of = accepted_as_of if accepted_as_of is not None else datetime.now(timezone.utc)
    query = {"_id": owner, "owner_id": owner}
    try:
        expected = source_state if source_state is not None else await db[STATES].find_one(query, HEADER_PROJECTION)
        if not expected:
            return {}
        if not _header_valid(expected, owner):
            return rejected("provider_statement_fee_source_invalid")
        if expected.get("status") != "active":
            return {}
        present = await db[STATES].find_one({**query, "revision": expected["revision"],
            "provider_statement_reconciliation.events": {"$exists": True, "$ne": {}}}, HEADER_PROJECTION)
        if present is None:
            # An empty event set and a concurrently changed revision are
            # different results. Check the latter before allowing estimates.
            current = await db[STATES].find_one(query, HEADER_PROJECTION)
            if (current is None or not _header_valid(current, owner)
                    or _binding(current) != _binding(expected)):
                return rejected("provider_statement_fee_source_changed")
            return {}
        if not _header_valid(present, owner) or _binding(present) != _binding(expected):
            return rejected("provider_statement_fee_source_changed")
        state = await read_financial_position_state(db, owner)
        if not _header_valid(state, owner) or _binding(state) != _binding(expected):
            return rejected("provider_statement_fee_source_changed")
        reconciliation = state.get("provider_statement_reconciliation")
        if not isinstance(reconciliation, dict) or not isinstance(reconciliation.get("events"), dict):
            return rejected("provider_statement_fee_source_invalid")
        result = await prepare_in_thread(build_operational_statement_fee_index,
            owner, state, numbers, accepted_as_of=accepted_as_of)
        await require_current_snapshot(db, owner, state)
        return result
    except HTTPException as error:
        code = error.detail.get("code") if isinstance(error.detail, dict) else None
        reason = ("provider_statement_fee_source_changed" if code == "operational_financial_snapshot_changed"
                  else "provider_statement_fee_source_invalid" if code == "operational_financial_snapshot_invalid"
                  else "provider_statement_fee_source_unavailable")
        return rejected(reason)
    except (PyMongoError, TimeoutError):
        return rejected("provider_statement_fee_source_unavailable")
    except (ValueError, TypeError, KeyError, AttributeError):
        return rejected("provider_statement_fee_source_invalid")
