"""Current Salla order states and dated native history for bank receipt admission."""
from operational_payment_evidence import _instant

PROOF_FIELD = "salla_bank_in_progress_proof"
HISTORY_MISSING = "operational_order_bank_in_progress_history_missing"
HISTORY_UNPROVEN = "operational_order_bank_in_progress_unproven"
SOURCE_INVALID = "operational_order_bank_status_source_incomplete"

_QUALIFYING = {"in_progress", "completed", "delivering", "delivered"}
_STATUS = {
    "in_progress": "in_progress", "processing": "in_progress", "under_processing": "in_progress",
    "قيد التنفيذ": "in_progress", "جاري التنفيذ": "in_progress",
    "completed": "completed", "تم التنفيذ": "completed",
    "delivering": "delivering", "out_for_delivery": "delivering", "in_delivery": "delivering",
    "shipping": "delivering", "جاري التوصيل": "delivering", "جارٍ التوصيل": "delivering",
    "قيد التوصيل": "delivering", "delivered": "delivered", "تم التوصيل": "delivered",
    "cancelled": "cancelled", "canceled": "cancelled", "ملغي": "cancelled", "ملغى": "cancelled",
}
_HISTORY_SOURCE = "salla_order_histories"


def _state(value):
    if isinstance(value, dict):
        effective = value.get("customized") or value
        if not isinstance(effective, dict):
            return None
        states = {_state(effective[key]) for key in ("slug", "code", "name", "name_ar", "label", "title")
                  if effective.get(key)}
        return states.pop() if len(states) == 1 else None
    label = " ".join(str(value or "").strip().casefold().replace("_", " ").split())
    return _STATUS.get(label) or _STATUS.get(label.replace(" ", "_"))


def _time(value, error):
    try:
        return _instant(value)
    except ValueError:
        raise ValueError(error) from None


def _source(raw, source, owner, number, identity, as_of):
    # A genuine cancellation is a valid, nonqualifying order status. Authenticate
    # the snapshot before returning it, including on the cancellation path.
    if (not isinstance(source, dict) or not isinstance(raw, dict) or not owner or not number or not identity
            or source.get("user_id") != owner
            or str(raw.get("id") or "") != identity
            or str(raw.get("reference_id") or raw.get("order_number") or "") != number
            or any(key in row and str(row[key]) != expected for row, key, expected in (
                (raw, "order_number", number), (source, "order_number", number),
                (source, "order_id", identity), (source, "salla_order_id", identity)))):
        raise ValueError(SOURCE_INVALID)
    version = _time(raw.get("updated_at") or raw.get("date_updated"), SOURCE_INVALID)
    created = _time(raw.get("date") or raw.get("created_at") or raw.get("order_date"), SOURCE_INVALID)
    watermark = source.get("g47_salla_snapshot", {})
    status_version = source.get("last_salla_direct_status_source_updated_at")
    if (not isinstance(watermark, dict) or watermark.get("requires_authoritative_refresh")
            or not created <= version <= _time(as_of, SOURCE_INVALID)
            or status_version is not None and _time(status_version, SOURCE_INVALID) > version):
        raise ValueError(SOURCE_INVALID)
    snapshot_version = watermark.get("source_updated_at")
    if ((snapshot_version is not None and _time(snapshot_version, SOURCE_INVALID) != version)
            or (watermark.get("component_pending") and snapshot_version is None)):
        raise ValueError(SOURCE_INVALID)
    native = raw.get("status")
    status = _state(native)
    # Intake also projects the generic Salla parent name/slug. Accept its
    # exact copies when a customized status exists; only the custom state gates.
    parent = native if isinstance(native, dict) and native.get("customized") else {}
    projections = ((raw.get("status_slug"), "slug"), (source.get("order_status_slug"), "slug"),
                   (source.get("order_status"), "name"))
    if (any(_state(value) != status and value != parent.get(key) for value, key in projections if value)
            or status != "cancelled" and (watermark.get("cancelled") or any(
                row.get(key) is True for row in (raw, source) for key in ("is_cancelled", "cancelled")))):
        raise ValueError(SOURCE_INVALID)
    return status, created, version


def bank_status_evidence(raw, source, *, owner, order_number, provider_order_id, as_of, require_history=False):
    """Return the qualifying current status, or the first proven transition for cutover.

    A current source version is a status observation, not a first-transition date.
    Historical evidence cannot qualify a currently nonqualifying, unposted order.
    """
    status, created, version = _source(raw, source, owner, order_number, provider_order_id, as_of)
    if status not in _QUALIFYING:
        return None
    if not require_history:
        return {"source": "salla_order_status", "status": status, "observed_at": version.isoformat()}
    proof = source.get(PROOF_FIELD)
    if proof is None:
        raise ValueError(HISTORY_MISSING)
    if (not isinstance(proof, dict) or proof.get("source") != _HISTORY_SOURCE
            or proof.get("owner") != owner or proof.get("order_id") != provider_order_id
            or proof.get("order_number") != order_number
            or _time(proof.get("order_created_at"), HISTORY_UNPROVEN) != created):
        raise ValueError(HISTORY_UNPROVEN)
    observed = _time(proof.get("source_updated_at"), HISTORY_UNPROVEN)
    if not created <= observed <= version:
        raise ValueError(HISTORY_UNPROVEN)
    # Legacy history checked only in_progress. It cannot date the first of all
    # four statuses, e.g. a completed order subsequently reopened in_progress.
    if "policy_version" not in proof or (proof.get("reached") is False and observed < version):
        raise ValueError(HISTORY_MISSING)
    if (type(proof["policy_version"]) is not int or proof["policy_version"] != 2
            or proof.get("reached") is not True or not isinstance(proof.get("event_id"), str)
            or not proof["event_id"].strip() or not isinstance(proof.get("status"), str)
            or proof["status"] not in _QUALIFYING):
        raise ValueError(HISTORY_UNPROVEN)
    at = _time(proof.get("at"), HISTORY_UNPROVEN)
    if not created <= at <= observed:
        raise ValueError(HISTORY_UNPROVEN)
    return {"source": "salla_order_history", "status": proof["status"],
            "observed_at": at.isoformat(), "event_id": proof["event_id"]}


def reached_in_progress(raw, source, **scope):
    return bank_status_evidence(raw, source, **scope) is not None


def _event_status(row):
    status_kinds = {"status_changed", "status_updated", "order.status.updated"}
    kinds = [row[key] for key in ("type", "event") if row.get(key)]
    if not kinds or any(kind not in (*status_kinds, "activity") for kind in kinds):
        return None
    if not any(kind in status_kinds for kind in kinds):
        if row.get("note") != "" or not row.get("customized") or not row.get("status"):
            return None
    # Free-text activity notes or contradictory target fields are never evidence.
    states = {_state(row[key]) for key in ("new_status", "to_status", "status", "customized")
              if row.get(key) is not None}
    if len(states) > 1 and states & _QUALIFYING:
        # Ignoring an ambiguous earlier event could falsely move the first
        # qualifying transition past the financial cutover.
        raise ValueError(HISTORY_UNPROVEN)
    return states.pop() if len(states) == 1 else None


def history_proof(raw, source, rows, *, owner, order_number, provider_order_id, as_of):
    """Date the first qualifying transition in a complete order-scoped Salla history."""
    status, created, version = _source(raw, source, owner, order_number, provider_order_id, as_of)
    if status not in _QUALIFYING:
        raise ValueError(SOURCE_INVALID)
    observations = []
    for row in rows:
        if (not isinstance(row, dict) or any(row.get(key) is not None and str(row[key]) != expected
                for key, expected in (("order_id", provider_order_id), ("order_number", order_number),
                                      ("reference_id", order_number)))):
            raise ValueError(HISTORY_UNPROVEN)
        state = _event_status(row)
        if state in _QUALIFYING:
            event = str(row.get("id") or "").strip()
            at = _time(row.get("created_at") or row.get("date"), HISTORY_UNPROVEN)
            if (not event or not created <= at <= version
                    or row.get("date") and _time(row["date"], HISTORY_UNPROVEN) != at):
                raise ValueError(HISTORY_UNPROVEN)
            observations.append((at, event, state))
    proof = {"source": _HISTORY_SOURCE, "policy_version": 2, "owner": owner, "order_id": provider_order_id,
             "order_number": order_number, "order_created_at": created.isoformat(),
             "source_updated_at": version.isoformat(), "reached": bool(observations)}
    if observations:
        at, event, state = min(observations)
        proof.update(at=at.isoformat(), event_id=event, status=state)
    return proof
