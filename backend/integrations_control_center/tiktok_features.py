"""Single native TikTok feature-flag authority; ADR-001 principles1,3,9."""
import os

_FEATURES = {
    "campaign_management": ("TIKTOK_NATIVE_MANAGEMENT_ENABLED", "true"),
    "ad_builder": ("TIKTOK_AD_BUILDER_ENABLED", "true"),
    "child_management": ("TIKTOK_CHILD_MANAGEMENT_ENABLED", "true"),
    "periodic_observations": ("MEZAN_TIKTOK_AI_PERIODIC_ENABLED", "false"),
    "spend_guard": ("TIKTOK_SPEND_GUARD_ENABLED", "true"),
}


def enabled(feature):
    name, default = _FEATURES[feature]
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}
