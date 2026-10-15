import asyncio

from bnpl.auto_sync_service import (
    BACKGROUND_CHECKS_DISABLED,
    run_auto_sync_for_all_users,
    run_auto_sync_for_user,
    run_tamara_attribution_sweep,
)


def test_background_checks_are_hard_disabled():
    assert BACKGROUND_CHECKS_DISABLED is True


def test_hourly_bnpl_scheduler_does_no_provider_work():
    result = asyncio.run(run_auto_sync_for_all_users(None))
    assert result["disabled"] is True
    assert result["pairs_processed"] == 0
    assert result["users_processed"] == 0
    assert result["by_provider"] == {"tabby": 0, "tamara": 0}


def test_manual_shared_auto_sync_does_no_provider_work():
    result = asyncio.run(run_auto_sync_for_user(None, "owner-1"))
    assert result["disabled"] is True
    assert result["providers"] == ["tabby", "tamara"]
    assert all(item["disabled"] is True for item in result["results"])
    assert all(item["fetched"] == 0 for item in result["results"])


def test_tamara_background_sweep_does_no_scan():
    result = asyncio.run(run_tamara_attribution_sweep(None))
    assert result["disabled"] is True
    assert result["rows_scanned"] == 0
    assert result["rows_updated"] == 0
