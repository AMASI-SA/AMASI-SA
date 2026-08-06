from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:180]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


module = "backend/integrations_control_center/snapchat_platform_source_integrity.py"

replace_once(
    module,
    '''async def fetch_account_total_campaign_rows(
''',
    '''def _normalized_requested_metrics(
    metrics: dict[str, Any] | None,
) -> dict[str, int | float]:
    source = metrics if isinstance(metrics, dict) else {}
    output: dict[str, int | float] = {}
    for key in STAT_FIELDS:
        value = _as_number(source.get(key))
        number = float(value) if value is not None else 0.0
        output[key] = int(number) if number.is_integer() else number
    return output


def extract_account_total_metrics(
    payload: dict[str, Any],
) -> tuple[dict[str, int | float] | None, list[dict[str, Any]], int]:
    """Extract the exact ad-account TOTAL row without a campaign breakdown."""
    wrapped_stats = payload.get("total_stats") or []
    if not isinstance(wrapped_stats, list):
        raise SnapchatNativeSyncError(
            "snapchat_account_total_payload_invalid",
            "Snapchat returned invalid direct account TOTAL data.",
            status_code=502,
            retryable=True,
        )
    errors: list[dict[str, Any]] = []
    successful_subrequests = 0
    metrics: dict[str, int | float] | None = None
    for wrapped in wrapped_stats:
        if not isinstance(wrapped, dict):
            continue
        status = _text(wrapped.get("sub_request_status") or "SUCCESS").upper()
        if "FAIL" in status or "ERROR" in status:
            errors.append(_subrequest_error(wrapped, status))
            continue
        successful_subrequests += 1
        stat = wrapped.get("total_stat", wrapped)
        if not isinstance(stat, dict):
            continue
        raw = stat.get("stats")
        if isinstance(raw, dict):
            metrics = _normalized_requested_metrics(raw)
    return metrics, errors, successful_subrequests


async def fetch_account_total_direct_metrics(
    context: SnapchatSyncContext,
    client: httpx.AsyncClient,
    access_token: str,
    *,
    account_id: str,
    request_start: datetime,
    request_end: datetime,
) -> tuple[dict[str, int | float], list[dict[str, Any]]]:
    """Read the exact All Ads total shown at ad-account level."""
    url = f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    payload = await context.get_json(
        client,
        url,
        headers=headers,
        params={
            "start_time": request_start.isoformat(timespec="seconds"),
            "end_time": request_end.isoformat(timespec="seconds"),
            "granularity": PLATFORM_TOTAL_GRANULARITY,
            "fields": ",".join(STAT_FIELDS),
            "omit_empty": "false",
            "conversion_source_types": CONVERSION_SOURCE_TYPES,
            "swipe_up_attribution_window": SWIPE_ATTRIBUTION_WINDOW,
            "view_attribution_window": VIEW_ATTRIBUTION_WINDOW,
            "action_report_time": ACTION_REPORT_TIME,
        },
    )
    metrics, errors, successful_subrequests = extract_account_total_metrics(
        payload
    )
    if metrics is None:
        first = errors[0] if errors else {}
        raise SnapchatNativeSyncError(
            _text(first.get("code") or "snapchat_account_direct_total_missing"),
            _text(
                first.get("message")
                or "Snapchat did not return the direct ad-account TOTAL row."
            ),
            status_code=502,
            retryable=True,
            result={
                "successful_subrequests": successful_subrequests,
                "errors": errors[:10],
            },
        )
    return metrics, errors


async def fetch_account_total_campaign_rows(
''',
)

replace_once(
    module,
    '''    source = [
        row
        for row in rows
        if _text(row.get("campaign_id"))
        or row.get("direct_account_fallback") is True
    ]
''',
    '''    campaign_rows = [
        row for row in rows if _text(row.get("campaign_id"))
    ]
    source = campaign_rows or [
        row for row in rows if row.get("direct_account_fallback") is True
    ]
''',
)

replace_once(
    module,
    '''def total_snapshot_is_authoritative(
    *,
    breakdown_seen: bool,
    errors: list[dict[str, Any]],
) -> bool:
    """Only complete campaign breakdowns may replace the prior snapshot."""
    return bool(breakdown_seen and not errors)
''',
    '''def total_snapshot_is_authoritative(
    *,
    breakdown_seen: bool,
    account_metrics_available: bool,
    errors: list[dict[str, Any]],
) -> bool:
    """Require both the direct account total and complete campaign breakdown."""
    return bool(breakdown_seen and account_metrics_available and not errors)
''',
)

replace_once(
    module,
    '''    provider_start: Any,
    provider_end: Any,
) -> None:
''',
    '''    provider_start: Any,
    provider_end: Any,
    provider_breakdown: str | None,
) -> None:
''',
)
replace_once(
    module,
    '''        "provider_breakdown": PLATFORM_TOTAL_BREAKDOWN,
''',
    '''        "provider_breakdown": provider_breakdown,
''',
)

replace_once(
    module,
    '''    rows: list[dict[str, Any]],
    provider_start: datetime,
''',
    '''    rows: list[dict[str, Any]],
    account_metrics: dict[str, Any],
    provider_start: datetime,
''',
)
replace_once(
    module,
    '''            provider_start=row.get("start_time") or provider_start,
            provider_end=row.get("end_time") or provider_end,
        )
    account_metrics = aggregate_total_campaign_metrics(rows)
''',
    '''            provider_start=row.get("start_time") or provider_start,
            provider_end=row.get("end_time") or provider_end,
            provider_breakdown=PLATFORM_TOTAL_BREAKDOWN,
        )
''',
)
replace_once(
    module,
    '''        provider_start=provider_start,
        provider_end=provider_end,
    )
''',
    '''        provider_start=provider_start,
        provider_end=provider_end,
        provider_breakdown=None,
    )
''',
)

replace_once(
    module,
    '''            rows, day_errors, breakdown_seen = (
                await fetch_account_total_campaign_rows(
                    context,
                    client,
                    access_token,
                    account_id=account_id,
                    request_start=request_start,
                    request_end=request_end,
                )
            )
            for error in day_errors:
                errors.append({"date": report_date.isoformat(), **error})
            if not total_snapshot_is_authoritative(
                breakdown_seen=breakdown_seen,
                errors=day_errors,
            ):
''',
    '''            account_metrics, account_errors = (
                await fetch_account_total_direct_metrics(
                    context,
                    client,
                    access_token,
                    account_id=account_id,
                    request_start=request_start,
                    request_end=request_end,
                )
            )
            rows, campaign_errors, breakdown_seen = (
                await fetch_account_total_campaign_rows(
                    context,
                    client,
                    access_token,
                    account_id=account_id,
                    request_start=request_start,
                    request_end=request_end,
                )
            )
            day_errors = [*account_errors, *campaign_errors]
            for error in day_errors:
                errors.append({"date": report_date.isoformat(), **error})
            if not total_snapshot_is_authoritative(
                breakdown_seen=breakdown_seen,
                account_metrics_available=bool(account_metrics),
                errors=day_errors,
            ):
''',
)
replace_once(
    module,
    '''                rows=rows,
                provider_start=request_start,
''',
    '''                rows=rows,
                account_metrics=account_metrics,
                provider_start=request_start,
''',
)

replace_once(
    module,
    '''        "provider_breakdown": PLATFORM_TOTAL_BREAKDOWN,
        "request_windows": request_windows,
''',
    '''        "provider_breakdown": PLATFORM_TOTAL_BREAKDOWN,
        "direct_account_total_requested": True,
        "request_windows": request_windows,
''',
)

replace_once(
    module,
    '''        "platform_total_snapshot_ready": True,
        "platform_source_isolated": True,
''',
    '''        "platform_total_snapshot_ready": True,
        "platform_direct_account_total_ready": bool(account_rows),
        "platform_source_isolated": True,
''',
)

replace_once(
    module,
    '''    "extract_account_total_campaign_rows",
    "fetch_account_total_campaign_rows",
''',
    '''    "extract_account_total_campaign_rows",
    "extract_account_total_metrics",
    "fetch_account_total_campaign_rows",
    "fetch_account_total_direct_metrics",
''',
)

backend_test = Path("backend/tests/test_snapchat_platform_source_integrity_v1.py")
text = backend_test.read_text(encoding="utf-8")
text = text.replace(
    '''    audit_platform_purchase_totals,
    extract_account_total_campaign_rows,
    total_snapshot_is_authoritative,
''',
    '''    audit_platform_purchase_totals,
    extract_account_total_campaign_rows,
    extract_account_total_metrics,
    total_snapshot_is_authoritative,
''',
    1,
)
text = text.replace(
    '''        requested_days=1,
    ) is True
''',
    '''        account_metrics_available=True,
        requested_days=1,
    ) is True
''',
) if False else text
text = text.replace(
    '''    assert total_snapshot_is_authoritative(
        breakdown_seen=True,
        errors=[],
    ) is True
    assert total_snapshot_is_authoritative(
        breakdown_seen=False,
        errors=[],
    ) is False
    assert total_snapshot_is_authoritative(
        breakdown_seen=True,
        errors=[{"code": "partial"}],
    ) is False
''',
    '''    assert total_snapshot_is_authoritative(
        breakdown_seen=True,
        account_metrics_available=True,
        errors=[],
    ) is True
    assert total_snapshot_is_authoritative(
        breakdown_seen=False,
        account_metrics_available=True,
        errors=[],
    ) is False
    assert total_snapshot_is_authoritative(
        breakdown_seen=True,
        account_metrics_available=False,
        errors=[],
    ) is False
    assert total_snapshot_is_authoritative(
        breakdown_seen=True,
        account_metrics_available=True,
        errors=[{"code": "partial"}],
    ) is False
''',
    1,
)
if "test_direct_account_total_stays_separate_from_campaign_breakdown" not in text:
    text += '''


def test_direct_account_total_stays_separate_from_campaign_breakdown():
    payload = {
        "total_stats": [{
            "sub_request_status": "SUCCESS",
            "total_stat": {
                "stats": {
                    "spend": 489_090_000,
                    "conversion_purchases": 21,
                    "conversion_purchases_value": 811_370_000,
                },
            },
        }],
    }
    metrics, errors, successful = extract_account_total_metrics(payload)
    assert errors == []
    assert successful == 1
    assert metrics["spend"] == 489_090_000
    assert metrics["conversion_purchases"] == 21
    assert metrics["conversion_purchases_value"] == 811_370_000
    assert metrics["impressions"] == 0
'''
backend_test.write_text(text, encoding="utf-8")
