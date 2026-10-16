from datetime import date
from unittest.mock import AsyncMock
import pytest
from unified_marketing.readers import snapchat_v2 as reader

DAY = date(2026, 10, 15)

@pytest.mark.asyncio
async def test_dashboard_includes_both_accounts_without_changing_selection(monkeypatch):
    accounts = [
        {"ad_account_id": "sa", "currency": "SAR", "timezone": "Asia/Riyadh", "connection_status": "connected"},
        {"ad_account_id": "us", "currency": "USD", "timezone": "America/New_York", "connection_status": "connected"},
    ]
    monkeypatch.setattr(reader, "list_enabled_accounts", AsyncMock(return_value=accounts))
    calls = []
    async def one(db, user_id, **kwargs):
        account = kwargs.get("account_id") or "sa"
        calls.append((account, kwargs["timezone_name"]))
        value = {"sa": 100, "us": 375}[account]
        return {"rows": [], "total_sar": value, "daily_sar": {DAY.isoformat(): value},
                "daily_state": {DAY.isoformat(): "confirmed_data"}, "hourly_sar": {},
                "bank_commissions": {"accounts": [], "total_fee_sar": 0,
                    "fee_subject_spend_sar": 0, "total_effective_spend_sar": value,
                    "coverage": {"complete": True}},
                "quality": {"complete": True, "amount_complete": True,
                    "coverage_complete": True, "amount_available": True}}
    monkeypatch.setattr(reader, "_load_snapchat_v2_single_account_dashboard_spend", one)
    result = await reader.load_snapchat_v2_dashboard_spend(
        object(), "owner", date_from=DAY, date_to=DAY, timezone_name="Asia/Riyadh")
    assert result["total_sar"] == 475
    assert calls == [("sa", "Asia/Riyadh"), ("us", "Asia/Riyadh")]
    assert len(result["accounts"]) == 2


def test_portfolio_never_turns_missing_fx_or_account_data_into_zero():
    from unified_marketing.readers.dashboard_portfolio import combine_dashboard_spend
    ready = {"total_sar": 100, "daily_sar": {DAY.isoformat(): 100},
             "quality": {"complete": True, "amount_available": True, "coverage_complete": True}}
    missing = {"total_sar": None, "quality": {"complete": False, "amount_available": False,
                "reason_codes": ["fx_missing"]}}
    result = combine_dashboard_spend([ready, missing], [], DAY, DAY, "Asia/Riyadh")
    assert result["total_sar"] is None
    assert result["daily_sar"][DAY.isoformat()] is None
    assert result["quality"]["complete"] is False
    assert "fx_missing" in result["quality"]["reason_codes"]


def test_portfolio_preserves_open_day_provisional_and_account_fees():
    from unified_marketing.readers.dashboard_portfolio import combine_dashboard_spend
    reports = []
    for amount, fee in [(100, 2), (375, 3)]:
        reports.append({"total_sar": amount, "daily_sar": {DAY.isoformat(): amount},
            "daily_state": {DAY.isoformat(): "provisional_data"},
            "hourly_sar": {DAY.isoformat(): [{"hour_index": 0, "hour": "00:00", "spend_sar": amount, "status": "provisional"}]},
            "bank_commissions": {"accounts": [{"fee": fee}], "total_fee_sar": fee,
                "fee_subject_spend_sar": amount, "total_effective_spend_sar": amount+fee, "coverage": {"complete": True}},
            "quality": {"complete": False, "amount_available": True, "provisional": True}})
    result = combine_dashboard_spend(reports, [], DAY, DAY, "Asia/Riyadh")
    assert result["total_sar"] == 475
    assert result["quality"]["status"] == "provisional"
    assert result["quality"]["complete"] is False
    assert result["hourly_sar"][DAY.isoformat()][0]["spend_sar"] == 475
    assert result["bank_commissions"]["total_fee_sar"] == 5
    assert result["bank_commissions"]["total_effective_spend_sar"] == 480
