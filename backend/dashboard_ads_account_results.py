"""Account-scoped Salla outcomes for the existing executive snapshot.

The caller supplies the already filtered executive orders and the Snapchat
account IDs already read for spend. Only a bounded campaign identity catalog
is read here; there are no new order/provider/cost-source reads or background
jobs. Hover costs reuse evidence captured during the existing pricing pass.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from dashboard_ads_costs import DashboardAdsCostCollector
from dashboard_ads_periods import dashboard_day_segments, order_riyadh_timestamp
from dashboard_cpu_budget import ADVERTISING_BUDGET_MS, CPUWorkBudget
from dashboard_v2_ads_executive import resolve_salla_ad_platform
from order_currency import order_total_sar
from snapchat_v2.salla_outcomes import (
    _exact_order_account,
    _match_order_campaign,
    _unique_lookup,
    _unmatched_reason,
)


CAMPAIGN_COLLECTION = "mezan_snapchat_entity_facts_v2"
MAX_CAMPAIGN_ROWS = 5_000
CATALOG_READ_TIMEOUT_MS = 1_500


async def _campaign_catalog(db: Any, user_id: str) -> tuple[list[dict], str | None]:
    """Read owner-wide identities so cross-account names remain ambiguous."""
    try:
        collection = (db[CAMPAIGN_COLLECTION] if hasattr(db, "__getitem__")
                      else getattr(db, CAMPAIGN_COLLECTION))
        cursor = collection.find(
            {"user_id": str(user_id), "provider": "snapchat_ads", "entity_type": "campaign"},
            {"_id": 0, "ad_account_id": 1, "external_id": 1, "name": 1},
        )
        cursor = cursor.limit(MAX_CAMPAIGN_ROWS + 1)
        if hasattr(cursor, "max_time_ms"):
            cursor = cursor.max_time_ms(CATALOG_READ_TIMEOUT_MS)
        rows = await cursor.to_list(length=MAX_CAMPAIGN_ROWS + 1)
    except Exception:  # A catalog outage must not hide the financial snapshot.
        return [], "campaign_catalog_unavailable"
    if len(rows) > MAX_CAMPAIGN_ROWS:
        return [], "campaign_catalog_truncated"
    return [{"account_id": str(row.get("ad_account_id") or "").strip(),
             "campaign_id": str(row.get("external_id") or "").strip(),
             "campaign_name": row.get("name")} for row in rows], None


async def build_dashboard_ads_account_results(
    db: Any,
    user_id: str,
    orders: list[dict[str, Any]],
    *,
    snapchat_account_ids: list[str],
    date_from: str | None,
    date_to: str | None,
    cost_collector: DashboardAdsCostCollector | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Partition the executive cohort once without guessing any account.

    Canonical account totals are unknown when a Snapchat order cannot be
    assigned. Known matched subtotals remain explicit. Orders proven to belong
    to a different account are not redistributed to the shown ones. Both kinds
    still belong to the all-order totals, never to the direct/unattributed row.
    """
    account_ids = sorted({str(value).strip() for value in snapchat_account_ids
                          if value is not None and str(value).strip()})
    keys = [("snapchat", account_id) for account_id in account_ids]
    keys += [(provider, "") for provider in ("google", "tiktok", "meta")]
    direct_key = ("direct", "")
    keys.append(direct_key)
    buckets: dict[tuple[str, str], list[dict]] = {key: [] for key in keys}
    unassigned_bucket: list[dict] = []
    segments = dashboard_day_segments(date_from, date_to, now=now)
    segment_buckets = {segment["id"]: {key: [] for key in keys} for segment in segments}
    segment_unassigned_rows: dict[str, list[dict]] = {segment["id"]: [] for segment in segments}
    segment_totals: dict[str, list[dict]] = {segment["id"]: [] for segment in segments}
    has_available_segment = any(segment["status"] in {"active", "completed"} for segment in segments)
    segment_unassigned: Counter[str] = Counter()
    segment_outside: Counter[str] = Counter()
    uncertain_times: Counter[tuple[str, str]] = Counter()
    unassigned_time_orders = 0
    untimed_unassigned_orders = 0
    costs = cost_collector if cost_collector is not None else DashboardAdsCostCollector()
    budget = CPUWorkBudget(ADVERTISING_BUDGET_MS)
    catalog_loaded = False
    catalog_complete = True
    id_lookup: dict = {}
    name_lookup: dict = {}
    exact_owners: dict[str, set[str]] = defaultdict(set)
    reasons: Counter[str] = Counter()
    unassigned_orders = 0
    outside_scope_orders = 0

    def segment_for(order: dict) -> str | None:
        timestamp = order_riyadh_timestamp(order)
        if timestamp is None:
            return None
        for segment in segments:
            if segment["status"] not in {"active", "completed"}:
                continue
            start = datetime.fromisoformat(segment["start_at"])
            cutoff = datetime.fromisoformat(segment["cutoff_at"])
            if start <= timestamp < cutoff:
                return segment["id"]
        return None

    for order in orders:
        await budget.checkpoint()
        # Use the same platform classifier and the same prefiltered population
        # as the executive breakdown; this view cannot change its source policy.
        provider = resolve_salla_ad_platform(order)
        segment_id = segment_for(order)
        if segment_id:
            segment_totals[segment_id].append(order)
        elif has_available_segment:
            # Account attribution cannot exclude an order from the executive
            # total or make its unknown time appear precisely assigned.
            unassigned_time_orders += 1
        key: tuple[str, str] | None = (
            (provider, "") if provider in {"snapchat", "google", "tiktok", "meta"} else direct_key
        )
        if provider == "snapchat" and not catalog_loaded:
            catalog_loaded = True
            if account_ids:
                identities, catalog_error = await _campaign_catalog(db, user_id)
            else:
                identities, catalog_error = [], "snapchat_accounts_unavailable"
            catalog_complete = catalog_error is None
            if catalog_error:
                reasons[catalog_error] += 1
            id_lookup = _unique_lookup(identities, "campaign_id")
            name_lookup = _unique_lookup(identities, "campaign_name")
            for identity in identities:
                await budget.checkpoint()
                if identity["campaign_id"] and identity["account_id"]:
                    exact_owners[identity["campaign_id"]].add(identity["account_id"])

        if provider == "snapchat":
            key = None
            if catalog_complete:
                exact_account, ambiguous_account = _exact_order_account(order, exact_owners)
                if exact_account is not None and exact_account not in account_ids:
                    outside_scope_orders += 1
                    if segment_id:
                        segment_outside[segment_id] += 1
                    continue
                campaign, method = _match_order_campaign(order, id_lookup=id_lookup, name_lookup=name_lookup)
                if ambiguous_account:
                    campaign, method = None, "ambiguous_id"
                if campaign is None:
                    reasons[_unmatched_reason(order, method)] += 1
                elif campaign[0] not in account_ids:
                    outside_scope_orders += 1
                    if segment_id:
                        segment_outside[segment_id] += 1
                    continue
                else:
                    key = ("snapchat", campaign[0])
            if key is None:
                unassigned_orders += 1
                unassigned_bucket.append(order)
                if segment_id:
                    segment_unassigned[segment_id] += 1
                    segment_unassigned_rows[segment_id].append(order)
                elif has_available_segment:
                    untimed_unassigned_orders += 1

        if key is not None:
            buckets[key].append(order)
            if segment_id:
                segment_buckets[segment_id][key].append(order)
        if segment_id is None and has_available_segment:
            if key is not None:
                uncertain_times[key] += 1
            elif provider == "snapchat":
                # The missing time and account cannot be guessed independently.
                for account_id in account_ids:
                    uncertain_times[("snapchat", account_id)] += 1

    attribution_complete = catalog_complete and not unassigned_orders

    async def result_row(
        rows: list[dict], *, assigned: bool, timed: bool = True,
        available: bool = True, row_key: tuple[str, str] | None = None,
        extra_reasons: list[str] | None = None,
    ) -> dict:
        known_sales = 0.0
        currency_complete = True
        for row in rows:
            await budget.checkpoint()
            amount = order_total_sar(row)
            if amount is None:
                currency_complete = False
            else:
                known_sales += amount
        known_sales = round(known_sales, 2)
        scope_complete = assigned and timed and available
        row_reasons = list(extra_reasons or [])
        if not assigned:
            row_reasons.append("account_attribution_incomplete")
        if not timed:
            row_reasons.append("order_time_unverified")
        row = {
            "salla_orders": len(rows) if scope_complete else None,
            "salla_sales_sar": known_sales if scope_complete and currency_complete else None,
            "known_salla_orders": len(rows),
            "known_salla_sales_sar": known_sales,
            "sales_currency_conversion_complete": currency_complete,
            "attribution_complete": assigned,
            "time_assignment_complete": timed,
            "hover_summary": await costs.summary(rows, scope_complete=scope_complete, reason_codes=row_reasons),
        }
        if row_key is not None:
            row["provider"] = row_key[0]
            if row_key[0] == "snapchat":
                row["account_id"] = row_key[1]
            elif row_key == direct_key:
                row["ad_spend_applicable"] = False
        return row

    async def unassigned_row(rows: list[dict], **kwargs) -> dict:
        # These orders have a known platform but no proven account. Retain the
        # available order/cost evidence without claiming an advertising spend
        # allocation, changing account totals, or counting the cohort twice.
        row = await result_row(rows, assigned=False, **kwargs)
        row.update({
            "provider": "snapchat",
            "cohort_basis": "snapchat_unassigned_orders",
            "ad_spend_allocation_complete": False,
        })
        return row

    async def build_rows(bucket_map, total_rows, *, assigned, available=True, segment=False, extra_reasons=None):
        accounts = []
        providers = {}
        direct = None
        for key, rows in bucket_map.items():
            row = await result_row(
                rows, assigned=assigned if key[0] == "snapchat" else True,
                timed=not uncertain_times[key] if segment else True,
                available=available, row_key=key, extra_reasons=extra_reasons,
            )
            if key[0] == "snapchat":
                accounts.append(row)
            elif key == direct_key:
                direct = row
            else:
                providers[key[0]] = row
        total = await result_row(
            total_rows, assigned=True,
            timed=not unassigned_time_orders if segment else True,
            available=available, extra_reasons=extra_reasons,
        )
        total["cohort_basis"] = "executive_filtered_orders"
        row_fees = [row["hover_summary"]["payment_fees_sar"]
                    for row in accounts + list(providers.values()) + [direct]]
        total_fee = total["hover_summary"]["payment_fees_sar"]
        fully_represented = sum(len(rows) for rows in bucket_map.values()) == len(total_rows)
        total["hover_summary"]["row_payment_fees_rounding_delta_sar"] = (
            round(sum(row_fees) - total_fee, 2)
            if fully_represented and total_fee is not None and all(fee is not None for fee in row_fees) else None
        )
        return accounts, providers, direct, total

    accounts, providers, direct, daily_total = await build_rows(buckets, orders, assigned=attribution_complete)
    unassigned_result = await unassigned_row(unassigned_bucket, extra_reasons=sorted(reasons))
    coverage = {
        "status": ("unavailable" if not catalog_complete
                   else "partial" if unassigned_orders else "complete"),
        "unassigned_orders": unassigned_orders,
        "outside_account_scope_orders": outside_scope_orders,
        "unrepresented_orders": unassigned_orders + outside_scope_orders,
        "catalog_complete": catalog_complete,
        "time_assignment_complete": not unassigned_time_orders,
        "unassigned_time_orders": unassigned_time_orders,
        "reason_codes": sorted(set(reasons) | ({"order_time_unverified"} if unassigned_time_orders else set())),
    }
    for segment in segments:
        segment_id = segment["id"]
        available = segment["status"] in {"active", "completed"}
        assigned = catalog_complete and not segment_unassigned[segment_id]
        segment_accounts, segment_providers, segment_direct, total = await build_rows(
            segment_buckets[segment_id], segment_totals[segment_id], assigned=assigned, available=available, segment=True,
            extra_reasons=segment["reason_codes"] + ([] if available else [segment["status"]]),
        )
        segment.update({
            "source_only": True,
            "accounts": segment_accounts,
            "provider_results": segment_providers,
            "direct_result": segment_direct,
            "unassigned_result": await unassigned_row(
                segment_unassigned_rows[segment_id], timed=not untimed_unassigned_orders, available=available,
                extra_reasons=segment["reason_codes"] + sorted(reasons) + ([] if available else [segment["status"]]),
            ),
            "total": total,
            "coverage": {
                "status": "unavailable" if not catalog_complete else "partial" if not assigned else "complete",
                "catalog_complete": catalog_complete,
                "unassigned_orders": segment_unassigned[segment_id],
                "outside_account_scope_orders": segment_outside[segment_id],
                "unrepresented_orders": segment_unassigned[segment_id] + segment_outside[segment_id],
                "time_assignment_complete": not unassigned_time_orders,
                "unassigned_time_orders": unassigned_time_orders,
                "reason_codes": sorted(set(reasons) | ({"order_time_unverified"} if unassigned_time_orders else set())),
            },
        })
        if available and (not assigned or unassigned_time_orders):
            segment["status"] = "partial"

    daily_rows = accounts + list(providers.values()) + [direct, daily_total]
    for daily_row in daily_rows:
        half_fees = []
        for segment in segments:
            if daily_row.get("provider") == "snapchat":
                half_row = next(row for row in segment["accounts"] if row["account_id"] == daily_row["account_id"])
            elif daily_row.get("provider") == "direct":
                half_row = segment["direct_result"]
            elif daily_row.get("provider"):
                half_row = segment["provider_results"][daily_row["provider"]]
            else:
                half_row = segment["total"]
            half_fees.append(half_row["hover_summary"]["payment_fees_sar"])
        day_fee = daily_row["hover_summary"]["payment_fees_sar"]
        daily_row["hover_summary"]["half_day_payment_fees_rounding_delta_sar"] = (
            round(sum(half_fees) - day_fee, 2)
            if day_fee is not None and all(fee is not None for fee in half_fees) else None
        )

    return {
        "date_from": date_from,
        "date_to": date_to,
        "timezone": "Asia/Riyadh",
        "source_only": True,
        "accounts": accounts,
        "provider_results": providers,
        "direct_result": direct,
        "unassigned_result": unassigned_result,
        "daily_total": daily_total,
        "segments": segments,
        "coverage": coverage,
    }
