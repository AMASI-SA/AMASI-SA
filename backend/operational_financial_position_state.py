"""Read one financial snapshot without materializing historical audit pages.

This is a report-only input, never the state used by a mutation or an audit
reader. Financial packets retain their full owner/hash/length/marker checks.
Audit metadata is checked here, but historical audit contents are deliberately
left to the unchanged full store reader.
"""
from operational_balance_archive import (
    ArchiveError, MARKER, _audit_metadata, unpack_financial_snapshot, unpack_issues,
)
from operational_balance_store import STATES, fail, initial
from operational_financial_activity_runtime import preparation_checkpoint, prepare_in_thread


def _report_state(stored, owner):
    preparation_checkpoint()
    if not isinstance(stored, dict) or stored.get("owner_id") != owner or stored.get("_id") != owner:
        raise ArchiveError("financial report owner mismatch")
    if not isinstance(stored.get("engine", {}), dict):
        raise ArchiveError("invalid financial report engine")
    if type(stored.get("revision")) is not int or stored["revision"] < 0:
        raise ArchiveError("invalid financial report revision")
    state = unpack_issues(unpack_financial_snapshot(stored))
    if MARKER in state:
        meta, _ = _audit_metadata(state)
        count, seen = 0, set()
        for ref in meta["pages"]:
            preparation_checkpoint()
            if (not isinstance(ref, dict) or set(ref) != {"id", "count"}
                    or not isinstance(ref["id"], str) or ref["id"] in seen
                    or type(ref["count"]) is not int or ref["count"] <= 0):
                raise ArchiveError("invalid reconciliation audit reference")
            seen.add(ref["id"])
            count += ref["count"]
        if count != meta["count"]:
            raise ArchiveError("incomplete reconciliation audit manifest")
    elif not isinstance(state.get("engine", {}).get("audit", []), list):
        raise ArchiveError("missing reconciliation audit manifest")
    # Do not hand a fabricated empty audit to a normal store/mutation reader.
    # This separate report value has no audit or idempotency capabilities.
    result = {key: value for key, value in state.items() if key not in {"audit", "requests", MARKER}}
    result["engine"] = {key: value for key, value in state.get("engine", {}).items() if key != "audit"}
    preparation_checkpoint()
    return result


async def read_financial_position_state(db, owner):
    stored = await db[STATES].find_one({"_id": owner, "owner_id": owner})
    try:
        return await prepare_in_thread(_report_state, stored if stored is not None else initial(owner), owner)
    except ArchiveError:
        fail("operational_financial_snapshot_invalid", "تعذر التحقق من لقطة التقرير المالي المحفوظة", 503)


def require_snapshot_binding(state, *, revision, as_of, started_at):
    """A forecast can replace a summary only for the exact saved owner snapshot."""
    if revision is None or as_of is None or started_at is None:
        fail("operational_financial_snapshot_binding_required", "يلزم تحديد لقطة الملخص لقراءة توقعاته", 422)
    if (type(state.get("revision")) is not int or state["revision"] != revision or state.get("engine", {}).get("as_of") != as_of
            or state.get("started_at") != started_at):
        fail("operational_financial_snapshot_changed", "تغيرت اللقطة المحفوظة؛ أعد قراءة الملخص قبل التوقعات", 409)


async def require_current_snapshot(db, owner, state):
    """Reject a forecast if the aggregate changes while its source reads run."""
    current = await db[STATES].find_one({"_id": owner, "owner_id": owner}, {
        "_id": 1, "owner_id": 1, "revision": 1, "started_at": 1, "status": 1, "engine.as_of": 1,
    })
    if (current is None or current.get("owner_id") != owner or current.get("_id") != owner
            or current.get("status") != state.get("status")):
        fail("operational_financial_snapshot_changed", "تغيرت اللقطة المحفوظة؛ أعد قراءة الملخص قبل التوقعات", 409)
    require_snapshot_binding(current, revision=state["revision"],
        as_of=state.get("engine", {}).get("as_of"), started_at=state.get("started_at"))
