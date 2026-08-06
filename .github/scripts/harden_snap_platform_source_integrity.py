from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    file = Path(path)
    text = file.read_text(encoding="utf-8")
    if new in text:
        return
    if old not in text:
        raise SystemExit(f"pattern not found in {path}: {old[:160]!r}")
    file.write_text(text.replace(old, new, 1), encoding="utf-8")


module = "backend/integrations_control_center/snapchat_platform_source_integrity.py"
replace_once(
    module,
    """def aggregate_total_campaign_metrics(
    rows: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    source = [
        row
        for row in rows
        if _text(row.get("campaign_id"))
        or row.get("direct_account_fallback") is True
    ]
    if not source:
        return {key: 0 for key in STAT_FIELDS}
    bucket = _new_bucket()
    for row in source:
        metrics = {
            key: _as_number((row.get("metrics") or {}).get(key))
            for key in STAT_FIELDS
        }
        _add_to_bucket(
            bucket,
            metrics,
            provider_start=row.get("start_time"),
            provider_end=row.get("end_time"),
        )
    return _finalize_bucket(bucket)
""",
    """def aggregate_total_campaign_metrics(
    rows: list[dict[str, Any]],
) -> dict[str, int | float | None]:
    \"\"\"Sum requested TOTAL metrics, treating omitted zero fields as zero.

    Snapchat may omit a requested conversion field on a campaign that has no
    value.  Requiring every campaign row to carry every key would turn a valid
    account total into ``None``.  A successful authoritative breakdown makes
    an omitted requested field equivalent to zero for that campaign.
    \"\"\"
    source = [
        row
        for row in rows
        if _text(row.get("campaign_id"))
        or row.get("direct_account_fallback") is True
    ]
    if not source:
        return {key: 0 for key in STAT_FIELDS}
    sums = {key: 0.0 for key in STAT_FIELDS}
    for row in source:
        metrics = row.get("metrics") or {}
        for key in STAT_FIELDS:
            value = _as_number(metrics.get(key))
            if value is not None:
                sums[key] += float(value)
    return {
        key: int(value) if float(value).is_integer() else value
        for key, value in sums.items()
    }


def total_snapshot_is_authoritative(
    *,
    breakdown_seen: bool,
    errors: list[dict[str, Any]],
) -> bool:
    \"\"\"Only complete campaign breakdowns may replace the prior snapshot.\"\"\"
    return bool(breakdown_seen and not errors)
""",
)
replace_once(
    module,
    """            persisted = await persist_account_total_day(
                context,
                account=account,
                timezone_name=timezone_name,
                date_string=report_date.isoformat(),
                rows=rows,
                provider_start=request_start,
                provider_end=request_end,
                authoritative_breakdown=breakdown_seen,
                errors=day_errors,
            )
            saved += int(persisted["account_rows_saved"])
            campaign_saved += int(persisted["campaign_rows_saved"])
            for error in day_errors:
                errors.append({"date": report_date.isoformat(), **error})
""",
    """            for error in day_errors:
                errors.append({"date": report_date.isoformat(), **error})
            if not total_snapshot_is_authoritative(
                breakdown_seen=breakdown_seen,
                errors=day_errors,
            ):
                errors.append({
                    "date": report_date.isoformat(),
                    "code": "snapchat_platform_total_snapshot_partial",
                    "message": (
                        "Snapchat TOTAL campaign breakdown was incomplete; "
                        "the previous complete snapshot was preserved."
                    ),
                    "retryable": True,
                })
                continue
            persisted = await persist_account_total_day(
                context,
                account=account,
                timezone_name=timezone_name,
                date_string=report_date.isoformat(),
                rows=rows,
                provider_start=request_start,
                provider_end=request_end,
                authoritative_breakdown=True,
                errors=[],
            )
            saved += int(persisted["account_rows_saved"])
            campaign_saved += int(persisted["campaign_rows_saved"])
""",
)
replace_once(
    module,
    """    "refresh_account_total_snapshots",
]
""",
    """    "refresh_account_total_snapshots",
    "total_snapshot_is_authoritative",
]
""",
)

backend_test = "backend/tests/test_snapchat_platform_source_integrity_v1.py"
text = Path(backend_test).read_text(encoding="utf-8")
text = text.replace(
    """    audit_platform_purchase_totals,
    extract_account_total_campaign_rows,
)
""",
    """    audit_platform_purchase_totals,
    extract_account_total_campaign_rows,
    total_snapshot_is_authoritative,
)
""",
    1,
)
if "test_total_aggregation_treats_omitted_zero_metrics_as_zero" not in text:
    text += """


def test_total_aggregation_treats_omitted_zero_metrics_as_zero():
    rows = [
        {
            "campaign_id": "campaign-1",
            "metrics": {
                "spend": 100_000_000,
                "conversion_purchases": 2,
                "conversion_purchases_value": 300_000_000,
            },
        },
        {
            "campaign_id": "campaign-2",
            "metrics": {
                "spend": 50_000_000,
            },
        },
    ]
    metrics = aggregate_total_campaign_metrics(rows)
    assert metrics["spend"] == 150_000_000
    assert metrics["conversion_purchases"] == 2
    assert metrics["conversion_purchases_value"] == 300_000_000
    assert metrics["impressions"] == 0


def test_partial_total_response_never_replaces_complete_snapshot():
    assert total_snapshot_is_authoritative(
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
"""
Path(backend_test).write_text(text, encoding="utf-8")
