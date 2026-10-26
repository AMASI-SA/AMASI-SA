"""Bounded, owner-only provenance for the Snapchat hierarchy readiness reader.

This is a database diagnostic, not a sync, repair, financial proof or decision
gate. Source schemas and reader selection are documented in PROVENANCE.md.
"""
from __future__ import annotations

import asyncio
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException
from pymongo.errors import PyMongoError

from snapchat_v2.accounts import SNAPCHAT_ACCOUNTS_COLLECTION
from snapchat_v2.committed_totals import COLLECTION as COMMITTED_COLLECTION
from snapchat_v2.entities import SNAPCHAT_ENTITY_FACTS_COLLECTION
from snapchat_v2.empty_hierarchy import VERSION as EMPTY_PROOF_VERSION, valid_proof
from snapchat_v2.models import (
    DEFAULT_SWIPE_ATTRIBUTION_WINDOW, DEFAULT_VIEW_ATTRIBUTION_WINDOW,
    SNAPCHAT_PROVIDER, build_attribution_key,
)
from snapchat_v2.sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
from snapchat_v2.total_facts import SNAPCHAT_TOTAL_FACTS_COLLECTION

MAX_DAYS = 7
MAX_FACT_ROWS = 2000
MAX_PRODUCING_RUNS = 100
MAX_RECENT_RUNS = 5
QUERY_TIMEOUT_MS = 1500
REQUEST_TIMEOUT_SECONDS = 8
LEVELS = ("campaign", "ad_squad", "ad")
LEVEL_FIELDS = {level: f"{level}_sync_status" for level in LEVELS}
INTEGRATION_ACCOUNTS = "mezan_integration_accounts_v2"
IDENTIFIER = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
FACT_FIELDS = (
    "user_id", "provider", "ad_account_id", "entity_type", "external_id",
    "report_date", "account_timezone", "currency", "action_report_time",
    "attribution_key", "sync_run_id",
)
COVERAGE_FIELDS = (
    "status", "data_state", "expected_requests", "completed_requests",
    "rows_received", "reason", "campaigns_requested", "identity_parent_matches",
    "campaign_rows",
)
EMPTY_ROOT_FIELDS = ("contract_version", "ad_account_id", "currency", "account_timezone",
                     "action_report_time", "attribution_key")
EMPTY_COVERAGE_FIELDS = (*COVERAGE_FIELDS, "provider_granularity", "frequency_summed")
EMPTY_PROOF_FIELDS = [*EMPTY_ROOT_FIELDS,
    *(f"campaign_response.{field}" for field in EMPTY_COVERAGE_FIELDS),
    *(f"catalog_responses.{level}.{field}" for level in LEVELS for field in EMPTY_COVERAGE_FIELDS)]
def _object_keys(path):
    return {"$map": {"input": {"$objectToArray": {"$ifNull": [f"${path}", {}]}},
                     "as": "member", "in": "$$member.k"}}


def _empty_proof_shape(path):
    # Reject unprojected keys rather than making differently stored proofs
    # compare equal after projection. The empty proof reader remains stricter
    # than old no-data run flags; this is not an alternate acceptance source.
    return {"$and": [
        {"$setEquals": [_object_keys(path), [*EMPTY_ROOT_FIELDS, "report_dates", "campaign_response", "catalog_responses"]]},
        {"$setEquals": [_object_keys(f"{path}.catalog_responses"), list(LEVELS)]},
        *({"$setIsSubset": [_object_keys(coverage_path), list(EMPTY_COVERAGE_FIELDS)]}
          for coverage_path in (f"{path}.campaign_response", *(f"{path}.catalog_responses.{level}" for level in LEVELS))),
    ]}


RUN_PROJECTION = {
    "_id": 0, "sync_run_id": 1, "status": 1, "run_type": 1, "stage": 1,
    "stage_status": 1, "started_at": 1, "finished_at": 1, "heartbeat_at": 1,
    "financial_sync_status": 1, "identity_sync_status": 1,
    "request_window.date_from": 1, "request_window.date_to": 1,
    "request_window.action_report_time": 1, "last_error.code": 1,
    **{f"summary.empty_hierarchy_proof.{field}": 1 for field in EMPTY_PROOF_FIELDS},
    "summary.empty_hierarchy_proof.report_dates": {"$slice": MAX_DAYS + 1},
    "empty_proof_shape_valid": _empty_proof_shape("summary.empty_hierarchy_proof"),
    **{field: 1 for field in LEVEL_FIELDS.values()},
    **{f"coverage.{level}.{field}": 1
       for level in (*LEVELS, "financial") for field in COVERAGE_FIELDS},
}


def _problem(code: str, status: int = 422) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code})


def _text(value: Any, limit: int = 128) -> str | None:
    return str(value)[:limit] if isinstance(value, (str, int)) and not isinstance(value, bool) else None


def _timestamp(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.isoformat()
    return _text(value, 80)


async def _find_rows(db, collection, query, projection, limit, *, sort=None):
    cursor = db[collection].find(query, projection)
    if sort:
        cursor = cursor.sort(sort)
    cursor = cursor.limit(limit + 1).max_time_ms(QUERY_TIMEOUT_MS)
    rows = list(await cursor.to_list(length=limit + 1))
    return rows[:limit], len(rows) > limit


async def _aggregate_rows(db, collection, pipeline, limit):
    cursor = db[collection].aggregate(pipeline, maxTimeMS=QUERY_TIMEOUT_MS)
    rows = list(await cursor.to_list(length=limit + 1))
    return rows[:limit], len(rows) > limit


def _committed_projection():
    # Slice before mapping: bounded arrays, with neither raw provider payloads
    # nor metric values returned from the database.
    return {
        "_id": 0, "report_date": 1, "sync_run_id": 1,
        "empty_rows_explicit": {"$and": [{"$eq": [f"$rows.{level}", []]} for level in LEVELS]},
        "empty_proof_shape_valid": _empty_proof_shape("empty_hierarchy_proof"),
        "empty_hierarchy_proof": {
            **{field: f"$empty_hierarchy_proof.{field}" for field in EMPTY_ROOT_FIELDS},
            "report_dates": {"$slice": [{"$ifNull": ["$empty_hierarchy_proof.report_dates", []]}, MAX_DAYS + 1]},
            "campaign_response": {field: f"$empty_hierarchy_proof.campaign_response.{field}" for field in EMPTY_COVERAGE_FIELDS},
            "catalog_responses": {level: {field: f"$empty_hierarchy_proof.catalog_responses.{level}.{field}"
                                          for field in EMPTY_COVERAGE_FIELDS} for level in LEVELS},
        },
        "rows": {level: {"$map": {
            "input": {"$slice": [{"$ifNull": [f"$rows.{level}", []]}, MAX_FACT_ROWS + 1]},
            "as": "fact", "in": {
                **{field: f"$$fact.{field}" for field in FACT_FIELDS},
                "coverage": {"status": "$$fact.coverage.status"},
            },
        }} for level in LEVELS},
    }


def _public_run(run):
    result = {field: _text(run.get(field)) for field in (
        "sync_run_id", "status", "run_type", "stage", "stage_status",
        "financial_sync_status", "identity_sync_status", *LEVEL_FIELDS.values(),
    )}
    result.update({field: _timestamp(run.get(field))
                   for field in ("started_at", "finished_at", "heartbeat_at")})
    window = run.get("request_window") or {}
    result["request_window"] = {field: _text(window.get(field), 40)
                                for field in ("date_from", "date_to", "action_report_time")}
    result["last_error_code"] = _text((run.get("last_error") or {}).get("code"))
    result["coverage"] = {}
    for level in (*LEVELS, "financial"):
        coverage = (run.get("coverage") or {}).get(level) or {}
        result["coverage"][level] = {
            field: value for field in COVERAGE_FIELDS
            if (value := coverage.get(field)) is not None
            and isinstance(value, (str, int, bool))
        }
        result["coverage"][level] = {
            field: value[:128] if isinstance(value, str) else value
            for field, value in result["coverage"][level].items()
        }
    return result


def _run_blockers(run, level, days):
    if run is None:
        return ["producing_run_missing_in_account_scope"]
    blockers = []
    for field, code in (
        ("status", "producing_run_not_complete"),
        ("financial_sync_status", "producing_financial_not_complete"),
        (LEVEL_FIELDS[level], "producing_level_not_complete"),
    ):
        if run.get(field) != "complete":
            blockers.append(code)
    window = run.get("request_window") or {}
    if (not isinstance(window.get("date_from"), str)
            or not isinstance(window.get("date_to"), str)
            or window["date_from"] > min(days) or window["date_to"] < max(days)):
        blockers.append("producing_run_window_does_not_cover_facts")
    if window.get("action_report_time") != "conversion":
        blockers.append("producing_run_action_report_time_mismatch")
    return blockers


def _level_evidence(level, facts, dates, runs, *, exact, invalid_rows, committed_days):
    by_day = defaultdict(list)
    by_run = defaultdict(set)
    blockers = set()
    if not exact:
        blockers.add("source_row_or_run_limit_exceeded")
    if invalid_rows:
        blockers.add("committed_fact_identity_invalid")
    for fact in facts:
        day = fact.get("report_date")
        if day not in dates:
            blockers.add("source_fact_date_outside_period")
            continue
        by_day[day].append(fact)
        if (fact.get("coverage") or {}).get("status") != "complete":
            blockers.add("source_fact_coverage_not_complete")
        run_id = str(fact.get("sync_run_id") or "")
        if not run_id:
            blockers.add("source_fact_producing_run_missing")
        else:
            by_run[run_id].add(day)
    missing = [day for day in dates if not by_day[day]]
    if missing:
        blockers.add("source_dates_missing" if exact and not invalid_rows else "source_dates_unobserved")
    if not facts:
        blockers.add("no_source_facts" if exact and not invalid_rows else "no_observed_source_facts")
    producing = []
    for run_id, days in sorted(by_run.items())[:MAX_PRODUCING_RUNS]:
        reasons = _run_blockers(runs.get(run_id), level, days)
        blockers.update(reasons)
        producing.append({"sync_run_id": run_id, "fact_dates": sorted(days),
                          "proven": not reasons, "blockers": reasons})
    proven = not blockers
    return {
        "status": "unavailable" if not exact or invalid_rows else "complete" if proven else "partial",
        "period_complete": None if not exact or invalid_rows else proven,
        "fact_count": len(facts) if exact and not invalid_rows else None,
        "observed_fact_count": len(facts),
        "entity_count": len({fact.get("external_id") for fact in facts if fact.get("external_id")})
                        if exact and not invalid_rows else None,
        "missing_dates": missing if exact and not invalid_rows else None,
        "dates_without_observed_facts": missing, "blockers": sorted(blockers),
        "empty_level_proof": "unavailable_current_fact_contract" if missing else "not_needed",
        "days": [{"date": day, "source": "committed" if day in committed_days else "mutable_total",
                  "observed_rows": len(by_day[day]),
                  "row_count": len(by_day[day]) if exact and not invalid_rows else None,
                  "coverage_complete_rows": sum((row.get("coverage") or {}).get("status") == "complete"
                                                for row in by_day[day])} for day in dates],
        "producing_run_evidence": producing,
    }


def _empty_period_evidence(committed, dates, runs, *, account_id, currency, zone, exact):
    """Apply the reader's exact committed empty contract to bounded projections."""
    if not any((row.get("empty_hierarchy_proof") or {}).get("contract_version") == EMPTY_PROOF_VERSION
               for row in committed):
        return None
    blockers = set()
    producing = defaultdict(set)
    if not exact:
        blockers.add("source_row_or_run_limit_exceeded")
    if {row.get("report_date") for row in committed} != set(dates):
        blockers.add("committed_empty_dates_missing")
    for snapshot in committed:
        day = snapshot.get("report_date")
        proof = snapshot.get("empty_hierarchy_proof") or {}
        if (snapshot.get("empty_rows_explicit") is not True
                or snapshot.get("empty_proof_shape_valid") is not True
                or any((snapshot.get("rows") or {}).get(level) != [] for level in LEVELS)
                or not valid_proof(proof, account_id=account_id, currency=currency,
                    timezone_name=zone, action="conversion", days={day})):
            blockers.add("committed_empty_proof_invalid")
            continue
        run_id = snapshot.get("sync_run_id")
        run = runs.get(run_id)
        if not run_id or run is None:
            blockers.add("producing_run_missing_in_account_scope")
            continue
        producing[run_id].add(day)
        for level in LEVELS:
            blockers.update(_run_blockers(run, level, {day}))
        if run.get("identity_sync_status") != "complete":
            blockers.add("producing_identity_not_complete")
        if (run.get("empty_proof_shape_valid") is not True
                or (run.get("summary") or {}).get("empty_hierarchy_proof") != proof):
            blockers.add("producing_empty_proof_mismatch")
    return {"proven": not blockers, "blockers": sorted(blockers),
            "producing": [{"sync_run_id": run_id, "fact_dates": [], "empty_proof_dates": sorted(days),
                           "proven": not blockers, "blockers": sorted(blockers)}
                          for run_id, days in sorted(producing.items())]}


async def _diagnose(db, user_id, account_id, date_from, date_to):
    scope = {"user_id": user_id, "provider": SNAPCHAT_PROVIDER, "ad_account_id": account_id}
    selected, overflow = await _find_rows(db, INTEGRATION_ACCOUNTS, {
        "user_id": user_id, "provider": SNAPCHAT_PROVIDER,
        "mezan_selected": True, "connection_status": "connected",
        "connection_provenance": "api_connection",
        "$or": [{"ad_account_id": account_id}, {"external_account_id": account_id}],
    }, {"_id": 0, "ad_account_id": 1, "external_account_id": 1, "currency": 1, "timezone": 1}, 1)
    if overflow or not selected:
        raise _problem("snapchat_readiness_selected_account_unavailable", 404)
    native = selected[0]
    if (str(native.get("ad_account_id") or native.get("external_account_id") or "") != account_id
            or native.get("external_account_id") not in {None, account_id}):
        raise _problem("snapchat_readiness_selected_account_unavailable", 404)
    reporting, overflow = await _find_rows(db, SNAPCHAT_ACCOUNTS_COLLECTION, {
        **scope, "active": True,
    }, {"_id": 0, "currency": 1, "timezone": 1, "connection_status": 1}, 1)
    base = {"provider": SNAPCHAT_PROVIDER, "account_id": account_id,
            "period": {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()},
            "read_only": True, "provider_calls": 0, "writes_performed": False,
            "read_consistency": "independent_bounded_reads_not_a_transaction",
            "decision_eligibility": {"eligible": False, "reason": "provenance_diagnostic_only"}}
    if overflow or not reporting:
        return {**base, "status": "unavailable", "blockers": ["reporting_account_registry_missing_or_ambiguous"]}
    account = reporting[0]
    if account.get("connection_status") != "connected":
        return {**base, "status": "unavailable", "blockers": ["reporting_account_not_connected"]}
    zone = account.get("timezone")
    try:
        if not isinstance(zone, str) or len(zone) > 80:
            raise ValueError
        ZoneInfo(zone)
    except (ValueError, ZoneInfoNotFoundError):
        return {**base, "status": "unavailable", "blockers": ["reporting_account_timezone_unavailable"]}
    windows = {"swipe": DEFAULT_SWIPE_ATTRIBUTION_WINDOW, "view": DEFAULT_VIEW_ATTRIBUTION_WINDOW}
    attribution = build_attribution_key("conversion", windows)
    dates = [(date_from + timedelta(days=i)).isoformat() for i in range((date_to - date_from).days + 1)]
    period_query = {**scope, "report_date": {"$gte": dates[0], "$lte": dates[-1]}}
    fact_projection = {"_id": 0, **{field: 1 for field in FACT_FIELDS}, "coverage.status": 1}
    raw, raw_truncated = await _find_rows(db, SNAPCHAT_TOTAL_FACTS_COLLECTION, {
        **period_query, "entity_type": {"$in": list(LEVELS)},
    }, fact_projection, MAX_FACT_ROWS, sort=[("report_date", 1), ("external_id", 1)])
    reader_filter = {"account_timezone": zone, "action_report_time": "conversion", "attribution_key": attribution}
    committed, committed_truncated = await _aggregate_rows(db, COMMITTED_COLLECTION, [
        {"$match": {**period_query, **reader_filter}}, {"$sort": {"report_date": 1}},
        {"$limit": MAX_DAYS + 1}, {"$project": _committed_projection()},
    ], MAX_DAYS)
    committed_days = {row.get("report_date") for row in committed}
    committed_truncated = committed_truncated or len(committed_days) != len(committed)
    selected_facts = {level: [] for level in LEVELS}
    invalid = Counter()
    truncated = {level: committed_truncated or (raw_truncated and set(dates) - committed_days != set())
                 for level in LEVELS}
    raw_counts = Counter(row.get("entity_type") for row in raw)
    matching_counts = Counter()
    exclusions = {level: Counter() for level in LEVELS}
    for row in raw:
        level = row["entity_type"]
        mismatch = [field for field, expected in reader_filter.items() if row.get(field) != expected]
        exclusions[level].update(mismatch)
        if mismatch:
            continue
        matching_counts[level] += 1
        if row.get("report_date") not in committed_days:
            selected_facts[level].append(row)
    for snapshot in committed:
        for level in LEVELS:
            rows = (snapshot.get("rows") or {}).get(level) or []
            if not isinstance(rows, list):
                invalid[level] += 1
                continue
            truncated[level] = truncated[level] or len(rows) > MAX_FACT_ROWS
            for row in rows[:MAX_FACT_ROWS]:
                expected = {**scope, **reader_filter, "entity_type": level, "report_date": snapshot.get("report_date")}
                if (not isinstance(row, dict) or any(row.get(key) != value for key, value in expected.items())
                        or not isinstance(row.get("external_id"), str) or not row["external_id"]):
                    invalid[level] += 1
                    continue
                selected_facts[level].append(row)
    run_ids = sorted({str(row.get("sync_run_id")) for facts in selected_facts.values()
                      for row in facts if row.get("sync_run_id")} |
                     {str(row["sync_run_id"]) for row in committed
                      if (row.get("empty_hierarchy_proof") or {}).get("contract_version") == EMPTY_PROOF_VERSION
                      and row.get("sync_run_id")})
    run_limit = len(run_ids) > MAX_PRODUCING_RUNS
    runs, runs_truncated = await _find_rows(db, SNAPCHAT_SYNC_RUNS_COLLECTION, {
        **scope, "sync_run_id": {"$in": run_ids[:MAX_PRODUCING_RUNS]},
    }, RUN_PROJECTION, MAX_PRODUCING_RUNS)
    run_map = {row["sync_run_id"]: row for row in runs}
    recent, _ = await _find_rows(db, SNAPCHAT_SYNC_RUNS_COLLECTION, {
        **scope, "request_window.date_from": {"$lte": dates[-1]},
        "request_window.date_to": {"$gte": dates[0]},
    }, RUN_PROJECTION, MAX_RECENT_RUNS, sort=[("started_at", -1)])
    identities, _ = await _aggregate_rows(db, SNAPCHAT_ENTITY_FACTS_COLLECTION, [
        {"$match": {**scope, "active": True, "entity_type": {"$in": list(LEVELS)}}},
        {"$group": {"_id": "$entity_type", "count": {"$sum": 1}}}, {"$limit": len(LEVELS)},
    ], len(LEVELS))
    catalog = {row["_id"]: row["count"] for row in identities}
    empty = _empty_period_evidence(committed, dates, run_map,
        account_id=account_id, currency=str(account.get("currency") or ""), zone=zone,
        exact=not (committed_truncated or run_limit or runs_truncated))
    levels = {}
    for level in LEVELS:
        evidence = _level_evidence(level, selected_facts[level], dates, run_map,
            exact=not (truncated[level] or run_limit or runs_truncated),
            invalid_rows=invalid[level], committed_days=committed_days)
        if empty is not None:
            evidence["empty_hierarchy_proven"] = empty["proven"]
            if empty["proven"] and not selected_facts[level] and not invalid[level] and not truncated[level]:
                evidence.update(status="complete", period_complete=True, fact_count=0, observed_fact_count=0,
                    entity_count=0, missing_dates=[], blockers=[], empty_level_proof="provider_confirmed_empty_hierarchy",
                    producing_run_evidence=empty["producing"])
                for day in evidence["days"]:
                    day["source"] = "committed_empty"
            elif not empty["proven"]:
                evidence["blockers"] = sorted(set(evidence["blockers"]) | set(empty["blockers"]))
        levels[level] = {**evidence,
            "raw_window_observed_rows": raw_counts[level], "raw_window_count_exact": not raw_truncated,
            "raw_reader_matching_observed_rows": matching_counts[level],
            "raw_filter_mismatch_counts": dict(exclusions[level]),
            "current_active_identity_count": catalog.get(level, 0),
            "identity_count_is_period_coverage_proof": False}
    return {**base, "status": "complete" if all(row["period_complete"] is True for row in levels.values()) else "incomplete",
        "account": {"currency": _text(account.get("currency"), 12), "timezone": zone,
                    "selected_metadata_matches_reporting": all(native.get(field) == account.get(field)
                                                                for field in ("currency", "timezone"))},
        "reader_filter": {**reader_filter, "attribution_windows": windows}, "levels": levels,
        "producing_runs": [_public_run(row) for row in runs],
        "recent_overlapping_runs": [_public_run(row) for row in recent],
        "recent_runs_are_substitute_proof": False,
        "collections": {"mutable_total": SNAPCHAT_TOTAL_FACTS_COLLECTION,
                        "committed": COMMITTED_COLLECTION, "runs": SNAPCHAT_SYNC_RUNS_COLLECTION,
                        "identities": SNAPCHAT_ENTITY_FACTS_COLLECTION},
        "limits": {"max_days": MAX_DAYS, "max_raw_rows": MAX_FACT_ROWS,
                   "max_committed_rows_per_day_level": MAX_FACT_ROWS,
                   "max_producing_runs": MAX_PRODUCING_RUNS,
                   "max_recent_runs": MAX_RECENT_RUNS,
                   "query_timeout_ms": QUERY_TIMEOUT_MS, "request_timeout_seconds": REQUEST_TIMEOUT_SECONDS},
        "observed_at": datetime.now(timezone.utc).isoformat()}


async def diagnose_snapchat_readiness_provenance(db, user_id: str, account_id: str, *, date_from: date, date_to: date):
    if not isinstance(account_id, str) or not IDENTIFIER.fullmatch(account_id):
        raise _problem("snapchat_readiness_account_id_invalid")
    if not isinstance(user_id, str) or not user_id or len(user_id) > 128:
        raise _problem("snapchat_readiness_owner_invalid", 403)
    if not isinstance(date_from, date) or not isinstance(date_to, date) or not 1 <= (date_to - date_from).days + 1 <= MAX_DAYS:
        raise _problem("snapchat_readiness_period_invalid")
    try:
        return await asyncio.wait_for(_diagnose(db, user_id, account_id, date_from, date_to), REQUEST_TIMEOUT_SECONDS)
    except asyncio.TimeoutError:
        raise _problem("snapchat_readiness_diagnostic_timeout", 504) from None
    except PyMongoError:
        raise _problem("snapchat_readiness_diagnostic_database_unavailable", 503) from None


def attach_snapchat_readiness_provenance_routes(router: APIRouter, db, current_user: Callable, require_owner: Callable):
    @router.get("/snapchat/readiness-provenance/{account_id}")
    async def readiness_provenance(account_id: str, date_from: date, date_to: date,
                                   user: dict = Depends(current_user)):
        owner = require_owner(user)
        user_id = str(owner.get("id") or owner.get("_id") or "")
        return await diagnose_snapchat_readiness_provenance(
            db, user_id, account_id, date_from=date_from, date_to=date_to,
        )
