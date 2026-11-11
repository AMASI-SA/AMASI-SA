"""Owner-selected cooldown for automatic Snapchat reporting, across restarts."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os

from .sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION

INTERVAL_ENV = "SNAPCHAT_REPORTING_V2_SHADOW_INTERVAL_SECONDS"
DEFAULT_INTERVAL_SECONDS = 7200
MIN_INTERVAL_SECONDS = 7200
MAX_INTERVAL_SECONDS = 86400


def shadow_interval_seconds() -> int:
    try:
        value = int(os.environ.get(INTERVAL_ENV, str(DEFAULT_INTERVAL_SECONDS)))
    except (TypeError, ValueError, OverflowError):
        value = DEFAULT_INTERVAL_SECONDS
    # The owner chose two hours. Surviving five-minute settings must not
    # silently restore the old load after deployment or a process restart.
    return min(max(value, MIN_INTERVAL_SECONDS), MAX_INTERVAL_SECONDS)


def _instant(value):
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def automatic_next_due(db, user_id: str, account_id: str, *, now: datetime):
    """Recheck under the existing account lease before any provider work.

    Finished attempts cool down from completion; legacy missing completion
    timestamps fall back to their start. A skipped lease attempt must never
    postpone another worker. Abandoned runs retain the existing recovery path.
    Recent manual refreshes also satisfy automatic freshness for this account.
    """
    current = now.astimezone(timezone.utc)
    rows = await db[SNAPCHAT_SYNC_RUNS_COLLECTION].find(
        {"user_id": str(user_id), "ad_account_id": str(account_id),
         "status": {"$in": ["complete", "partial", "failed"]}},
        {"_id": 0, "started_at": 1, "finished_at": 1},
    ).sort([("started_at", -1)]).limit(50).to_list(length=50)
    markers = []
    for row in rows:
        marker = _instant(row.get("finished_at")) or _instant(row.get("started_at"))
        if marker is not None and marker <= current + timedelta(minutes=5):
            markers.append(marker)
    if not markers:
        return None
    due = max(markers) + timedelta(seconds=shadow_interval_seconds())
    return due if due > current else None
