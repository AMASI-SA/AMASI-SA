"""Fail-closed Snapchat spend source shared by both Dashboard read paths.

Only Riyadh-day, account-grain native facts are financial evidence.  A fact is
usable only when one unambiguous completed analytics run proves the same
tenant/account/day and the provider window is complete/fresh.  Legacy facts
without that proof remain unknown; this module never refreshes or writes data.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from ads_manager.account_cost_settings import COLLECTION as COST_SETTINGS_COLLECTION
from dashboard_v2_ad_costs import apply_cost_settings_to_fact_rows
from integrations_control_center import snapchat_account_hourly_refresh as snapchat_hourly
from integrations_control_center.snapchat_native_data_common import (
    ATTRIBUTION_MODEL,
    SNAPCHAT_PERFORMANCE_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
)


RIYADH_TZ = ZoneInfo("Asia/Riyadh")
RUNS_COLLECTION = "mezan_integration_sync_runs_v2"
ANALYTICS_RUN_TYPE = "analytics_refresh"
SOURCE_PREFIX = "snapchat_account_hourly_campaign_breakdown_riyadh_"
KNOWN_STATES = {"confirmed_data", "confirmed_zero", "confirmed_no_data"}
CURRENT_RUN_MAX_AGE = timedelta(minutes=20)
FUTURE_CLOCK_TOLERANCE = timedelta(minutes=5)
MAX_ACCOUNTS = 500
MAX_FACTS = 100_000
MAX_OVERLAP_RUNS = 100


def _text(value: Any) -> str:
    return str(value or "").strip()


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf") or parsed < 0:
        return None
    return parsed


def _strict_int(value: Any) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value


def _strict_nonnegative_number(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    parsed = float(value)
    if parsed != parsed or abs(parsed) == float("inf") or parsed < 0:
        return None
    return parsed


def _utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


async def _list(cursor: Any, limit: int) -> list[dict[str, Any]]:
    if hasattr(cursor, "to_list"):
        return await cursor.to_list(length=limit)
    return [row async for row in cursor]


async def _sorted_list(cursor: Any, limit: int) -> tuple[list[dict[str, Any]], bool]:
    if hasattr(cursor, "sort"):
        try:
            cursor = cursor.sort("started_at", -1)
        except TypeError:
            cursor = cursor.sort([("started_at", -1)])
    if hasattr(cursor, "limit"):
        cursor = cursor.limit(limit + 1)
    rows = await _list(cursor, limit + 1)
    return rows[:limit], len(rows) > limit


def _days(start: date, end: date) -> list[date]:
    if end < start:
        return []
    return [start + timedelta(days=offset) for offset in range((end - start).days + 1)]


def _coverage(value: Any) -> tuple[bool, str]:
    if not isinstance(value, dict) or value.get("status") != "complete":
        return False, "unknown_incomplete"
    state = _text(value.get("data_state"))
    if state not in KNOWN_STATES:
        return False, "unknown_incomplete"
    expected_i = _strict_int(value.get("expected_requests"))
    completed_i = _strict_int(value.get("completed_requests"))
    if expected_i is None or completed_i is None:
        return False, "unknown_incomplete"
    return expected_i > 0 and completed_i == expected_i, state


def _proof_scoped_value(
    row: dict[str, Any],
    key: str,
    proof: dict[str, Any],
) -> Any:
    return row.get(
        f"financial_{key}" if proof.get("financial_contract") is True else key
    )


def _run_contract(
    run: Any,
    *,
    user_id: str,
    expected_source: str,
    now: datetime,
) -> dict[str, Any]:
    value = run if isinstance(run, dict) else {}
    summary = value.get("summary") if isinstance(value.get("summary"), dict) else {}
    started = _utc(value.get("started_at"))
    finished = _utc(value.get("finished_at"))
    try:
        date_from = date.fromisoformat(_text(summary.get("date_from")))
        date_to = date.fromisoformat(_text(summary.get("date_to")))
    except ValueError:
        date_from = None
        date_to = None
    financial_proof = (
        summary.get("financial_proof")
        if isinstance(summary.get("financial_proof"), dict)
        else {}
    )
    financial_contract = _strict_int(financial_proof.get("version")) == 1
    if financial_contract:
        raw_coverage = financial_proof.get("coverage")
        accounts_complete_i = _strict_int(
            financial_proof.get("accounts_complete")
        )
        errors_i = _strict_int(financial_proof.get("errors_count"))
        run_status_ok = (
            financial_proof.get("status") == "complete"
            and value.get("status") in {"complete", "partial"}
        )
    else:
        raw_coverage = summary.get("coverage")
        accounts_complete_i = _strict_int(summary.get("accounts_complete"))
        errors_i = _strict_int(summary.get("errors_count"))
        run_status_ok = value.get("status") == "complete"
    coverage_ok, data_state = _coverage(raw_coverage)
    coverage = raw_coverage if isinstance(raw_coverage, dict) else {}
    coverage_completed_i = _strict_int(coverage.get("completed_requests"))
    calls = summary.get("account_provider_calls")
    participants: set[str] = set()
    participants_ok = isinstance(calls, list) and bool(calls)
    if participants_ok:
        for item in calls:
            account_id = _text(item.get("ad_account_id") if isinstance(item, dict) else None)
            call_count = _strict_int(item.get("provider_calls") if isinstance(item, dict) else None)
            if not account_id or call_count is None or call_count <= 0:
                participants_ok = False
                break
            participants.add(account_id)
        participants_ok = participants_ok and len(participants) == len(calls)
    attempted_i = _strict_int(summary.get("accounts_attempted"))
    provider_calls_i = _strict_int(summary.get("provider_calls"))
    participant_calls = sum(
        _strict_int(item.get("provider_calls")) or 0
        for item in calls or []
        if isinstance(item, dict)
    )
    participants_ok = (
        participants_ok
        and attempted_i is not None
        and accounts_complete_i is not None
        and provider_calls_i is not None
        and attempted_i > 0
        and accounts_complete_i == attempted_i
        and len(participants) == attempted_i
        and provider_calls_i > 0
        and participant_calls == provider_calls_i
        and coverage_completed_i is not None
        and coverage_completed_i <= provider_calls_i
    )
    interval_ok = (
        started is not None
        and finished is not None
        and started <= finished <= now.astimezone(timezone.utc) + FUTURE_CLOCK_TOLERANCE
    )
    window_ok = date_from is not None and date_to is not None and date_from <= date_to
    complete = (
        bool(_text(value.get("run_id")))
        and value.get("user_id") == user_id
        and value.get("provider") == SNAPCHAT_PROVIDER_ID
        and value.get("run_type") == ANALYTICS_RUN_TYPE
        and run_status_ok
        and _text(value.get("source_mode")) == expected_source
        and expected_source.startswith(SOURCE_PREFIX)
        and interval_ok
        and window_ok
        and coverage_ok
        and participants_ok
        and errors_i == 0
    )
    return {
        "raw": value,
        "run_id": _text(value.get("run_id")),
        "complete": complete,
        "interval_ok": interval_ok,
        "data_state": data_state,
        "started_at": started,
        "finished_at": finished,
        "date_from": date_from,
        "date_to": date_to,
        "participants": participants,
        "participants_proven": participants_ok,
        "financial_contract": financial_contract,
    }


def _overlaps(left: dict[str, Any], right: dict[str, Any]) -> bool:
    left_start = left.get("started_at")
    left_end = left.get("finished_at")
    right_start = right.get("started_at")
    right_end = right.get("finished_at")
    if not all(isinstance(item, datetime) for item in (left_start, left_end, right_start, right_end)):
        return True
    return left_start <= right_end and right_start <= left_end


async def _proofs_by_day(
    db: Any,
    user_id: str,
    days: list[date],
    *,
    expected_source: str,
    now: datetime,
) -> dict[str, dict[str, Any]]:
    if not days:
        return {}
    base_query = {
        "user_id": user_id,
        "provider": SNAPCHAT_PROVIDER_ID,
        "run_type": ANALYTICS_RUN_TYPE,
    }
    latest_rows, _ = await _sorted_list(
        db[RUNS_COLLECTION].find(
            {**base_query, "source_mode": expected_source},
            {"_id": 0},
        ),
        1,
    )
    latest_global = (
        _run_contract(
            latest_rows[0],
            user_id=user_id,
            expected_source=expected_source,
            now=now,
        )
        if latest_rows else {}
    )
    result: dict[str, dict[str, Any]] = {}
    for report_date in days:
        rows, _ = await _sorted_list(
            db[RUNS_COLLECTION].find(
                {
                    **base_query,
                    "summary.date_from": {"$lte": report_date.isoformat()},
                    "summary.date_to": {"$gte": report_date.isoformat()},
                },
                {"_id": 0},
            ),
            1,
        )
        candidates = [
            _run_contract(
                row,
                user_id=user_id,
                expected_source=expected_source,
                now=now,
            )
            for row in rows
        ]
        candidates.sort(
            key=lambda item: item.get("started_at") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        candidate = candidates[0] if candidates else {}
        ambiguous = False
        overlap_contracts: list[dict[str, Any]] = []
        overlap_truncated = False
        latest_started = latest_global.get("started_at")
        candidate_started = candidate.get("started_at")
        latest_window_ok = (
            isinstance(latest_global.get("date_from"), date)
            and isinstance(latest_global.get("date_to"), date)
            and latest_global["date_from"] <= latest_global["date_to"]
        )
        if (
            isinstance(latest_started, datetime)
            and isinstance(candidate_started, datetime)
            and latest_started > candidate_started
            and _text(latest_global.get("raw", {}).get("source_mode")) == expected_source
            and not latest_window_ok
        ):
            ambiguous = True
        if candidate.get("complete") is True:
            started = candidate.get("started_at")
            finished = candidate.get("finished_at")
            overlap_rows, overlap_truncated = await _sorted_list(
                db[RUNS_COLLECTION].find(
                    {
                        **base_query,
                        "source_mode": expected_source,
                        "started_at": {"$lte": finished.isoformat()},
                        "$or": [
                            {"finished_at": {"$gte": started.isoformat()}},
                            {"finished_at": None},
                            {"finished_at": {"$exists": False}},
                        ],
                    },
                    {"_id": 0},
                ),
                MAX_OVERLAP_RUNS,
            )
            candidate_occurrence_skipped = False
            for row in overlap_rows:
                other = _run_contract(
                    row,
                    user_id=user_id,
                    expected_source=expected_source,
                    now=now,
                )
                if not candidate_occurrence_skipped and row == candidate.get("raw"):
                    candidate_occurrence_skipped = True
                    continue
                overlap_contracts.append(other)
            if not candidate_occurrence_skipped:
                ambiguous = True
        result[report_date.isoformat()] = {
            **candidate,
            "ambiguous": ambiguous,
            "usable": candidate.get("complete") is True and not ambiguous,
            "overlap_contracts": overlap_contracts,
            "overlap_truncated": overlap_truncated,
            "no_data_overlap_ambiguous": overlap_truncated or any(
                _overlaps(candidate, other) for other in overlap_contracts
            ),
        }
    return result


def _possible_writer_for_fact(
    contract: dict[str, Any],
    *,
    updated: datetime,
    report_date: date,
    account_id: str,
    now: datetime,
) -> bool:
    started = contract.get("started_at")
    finished = contract.get("finished_at")
    if not isinstance(started, datetime):
        return True
    possible_end = (
        finished
        if isinstance(finished, datetime)
        else now.astimezone(timezone.utc) + FUTURE_CLOCK_TOLERANCE
    )
    if not started <= updated <= possible_end:
        return False
    date_from = contract.get("date_from")
    date_to = contract.get("date_to")
    if isinstance(date_from, date) and isinstance(date_to, date):
        if date_from <= date_to and not date_from <= report_date <= date_to:
            return False
    participants = contract.get("participants")
    if (
        contract.get("participants_proven") is True
        and isinstance(participants, set)
        and account_id not in participants
    ):
        return False
    return True


def _window_valid(row: dict[str, Any], report_date: date, *, now: datetime) -> bool:
    provider_start = _utc(row.get("provider_window_start"))
    provider_end = _utc(row.get("provider_window_end"))
    day_start = datetime.combine(report_date, time.min, tzinfo=RIYADH_TZ).astimezone(timezone.utc)
    day_end = (datetime.combine(report_date, time.min, tzinfo=RIYADH_TZ) + timedelta(days=1)).astimezone(timezone.utc)
    if provider_start != day_start or provider_end is None or provider_end <= provider_start:
        return False
    today = now.astimezone(RIYADH_TZ).date()
    if report_date < today:
        return provider_end == day_end
    if report_date != today:
        return False
    local_now = now.astimezone(RIYADH_TZ)
    current_hour_start = local_now.replace(minute=0, second=0, microsecond=0)
    lower = max(day_start, current_hour_start.astimezone(timezone.utc))
    upper = min(
        day_end,
        (current_hour_start + timedelta(hours=1)).astimezone(timezone.utc),
    )
    return lower <= provider_end <= upper


def _fact_bound(
    fact: dict[str, Any],
    account: dict[str, Any],
    report_date: date,
    proof: dict[str, Any],
    *,
    integration: dict[str, Any],
    now: datetime,
) -> bool:
    if proof.get("usable") is not True:
        return False
    started = proof.get("started_at")
    finished = proof.get("finished_at")
    updated = _utc(fact.get("updated_at"))
    account_id = _text(account.get("ad_account_id") or account.get("external_account_id"))
    if not all(isinstance(item, datetime) for item in (started, finished, updated)):
        return False
    if account_id not in proof.get("participants", set()) or not started <= updated <= finished:
        return False
    if proof.get("overlap_truncated") is True:
        return False
    possible_writers = 1 + sum(
        1
        for contract in (proof.get("overlap_contracts") or [])
        if _possible_writer_for_fact(
            contract,
            updated=updated,
            report_date=report_date,
            account_id=account_id,
            now=now,
        )
    )
    if possible_writers != 1:
        return False
    source = _text(_proof_scoped_value(integration, "source_mode", proof))
    if (
        _text(_proof_scoped_value(account, "source_mode", proof)) != source
        or _text(fact.get("source_mode")) != source
    ):
        return False
    if not _window_valid(fact, report_date, now=now):
        return False
    if report_date == now.astimezone(RIYADH_TZ).date():
        account_sync = _utc(
            _proof_scoped_value(account, "last_sync_at", proof)
        )
        integration_sync = _utc(
            _proof_scoped_value(integration, "last_sync_at", proof)
        )
        if not all(isinstance(item, datetime) for item in (account_sync, integration_sync)):
            return False
        if not (started <= account_sync <= finished and started <= integration_sync <= finished):
            return False
        if now.astimezone(timezone.utc) - finished > CURRENT_RUN_MAX_AGE:
            return False
    return True


def _current_no_data_fresh(
    proof: dict[str, Any],
    accounts: list[dict[str, Any]],
    integration: dict[str, Any],
    report_date: date,
    *,
    now: datetime,
) -> bool:
    started = proof.get("started_at")
    finished = proof.get("finished_at")
    if not isinstance(started, datetime) or not isinstance(finished, datetime):
        return False
    day_start = datetime.combine(report_date, time.min, tzinfo=RIYADH_TZ).astimezone(timezone.utc)
    provider_floor = started.astimezone(RIYADH_TZ).replace(
        minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    current_hour_start = now.astimezone(RIYADH_TZ).replace(
        minute=0, second=0, microsecond=0
    ).astimezone(timezone.utc)
    lower = max(day_start, current_hour_start)
    if provider_floor < lower:
        return False
    if now.astimezone(timezone.utc) - finished > CURRENT_RUN_MAX_AGE:
        return False
    syncs = [
        _utc(_proof_scoped_value(integration, "last_sync_at", proof)),
        *[
            _utc(_proof_scoped_value(row, "last_sync_at", proof))
            for row in accounts
        ],
    ]
    return all(isinstance(item, datetime) and started <= item <= finished for item in syncs)


def _empty_result(days: list[date], *, state: str, connected: bool, reason: str | None) -> dict[str, Any]:
    numeric = state == "not_connected"
    daily = {day.isoformat(): (0.0 if numeric else None) for day in days}
    return {
        "rows": [],
        "daily_sar": daily,
        "daily_state": {day.isoformat(): state for day in days},
        "total_sar": 0.0 if numeric else None,
        "bank_commissions": None,
        "quality": {
            "status": "complete" if numeric else "incomplete",
            "data_state": state,
            "coverage_complete": numeric,
            "amount_complete": numeric,
            "complete": numeric,
            "connected": connected,
            "reason_codes": [reason] if reason else [],
            "timezone": "Asia/Riyadh",
            "source_collection": SNAPCHAT_PERFORMANCE_COLLECTION,
            "amount_field": "spend_native",
            "fx_authority": "mezan_ad_account_cost_settings_v2",
            "fx_provenance": [],
            "proof_runs": [],
        },
    }


async def load_snapchat_dashboard_spend(
    db: Any,
    user_id: str,
    *,
    start: date,
    end: date,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return canonical Snapchat spend and explicit tri-state quality."""
    days = _days(start, end)
    if not days:
        return _empty_result([], state="unknown_incomplete", connected=False, reason="invalid_date_range")
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)

    account_rows = await _list(
        db.mezan_integration_accounts_v2.find(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "connection_status": "connected",
                "mezan_selected": True,
            },
            {"_id": 0},
        ),
        MAX_ACCOUNTS + 1,
    )
    integration = await db.mezan_integrations_v2.find_one(
        {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
        {"_id": 0},
    ) or {}
    if integration and (
        integration.get("user_id") != user_id
        or integration.get("provider") != SNAPCHAT_PROVIDER_ID
    ):
        return _empty_result(
            days,
            state="unknown_incomplete",
            connected=True,
            reason="integration_identity_unproven",
        )
    if len(account_rows) > MAX_ACCOUNTS:
        return _empty_result(days, state="unknown_incomplete", connected=True, reason="selected_accounts_truncated")
    accounts = account_rows
    account_by_id: dict[str, dict[str, Any]] = {}
    duplicate_account_ids: set[str] = set()
    account_identity_ids: set[str] = set()
    account_identity_missing = False
    account_identity_ambiguous = False
    for row in accounts:
        if (
            row.get("user_id") != user_id
            or row.get("provider") != SNAPCHAT_PROVIDER_ID
            or row.get("connection_status") != "connected"
            or row.get("mezan_selected") is not True
        ):
            account_identity_missing = True
            continue
        ad_account_id = _text(row.get("ad_account_id"))
        external_account_id = _text(row.get("external_account_id"))
        if ad_account_id and external_account_id and ad_account_id != external_account_id:
            account_identity_ambiguous = True
            continue
        account_id = ad_account_id or external_account_id
        identity_id = _text(row.get("mezan_integration_account_id"))
        if not account_id:
            account_identity_missing = True
            continue
        if (
            account_id in account_by_id
            or (identity_id and identity_id in account_identity_ids)
        ):
            duplicate_account_ids.add(account_id)
            continue
        account_by_id[account_id] = row
        if identity_id:
            account_identity_ids.add(identity_id)
    if account_identity_missing:
        return _empty_result(
            days,
            state="unknown_incomplete",
            connected=True,
            reason="selected_account_identity_missing",
        )
    if account_identity_ambiguous:
        return _empty_result(
            days,
            state="unknown_incomplete",
            connected=True,
            reason="selected_account_identity_ambiguous",
        )
    if duplicate_account_ids:
        return _empty_result(
            days,
            state="unknown_incomplete",
            connected=True,
            reason="selected_account_identity_duplicate",
        )
    if not account_by_id:
        any_account = await db.mezan_integration_accounts_v2.find_one(
            {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
            {"_id": 1},
        )
        if not integration and not any_account:
            return _empty_result(days, state="not_connected", connected=False, reason=None)
        return _empty_result(days, state="unknown_incomplete", connected=True, reason="selected_account_missing")

    source = _text(integration.get("source_mode"))
    expected_source = _text(snapchat_hourly.ACCOUNT_REFRESH_SOURCE_MODE)
    if not expected_source or source != expected_source:
        return _empty_result(days, state="unknown_incomplete", connected=True, reason="integration_source_unproven")

    proofs = await _proofs_by_day(
        db, user_id, days, expected_source=expected_source, now=current
    )
    facts = await _list(
        db[SNAPCHAT_PERFORMANCE_COLLECTION].find(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "entity_type": "ad_account",
                "ad_account_id": {"$in": sorted(account_by_id)},
                "date": {"$gte": start.isoformat(), "$lte": end.isoformat()},
            },
            {"_id": 0},
        ),
        MAX_FACTS + 1,
    )
    if len(facts) > MAX_FACTS:
        return _empty_result(days, state="unknown_incomplete", connected=True, reason="facts_truncated")

    reasons: set[str] = set()
    valid_native: dict[tuple[str, str], dict[str, Any]] = {}
    coverage_conflict_days: set[str] = set()
    grouped_facts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        grouped_facts[
            (_text(fact.get("ad_account_id")), _text(fact.get("date")))
        ].append(fact)
    for key, grouped in grouped_facts.items():
        if len(grouped) != 1:
            reasons.add("fact_identity_or_duplicate")
            account_id, day_text = key
            proof = proofs.get(day_text) or {}
            proof_started = proof.get("started_at")
            proof_finished = proof.get("finished_at")
            if (
                proof.get("data_state") == "confirmed_no_data"
                and proof.get("usable") is True
                and isinstance(proof_started, datetime)
                and isinstance(proof_finished, datetime)
                and account_id in proof.get("participants", set())
                    and any(
                        isinstance(_utc(row.get("updated_at")), datetime)
                        and _utc(row.get("updated_at")) >= proof_started
                        for row in grouped
                    )
            ):
                coverage_conflict_days.add(day_text)
            continue
        fact = grouped[0]
        account_id = _text(fact.get("ad_account_id"))
        day_text = _text(fact.get("date"))
        account = account_by_id.get(account_id)
        try:
            report_date = date.fromisoformat(day_text)
        except ValueError:
            report_date = None
        if (
            account is None
            or fact.get("user_id") != user_id
            or report_date is None
            or report_date < start
            or report_date > end
            or fact.get("provider") != SNAPCHAT_PROVIDER_ID
            or fact.get("entity_type") != "ad_account"
            or _text(fact.get("external_id")) != account_id
            or _text(fact.get("attribution_model")) != ATTRIBUTION_MODEL
            or _text(fact.get("date_timezone")) != "Asia/Riyadh"
            or _text(fact.get("business_timezone")) != "Asia/Riyadh"
            or _text(fact.get("stored_granularity")) != "RIYADH_DAY"
            or _text(fact.get("provider_granularity")) != "HOUR"
            or _text(fact.get("provider_breakdown")) != "campaign"
        ):
            reasons.add("fact_identity_or_duplicate")
            continue
        proof = proofs.get(day_text) or {}
        proof_started = proof.get("started_at")
        proof_finished = proof.get("finished_at")
        fact_updated = _utc(fact.get("updated_at"))
        if (
            proof.get("data_state") == "confirmed_no_data"
            and proof.get("usable") is True
            and isinstance(proof_started, datetime)
            and isinstance(proof_finished, datetime)
            and isinstance(fact_updated, datetime)
            and fact_updated >= proof_started
            and account_id in proof.get("participants", set())
        ):
            coverage_conflict_days.add(day_text)
            reasons.add("fact_coverage_conflict")
            continue
        native = _strict_nonnegative_number(fact.get("spend_native"))
        currency = _text(fact.get("currency_native") or fact.get("currency")).upper()
        account_currency = _text(account.get("currency")).upper()
        fact_identity = _text(fact.get("mezan_integration_account_id"))
        account_identity = _text(account.get("mezan_integration_account_id"))
        if (
            native is None
            or currency not in {"SAR", "USD"}
            or (account_currency and account_currency != currency)
            or (fact_identity and fact_identity != account_identity)
        ):
            reasons.add("native_spend_or_currency_unproven")
            continue
        if not _fact_bound(fact, account, report_date, proof, integration=integration, now=current):
            reasons.add("fact_run_or_window_unproven")
            continue
        if proof.get("data_state") == "confirmed_no_data" or (
            proof.get("data_state") == "confirmed_zero" and native > 0
        ):
            reasons.add("fact_coverage_conflict")
            coverage_conflict_days.add(day_text)
            continue
        valid_native[key] = {
            **fact,
            "currency_native": currency,
            "mezan_integration_account_id": account_identity or None,
        }

    account_identities = sorted({
        _text(row.get("mezan_integration_account_id"))
        for row in accounts
        if _text(row.get("mezan_integration_account_id"))
    })
    setting_clauses: list[dict[str, Any]] = [
        {"external_account_id": {"$in": sorted(account_by_id)}}
    ]
    if account_identities:
        setting_clauses.append({
            "mezan_integration_account_id": {"$in": account_identities}
        })
    setting_rows = await _list(
        db[COST_SETTINGS_COLLECTION].find(
            {
                "user_id": user_id,
                "provider": SNAPCHAT_PROVIDER_ID,
                "$or": setting_clauses,
            },
            {"_id": 0},
        ),
        MAX_ACCOUNTS + 1,
    )
    if len(setting_rows) > MAX_ACCOUNTS:
        return _empty_result(
            days,
            state="unknown_incomplete",
            connected=True,
            reason="fx_settings_truncated",
        )
    account_external_by_identity = {
        _text(row.get("mezan_integration_account_id")): account_id
        for account_id, row in account_by_id.items()
        if _text(row.get("mezan_integration_account_id"))
    }
    account_identity_by_external = {
        account_id: _text(row.get("mezan_integration_account_id"))
        for account_id, row in account_by_id.items()
    }
    seen_setting_identities: set[str] = set()
    seen_setting_external_ids: set[str] = set()
    seen_setting_accounts: set[str] = set()
    for setting in setting_rows:
        identity = _text(setting.get("mezan_integration_account_id"))
        external_id = _text(setting.get("external_account_id"))
        setting_currency = _text(setting.get("native_currency")).upper()
        setting_rate = _strict_nonnegative_number(setting.get("exchange_rate_to_sar"))
        commission_pct = _strict_nonnegative_number(setting.get("bank_commission_pct"))
        apply_commission = setting.get("apply_bank_commission")
        identity_external = account_external_by_identity.get(identity) if identity else None
        external_identity = account_identity_by_external.get(external_id) if external_id else None
        canonical_account_id = identity_external or external_id
        if (
            setting.get("user_id") != user_id
            or setting.get("provider") != SNAPCHAT_PROVIDER_ID
            or (bool(identity) and identity in seen_setting_identities)
            or (bool(external_id) and external_id in seen_setting_external_ids)
            or (
                external_id not in account_by_id
                and identity not in account_identities
            )
            or (identity_external is not None and external_id and identity_external != external_id)
            or (external_identity is not None and identity and external_identity != identity)
            or not canonical_account_id
            or canonical_account_id in seen_setting_accounts
            or setting_currency not in {"SAR", "USD"}
            or setting_rate is None
            or setting_rate <= 0
            or setting_rate > 20
            or (setting_currency == "SAR" and setting_rate != 1.0)
            or commission_pct is None
            or commission_pct > 20
            or not isinstance(apply_commission, bool)
        ):
            return _empty_result(
                days,
                state="unknown_incomplete",
                connected=True,
                reason="fx_settings_identity_ambiguous",
            )
        if identity:
            seen_setting_identities.add(identity)
        if external_id:
            seen_setting_external_ids.add(external_id)
        seen_setting_accounts.add(canonical_account_id)
    costed = apply_cost_settings_to_fact_rows(
        {"snapchat": list(valid_native.values()), "meta": [], "tiktok": []},
        accounts,
        setting_rows,
    )
    adjusted = costed.get("platform_rows", {}).get("snapchat") or []
    adjusted_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for row in adjusted:
        key = (_text(row.get("ad_account_id")), _text(row.get("date")))
        native = _strict_nonnegative_number(row.get("spend_native"))
        effective = _strict_nonnegative_number(row.get("effective_spend_sar"))
        rate = _strict_nonnegative_number(row.get("effective_exchange_rate_to_sar"))
        if (
            key not in valid_native
            or key in adjusted_by_key
            or native is None
            or effective is None
            or rate is None
            or rate <= 0
            or row.get("effective_spend_source") != "native_spend_x_account_rate"
            or _text(row.get("effective_native_currency")).upper() != _text(valid_native[key].get("currency_native")).upper()
            or abs(
                native
                - float(valid_native[key].get("spend_native"))
            ) > 0.000001
            or abs(effective - native * rate) > 0.01
        ):
            reasons.add("mezan2_fx_unproven")
            continue
        adjusted_by_key[key] = row

    daily_sar: dict[str, float | None] = {}
    daily_state: dict[str, str] = {}
    proof_rows: list[dict[str, Any]] = []
    today = current.astimezone(RIYADH_TZ).date()
    for report_date in days:
        day_text = report_date.isoformat()
        proof = proofs.get(day_text) or {}
        if proof.get("usable") is not True:
            reasons.add("run_proof_missing_or_ambiguous")
            daily_sar[day_text] = None
            daily_state[day_text] = "unknown_incomplete"
            continue
        if not set(account_by_id).issubset(proof.get("participants", set())):
            reasons.add("selected_account_not_in_run")
            daily_sar[day_text] = None
            daily_state[day_text] = "unknown_incomplete"
            continue
        integration_coverage, integration_state = _coverage(
            _proof_scoped_value(integration, "coverage", proof)
        )
        integration_quality = _proof_scoped_value(
            integration, "data_quality", proof
        )
        account_states: dict[str, str] = {}
        for account_id, account in account_by_id.items():
            complete, state = _coverage(
                _proof_scoped_value(account, "coverage", proof)
            )
            if (
                _proof_scoped_value(account, "data_quality", proof)
                != "complete"
                or not complete
            ):
                state = "unknown_incomplete"
            account_states[account_id] = state
        proof_rows.append({
            "date": day_text,
            "run_id": proof.get("run_id") or None,
            "started_at": proof["started_at"].isoformat(),
            "finished_at": proof["finished_at"].isoformat(),
            "data_state": proof.get("data_state"),
        })
        keys = [(account_id, day_text) for account_id in sorted(account_by_id)]
        if (
            proof.get("data_state") == "confirmed_no_data"
            and day_text not in coverage_conflict_days
            and proof.get("no_data_overlap_ambiguous") is not True
            and not any(key in adjusted_by_key for key in keys)
        ):
            current_ok = True
            if report_date == today:
                current_ok = (
                    integration_quality == "complete"
                    and integration_coverage
                    and integration_state == "confirmed_no_data"
                    and all(account_states.get(account_id) == "confirmed_no_data" for account_id in account_by_id)
                    and _current_no_data_fresh(
                        proof, accounts, integration, report_date, now=current
                    )
                )
            if current_ok:
                daily_sar[day_text] = None
                daily_state[day_text] = "confirmed_no_data"
                continue
        rows = [adjusted_by_key.get(key) for key in keys]
        if any(row is None for row in rows):
            reasons.add("account_day_fact_missing")
            daily_sar[day_text] = None
            daily_state[day_text] = "unknown_incomplete"
            continue
        if report_date == today:
            if (
                integration_quality != "complete"
                or not integration_coverage
                or integration_state != proof.get("data_state")
                or any(account_states.get(account_id) not in KNOWN_STATES for account_id in account_by_id)
            ):
                reasons.add("current_coverage_conflict")
                daily_sar[day_text] = None
                daily_state[day_text] = "unknown_incomplete"
                continue
            for account_id, row in zip(sorted(account_by_id), rows):
                if account_states.get(account_id) == "confirmed_no_data" or (
                    account_states.get(account_id) == "confirmed_zero"
                    and float(row["effective_spend_sar"]) > 0
                ):
                    reasons.add("current_account_coverage_conflict")
                    daily_sar[day_text] = None
                    daily_state[day_text] = "unknown_incomplete"
                    break
            if daily_state.get(day_text) == "unknown_incomplete":
                continue
        value = round(sum(float(row["effective_spend_sar"]) for row in rows), 2)
        daily_sar[day_text] = value
        daily_state[day_text] = "confirmed_data" if value > 0 else "confirmed_zero"

    states = list(daily_state.values())
    amount_complete = bool(states) and all(state in {"confirmed_data", "confirmed_zero"} for state in states)
    coverage_complete = bool(states) and all(state in KNOWN_STATES for state in states)
    total = round(sum(float(value) for value in daily_sar.values()), 2) if amount_complete else None
    if amount_complete:
        state = "confirmed_data" if total and total > 0 else "confirmed_zero"
    elif states and all(item == "confirmed_no_data" for item in states):
        state = "confirmed_no_data"
    else:
        state = "unknown_incomplete"
    if state == "unknown_incomplete" and not reasons:
        reasons.add("range_amount_incomplete")
    bank_commissions = None
    if amount_complete:
        bank_commissions = {key: value for key, value in costed.items() if key != "platform_rows"}
    return {
        "rows": list(adjusted_by_key.values()) if amount_complete else [],
        "daily_sar": daily_sar,
        "daily_state": daily_state,
        "total_sar": total,
        "bank_commissions": bank_commissions,
        "quality": {
            "status": "complete" if coverage_complete else "incomplete",
            "data_state": state,
            "coverage_complete": coverage_complete,
            "amount_complete": amount_complete,
            "complete": amount_complete,
            "connected": True,
            "reason_codes": sorted(reasons),
            "selected_account_count": len(account_by_id),
            "timezone": "Asia/Riyadh",
            "source_collection": SNAPCHAT_PERFORMANCE_COLLECTION,
            "amount_field": "spend_native",
            "fx_authority": "mezan_ad_account_cost_settings_v2",
            "fx_provenance": [
                {
                    "ad_account_id": _text(row.get("external_account_id")),
                    "currency_native": row.get("native_currency"),
                    "exchange_rate_to_sar": row.get("exchange_rate_to_sar"),
                    "configured": row.get("configured") is True,
                    "applied_once": True,
                }
                for row in (bank_commissions or {}).get("accounts", [])
            ],
            "proof_runs": proof_rows,
            "historical_pre_proof_policy": "unknown_incomplete_no_backfill",
        },
    }


__all__ = ["load_snapchat_dashboard_spend"]
