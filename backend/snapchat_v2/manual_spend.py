"""Bounded account-spend observations for the dashboard, separate from finance.

Only the explicit manual job produces these snapshots. They never become
canonical facts, reconciliation evidence, or automatic-sync freshness markers.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
import math
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

from .accounts import list_enabled_accounts
from .client import SNAPCHAT_API_BASE, SnapchatClientError, SnapchatV2Client

COLLECTION = "mezan_snapchat_manual_spend_snapshots_v2"
JOBS_COLLECTION = "mezan_snapchat_manual_spend_jobs_v2"
SCOPE = "snapchat_spend_only"
SOURCE_MODE = "manual_account_hour"
VERSION = "snapchat-manual-account-spend-v1"
RIYADH = "Asia/Riyadh"
MAX_DAYS = 93
MAX_ACCOUNTS = 10
MAX_WINDOW_DAYS = 7
MAX_JOB_HTTP_REQUESTS = 128
HTTP_TIMEOUT_SECONDS = 15
ACCOUNT_TIMEOUT_SECONDS = 120
JOB_TIMEOUT_SECONDS = 300
SNAPSHOT_MAX_AGE = timedelta(hours=24)
SNAPSHOT_STALE_AFTER = timedelta(hours=2)
RECORD_RETENTION = timedelta(days=7)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def stored_time(value: Any) -> datetime:
    if not isinstance(value, datetime):
        raise ValueError("invalid_stored_timestamp")
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _provider_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("missing_provider_timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("naive_provider_timestamp")
    return parsed.astimezone(timezone.utc)


def _number(value: Any) -> float:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise ValueError("explicit_spend_required")
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("invalid_spend")
    return number


def account_identity(account: dict[str, Any]) -> dict[str, str]:
    account_id = account.get("ad_account_id")
    currency = account.get("currency")
    zone = account.get("timezone")
    if (not isinstance(account_id, str) or not account_id or len(account_id) > 128
            or not isinstance(currency, str) or len(currency) != 3 or not currency.isalpha()
            or currency != currency.upper() or not isinstance(zone, str)):
        raise ValueError("invalid_account_identity")
    ZoneInfo(zone)
    return {"ad_account_id": account_id, "currency": currency, "timezone": zone}


def period_window(date_from: date, date_to: date, requested_at: datetime) -> tuple[datetime, datetime]:
    current = stored_time(requested_at)
    today = current.astimezone(ZoneInfo(RIYADH)).date()
    if date_to < date_from or (date_to - date_from).days >= MAX_DAYS or date_to > today:
        raise ValueError("invalid_snapchat_manual_spend_period")
    start = datetime.combine(date_from, time.min, ZoneInfo(RIYADH)).astimezone(timezone.utc)
    end = datetime.combine(date_to + timedelta(days=1), time.min, ZoneInfo(RIYADH)).astimezone(timezone.utc)
    # Snapchat reports the current partial hour under the enclosing HOUR window.
    cap = current.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return start, min(end, cap)


def _parse_hours(payload: dict[str, Any], account_id: str, start: datetime,
                 end: datetime) -> list[dict[str, Any]]:
    if str(payload.get("request_status", "")).upper() != "SUCCESS":
        raise ValueError("provider_request_incomplete")
    paging = payload.get("paging", {})
    if not isinstance(paging, dict) or paging.get("next_link") not in (None, ""):
        raise ValueError("provider_pagination_incomplete")
    wrappers = payload.get("timeseries_stats")
    if not isinstance(wrappers, list) or len(wrappers) != 1 or not isinstance(wrappers[0], dict):
        raise ValueError("invalid_account_envelope")
    wrapper = wrappers[0]
    if str(wrapper.get("sub_request_status", "")).upper() != "SUCCESS":
        raise ValueError("provider_subrequest_incomplete")
    stat = wrapper.get("timeseries_stat")
    if (not isinstance(stat, dict) or stat.get("id") != account_id
            or stat.get("type") != "AD_ACCOUNT" or stat.get("granularity") != "HOUR"
            or _provider_time(stat.get("start_time")) != start
            or _provider_time(stat.get("end_time")) != end
            or "breakdown_stats" in stat):
        raise ValueError("provider_scope_mismatch")
    points = stat.get("timeseries")
    expected = int((end - start).total_seconds() // 3600)
    if not isinstance(points, list) or not points or len(points) != expected:
        raise ValueError("hourly_spend_incomplete")
    values: dict[datetime, float] = {}
    for point in points:
        if not isinstance(point, dict) or not isinstance(point.get("stats"), dict):
            raise ValueError("hourly_spend_incomplete")
        left, right = _provider_time(point.get("start_time")), _provider_time(point.get("end_time"))
        if (left < start or left >= end or left.minute or left.second or left.microsecond
                or right != left + timedelta(hours=1) or left in values):
            raise ValueError("invalid_hourly_coverage")
        values[left] = round(_number(point["stats"].get("spend")) / 1_000_000, 6)
    if len(values) != expected:
        raise ValueError("hourly_spend_incomplete")
    return [{"hour_start_utc": point, "hour_end_utc": point + timedelta(hours=1),
             "spend_native": values[point]} for point in sorted(values)]


class _BudgetHTTP:
    def __init__(self, http: Any, job_budget: dict[str, int], maximum: int):
        self.http, self.job_budget, self.maximum = http, job_budget, maximum
        self.calls = 0

    async def get(self, *args, **kwargs):
        if self.calls >= self.maximum or self.job_budget["used"] >= MAX_JOB_HTTP_REQUESTS:
            raise SnapchatClientError("manual_spend_request_budget", "Spend request budget reached.")
        self.calls += 1
        self.job_budget["used"] += 1
        return await self.http.get(*args, **kwargs)


async def fetch_manual_spend(db: Any, *, user_id: str, account: dict[str, Any],
                             date_from: date, date_to: date, job_id: str,
                             requested_at: datetime, now: Callable[[], datetime] = utcnow,
                             client_factory: Callable[..., Any] | None = None,
                             job_budget: dict[str, int] | None = None) -> dict[str, Any]:
    identity = account_identity(account)
    start, end = period_window(date_from, date_to, requested_at)
    factory = client_factory or SnapchatV2Client
    client = factory(db, str(user_id), now=now)
    native_tz = ZoneInfo(identity["timezone"])
    budget = job_budget if job_budget is not None else {"used": 0}
    chunks = math.ceil((end - start) / timedelta(days=MAX_WINDOW_DAYS))
    rows: list[dict[str, Any]] = []
    request_started = stored_time(now())
    async with client.client_factory(timeout=HTTP_TIMEOUT_SECONDS) as http:
        transport = _BudgetHTTP(http, budget, maximum=chunks * 4)
        cursor = start
        while cursor < end:
            stop = min(end, cursor + timedelta(days=MAX_WINDOW_DAYS))
            params = {"granularity": "HOUR", "fields": "spend", "omit_empty": "false",
                      "start_time": cursor.astimezone(native_tz).isoformat(timespec="seconds"),
                      "end_time": stop.astimezone(native_tz).isoformat(timespec="seconds")}
            try:
                payload = await client._request_json(transport,
                    f"{SNAPCHAT_API_BASE}/adaccounts/{quote(identity['ad_account_id'], safe='')}/stats",
                    params=params)
            except SnapchatClientError as exc:
                if exc.code == "snapchat_provider_http_400":
                    raise SnapchatClientError("unsupported_account_hour",
                        "Account-level hourly spend is unavailable.") from exc
                raise
            rows.extend(_parse_hours(payload, identity["ad_account_id"], cursor, stop))
            cursor = stop
    observed = stored_time(now())
    if observed < request_started or observed - request_started > timedelta(seconds=ACCOUNT_TIMEOUT_SECONDS):
        raise ValueError("manual_spend_observation_expired")
    if not math.isfinite(sum(row["spend_native"] for row in rows)):
        raise ValueError("invalid_spend_total")
    return {"_id": f"{job_id}:{identity['ad_account_id']}", "version": VERSION,
            "user_id": str(user_id), "provider": "snapchat_ads", "scope": SCOPE,
            "source_mode": SOURCE_MODE, "job_id": job_id,
            "ad_account_id": identity["ad_account_id"], "currency": identity["currency"],
            "account_timezone": identity["timezone"], "projection_timezone": RIYADH,
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "requested_at": stored_time(requested_at), "observed_at": observed,
            "expires_at": observed + RECORD_RETENTION,
            "window_start_utc": start, "window_end_utc": end, "hours": rows,
            "coverage": {"status": "complete", "expected_windows": chunks,
                         "completed_windows": chunks, "expected_hours": len(rows),
                         "received_hours": len(rows)}, "http_requests": transport.calls}


def _snapshot_days(snapshot: dict[str, Any], date_from: date, date_to: date) -> list[dict[str, Any]]:
    start, end = period_window(date_from, date_to, stored_time(snapshot["requested_at"]))
    if stored_time(snapshot["window_start_utc"]) != start or stored_time(snapshot["window_end_utc"]) != end:
        raise ValueError("snapshot_window_mismatch")
    hours = snapshot["hours"]
    count = int((end - start).total_seconds() // 3600)
    chunks = math.ceil((end - start) / timedelta(days=MAX_WINDOW_DAYS))
    if snapshot.get("coverage") != {"status": "complete", "expected_windows": chunks,
            "completed_windows": chunks, "expected_hours": count, "received_hours": count}:
        raise ValueError("snapshot_coverage_incomplete")
    if not isinstance(hours, list) or len(hours) != count or not hours:
        raise ValueError("snapshot_hours_incomplete")
    amounts: dict[datetime, float] = {}
    for index, row in enumerate(hours):
        point = start + timedelta(hours=index)
        if (stored_time(row["hour_start_utc"]) != point
                or stored_time(row["hour_end_utc"]) != point + timedelta(hours=1)):
            raise ValueError("snapshot_hours_mismatch")
        amounts[point] = _number(row["spend_native"])
    observed = stored_time(snapshot["observed_at"])
    days = []
    for offset in range((date_to - date_from).days + 1):
        day = date_from + timedelta(days=offset)
        midnight = datetime.combine(day, time.min, ZoneInfo(RIYADH)).astimezone(timezone.utc)
        daily_hours = []
        for index in range(24):
            point = midnight + timedelta(hours=index)
            daily_hours.append({"sequence": index, "local_hour": f"{index:02}:00",
                "hour_start_utc": point, "hour_end_utc": point + timedelta(hours=1),
                "spend_native": amounts.get(point), "status": "provisional" if point in amounts else "future"})
        total = round(sum(value["spend_native"] for value in daily_hours if value["spend_native"] is not None), 6)
        if not math.isfinite(total):
            raise ValueError("invalid_spend_total")
        days.append({"report_date": day.isoformat(), "base_spend_native": total,
            "hours": daily_hours, "data_state": "provisional_data" if total > 0 else "provisional_zero",
            "generated_at": observed, "source_latest_updated_at": observed, "source_sync_run_ids": []})
    return days


async def _canonical_supersedes(db: Any, *, user_id: str, account_id: str,
                                projections: list[dict[str, Any]], dates: set[str],
                                observed: datetime) -> bool:
    newer = set()
    for row in projections:
        day = row.get("report_date")
        if (day not in dates or row.get("ad_account_id") != account_id
                or row.get("user_id") != str(user_id) or row.get("projection_timezone") != RIYADH
                or row.get("amount_complete") is not True):
            continue
        try:
            if stored_time(row.get("source_latest_updated_at")) <= observed:
                continue
        except (TypeError, ValueError):
            continue
        run_ids = row.get("source_sync_run_ids") or []
        if (not isinstance(run_ids, list) or not run_ids or len(run_ids) > 100
                or any(not isinstance(value, str) or not value for value in run_ids)):
            continue
        runs = await db["mezan_snapchat_sync_runs_v2"].find({"user_id": str(user_id),
            "provider": "snapchat_ads", "ad_account_id": account_id,
            "sync_run_id": {"$in": run_ids}, "financial_sync_status": "complete",
            "status": {"$in": ["complete", "partial"]}},
            {"sync_run_id": 1, "finished_at": 1}).limit(100).to_list(length=100)
        try:
            proven = {run["sync_run_id"] for run in runs}
            fresh = any(stored_time(run.get("finished_at")) > observed for run in runs)
        except (KeyError, TypeError, ValueError):
            continue
        if proven == set(run_ids) and fresh:
            newer.add(day)
    return newer == dates


async def load_manual_spend_overlay(db: Any, *, user_id: str, account: dict[str, Any],
                                   date_from: date, date_to: date,
                                   projections: list[dict[str, Any]], now: datetime) -> dict[str, Any] | None:
    """Return a validated presentation copy; never modify financial collections."""
    try:
        identity = account_identity(account)
        current = stored_time(now)
        if not any(account_identity(row) == identity for row in await list_enabled_accounts(db, str(user_id))):
            return None
        query = {"user_id": str(user_id), "provider": "snapchat_ads", "scope": SCOPE,
            "ad_account_id": identity["ad_account_id"], "currency": identity["currency"],
            "account_timezone": identity["timezone"], "projection_timezone": RIYADH,
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "version": VERSION, "source_mode": SOURCE_MODE,
            "observed_at": {"$gte": current - SNAPSHOT_MAX_AGE, "$lte": current}}
        # Failed attempts and unpublished late-worker candidates must never
        # push the previous successful observation out of the bounded lookup.
        published = await db[JOBS_COLLECTION].find({"user_id": str(user_id), "scope": SCOPE,
            "active": False, "status": {"$in": ["complete", "partial"]},
            "date_from": date_from.isoformat(), "date_to": date_to.isoformat(),
            "accounts": {"$elemMatch": identity},
            "account_results": {"$elemMatch": {"account_id": identity["ad_account_id"],
                "status": "complete", "observed_at": {"$gte": current - SNAPSHOT_MAX_AGE, "$lte": current}}}
        }, {"_id": 1}).sort("finished_at", -1).limit(10).to_list(length=10)
        if not published:
            return None
        query["job_id"] = {"$in": [job["_id"] for job in published]}
        candidates = await db[COLLECTION].find(query).sort("observed_at", -1).limit(10).to_list(length=10)
        for snapshot in candidates:
            try:
                observed, requested = stored_time(snapshot["observed_at"]), stored_time(snapshot["requested_at"])
                if not requested <= observed <= current:
                    continue
                job = await db[JOBS_COLLECTION].find_one({"_id": snapshot["job_id"],
                    "user_id": str(user_id), "scope": SCOPE, "active": False,
                    "status": {"$in": ["complete", "partial"]},
                    "date_from": date_from.isoformat(), "date_to": date_to.isoformat()})
                if not job or stored_time(job["created_at"]) != requested or observed > stored_time(job["deadline_at"]):
                    continue
                proof = next((row for row in job.get("account_results", [])
                    if row.get("account_id") == identity["ad_account_id"] and row.get("status") == "complete"
                    and row.get("snapshot_job_id") == snapshot["job_id"]), None)
                frozen = next((row for row in job.get("accounts", []) if row.get("ad_account_id") == identity["ad_account_id"]), None)
                if (not proof or frozen != identity or stored_time(proof["observed_at"]) != observed
                        or observed > stored_time(job["finished_at"])):
                    continue
                days = _snapshot_days(snapshot, date_from, date_to)
                if await _canonical_supersedes(db, user_id=str(user_id), account_id=identity["ad_account_id"],
                        projections=projections, dates={row["report_date"] for row in days}, observed=observed):
                    return None
                return {"projections": days, "observed_at": observed, "sync_run_id": snapshot["job_id"],
                    "data_state": "explicit_hour_spend", "source_mode": SOURCE_MODE,
                    "source_collection": COLLECTION, "stale": current - observed > SNAPSHOT_STALE_AFTER}
            except (KeyError, TypeError, ValueError, OverflowError):
                continue
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return None
