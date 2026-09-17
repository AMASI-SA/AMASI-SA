"""The report's Mongo projection must retain stored campaign evidence."""
import pytest

from snapchat_v2.salla_outcomes import ORDER_PROJECTION, _match_order_campaign


@pytest.mark.parametrize("field", ["campaign_id", "source_campaign_id", "utm_campaign_id", "ad_campaign_id", "utm_campaign"])
def test_normalized_campaign_identity_survives_report_projection(field):
    stored = {"source": "snapchat", field: "campaign-proven"}
    projected = {key: value for key, value in stored.items() if ORDER_PROJECTION.get(key)}
    assert _match_order_campaign(
        projected, id_lookup={"campaign-proven": ("account", "campaign-proven")}, name_lookup={},
    ) == (("account", "campaign-proven"), "campaign_id")


def test_provider_correction_is_not_overridden_by_root_campaign_fallback():
    order = {
        "source": "snapchat", "campaign_id": "old-campaign",
        "raw_by_source": {"salla_direct": {"source_details": {
            "utm_source": "snapchat", "utm_campaign": "corrected-campaign",
        }}},
    }
    assert _match_order_campaign(order, id_lookup={
        "old-campaign": ("account", "old-campaign"),
        "corrected-campaign": ("account", "corrected-campaign"),
    }, name_lookup={}) == (("account", "corrected-campaign"), "campaign_id")


def test_provider_platform_correction_blocks_stale_snapchat_root():
    order = {
        "source": "snapchat", "campaign_id": "old-campaign",
        "raw_by_source": {"salla_direct": {"utm_source": "meta"}},
    }
    assert _match_order_campaign(order, id_lookup={
        "old-campaign": ("account", "old-campaign"),
    }, name_lookup={}) == (None, "foreign_platform")


def test_unknown_provider_campaign_does_not_fall_back_to_known_stale_id():
    order = {
        "source": "snapchat", "campaign_id": "old-campaign",
        "raw_by_source": {"salla_direct": {"utm_campaign": "unknown-campaign"}},
    }
    assert _match_order_campaign(order, id_lookup={
        "old-campaign": ("account", "old-campaign"),
    }, name_lookup={}) == (None, "unmatched")


def test_source_only_order_is_not_distributed_across_campaigns():
    assert _match_order_campaign({"source": "snapchat"}, id_lookup={
        "campaign-proven": ("account", "campaign-proven"),
    }, name_lookup={}) == (None, "unmatched")
