from __future__ import annotations

from datetime import date

import pytest

from integrations_control_center import snapchat_account_hourly_refresh as hourly
from integrations_control_center import snapchat_campaign_catalog_refresh as catalog
from integrations_control_center.snapchat_native_data_common import (
    SnapchatNativeSyncError,
)


@pytest.mark.asyncio
async def test_campaign_catalog_reads_only_campaign_entities(monkeypatch):
    captured = {}

    async def fake_sync(context, client, access_token, account, **kwargs):
        captured.update(kwargs)
        captured["account"] = account
        captured["access_token"] = access_token
        return 4, 4, []

    monkeypatch.setattr(catalog, "_sync_entity_type", fake_sync)
    result = await catalog.refresh_snapchat_campaign_catalog(
        object(),
        object(),
        "token",
        {"ad_account_id": "account-1"},
    )

    assert captured == {
        "entity_type": "campaign",
        "plural_key": "campaigns",
        "singular_key": "campaign",
        "extra_params": {},
        "account": {"ad_account_id": "account-1"},
        "access_token": "token",
    }
    assert result["campaign_entities_saved"] == 4
    assert result["campaign_entities_observed"] == 4
    assert result["errors_count"] == 0
    assert result["provider_write_reached"] is False
    assert result["accounting_write_reached"] is False
    assert result["qoyod_write_reached"] is False


@pytest.mark.asyncio
async def test_installer_refreshes_catalog_before_performance(monkeypatch):
    events = []
    original = hourly.refresh_snapchat_account_hours

    async def base_refresh(context, client, access_token, account, **kwargs):
        events.append("performance")
        return {
            "rows_saved": 7,
            "errors_count": 0,
            "errors": [],
        }

    async def fake_catalog(context, client, access_token, account):
        events.append("catalog")
        return {
            "source_mode": catalog.CAMPAIGN_CATALOG_SOURCE_MODE,
            "campaign_entities_saved": 12,
            "campaign_entities_observed": 12,
            "errors_count": 0,
            "errors": [],
        }

    try:
        monkeypatch.setattr(hourly, "refresh_snapchat_account_hours", base_refresh)
        monkeypatch.setattr(catalog, "refresh_snapchat_campaign_catalog", fake_catalog)
        catalog.install_snapchat_campaign_catalog_refresh()

        result = await hourly.refresh_snapchat_account_hours(
            object(),
            object(),
            "token",
            {"ad_account_id": "account-1"},
            start_date=date(2026, 8, 4),
            end_date=date(2026, 8, 4),
        )

        assert events == ["catalog", "performance"]
        assert result["rows_saved"] == 7
        assert result["campaign_catalog"]["campaign_entities_saved"] == 12
        assert result["errors_count"] == 0
        assert getattr(
            hourly.refresh_snapchat_account_hours,
            "_mezan_campaign_catalog_refresh",
            False,
        ) is True
    finally:
        hourly.refresh_snapchat_account_hours = original


@pytest.mark.asyncio
async def test_catalog_failure_does_not_block_spend_refresh(monkeypatch):
    events = []

    async def base_refresh(context, client, access_token, account, **kwargs):
        events.append("performance")
        return {
            "rows_saved": 5,
            "errors_count": 0,
            "errors": [],
        }

    async def failed_catalog(context, client, access_token, account):
        events.append("catalog")
        raise SnapchatNativeSyncError(
            "snapchat_campaign_catalog_failed",
            "campaign catalogue temporarily unavailable",
            status_code=502,
            retryable=True,
        )

    monkeypatch.setattr(catalog, "refresh_snapchat_campaign_catalog", failed_catalog)
    result = await catalog._refresh_with_campaign_catalog(
        base_refresh,
        object(),
        object(),
        "token",
        {"ad_account_id": "account-1"},
        start_date=date(2026, 8, 4),
        end_date=date(2026, 8, 4),
    )

    assert events == ["catalog", "performance"]
    assert result["rows_saved"] == 5
    assert result["errors_count"] == 1
    assert result["errors"][0]["kind"] == "campaign_catalog"
    assert result["errors"][0]["code"] == "snapchat_campaign_catalog_failed"


@pytest.mark.asyncio
async def test_reauth_from_catalog_stops_the_cycle(monkeypatch):
    called = False

    async def base_refresh(context, client, access_token, account, **kwargs):
        nonlocal called
        called = True
        return {}

    async def needs_reauth(context, client, access_token, account):
        raise SnapchatNativeSyncError(
            "snapchat_needs_reauth",
            "renew authorization",
            status_code=409,
        )

    monkeypatch.setattr(catalog, "refresh_snapchat_campaign_catalog", needs_reauth)
    with pytest.raises(SnapchatNativeSyncError) as error:
        await catalog._refresh_with_campaign_catalog(
            base_refresh,
            object(),
            object(),
            "token",
            {"ad_account_id": "account-1"},
            start_date=date(2026, 8, 4),
            end_date=date(2026, 8, 4),
        )

    assert error.value.code == "snapchat_needs_reauth"
    assert called is False
