"""Fail-closed transition gate between the legacy and Mezan 2 ledgers.

The gate is persisted on the same permanent ``mz2_atomic_owners`` row that
serializes every owner transaction.  A transition and a compliant financial
writer therefore cannot pass one another between a read and a later write.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import HTTPException


COLLECTION = "mz2_atomic_owners"
CONTRACT_REVISION = 1
STATES = frozenset({"legacy_active", "transition_blocked", "v2_active"})
Writer = Literal["legacy", "v2"]

STATE_FIELD = "ledger_backend_state"
REVISION_FIELD = "ledger_backend_revision"
CONTRACT_FIELD = "ledger_backend_contract_revision"
ACTIVATION_FIELD = "ledger_backend_activation_ref"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error(code: str, message: str, *, status: int = 423, **details: Any) -> None:
    raise HTTPException(status, detail={"code": code, "message": message, **details})


def _real_database(db: Any) -> bool:
    raw = getattr(db, "_db", db)
    return getattr(raw, "client", None) is not None


def _collection(db: Any):
    try:
        return db[COLLECTION]
    except (AttributeError, KeyError, TypeError):
        return getattr(db, COLLECTION, None)


def _validate(owner: str, row: dict[str, Any] | None) -> dict[str, Any]:
    # A coordination row predates this contract and may contain only the
    # atomic revision or write-pause fields.  Absence of *all* transition
    # fields is the one supported legacy default.  Partial state fails closed.
    fields = {STATE_FIELD, REVISION_FIELD, CONTRACT_FIELD, ACTIVATION_FIELD}
    present = fields & set(row or {})
    if row is None or not present:
        return {
            "owner_id": owner,
            "state": "legacy_active",
            "state_revision": 0,
            "contract_revision": CONTRACT_REVISION,
            "activation_ref": None,
        }
    state = row.get(STATE_FIELD)
    revision = row.get(REVISION_FIELD)
    contract = row.get(CONTRACT_FIELD)
    activation_ref = row.get(ACTIVATION_FIELD)
    if (
        row.get("_id") != owner
        or present != fields
        or state not in STATES
        or type(revision) is not int
        or revision < 1
        or contract != CONTRACT_REVISION
        or (state == "v2_active" and not str(activation_ref or "").strip())
    ):
        _error(
            "accounting_transition_contract_invalid",
            "حالة انتقال الكاتب المحاسبي غير معروفة أو بإصدار غير متوافق",
            state=state,
            state_revision=revision,
            contract_revision=contract,
        )
    return {
        "owner_id": owner,
        "state": state,
        "state_revision": revision,
        "contract_revision": contract,
        "activation_ref": activation_ref,
        "updated_at": row.get("ledger_backend_updated_at"),
        "updated_by": row.get("ledger_backend_updated_by"),
    }


async def transition_state(db: Any, owner: str, *, mongo_session: Any = None) -> dict[str, Any]:
    collection = _collection(db)
    # Lightweight unit doubles that do not expose a database are deliberately
    # unmanaged. Real Mongo deployments always expose ``client`` and therefore
    # cannot bypass the persisted transition contract.
    if collection is None:
        if _real_database(db):
            _error("accounting_transition_store_missing", "مخزن انتقال الكاتب غير متاح")
        return _validate(owner, None)
    kwargs = {"session": mongo_session} if mongo_session is not None else {}
    row = await collection.find_one({"_id": owner}, **kwargs)
    return _validate(owner, row)


async def assert_writer_allowed(
    db: Any,
    owner: str,
    writer: Writer,
    *,
    mongo_session: Any = None,
) -> dict[str, Any]:
    state = await transition_state(db, owner, mongo_session=mongo_session)
    current = state["state"]
    if current == "transition_blocked":
        _error("accounting_transition_blocked", "الكتابة المحاسبية متوقفة أثناء الانتقال")
    if current == "legacy_active" and writer != "legacy":
        _error("accounting_v2_not_active", "كاتب ميزان 2 غير مفعّل")
    if current == "v2_active" and writer != "v2":
        _error("accounting_legacy_writer_disabled", "الكاتب القديم مقفل بعد تفعيل ميزان 2")
    return state


async def advance_transition(
    db: Any,
    *,
    owner: str,
    actor_id: str,
    target: Literal["transition_blocked", "v2_active"],
    expected_revision: int,
    activation_ref: str,
) -> dict[str, Any]:
    """Advance one irreversible transition step inside the owner transaction."""
    current = await transition_state(db, owner)
    if current["state_revision"] != expected_revision:
        _error(
            "accounting_transition_revision_mismatch",
            "تغيرت حالة الانتقال؛ حدّث الصفحة قبل المتابعة",
            status=409,
            expected_revision=expected_revision,
            actual_revision=current["state_revision"],
        )
    if (current["state"], target) not in {
        ("legacy_active", "transition_blocked"),
        ("transition_blocked", "v2_active"),
    }:
        _error(
            "accounting_transition_invalid",
            "انتقال حالة الكاتب غير مسموح",
            status=409,
            current=current["state"],
            target=target,
        )
    reference = str(activation_ref or "").strip()
    if target == "v2_active" and len(reference) < 3:
        _error(
            "accounting_transition_activation_ref_required",
            "مرجع تفعيل ميزان 2 مطلوب",
            status=422,
        )
    document = {
        STATE_FIELD: target,
        REVISION_FIELD: expected_revision + 1,
        CONTRACT_FIELD: CONTRACT_REVISION,
        ACTIVATION_FIELD: reference or None,
        "ledger_backend_updated_at": _now(),
        "ledger_backend_updated_by": actor_id,
    }
    result = await db[COLLECTION].update_one(
        {"_id": owner},
        {"$set": document},
        upsert=current["state_revision"] == 0,
    )
    if not (getattr(result, "matched_count", 0) or getattr(result, "upserted_id", None)):
        _error(
            "accounting_transition_revision_mismatch",
            "تغيرت حالة الانتقال؛ حدّث الصفحة قبل المتابعة",
            status=409,
        )
    return _validate(owner, {"_id": owner, **document})
