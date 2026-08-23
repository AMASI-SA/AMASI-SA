from __future__ import annotations

from datetime import datetime
from typing import Any, TypedDict


class SnapchatHourlyFact(TypedDict, total=False):
    user_id: str
    provider: str
    ad_account_id: str

    campaign_id: str | None
    ad_squad_id: str | None
    ad_id: str | None

    hour_start_utc: datetime
    hour_end_utc: datetime
    account_timezone: str

    currency: str
    spend_native: float

    impressions: int
    swipes: int
    video_views: int

    purchases: int
    purchase_value_native: float

    action_report_time: str | None
    attribution_windows: dict[str, Any]

    coverage: dict[str, Any]
    source: dict[str, Any]

    sync_run_id: str

    created_at: datetime
    updated_at: datetime
