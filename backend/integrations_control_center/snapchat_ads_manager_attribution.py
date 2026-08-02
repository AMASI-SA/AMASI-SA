"""Compatibility entrypoint for Snapchat reporting installers."""

from .snapchat_freshness_impl_v6 import (
    ADS_MANAGER_ACTION_REPORT_TIME,
    ADS_MANAGER_SOURCE_MODE,
    FRESHNESS_COLLECTION,
    SNAPCHAT_ACTION_REPORT_TIME,
    SNAPCHAT_SOURCE_MODE,
    _account_id_from_stats_url,
    extract_provider_freshness,
    install_snapchat_ads_manager_attribution as _install_snapchat_freshness,
    summarize_conversion_freshness,
)
from .snapchat_operational_error_resolution import (
    install_snapchat_operational_error_resolution,
)
from .snapchat_salla_source_hybrid import (
    HYBRID_CONTRACT_VERSION,
    HYBRID_SOURCE,
    install_snapchat_salla_source_hybrid,
)


def install_snapchat_ads_manager_attribution() -> None:
    """Install freshness, operational error resolution, then hybrid KPIs."""
    _install_snapchat_freshness()
    install_snapchat_operational_error_resolution()
    install_snapchat_salla_source_hybrid()


__all__ = [
    "ADS_MANAGER_ACTION_REPORT_TIME",
    "ADS_MANAGER_SOURCE_MODE",
    "FRESHNESS_COLLECTION",
    "HYBRID_CONTRACT_VERSION",
    "HYBRID_SOURCE",
    "SNAPCHAT_ACTION_REPORT_TIME",
    "SNAPCHAT_SOURCE_MODE",
    "_account_id_from_stats_url",
    "extract_provider_freshness",
    "install_snapchat_ads_manager_attribution",
    "install_snapchat_operational_error_resolution",
    "install_snapchat_salla_source_hybrid",
    "summarize_conversion_freshness",
]
