from pathlib import Path


def replace_once(source: str, old: str, new: str, label: str) -> str:
    count = source.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one match, found {count}")
    return source.replace(old, new, 1)


hourly_path = Path("backend/integrations_control_center/snapchat_account_hourly_refresh.py")
scheduler_path = Path("backend/integrations_control_center/ads_auto_sync_scheduler.py")
hourly_test_path = Path("backend/tests/test_snapchat_account_hourly_refresh_v2.py")
scheduler_test_path = Path("backend/tests/test_ads_auto_sync_scheduler_v2.py")

hourly = hourly_path.read_text(encoding="utf-8")
scheduler = scheduler_path.read_text(encoding="utf-8")
hourly_tests = hourly_test_path.read_text(encoding="utf-8")
scheduler_tests = scheduler_test_path.read_text(encoding="utf-8")

hourly = replace_once(
    hourly,
    "from datetime import date, timedelta\n",
    "from datetime import date, datetime, timedelta, timezone\n",
    "hourly datetime imports",
)

hourly = replace_once(
    hourly,
    '''VIEW_ATTRIBUTION_WINDOW = "1_DAY"\n\n\nasync def _fetch_account_hours(\n''',
    '''VIEW_ATTRIBUTION_WINDOW = "1_DAY"\n\n\ndef snapchat_hourly_request_window(\n    start_date: date,\n    end_date: date,\n    *,\n    now: datetime | None = None,\n) -> tuple[datetime, datetime]:\n    """Return a provider-safe HOUR window without asking for future data.\n\n    The merchant date range is expressed in Riyadh time.  For a range that\n    includes today, Snap must only receive completed hour boundaries; sending\n    the following midnight while the day is still in progress causes account\n    stats requests to fail.\n    """\n    start, nominal_end = riyadh_business_window(start_date, end_date)\n    current = now or datetime.now(timezone.utc)\n    if current.tzinfo is None:\n        current = current.replace(tzinfo=timezone.utc)\n    business_tz = _timezone(BUSINESS_TIMEZONE)\n    completed_hour_end = current.astimezone(business_tz).replace(\n        minute=0,\n        second=0,\n        microsecond=0,\n    )\n    return start, min(nominal_end, completed_hour_end)\n\n\nasync def _fetch_account_hours(\n''',
    "provider-safe window helper",
)

hourly = replace_once(
    hourly,
    '''    account_id: str,\n    start_date: date,\n    end_date: date,\n) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:\n    start, end = riyadh_business_window(start_date, end_date)\n    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"\n''',
    '''    account_id: str,\n    request_start: datetime,\n    request_end: datetime,\n) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:\n    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"\n''',
    "fetch request-window signature",
)

hourly = replace_once(
    hourly,
    '''        "start_time": start.isoformat(timespec="seconds"),\n        "end_time": end.isoformat(timespec="seconds"),\n''',
    '''        "start_time": request_start.isoformat(timespec="seconds"),\n        "end_time": request_end.isoformat(timespec="seconds"),\n''',
    "fetch request times",
)

hourly = replace_once(
    hourly,
    '''    *,\n    start_date: date,\n    end_date: date,\n) -> dict[str, Any]:\n''',
    '''    *,\n    start_date: date,\n    end_date: date,\n    now: datetime | None = None,\n) -> dict[str, Any]:\n''',
    "refresh now argument",
)

hourly = replace_once(
    hourly,
    '''    rows, errors = await _fetch_account_hours(\n        context,\n        client,\n        access_token,\n        account_id=account_id,\n        start_date=start_date,\n        end_date=end_date,\n    )\n''',
    '''    request_start, request_end = snapchat_hourly_request_window(\n        start_date,\n        end_date,\n        now=now,\n    )\n    if request_end <= request_start:\n        return {\n            "provider": SNAPCHAT_PROVIDER_ID,\n            "ad_account_id": account_id,\n            "date_from": start_date.isoformat(),\n            "date_to": end_date.isoformat(),\n            "rows_saved": 0,\n            "errors_count": 0,\n            "errors": [],\n            "provider_calls": context.provider_calls,\n            "source_mode": ACCOUNT_REFRESH_SOURCE_MODE,\n            "business_timezone": BUSINESS_TIMEZONE,\n            "reason": "no_completed_hour_available",\n            "request_start_time": request_start.isoformat(timespec="seconds"),\n            "request_end_time": request_end.isoformat(timespec="seconds"),\n            "source_only": True,\n            "accounting_write_reached": False,\n            "qoyod_write_reached": False,\n        }\n\n    rows, errors = await _fetch_account_hours(\n        context,\n        client,\n        access_token,\n        account_id=account_id,\n        request_start=request_start,\n        request_end=request_end,\n    )\n''',
    "refresh clamped request",
)

hourly = replace_once(
    hourly,
    '''    saved = 0\n    cursor = start_date\n    while cursor <= end_date:\n''',
    '''    business_tz = _timezone(BUSINESS_TIMEZONE)\n    covered_end_date = (\n        request_end.astimezone(business_tz) - timedelta(microseconds=1)\n    ).date()\n    persist_end_date = min(end_date, covered_end_date)\n\n    saved = 0\n    cursor = start_date\n    while cursor <= persist_end_date:\n''',
    "covered date persistence",
)

hourly = replace_once(
    hourly,
    '''        window_start, window_end = riyadh_business_window(cursor, cursor)\n        await _upsert_performance(\n''',
    '''        window_start, window_end = riyadh_business_window(cursor, cursor)\n        if cursor == persist_end_date and request_end < window_end:\n            window_end = request_end\n        await _upsert_performance(\n''',
    "partial current-day provider end",
)

hourly = replace_once(
    hourly,
    '''        "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,\n        "source_only": True,\n''',
    '''        "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,\n        "request_start_time": request_start.isoformat(timespec="seconds"),\n        "request_end_time": request_end.isoformat(timespec="seconds"),\n        "source_only": True,\n''',
    "request window response",
)

hourly = replace_once(
    hourly,
    '''    "aggregate_account_hours_by_riyadh_day",\n    "refresh_snapchat_account_hours",\n''',
    '''    "aggregate_account_hours_by_riyadh_day",\n    "refresh_snapchat_account_hours",\n    "snapchat_hourly_request_window",\n''',
    "hourly helper export",
)

scheduler = replace_once(
    scheduler,
    '''def _safe_summary(result: dict[str, Any]) -> dict[str, Any]:\n    return {\n''',
    '''def _safe_summary(result: dict[str, Any]) -> dict[str, Any]:\n    error_samples = []\n    for item in list(result.get("error_samples") or result.get("errors") or [])[:10]:\n        if not isinstance(item, dict):\n            continue\n        error_samples.append({\n            key: item.get(key)\n            for key in ("error_id", "ad_account_id", "code", "message", "retryable", "kind", "error")\n            if item.get(key) is not None\n        })\n    return {\n''',
    "safe summary error samples",
)

scheduler = replace_once(
    scheduler,
    '''        "provider_calls": int(result.get("provider_calls") or 0),\n        "source_only": True,\n''',
    '''        "provider_calls": int(result.get("provider_calls") or 0),\n        "error_samples": error_samples,\n        "source_only": True,\n''',
    "safe summary include error samples",
)

scheduler = replace_once(
    scheduler,
    '''            for account in accounts:\n                try:\n                    item = await refresh_snapchat_account_hours(\n''',
    '''            for account in accounts:\n                account_id = str(account.get("ad_account_id") or "").strip()\n                try:\n                    item = await refresh_snapchat_account_hours(\n''',
    "scheduler account id before try",
)

scheduler = replace_once(
    scheduler,
    '''                        start_date=start_date,\n                        end_date=end_date,\n                    )\n                    items.append(item)\n                    errors.extend(item.get("errors") or [])\n                    account_id = str(account.get("ad_account_id") or "")\n                    await _collection(db, "mezan_integration_accounts_v2").update_one(\n''',
    '''                        start_date=start_date,\n                        end_date=end_date,\n                        now=now,\n                    )\n                    items.append(item)\n                    for item_error in item.get("errors") or []:\n                        code = str(item_error.get("code") or "snapchat_account_stats_partial")\n                        message = str(\n                            item_error.get("message")\n                            or item_error.get("error")\n                            or "Snapchat returned a partial account stats response."\n                        )\n                        error_id = await _record_error(\n                            db,\n                            user_id=user_id,\n                            provider=SNAPCHAT_PROVIDER_ID,\n                            run_id=run_id,\n                            source_mode=ACCOUNT_REFRESH_SOURCE_MODE,\n                            code=code,\n                            message=f"account={account_id}: {message}",\n                            retryable=True,\n                        )\n                        errors.append({\n                            "error_id": error_id,\n                            "ad_account_id": account_id,\n                            "code": code,\n                            "message": message[:300],\n                            "retryable": True,\n                        })\n                    await _collection(db, "mezan_integration_accounts_v2").update_one(\n''',
    "scheduler pass now and persist partial errors",
)

scheduler = replace_once(
    scheduler,
    '''                except SnapchatNativeSyncError as exc:\n                    if exc.code == "snapchat_needs_reauth":\n                        raise\n                    errors.append({"code": exc.code, "message": exc.message})\n''',
    '''                except SnapchatNativeSyncError as exc:\n                    if exc.code == "snapchat_needs_reauth":\n                        raise\n                    error_id = await _record_error(\n                        db,\n                        user_id=user_id,\n                        provider=SNAPCHAT_PROVIDER_ID,\n                        run_id=run_id,\n                        source_mode=ACCOUNT_REFRESH_SOURCE_MODE,\n                        code=exc.code,\n                        message=f"account={account_id}: {exc.message}",\n                        retryable=exc.retryable,\n                    )\n                    errors.append({\n                        "error_id": error_id,\n                        "ad_account_id": account_id,\n                        "code": exc.code,\n                        "message": exc.message[:300],\n                        "retryable": exc.retryable,\n                    })\n''',
    "scheduler persist account exceptions",
)

scheduler = replace_once(
    scheduler,
    '''            "provider_calls": context.provider_calls,\n        }\n''',
    '''            "provider_calls": context.provider_calls,\n            "error_samples": errors[:10],\n        }\n''',
    "scheduler result error samples",
)

hourly_tests = replace_once(
    hourly_tests,
    "from datetime import date\n",
    "from datetime import date, datetime, timezone\n",
    "hourly test datetime imports",
)

hourly_tests = replace_once(
    hourly_tests,
    '''from integrations_control_center.snapchat_account_hourly_refresh import (\n    aggregate_account_hours_by_riyadh_day,\n)\n''',
    '''from integrations_control_center.snapchat_account_hourly_refresh import (\n    aggregate_account_hours_by_riyadh_day,\n    snapchat_hourly_request_window,\n)\n''',
    "hourly helper test import",
)

hourly_tests += '''\n\ndef test_current_riyadh_day_ends_at_last_completed_hour():\n    start, end = snapchat_hourly_request_window(\n        date(2026, 8, 1),\n        date(2026, 8, 2),\n        now=datetime(2026, 8, 2, 12, 31, tzinfo=timezone.utc),\n    )\n\n    assert start.isoformat() == "2026-08-01T00:00:00+03:00"\n    assert end.isoformat() == "2026-08-02T15:00:00+03:00"\n    assert end <= datetime(2026, 8, 2, 12, 31, tzinfo=timezone.utc).astimezone(end.tzinfo)\n\n\ndef test_midnight_window_does_not_request_the_following_day():\n    start, end = snapchat_hourly_request_window(\n        date(2026, 8, 1),\n        date(2026, 8, 2),\n        now=datetime(2026, 8, 1, 21, 10, tzinfo=timezone.utc),\n    )\n\n    assert start.isoformat() == "2026-08-01T00:00:00+03:00"\n    assert end.isoformat() == "2026-08-02T00:00:00+03:00"\n'''

scheduler_tests += '''\n\ndef test_safe_summary_preserves_account_error_samples():\n    summary = scheduler._safe_summary({\n        "errors_count": 1,\n        "error_samples": [{\n            "error_id": "err-1",\n            "ad_account_id": "account-1",\n            "code": "snapchat_request_failed",\n            "message": "provider rejected the range",\n            "retryable": True,\n            "secret": "must-not-leak",\n        }],\n    })\n\n    assert summary["error_samples"] == [{\n        "error_id": "err-1",\n        "ad_account_id": "account-1",\n        "code": "snapchat_request_failed",\n        "message": "provider rejected the range",\n        "retryable": True,\n    }]\n'''

hourly_path.write_text(hourly, encoding="utf-8")
scheduler_path.write_text(scheduler, encoding="utf-8")
hourly_test_path.write_text(hourly_tests, encoding="utf-8")
scheduler_test_path.write_text(scheduler_tests, encoding="utf-8")

print("SNAPCHAT_CURRENT_HOUR_SYNC_PATCH_APPLIED")
