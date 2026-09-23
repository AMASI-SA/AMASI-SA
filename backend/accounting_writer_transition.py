"""Fail-closed transition gate between the legacy and Mezan 2 ledgers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import HTTPException


COLLECTION = "mz2_writer_transition"
CONTRACT_REVISION = 1
STATES = frozenset({"legacy_active", "transition_blocked", "v2_active"})
Writer = Literal["legacy", "v2"]


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
    if row is None:
        return {
            "owner_id": owner,
            "state": "legacy_active",
            "state_revision": 0,
            "contract_revision": CONTRACT_REVISION,
            "activation_ref": None,
        }
    state = row.get("state")
    revision = row.get("state_revision")
    contract = row.get("contract_revision")
    activation_ref = row.get("activation_ref")
    if (
        row.get("_id") != owner
        or row.get("user_id") != owner
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
        "updated_at": row.get("updated_at"),
        "updated_by": row.get("updated_by"),
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
        "user_id": owner,
        "state": target,
        "state_revision": expected_revision + 1,
        "contract_revision": CONTRACT_REVISION,
        "activation_ref": reference or None,
        "updated_at": _now(),
        "updated_by": actor_id,
    }
    await db[COLLECTION].update_one({"_id": owner}, {"$set": document}, upsert=True)
    return _validate(owner, {"_id": owner, **document})
