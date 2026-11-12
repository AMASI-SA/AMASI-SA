"""Read saved Google spend once for both Dashboard surfaces.

Native account/day facts take precedence over the legacy manual daily amount.
An invalid or partially covered native day is unknown, never a manual fallback.
This module performs no provider calls, currency repricing, or database writes.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import math
from typing import Any
from zoneinfo import ZoneInfo

from .google_ads_reporting import GOOGLE_ADS_DAILY_COLLECTION, GOOGLE_ADS_REPORTING_SOURCE_MODE
from .google_account_selection import google_reporting_scope


MAX_ACCOUNTS = 50
MAX_ROWS = 20_000
SOURCE_CONTRACT = (
    f"{GOOGLE_ADS_DAILY_COLLECTION}:selected_accounts:stored_sar:"
    "daily_costs.google_ads_positive_legacy_days_only"
)


def google_account_id(value: Any) -> str:
    return str(value or "").replace("-", "").strip()


def _amount(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) and number >= 0 else None


def _observed_at(value: Any) -> str | None:
    try:
        point = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if point.tzinfo is None:
            point = point.replace(tzinfo=timezone.utc)
        return point.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError):
        return None


def _native_amount(row: dict, account: dict | None) -> tuple[float | None, bool, str | None]:
    account_id = google_account_id(row.get("ad_account_id"))
    currency = row.get("currency_native")
    native, sar, fx = (_amount(row.get(key)) for key in ("spend_native", "spend_sar", "fx_rate_to_sar"))
    if (not account or not account_id or row.get("source_mode") != GOOGLE_ADS_REPORTING_SOURCE_MODE
            or row.get("account_timezone") != "Asia/Riyadh"
            or currency not in {"SAR", "USD"}
            or (account.get("currency") and account["currency"] != currency)
            or (account.get("timezone") and account["timezone"] != row["account_timezone"])):
        return None, False, "google_native_identity_or_timezone_unproven"
    # The writer independently rounds its original float sum to six native
    # decimals and SAR cents. Re-rounding the stored native value can choose
    # the opposite cent at a tie; allow only the combined quantization bound.
    if (native is None or sar is None or fx is None or fx <= 0
            or (currency == "SAR" and fx != 1)
            or not math.isfinite(native * fx)
            or sar != round(sar, 2)
            or abs(sar - native * fx) > 0.005 + fx * 0.0000005 + 1e-9):
        return None, False, "google_native_amount_or_currency_unproven"

    if "source_close_proof" not in row:
        # Older saved facts can establish a positive observation, but their
        # generated zero buckets do not prove that Google reported zero spend.
        return (sar, False, "google_native_proof_missing") if native > 0 else (
            None, False, "google_native_zero_unproven")
    proof = row.get("source_close_proof")
    if not isinstance(proof, dict):
        return None, False, "google_native_proof_invalid"
    proof_native = _amount(proof.get("spend_native"))
    count = proof.get("provider_row_count")
    valid = (
        proof.get("complete") is True and proof.get("complete_response") is True
        and proof.get("explicit_spend_present") is True
        and isinstance(count, int) and not isinstance(count, bool) and count > 0
        and google_account_id(proof.get("account_id")) == account_id
        and proof.get("business_date") == row.get("date")
        and proof.get("source_mode") == GOOGLE_ADS_REPORTING_SOURCE_MODE
        and proof.get("timezone") == row["account_timezone"]
        and proof.get("currency") == currency
        and proof_native is not None and abs(proof_native - native) <= 0.000001
        and (native != 0 or proof.get("zero_confirmed") is True)
        and _observed_at(proof.get("observed_at")) is not None
        and _observed_at(proof.get("observed_at")) == _observed_at(row.get("observed_at"))
    )
    return (sar, True, None) if valid else (None, False, "google_native_proof_invalid")


async def load_google_dashboard_spend(
    db: Any, user_id: str, *, date_from: date | str, date_to: date | str,
    now: datetime | None = None,
) -> dict[str, Any]:
    start, end = date.fromisoformat(str(date_from)), date.fromisoformat(str(date_to))
    if end < start or (end - start).days >= 90:
        raise ValueError("invalid_date_range")
    dates = [(start + timedelta(days=index)).isoformat() for index in range((end - start).days + 1)]
    period = {"$gte": start.isoformat(), "$lte": end.isoformat()}
    # These are bounded, independent local reads. Selected IDs are applied
    # before a native row can establish coverage or suppress manual fallback.
    integrations, accounts, native_rows, legacy_rows = await asyncio.gather(
        db["mezan_integrations_v2"].find(
            {"user_id": user_id, "provider": "google_ads"},
            {"_id": 0, "connection_status": 1, "account_selection_configured": 1, "selected_account_ids": 1},
        ).to_list(length=2),
        db["mezan_integration_accounts_v2"].find(
            {"user_id": user_id, "provider": "google_ads", "connection_status": "connected",
             "connection_provenance": "api_connection"},
            {"_id": 0, "ad_account_id": 1, "external_account_id": 1, "mezan_selected": 1,
             "currency": 1, "timezone": 1},
        ).to_list(length=MAX_ACCOUNTS + 1),
        db[GOOGLE_ADS_DAILY_COLLECTION].find(
            {"user_id": user_id, "provider": "google_ads", "date": period},
            {"_id": 0, "ad_account_id": 1, "date": 1, "source_mode": 1, "account_timezone": 1,
             "currency_native": 1, "spend_native": 1, "spend_sar": 1, "fx_rate_to_sar": 1,
             "source_close_proof": 1, "observed_at": 1, "updated_at": 1},
        ).to_list(length=MAX_ROWS + 1),
        db["daily_costs"].find(
            {"user_id": user_id, "date": period},
            {"_id": 0, "date": 1, "google_ads": 1},
        ).to_list(length=MAX_ROWS + 1),
    )
    selection = google_reporting_scope(accounts, integrations[0] if len(integrations) == 1 else None)
    selected = selection["accounts"]
    by_account = {
        google_account_id(account.get("ad_account_id") or account.get("external_account_id")): account
        for account in selected
        if google_account_id(account.get("ad_account_id") or account.get("external_account_id"))
    }
    scope_complete = (len(accounts) <= MAX_ACCOUNTS and len(by_account) == len(selected)
                      and len(native_rows) <= MAX_ROWS and len(legacy_rows) <= MAX_ROWS
                      and len(integrations) <= 1)
    native_by_day, legacy_by_day = defaultdict(list), defaultdict(list)
    for row in native_rows:
        account_id = google_account_id(row.get("ad_account_id"))
        if selection["configured"] and account_id not in by_account:
            continue
        if by_account and account_id and account_id not in by_account:
            continue
        if row.get("date") in dates:
            native_by_day[row["date"]].append(row)
    for row in legacy_rows:
        if row.get("date") in dates and "google_ads" in row:
            legacy_by_day[row["date"]].append(row)
    expected = bool(selection["configured"] or accounts or native_by_day or any(
        row.get("connection_status") in {"connected", "active", "healthy", "data_available",
                                         "needs_reauth", "expired", "error"}
        for row in integrations
    ))
    today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("Asia/Riyadh")).date()
    daily, sources, reasons, observed = {}, {}, set(), []
    if selection["reason"]:
        reasons.add(selection["reason"])
    known_total = 0
    coverage_complete = scope_complete
    amount_complete = scope_complete
    for day in dates:
        rows = native_by_day[day]
        if not scope_complete:
            daily[day], sources[day] = None, "unknown"
            reasons.add("google_spend_scope_incomplete")
            continue
        if selection["configured"] and not selected:
            daily[day], sources[day] = None, "selection_incomplete"
            coverage_complete = amount_complete = False
            continue
        if rows:
            seen, amounts, proven = set(), [], True
            observed_after_day = True
            business_day = date.fromisoformat(day)
            invalid = not by_account
            if invalid:
                reasons.add("google_account_scope_missing")
            for row in rows:
                account_id = google_account_id(row.get("ad_account_id"))
                amount, certified, reason = _native_amount(row, by_account.get(account_id))
                if account_id in seen:
                    invalid = True
                    reasons.add("google_native_account_day_duplicate")
                seen.add(account_id)
                if amount is None:
                    invalid = True
                else:
                    amounts.append(amount)
                    timestamp = _observed_at(row.get("observed_at") or row.get("updated_at"))
                    if timestamp:
                        observed.append(timestamp)
                    observed_after_day = observed_after_day and bool(
                        timestamp and datetime.fromisoformat(timestamp).astimezone(
                            ZoneInfo("Asia/Riyadh")
                        ).date() > business_day
                    )
                proven = proven and certified
                if reason:
                    reasons.add(reason)
            if seen != set(by_account) or not selection["complete"]:
                invalid = True
                reasons.add("google_native_account_day_missing")
            if invalid:
                daily[day], sources[day] = None, "native_incomplete"
                coverage_complete = amount_complete = False
                # Do not count duplicate account/day amounts in the known subtotal.
                if len(seen) == len(rows):
                    known_total += sum(amounts)
                continue
            daily[day], sources[day] = round(sum(amounts), 2), "native"
            known_total += daily[day]
            coverage_complete = coverage_complete and proven
            # A frozen intraday observation does not become a completed daily
            # amount merely because the clock crossed midnight.
            amount_complete = amount_complete and proven and observed_after_day and business_day < today
            if not observed_after_day:
                reasons.add("google_day_close_unobserved")
            if business_day >= today:
                reasons.add("google_current_day_observation")
            continue

        if not selection["complete"]:
            daily[day], sources[day] = None, "selection_incomplete"
            coverage_complete = amount_complete = False
            continue
        manual = legacy_by_day[day]
        if len(manual) > 1:
            daily[day], sources[day] = None, "legacy_incomplete"
            coverage_complete = amount_complete = False
            reasons.add("google_legacy_day_ambiguous")
            continue
        manual_amount = _amount(manual[0].get("google_ads")) if len(manual) == 1 else None
        if manual and manual[0].get("google_ads") is not None and manual_amount is None:
            daily[day], sources[day] = None, "legacy_incomplete"
            coverage_complete = amount_complete = False
            reasons.add("google_legacy_amount_invalid")
            continue
        if manual_amount is not None and manual_amount > 0:
            daily[day], sources[day] = round(manual_amount, 2), "legacy_manual"
            # Legacy totals sum stored values before rounding the period;
            # the separately rounded daily display must not change that sum.
            known_total += manual_amount
            coverage_complete = amount_complete = False
            reasons.add("google_legacy_manual_fallback")
        elif expected:
            # DailyCostsIn defaults google_ads to zero and its writer always
            # persists that field. Such a zero does not prove a Google zero.
            daily[day], sources[day] = None, "missing"
            coverage_complete = amount_complete = False
            reasons.add("google_spend_missing")
        else:
            daily[day], sources[day] = None, "not_required"

    available = all(daily[day] is not None or sources[day] == "not_required" for day in dates)
    not_required = all(source == "not_required" for source in sources.values())
    provisional = available and not amount_complete
    return {
        "total_sar": round(known_total, 2) if available else None,
        "known_total_sar": round(known_total, 2), "daily_sar": daily, "daily_sources": sources,
        "account_ids": sorted(by_account), "source_contract": SOURCE_CONTRACT,
        "quality": {
            "status": "not_required" if not_required else "complete" if amount_complete else "provisional" if provisional else "incomplete",
            "data_state": "not_required" if not_required else "confirmed_data" if amount_complete else "provisional_data" if provisional else "unknown_incomplete",
            "amount_available": available, "amount_complete": available and amount_complete,
            "coverage_complete": available and coverage_complete, "provisional": provisional,
            "expected": expected, "reason_codes": sorted(reasons),
            "last_observed_at": max(observed, default=None),
            "observed_days": sum(value is not None for value in daily.values()),
            "requested_days": len(dates), "account_count": len(by_account),
        },
    }
