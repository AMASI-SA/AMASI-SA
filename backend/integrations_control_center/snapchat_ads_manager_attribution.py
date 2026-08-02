"""Snapchat reporting contract aligned with the merchant's Ads Manager view.

Mezan keeps its business-day window in Asia/Riyadh and converts the same
instants to each ad account's native timezone.  This installer changes only the
conversion reporting principle to impression time so Purchases and Purchase
Value follow the Ads Manager attribution view selected by the merchant.

The patch is intentionally internal and read-only: no campaign, accounting, or
Qoyod mutation is introduced.
"""
from __future__ import annotations

from typing import Final

ADS_MANAGER_ACTION_REPORT_TIME: Final[str] = "impression"
ADS_MANAGER_SOURCE_MODE: Final[str] = (
    "snapchat_account_hourly_campaign_breakdown_riyadh_impression_v4"
)


def install_snapchat_ads_manager_attribution() -> None:
    """Apply the Ads Manager attribution contract to all Snapchat readers.

    The modules expose constants as runtime configuration.  Updating every
    consumer together prevents a request/storage/response metadata mismatch.
    The operation is idempotent and safe to run whenever the V2 router is
    composed.
    """
    from . import snapchat_account_hourly_refresh as account_refresh
    from . import snapchat_dashboard_summary_routes as dashboard_summary
    from . import snapchat_native_performance_sync as performance_sync

    account_refresh.ACTION_REPORT_TIME = ADS_MANAGER_ACTION_REPORT_TIME
    account_refresh.ACCOUNT_REFRESH_SOURCE_MODE = ADS_MANAGER_SOURCE_MODE
    performance_sync.ACTION_REPORT_TIME = ADS_MANAGER_ACTION_REPORT_TIME
    dashboard_summary.ACTION_REPORT_TIME = ADS_MANAGER_ACTION_REPORT_TIME


__all__ = [
    "ADS_MANAGER_ACTION_REPORT_TIME",
    "ADS_MANAGER_SOURCE_MODE",
    "install_snapchat_ads_manager_attribution",
]
