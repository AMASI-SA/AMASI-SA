"""Strict saved campaign context for Google review, never product attribution.

No provider requests or writes. A campaign's reported purchases are not store
orders and must never be copied to each product associated with the campaign.
"""
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo

from accounting_currency import CURRENCY_CODES
from snapchat_v2.models import build_attribution_key


def _instant(value):
    if isinstance(value, datetime):
        # Mongo's native datetime decoder returns naive UTC by default.
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError("timestamp_unproved")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp_offset_unproved")
    return result.astimezone(timezone.utc)


def _number(value, *, integer=False):
    if type(value) not in (int, float) or not isfinite(value) or value < 0:
        raise ValueError("metric_unproved")
    if integer and value != int(value):
        raise ValueError("metric_fractional")
    return value


def snap_campaign_day(snapshot, run, *, owner_id, account_id, campaign_id, as_of):
    """Validate one explicit campaign row in an atomic completed native-day snapshot.

    An omitted row remains unknown even when the snapshot is complete: this
    module does not infer a historical campaign identity or zero activity.
    """
    now = _instant(as_of)
    required = {"user_id": owner_id, "provider": "snapchat_ads", "ad_account_id": account_id}
    if any(snapshot.get(k) != v or run.get(k) != v for k, v in required.items()):
        raise ValueError("source_scope_mismatch")
    run_id = snapshot.get("sync_run_id")
    if not isinstance(run_id, str) or not run_id.strip() or run_id != run.get("sync_run_id"):
        raise ValueError("source_run_mismatch")
    for key in ("status", "financial_sync_status", "campaign_sync_status", "ad_squad_sync_status", "ad_sync_status"):
        if run.get(key) != "complete":
            raise ValueError("source_run_incomplete")
    day = date.fromisoformat(snapshot["report_date"])
    if day.isoformat() != snapshot["report_date"]:
        raise ValueError("source_day_invalid")
    zone = ZoneInfo(snapshot["account_timezone"])
    start = datetime.combine(day, time.min, zone).astimezone(timezone.utc)
    end = datetime.combine(day + timedelta(days=1), time.min, zone).astimezone(timezone.utc)
    finished = _instant(run.get("finished_at"))
    if not end <= finished <= now or _instant(snapshot.get("finished_at")) != finished:
        raise ValueError("source_completion_unproved")
    for obj in (snapshot, run):
        if obj.get("expires_at") is not None and _instant(obj["expires_at"]) <= now:
            raise ValueError("source_expired")
    window = run.get("request_window") or {}
    if not window.get("date_from", "~") <= day.isoformat() <= window.get("date_to", ""):
        raise ValueError("source_run_window_mismatch")
    if _instant(window.get("start_utc")) > start or _instant(window.get("end_utc")) < end:
        raise ValueError("source_run_window_mismatch")
    action = snapshot.get("action_report_time")
    if action not in {"conversion", "impression"} or window.get("action_report_time") != action:
        raise ValueError("source_attribution_mismatch")
    rows = (snapshot.get("rows") or {}).get("campaign")
    if not isinstance(rows, list):
        raise ValueError("campaign_rows_unproved")
    selected = [r for r in rows if isinstance(r, dict) and r.get("external_id") == campaign_id]
    if len(selected) != 1:
        raise ValueError("campaign_row_missing_or_duplicate")
    row = selected[0]
    for key, value in {**required, "entity_type": "campaign", "campaign_id": campaign_id,
                       "report_date": day.isoformat(), "account_timezone": snapshot["account_timezone"],
                       "sync_run_id": run_id, "action_report_time": action,
                       "attribution_key": snapshot.get("attribution_key")}.items():
        if row.get(key) != value:
            raise ValueError("campaign_row_scope_mismatch")
    attribution = row.get("attribution_windows")
    if not isinstance(attribution, dict) or not attribution or build_attribution_key(action, attribution) != row.get("attribution_key"):
        raise ValueError("source_attribution_mismatch")
    if ((row.get("coverage") or {}).get("status") != "complete"
            or _instant(row.get("window_start_utc")) != start or _instant(row.get("window_end_utc")) != end
            or not end <= _instant(row.get("observed_at")) <= finished):
        raise ValueError("source_day_coverage_unproved")
    currency = row.get("currency")
    if currency not in CURRENCY_CODES:
        raise ValueError("currency_unproved")
    source = row.get("source") or {}
    if source.get("api") != "snapchat_marketing_api" or source.get("granularity") not in {"DAY", "TOTAL"} or source.get("breakdown") != "campaign":
        raise ValueError("source_provenance_unproved")
    metrics = {"spend_native": _number(row.get("spend_native")),
               "provider_reported_purchases": _number(row.get("purchases"), integer=True),
               "provider_reported_purchase_value_native": _number(row.get("purchase_value_native"))}
    # Existing collectors normalize absent raw metrics to numeric zero. Without
    # stored field-presence provenance a zero is not an observed provider zero.
    presence = {key: "positive_saved_source_observation" if value > 0 else "zero_or_source_omission_unproved"
                for key, value in metrics.items()}
    metrics = {key: value if value > 0 else None for key, value in metrics.items()}
    return {"provider": "snapchat_ads", "account_id": account_id, "campaign_id": campaign_id,
            "currency": currency, "timezone": snapshot["account_timezone"], "date": day.isoformat(),
            "window_start_utc": start.isoformat(), "window_end_utc": end.isoformat(),
            "attribution_key": row["attribution_key"], "attribution_windows": attribution,
            "action_report_time": action, "source_run_id": run_id, "observed_at": _instant(row["observed_at"]).isoformat(),
            "snapshot_finished_at": finished.isoformat(), "metrics": metrics,
            "metric_provenance": presence,
            "snapshot_age_seconds": (now - finished).total_seconds(), "live_freshness_verified": False,
            "source_collection": "mezan_snapchat_committed_totals_v1", "product_attribution_verified": False,
            "independent_store_orders": None, "profitability": None, "execution_authorized": False}
