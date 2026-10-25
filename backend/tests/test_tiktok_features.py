import pytest

from integrations_control_center.tiktok_features import enabled


@pytest.mark.parametrize("feature,variable", [("campaign_management", "TIKTOK_NATIVE_MANAGEMENT_ENABLED"), ("ad_builder", "TIKTOK_AD_BUILDER_ENABLED"), ("child_management", "TIKTOK_CHILD_MANAGEMENT_ENABLED")])
def test_owner_reviewed_native_tools_enabled_by_default_and_can_be_disabled(monkeypatch, feature, variable):
    monkeypatch.delenv(variable, raising=False)
    assert enabled(feature) is True
    monkeypatch.setenv(variable, "false")
    assert enabled(feature) is False


def test_periodic_billable_observations_remain_opt_in(monkeypatch):
    monkeypatch.delenv("MEZAN_TIKTOK_AI_PERIODIC_ENABLED", raising=False)
    assert enabled("periodic_observations") is False
