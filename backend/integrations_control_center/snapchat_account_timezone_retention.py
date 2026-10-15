"""Seven-day account-local retention for the Snapchat Ads Manager projection."""
from __future__ import annotations

from datetime import date, timedelta

from . import snapchat_account_timezone_manager as manager

ACCOUNT_MANAGER_LOCAL_DAYS = 7


def install_snapchat_account_timezone_retention() -> None:
    """Keep seven native account days without widening Riyadh accounting days."""

    def local_sync_bounds(start_date: date, end_date: date) -> tuple[date, date]:
        rolling_start = end_date - timedelta(days=ACCOUNT_MANAGER_LOCAL_DAYS - 1)
        padded_start = start_date - timedelta(days=manager.ACCOUNT_DATE_PADDING_DAYS)
        return (
            min(rolling_start, padded_start),
            end_date + timedelta(days=manager.ACCOUNT_DATE_PADDING_DAYS),
        )

    manager._local_sync_bounds = local_sync_bounds


__all__ = [
    "ACCOUNT_MANAGER_LOCAL_DAYS",
    "install_snapchat_account_timezone_retention",
]
