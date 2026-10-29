# Reused unchanged from saved order-evidence checkpoint66634b9; diagnostic only.
"""Conservative source binding for diagnostic order observations, not sales."""
from zoneinfo import ZoneInfo

from .google_order_timestamp_evidence import exact_source_instant


def _id(value):
    if type(value) is int and value > 0:
        value = str(value)
    if (not isinstance(value, str) or not value.isascii() or not value.isdigit()
            or not 1 <= len(value) <= 30 or not any(char != '0' for char in value)):
        return None
    return value


def _unknown(reason):
    return {"status": reason, "business_date_riyadh": None, "coverage_complete": False,
            "source_snapshot_immutable": False}


def bound_salla_order_date(row, *, owner_id):
    """Bind both Salla IDs and owner, then compare every supplied timestamp.

    A saved snapshot may be merged or incomplete. This checks internal
    consistency only, not provider authenticity, history, or coverage.
    """
    if not isinstance(owner_id, str) or not owner_id or owner_id.strip() != owner_id or not isinstance(row, dict) or row.get("user_id") != owner_id:
        return _unknown("identity_unverified")
    sources = row.get("raw_by_source")
    raw = sources.get("salla_direct") if isinstance(sources, dict) else None
    if not isinstance(raw, dict):
        return _unknown("identity_unverified")
    order_id, number = _id(row.get("order_id")), _id(row.get("order_number"))
    if order_id is None or number is None or order_id != _id(raw.get("id")) or number != _id(raw.get("reference_id")):
        return _unknown("identity_unverified")
    values = []
    for key in ("date", "created_at"):
        if key in raw:
            value = raw[key]
            if key == "date" and isinstance(value, dict):
                # A separate timezone declaration needs its own verified
                # interpretation; do not silently ignore a possible conflict.
                if value.get("timezone") is not None:
                    return _unknown("timestamp_unverified")
                value = value.get("date")
            instant = exact_source_instant(value)
            if instant is None:
                return _unknown("timestamp_unverified")
            values.append(instant)
    if not values or any(value != values[0] for value in values[1:]):
        return _unknown("timestamp_unverified")
    try:
        business_date = values[0].astimezone(ZoneInfo("Asia/Riyadh")).date().isoformat()
    except (ValueError, OverflowError):
        return _unknown("timestamp_unverified")
    return {"status": "observed_bound_source_instant", "business_date_riyadh": business_date,
            "coverage_complete": False, "source_snapshot_immutable": False}
