from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:180]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


platform = "backend/integrations_control_center/snapchat_platform_source_integrity.py"
replace_once(
    platform,
    '''REQUIRED_ACCOUNT_TOTAL_FIELDS = frozenset({"spend"})
''',
    '''REQUIRED_ACCOUNT_TOTAL_FIELDS = frozenset({"spend"})
DIRECT_ACCOUNT_TOTAL_FIELDS = ("spend",)
''',
)
replace_once(
    platform,
    '''            "granularity": PLATFORM_TOTAL_GRANULARITY,
            "fields": ",".join(STAT_FIELDS),
            "omit_empty": "false",
            "conversion_source_types": CONVERSION_SOURCE_TYPES,
            "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
            "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
            "action_report_time": ADS_MANAGER_ACTION_REPORT_TIME,
''',
    '''            "granularity": PLATFORM_TOTAL_GRANULARITY,
            "fields": ",".join(DIRECT_ACCOUNT_TOTAL_FIELDS),
            "omit_empty": "false",
''',
)
replace_once(
    platform,
    '''        "direct_account_total_requested": True,
        "account_spend_source": "direct_ad_account_total",
''',
    '''        "direct_account_total_requested": True,
        "direct_account_request_fields": list(DIRECT_ACCOUNT_TOTAL_FIELDS),
        "account_spend_source": "direct_ad_account_total",
''',
)
replace_once(
    platform,
    '''    "PLATFORM_TOTAL_BREAKDOWN",
    "PLATFORM_TOTAL_GRANULARITY",
''',
    '''    "DIRECT_ACCOUNT_TOTAL_FIELDS",
    "PLATFORM_TOTAL_BREAKDOWN",
    "PLATFORM_TOTAL_GRANULARITY",
''',
)

platform_test = "backend/tests/test_snapchat_platform_source_integrity_v1.py"
replace_once(
    platform_test,
    '''from pathlib import Path
from datetime import date, datetime, timezone
''',
    '''from pathlib import Path
from datetime import date, datetime, timezone

import pytest
''',
)
replace_once(
    platform_test,
    '''from integrations_control_center.snapchat_platform_source_integrity import (
    PLATFORM_TOTAL_SOURCE_MODE,
''',
    '''from integrations_control_center.snapchat_platform_source_integrity import (
    DIRECT_ACCOUNT_TOTAL_FIELDS,
    PLATFORM_TOTAL_SOURCE_MODE,
''',
)
replace_once(
    platform_test,
    '''    extract_account_total_metrics,
    merge_direct_spend_with_campaign_metrics,
''',
    '''    extract_account_total_metrics,
    fetch_account_total_direct_metrics,
    merge_direct_spend_with_campaign_metrics,
''',
)
marker = '''def test_direct_account_total_accepts_documented_spend_only_payload():
'''
if "test_direct_account_request_uses_spend_only_without_conversion_parameters" not in Path(platform_test).read_text(encoding="utf-8"):
    addition = '''@pytest.mark.asyncio
async def test_direct_account_request_uses_spend_only_without_conversion_parameters():
    class CaptureContext:
        def __init__(self):
            self.params = None

        async def get_json(self, client, url, *, headers, params=None):
            self.params = dict(params or {})
            return {
                "total_stats": [{
                    "sub_request_status": "SUCCESS",
                    "total_stat": {"stats": {"spend": 714_050_000}},
                }],
            }

    context = CaptureContext()
    metrics, errors = await fetch_account_total_direct_metrics(
        context,
        object(),
        "token-not-used",
        account_id="account-1",
        request_start=datetime(2026, 8, 6, 0, 0, tzinfo=timezone.utc),
        request_end=datetime(2026, 8, 6, 12, 0, tzinfo=timezone.utc),
    )

    assert errors == []
    assert metrics["spend"] == 714_050_000
    assert context.params["fields"] == "spend"
    assert tuple(context.params["fields"].split(",")) == DIRECT_ACCOUNT_TOTAL_FIELDS
    for forbidden in (
        "conversion_source_types",
        "swipe_up_attribution_window",
        "view_attribution_window",
        "action_report_time",
    ):
        assert forbidden not in context.params


''' + marker
    text = Path(platform_test).read_text(encoding="utf-8")
    if marker not in text:
        raise SystemExit("platform direct-total test marker missing")
    Path(platform_test).write_text(text.replace(marker, addition, 1), encoding="utf-8")

scheduler = "backend/integrations_control_center/ads_auto_sync_scheduler.py"
replace_once(
    scheduler,
    '''def _safe_summary(result: dict[str, Any]) -> dict[str, Any]:
    error_samples = []
''',
    '''def _safe_summary(result: dict[str, Any]) -> dict[str, Any]:
    error_samples = []
''',
)
replace_once(
    scheduler,
    '''    return {
        "date_from": result.get("date_from"),
''',
    '''    account_provider_calls = []
    for item in list(result.get("account_provider_calls") or [])[:20]:
        if not isinstance(item, dict):
            continue
        account_provider_calls.append({
            "ad_account_id": item.get("ad_account_id"),
            "provider_calls": int(item.get("provider_calls") or 0),
        })
    return {
        "date_from": result.get("date_from"),
''',
)
replace_once(
    scheduler,
    '''        "provider_calls": int(result.get("provider_calls") or 0),
        "error_samples": error_samples,
''',
    '''        "provider_calls": int(result.get("provider_calls") or 0),
        "provider_call_budget_scope": result.get("provider_call_budget_scope"),
        "account_provider_calls": account_provider_calls,
        "error_samples": error_samples,
''',
)
replace_once(
    scheduler,
    '''        context = SnapchatSyncContext(db, user_id, now=_utcnow)
        access_token = await context.access_token()
        items: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=35.0) as client:
            for account in accounts:
                account_id = str(account.get("ad_account_id") or "").strip()
                try:
                    item = await refresh_snapchat_account_hours(
                        context,
''',
    '''        token_context = SnapchatSyncContext(db, user_id, now=_utcnow)
        access_token = await token_context.access_token()
        items: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        provider_calls_total = 0
        account_provider_calls: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=35.0) as client:
            for account in accounts:
                account_context = SnapchatSyncContext(db, user_id, now=_utcnow)
                account_id = str(account.get("ad_account_id") or "").strip()
                try:
                    item = await refresh_snapchat_account_hours(
                        account_context,
''',
)
replace_once(
    scheduler,
    '''                            retryable=True,
                        )
                        errors.append({
                            "error_id": error_id,
                            "ad_account_id": account_id,
                            "code": code,
                            "message": message[:300],
                            "retryable": True,
                        })
''',
    '''                            retryable=bool(item_error.get("retryable")),
                        )
                        errors.append({
                            "error_id": error_id,
                            "ad_account_id": account_id,
                            "code": code,
                            "message": message[:300],
                            "retryable": bool(item_error.get("retryable")),
                        })
''',
)
replace_once(
    scheduler,
    '''                    errors.append({
                        "error_id": error_id,
                        "ad_account_id": account_id,
                        "code": exc.code,
                        "message": exc.message[:300],
                        "retryable": exc.retryable,
                    })
        rows_saved = sum(int(item.get("rows_saved") or 0) for item in items)
''',
    '''                    errors.append({
                        "error_id": error_id,
                        "ad_account_id": account_id,
                        "code": exc.code,
                        "message": exc.message[:300],
                        "retryable": exc.retryable,
                    })
                finally:
                    provider_calls_total += int(account_context.provider_calls)
                    account_provider_calls.append({
                        "ad_account_id": account_id,
                        "provider_calls": int(account_context.provider_calls),
                    })
        rows_saved = sum(int(item.get("rows_saved") or 0) for item in items)
''',
)
replace_once(
    scheduler,
    '''            "provider_calls": context.provider_calls,
            "error_samples": errors[:10],
''',
    '''            "provider_calls": provider_calls_total,
            "provider_call_budget_scope": "per_selected_account",
            "account_provider_calls": account_provider_calls,
            "error_samples": errors[:10],
''',
)

scheduler_test = "backend/tests/test_ads_auto_sync_scheduler_v2.py"
replace_once(
    scheduler_test,
    '''from datetime import datetime, timezone
''',
    '''from datetime import datetime, timezone
import inspect
''',
)
if "test_snapchat_call_budget_is_isolated_per_selected_account" not in Path(scheduler_test).read_text(encoding="utf-8"):
    Path(scheduler_test).write_text(
        Path(scheduler_test).read_text(encoding="utf-8")
        + '''\n\ndef test_snapchat_call_budget_is_isolated_per_selected_account():
    source = inspect.getsource(scheduler._refresh_snapchat)

    assert "token_context = SnapchatSyncContext" in source
    assert "account_context = SnapchatSyncContext" in source
    assert "provider_calls_total += int(account_context.provider_calls)" in source
    assert '"provider_call_budget_scope": "per_selected_account"' in source
    assert '"provider_calls": provider_calls_total' in source


def test_safe_summary_preserves_per_account_provider_calls():
    summary = scheduler._safe_summary({
        "provider_calls": 263,
        "provider_call_budget_scope": "per_selected_account",
        "account_provider_calls": [
            {"ad_account_id": "account-usd", "provider_calls": 132},
            {"ad_account_id": "account-sar", "provider_calls": 131},
        ],
    })

    assert summary["provider_calls"] == 263
    assert summary["provider_call_budget_scope"] == "per_selected_account"
    assert summary["account_provider_calls"] == [
        {"ad_account_id": "account-usd", "provider_calls": 132},
        {"ad_account_id": "account-sar", "provider_calls": 131},
    ]
''',
        encoding="utf-8",
    )

print("SNAP_TOTAL_REQUEST_BUDGET_V5_APPLIED")
