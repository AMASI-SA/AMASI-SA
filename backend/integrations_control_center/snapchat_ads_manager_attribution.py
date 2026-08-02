"""Compatibility entrypoint for Snapchat conversion freshness reporting."""

from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_SOURCE_MODE,
    FRESHNESS_COLLECTION,
    SNAPCHAT_ACTION_REPORT_TIME,
    SNAPCHAT_SOURCE_MODE,
    _account_id_from_stats_url,
    extract_provider_freshness,
    install_snapchat_ads_manager_attribution,
    summarize_conversion_freshness,
)

__all__ = [
    "ADS_MANAGER_ACTION_REPORT_TIME",
    "ADS_MANAGER_SOURCE_MODE",
    "FRESHNESS_COLLECTION",
    "SNAPCHAT_ACTION_REPORT_TIME",
    "SNAPCHAT_SOURCE_MODE",
    "_account_id_from_stats_url",
    "extract_provider_freshness",
    "install_snapchat_ads_manager_attribution",
    "summarize_conversion_freshness",
]
