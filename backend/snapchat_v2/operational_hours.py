"""Short-lived, current-day spend observations; never authoritative facts."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from .client import HTTP_TIMEOUT_SECONDS, SNAPCHAT_API_BASE, SnapchatClientError, _parse_datetime
from .models import DEFAULT_SWIPE_ATTRIBUTION_WINDOW, DEFAULT_VIEW_ATTRIBUTION_WINDOW
from .sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
from .facts import SNAPCHAT_HOURLY_FACTS_COLLECTION

RIYADH = "Asia/Riyadh"
VERSION = "snapchat-current-operational-hours-v1"
MAX_AGE = timedelta(minutes=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stored(value: Any) -> datetime:
    # Only server-produced BSON timestamps may lose their UTC tzinfo.
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return _parse_datetime(value, field="operational_timestamp")


def _number(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("explicit numeric spend required")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise ValueError("finite nonnegative spend required")
    return result


def _window(day: date, now: datetime) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, ZoneInfo(RIYADH)).astimezone(timezone.utc)
    end = min(start + timedelta(days=1), now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
    if day != now.astimezone(ZoneInfo(RIYADH)).date() or end <= start:
        raise ValueError("only the current Riyadh day is eligible")
    return start, end


def _parse(payload: dict[str, Any], account_id: str, start: datetime, end: datetime,
           *, breakdown: bool) -> tuple[dict[datetime, float], bool]:
    if str(payload.get("request_status", "")).upper() != "SUCCESS":
        raise ValueError("explicit successful request required")
    paging = payload.get("paging", {})
    if (not isinstance(paging, dict) or paging.get("next_link")
            or (paging.get("next_link") is not None and not isinstance(paging.get("next_link"), str))):
        raise ValueError("operational response is incomplete")
    wrappers = payload.get("timeseries_stats")
    if not isinstance(wrappers, list) or len(wrappers) != 1:
        raise ValueError("one account envelope required")
    wrapper = wrappers[0]
    if not isinstance(wrapper, dict) or str(wrapper.get("sub_request_status", "")).upper() != "SUCCESS":
        raise ValueError("explicit successful subrequest required")
    stat = wrapper.get("timeseries_stat")
    if (not isinstance(stat, dict) or stat.get("id") != account_id
            or stat.get("type") != "AD_ACCOUNT" or stat.get("granularity") != "HOUR"
            or _parse_datetime(stat.get("start_time"), field="start_time") != start
            or _parse_datetime(stat.get("end_time"), field="end_time") != end):
        raise ValueError("account/hour/window mismatch")
    expected = {start + timedelta(hours=i) for i in range(int((end - start).total_seconds() // 3600))}
    if breakdown:
        container = stat.get("breakdown_stats")
        series = container.get("campaign") if isinstance(container, dict) else None
    else:
        points = stat.get("timeseries")
        series = [{"id": account_id, "timeseries": points}] if isinstance(points, list) else None
    if not isinstance(series, list):
        raise ValueError("timeseries missing")
    if not series or (not breakdown and not series[0]["timeseries"]):
        return {}, True
    result: dict[datetime, float] = {}
    entities: set[str] = set()
    for entity in series:
        if not isinstance(entity, dict) or not isinstance(entity.get("id"), str) or not entity["id"] or entity["id"] in entities:
            raise ValueError("invalid/duplicate entity")
        entities.add(entity["id"])
        points = entity.get("timeseries")
        if not isinstance(points, list) or len(points) != len(expected):
            raise ValueError("missing hourly points")
        seen: set[datetime] = set()
        for point in points:
            if not isinstance(point, dict) or not isinstance(point.get("stats"), dict):
                raise ValueError("invalid hourly point")
            point_start = _parse_datetime(point.get("start_time"), field="start_time")
            point_end = _parse_datetime(point.get("end_time"), field="end_time")
            if point_start not in expected or point_start in seen or point_end != point_start + timedelta(hours=1):
                raise ValueError("hour gap/duplicate/window mismatch")
            seen.add(point_start)
            result[point_start] = result.get(point_start, 0.0) + _number(point["stats"].get("spend")) / 1_000_000
        if seen != expected:
            raise ValueError("incomplete hourly series")
    if any(not math.isfinite(value) for value in result.values()):
        raise ValueError("spend overflow")
    return result, False


async def fetch_current_operational_hours(client: Any, account: dict[str, Any], *,
        report_date: date, sync_run_id: str, now: datetime,
        projection_generated_at: datetime, owner_id: str,
        action_report_time: str = "conversion") -> dict[str, Any]:
    """Account HOUR, then a fixed campaign HOUR on 400; shared retries stay bounded."""
    current = _stored(now)
    start, end = _window(report_date, current)
    account_id = str(account.get("ad_account_id") or "")
    currency, account_timezone = account.get("currency"), account.get("timezone")
    if not account_id or not currency or not account_timezone or not sync_run_id or not owner_id or action_report_time != "conversion":
        raise ValueError("operational account identity incomplete")
    native_tz = ZoneInfo(account_timezone)
    params = {"granularity": "HOUR", "fields": "spend", "omit_empty": "false",
        "start_time": start.astimezone(native_tz).isoformat(timespec="seconds"),
        "end_time": end.astimezone(native_tz).isoformat(timespec="seconds"),
        "action_report_time": action_report_time,
        "swipe_up_attribution_window": DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
        "view_attribution_window": DEFAULT_VIEW_ATTRIBUTION_WINDOW, "limit": 200}
    fallback = False
    async with client.client_factory(timeout=HTTP_TIMEOUT_SECONDS) as http:
        try:
            payload = await client._request_json(http, f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats", params=params)
        except SnapchatClientError as exc:
            if exc.code != "snapchat_provider_http_400":
                raise
            fallback = True
            payload = await client._request_json(http, f"{SNAPCHAT_API_BASE}/adaccounts/{account_id}/stats", params={**params, "breakdown": "campaign"})
    amounts, empty = _parse(payload, account_id, start, end, breakdown=fallback)
    observed = _stored(client.now())
    if observed < current or observed - current > MAX_AGE:
        raise ValueError("observation request became stale")
    hours = []
    for index in range(24):
        point = start + timedelta(hours=index)
        covered = point < end and point <= current
        hours.append({"sequence": index, "local_hour": f"{index:02}:00",
            "hour_start_utc": point, "hour_end_utc": point + timedelta(hours=1),
            "spend_native": round(amounts.get(point, 0.0), 6) if covered else None,
            "status": "provisional" if covered else "future"})
    observation = {"version": VERSION, "user_id": str(client.user_id), "provider": "snapchat_ads",
        "ad_account_id": account_id, "currency": currency, "account_timezone": account_timezone,
        "projection_timezone": RIYADH, "report_date": report_date.isoformat(),
        "action_report_time": action_report_time, "attribution_windows": {
            "swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW, "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW},
        "sync_run_id": str(sync_run_id), "producer_owner_id": owner_id,
        "projection_generated_at": projection_generated_at,
        "window_start_utc": start, "window_end_utc": end, "requested_at": current,
        "observed_at": observed, "status": "complete", "expected_requests": 2 if fallback else 1,
        "completed_requests": 2 if fallback else 1, "provider_rows": len(amounts),
        "source_mode": "campaign_hour_http400" if fallback else "account_hour",
        "data_state": "observed_no_activity" if empty else "explicit_hour_spend",
        "explicit_spend_present": not empty}
    return {"operational_observation": observation, "operational_hours": hours}


async def current_operational_overlay(db: Any, *, user_id: str, account: dict[str, Any],
        projection: dict[str, Any], reconciliations: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Validate server provenance again at read time; never upgrade close proof."""
    try:
        current = _stored(now)
        day = current.astimezone(ZoneInfo(RIYADH)).date()
        proof = projection.get("operational_observation") or {}
        if not isinstance(proof, dict):
            return None
        account_id = str(account["ad_account_id"])
        run_id = str(projection.get("sync_run_id") or "")
        expected = {"version": VERSION, "user_id": str(user_id), "provider": "snapchat_ads",
            "ad_account_id": account_id, "currency": account["currency"],
            "account_timezone": account["timezone"], "projection_timezone": RIYADH,
            "report_date": day.isoformat(), "action_report_time": "conversion", "sync_run_id": run_id,
            "attribution_windows": {"swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW, "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW}}
        if not run_id or any(proof.get(key) != value for key, value in expected.items()):
            return None
        if any(projection.get(key) != value for key, value in expected.items()
               if key in {"user_id", "ad_account_id", "currency", "account_timezone", "projection_timezone", "report_date", "action_report_time"}):
            return None
        requested, observed = _stored(proof["requested_at"]), _stored(proof["observed_at"])
        generated = _stored(projection["generated_at"])
        start, end = _window(day, requested)
        count = 2 if proof.get("source_mode") == "campaign_hour_http400" else 1
        if (proof.get("source_mode") not in {"account_hour", "campaign_hour_http400"}
                or proof.get("status") != "complete"
                or type(proof.get("expected_requests")) is not int or proof["expected_requests"] != count
                or type(proof.get("completed_requests")) is not int or proof["completed_requests"] != count
                or not generated <= requested <= observed <= current or current - requested > MAX_AGE
                or _stored(proof["projection_generated_at"]) != generated
                or _stored(proof["window_start_utc"]) != start or _stored(proof["window_end_utc"]) != end):
            return None
        run = await db[SNAPCHAT_SYNC_RUNS_COLLECTION].find_one({"user_id": str(user_id),
            "ad_account_id": account_id, "sync_run_id": run_id, "provider": "snapchat_ads"})
        if not run or run.get("financial_sync_status") != "complete" or run.get("status") not in {"complete", "partial"}:
            return None
        if (not proof.get("producer_owner_id") or proof["producer_owner_id"] != run.get("owner_id")
                or projection.get("operational_attempt_run_id") != run_id
                or projection.get("operational_attempt_owner_id") != run.get("owner_id")):
            return None
        window = run.get("request_window") or {}
        if (window.get("action_report_time") != "conversion" or not window.get("date_from", "z") <= day.isoformat() <= window.get("date_to", "")
                or _stored(window["start_utc"]) > start or _stored(window["end_utc"]) < end):
            return None
        newer = await db[SNAPCHAT_SYNC_RUNS_COLLECTION].find_one({"user_id": str(user_id),
            "provider": "snapchat_ads", "ad_account_id": account_id,
            "sync_run_id": {"$ne": run_id}, "started_at": {"$gt": _stored(run["started_at"])},
            "request_window.date_from": {"$lte": day.isoformat()}, "request_window.date_to": {"$gte": day.isoformat()},
            "request_window.action_report_time": "conversion"})
        if newer:
            return None
        hours = projection.get("operational_hours")
        if not isinstance(hours, list) or len(hours) != 24:
            return None
        values = []
        for index, hour in enumerate(hours):
            if not isinstance(hour, dict):
                return None
            point = start + timedelta(hours=index)
            covered = point < end and point <= requested
            if (hour.get("sequence") != index or hour.get("local_hour") != f"{index:02}:00"
                    or _stored(hour["hour_start_utc"]) != point or _stored(hour["hour_end_utc"]) != point + timedelta(hours=1)
                    or hour.get("status") != ("provisional" if covered else "future")):
                return None
            if covered:
                values.append(_number(hour.get("spend_native")))
            elif hour.get("spend_native") is not None:
                return None
        empty = proof.get("data_state") == "observed_no_activity"
        if (not values or proof.get("data_state") not in {"observed_no_activity", "explicit_hour_spend"}
                or proof.get("explicit_spend_present") is not (not empty)
                or type(proof.get("provider_rows")) is not int
                or proof["provider_rows"] != (0 if empty else len(values)) or (empty and any(values))):
            return None
        if not math.isfinite(sum(values)):
            return None
        if sum(values) == 0:
            if _number(projection.get("base_spend_native")) > 0:
                return None
            positive = await db[SNAPCHAT_HOURLY_FACTS_COLLECTION].find_one({
                "user_id": str(user_id), "provider": "snapchat_ads", "ad_account_id": account_id,
                "entity_type": "ad_account", "external_id": account_id,
                "action_report_time": "conversion", "hour_start_utc": {"$gte": start, "$lt": end},
                "attribution_windows.swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW,
                "attribution_windows.view": DEFAULT_VIEW_ATTRIBUTION_WINDOW, "spend_native": {"$gt": 0}})
            if positive:
                return None
        matching = [row for row in reconciliations if all(row.get(key) == value for key, value in {
            "user_id": str(user_id), "ad_account_id": account_id, "report_date": day.isoformat(),
            "sync_run_id": run_id, "currency": account["currency"], "account_timezone": account["timezone"],
            "dashboard_timezone": RIYADH, "action_report_time": "conversion"}.items())
            and generated <= _stored(row["checked_at"]) <= current]
        if not matching:
            return None
        for row in matching:
            coverage = row.get("dashboard_provider_coverage") or {}
            if (coverage.get("status") != "complete" or type(coverage.get("expected_requests")) is not int
                    or coverage["expected_requests"] <= 0 or coverage.get("completed_requests") != coverage["expected_requests"]):
                return None
            if sum(values) == 0 and _number(row.get("provider_riyadh_window_spend_native")) > 0:
                return None
        return {"hours": hours, "spend_native": round(sum(values), 6), "sync_run_id": run_id,
            "data_state": proof["data_state"], "source_mode": proof["source_mode"], "observed_at": observed}
    except (KeyError, TypeError, ValueError, OverflowError, SnapchatClientError):
        return None
