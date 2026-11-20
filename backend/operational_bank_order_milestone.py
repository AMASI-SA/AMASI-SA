"""Bank credits require evidence that the order actually reached preparation in Salla."""
from operational_payment_evidence import _instant
from salla_payment_collection import provider_order_page_current

PROOF_FIELD = "salla_bank_in_progress_proof"
HISTORY_MISSING = "operational_order_bank_in_progress_history_missing"
HISTORY_UNPROVEN = "operational_order_bank_in_progress_unproven"
SOURCE_INVALID = "operational_order_bank_status_source_incomplete"

_STATUS = {
    "in_progress": "in_progress", "processing": "in_progress", "قيد التنفيذ": "in_progress",
    "completed": "completed", "تم التنفيذ": "completed",
    "delivering": "delivering", "out_for_delivery": "delivering", "جاري التوصيل": "delivering",
    "قيد التوصيل": "delivering", "delivered": "delivered", "تم التوصيل": "delivered",
}
_LATER = {"completed", "delivering", "delivered"}
_HISTORY_SOURCE = "salla_order_histories"


def _state(value):
    if isinstance(value, dict):
        effective = value.get("customized") or value
        if not isinstance(effective, dict):
            return None
        states = {_state(effective[key]) for key in ("slug", "code", "name") if effective.get(key)}
        return states.pop() if len(states) == 1 else None
    return _STATUS.get(str(value or "").strip().casefold())


def _source(raw, source, owner, number, identity, as_of):
    if (any(row.get(key) is True for row in (raw, source) for key in ("is_cancelled", "cancelled"))
            or not provider_order_page_current(source, raw, owner=owner, order_number=number,
                                               provider_order_id=identity, customer_balance_verified=True)):
        raise ValueError(SOURCE_INVALID)
    version = _instant(raw.get("updated_at") or raw.get("date_updated"))
    created = _instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
    status_version = source.get("last_salla_direct_status_source_updated_at")
    snapshot_version = (source.get("g47_salla_snapshot") or {}).get("source_updated_at")
    if (not created <= version <= _instant(as_of) or status_version and _instant(status_version) > version
            or snapshot_version is not None and _instant(snapshot_version) != version):
        raise ValueError(SOURCE_INVALID)
    status = _state(raw.get("status"))
    if status and any(_state(value) != status for value in (
            raw.get("status_slug"), source.get("order_status_slug"), source.get("order_status")) if value):
        raise ValueError(SOURCE_INVALID)
    return status, created, version


def reached_in_progress(raw, source, *, owner, order_number, provider_order_id, as_of, require_history=False):
    """Current preparation proves itself. Later states require actual, order-bound history."""
    status, created, version = _source(raw, source, owner, order_number, provider_order_id, as_of)
    if status == "in_progress" and not require_history:
        return True
    if status != "in_progress" and status not in _LATER:
        return False
    proof = source.get(PROOF_FIELD)
    if proof is None:
        raise ValueError(HISTORY_MISSING)
    if (not isinstance(proof, dict) or proof.get("source") != _HISTORY_SOURCE
            or proof.get("owner") != owner or proof.get("order_id") != provider_order_id
            or proof.get("order_number") != order_number or _instant(proof.get("order_created_at")) != created):
        raise ValueError(HISTORY_UNPROVEN)
    observed = _instant(proof.get("source_updated_at"))
    if not created <= observed <= version:
        raise ValueError(HISTORY_UNPROVEN)
    if proof.get("reached") is False and observed < version:
        raise ValueError(HISTORY_MISSING)
    if (proof.get("reached") is not True or not proof.get("event_id")
            or not created <= _instant(proof.get("at")) <= observed):
        raise ValueError(HISTORY_UNPROVEN)
    return True


def _progress_event(row):
    kinds = {row[key] for key in ("type", "event") if row.get(key)}
    status_kinds = {"status_changed", "status_updated", "order.status.updated"}
    if kinds - (status_kinds | {"activity"}):
        return False
    if not (kinds & status_kinds) and row.get("note") not in (None, ""):
        return False
    targets = [row[key] for key in ("new_status", "to_status") if row.get(key) is not None]
    if targets:
        return all(_state(target) == "in_progress" for target in targets)
    if kinds & status_kinds:
        labels = [row[key] for key in ("customized", "status") if row.get(key)]
        return bool(labels) and all(_state(value) == "in_progress" for value in labels)
    # Native activity rows can carry arbitrary free-text notes. Only the dedicated
    # Salla status shape with an empty note and agreeing labels proves preparation.
    return (row.get("type") == "activity" and row.get("note") == ""
            and _state(row.get("customized")) == _state(row.get("status")) == "in_progress")


def history_proof(raw, source, rows, *, owner, order_number, provider_order_id, as_of):
    """Summarize a complete response fetched from this owner's internal order endpoint."""
    status, created, version = _source(raw, source, owner, order_number, provider_order_id, as_of)
    if status != "in_progress" and status not in _LATER:
        raise ValueError(SOURCE_INVALID)
    observations = []
    for row in rows:
        if (not isinstance(row, dict) or any(row.get(key) is not None and str(row[key]) != expected
                for key, expected in (("order_id", provider_order_id), ("order_number", order_number),
                                      ("reference_id", order_number)))):
            raise ValueError(HISTORY_UNPROVEN)
        if _progress_event(row):
            event = str(row.get("id") or "").strip()
            at = _instant(row.get("created_at") or row.get("date"))
            if not event or not created <= at <= version or row.get("date") and _instant(row["date"]) != at:
                raise ValueError(HISTORY_UNPROVEN)
            observations.append((at, event))
    proof = {"source": _HISTORY_SOURCE, "owner": owner, "order_id": provider_order_id,
             "order_number": order_number, "order_created_at": created.isoformat(),
             "source_updated_at": version.isoformat(), "reached": bool(observations)}
    if observations:
        at, event = min(observations)
        proof.update(at=at.isoformat(), event_id=event)
    return proof

