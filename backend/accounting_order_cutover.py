"""Shared eligibility fence: an order must itself originate after cutover."""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo


class OrderCutoverError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def require_order_created_on_or_after_cutover(value, cutoff, *, source_timezone=False):
    """Return documented creation in UTC; equality with cutover is eligible.

    Only Salla source text opts into the existing Asia/Riyadh interpretation
    for naive timestamps. Provider evidence must contain an explicit offset.
    No delivery, capture, ingestion or current-time fallback is permitted.
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        raise OrderCutoverError("order_creation_timestamp_required")
    try:
        if isinstance(value, datetime):
            created = value
        elif isinstance(value, str):
            created = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        else:
            raise ValueError
        if created.tzinfo is None or created.utcoffset() is None:
            if not source_timezone:
                raise ValueError
            created = created.replace(tzinfo=ZoneInfo("Asia/Riyadh"))
        created = created.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise OrderCutoverError("order_creation_timestamp_invalid") from None
    try:
        cut = cutoff if isinstance(cutoff, datetime) else datetime.fromisoformat(
            str(cutoff).replace("Z", "+00:00"))
        if cut.tzinfo is None or cut.utcoffset() is None:
            raise ValueError
        cut = cut.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        raise OrderCutoverError("recognition_cutoff_not_configured") from None
    if created < cut:
        raise OrderCutoverError("pre_cutover_order")
    return created
