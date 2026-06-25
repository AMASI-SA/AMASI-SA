"""Ads V2 — Reports data layer.

EVERY read of advertising numbers in V2 MUST come through this module.
This is the Single Source of Truth boundary.

Phase 1 readers (all from `ads_daily` only):
  • get_spend_by_day        — totals per date
  • get_spend_by_account    — totals per account
  • get_spend_by_provider   — totals per provider
  • get_daily_rows          — raw ads_daily rows with filters
  • get_reconciliation_report — drift between two snapshots

Phase 2 will add: get_debt_by_account (from general_ledger).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional


def _meta(layer: str, ssot: str = "ads_daily") -> dict:
    return {
        "source_layer": f"ads_v2.data_layer.reports.{layer}",
        "ssot":         ssot,
        "computed_at":  datetime.now(timezone.utc).isoformat(),
    }


def _base_match(user_id: str, date_from: Optional[str],
                date_to: Optional[str],
                account_id: Optional[str] = None,
                provider: Optional[str] = None) -> dict:
    q: dict = {"user_id": user_id}
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if account_id:
        q["account_id"] = account_id
    if provider:
        q["provider"] = provider
    return q


# ─────────────────────────────────────────────────────────────────────
async def get_spend_by_day(
    db, user_id: str, date_from: str, date_to: str,
    provider: Optional[str] = None,
    account_id: Optional[str] = None,
) -> dict:
    """Daily totals from ads_daily. Returns aggregated by `date`."""
    match = _base_match(user_id, date_from, date_to, account_id, provider)
    pipeline = [
        {"$match": match},
        {"$group": {
            "_id": "$date",
            "spend_native_total": {"$sum": "$spend_native"},
            "spend_sar":          {"$sum": "$spend_sar"},
            "bank_fee_sar":       {"$sum": "$bank_fee_sar"},
            "gross_sar":          {"$sum": "$gross_sar"},
            "impressions":        {"$sum": "$impressions"},
            "clicks":             {"$sum": "$clicks"},
            "purchases":          {"$sum": "$purchases"},
            "accounts":           {"$addToSet": "$account_id"},
            "providers":          {"$addToSet": "$provider"},
            "review_statuses":    {"$addToSet": "$review_status"},
        }},
        {"$project": {
            "_id": 0,
            "date": "$_id",
            "spend_sar":      {"$round": ["$spend_sar", 2]},
            "bank_fee_sar":   {"$round": ["$bank_fee_sar", 2]},
            "gross_sar":      {"$round": ["$gross_sar", 2]},
            "impressions": 1, "clicks": 1, "purchases": 1,
            "accounts_count": {"$size": "$accounts"},
            "providers":      1,
            "review_statuses": 1,
        }},
        {"$sort": {"date": 1}},
    ]
    rows = await db.ads_daily.aggregate(pipeline).to_list(None)
    totals = {
        "spend_sar":    round(sum(r["spend_sar"] for r in rows), 2),
        "bank_fee_sar": round(sum(r["bank_fee_sar"] for r in rows), 2),
        "gross_sar":    round(sum(r["gross_sar"] for r in rows), 2),
    }
    return {"data": rows, "totals": totals,
            "meta": _meta("get_spend_by_day")}


async def get_spend_by_account(
    db, user_id: str, date_from: str, date_to: str,
    provider: Optional[str] = None,
) -> dict:
    """Per-account spend report.

    Currency SSOT: `ads_accounts.currency_native` (the user-configured
    billing currency). We DO NOT trust `ads_daily.currency_native` —
    that value is whatever the platform API happened to return (e.g.
    Snap always reports USD micros even for SAR-billed KSA accounts).

    Native spend (`spend_native` column / USD totals) is suppressed
    for accounts where the configured currency is SAR, since SAR-billed
    accounts have no meaningful "native foreign" amount to show.
    """
    match = _base_match(user_id, date_from, date_to, None, provider)
    pipeline = [
        {"$match": match},
        # Group strictly by account — no currency split.
        {"$group": {
            "_id": {"account_id": "$account_id", "provider": "$provider"},
            "spend_native_raw": {"$sum": "$spend_native"},
            "spend_sar":        {"$sum": "$spend_sar"},
            "bank_fee_sar":     {"$sum": "$bank_fee_sar"},
            "gross_sar":        {"$sum": "$gross_sar"},
            "days_count":       {"$sum": 1},
            "latest_date":      {"$max": "$date"},
        }},
        # Pull the canonical currency_native from ads_accounts (SSOT).
        {"$lookup": {
            "from": "ads_accounts",
            "let": {"a": "$_id.account_id", "u": user_id},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$id", "$$a"]},
                    {"$eq": ["$user_id", "$$u"]},
                ]}}},
                {"$project": {"_id": 0, "display_name": 1,
                              "external_account_id": 1,
                              "currency_native": 1,
                              "bank_fee": 1}},
            ],
            "as": "_acct",
        }},
        {"$addFields": {
            "_currency": {
                # Default to SAR when the account has no explicit setting.
                "$toUpper": {"$ifNull": [
                    {"$arrayElemAt": ["$_acct.currency_native", 0]},
                    "SAR",
                ]},
            },
        }},
        {"$project": {
            "_id": 0,
            "account_id":   "$_id.account_id",
            "provider":     "$_id.provider",
            "display_name": {"$arrayElemAt": ["$_acct.display_name", 0]},
            "external_account_id": {
                "$arrayElemAt": ["$_acct.external_account_id", 0]},
            "currency_native": "$_currency",
            # spend_native is null for SAR-billed accounts — meaningless
            # to display "USD micros" for a SAR account. Frontend renders
            # this as "—".
            "spend_native": {
                "$cond": [
                    {"$eq": ["$_currency", "SAR"]},
                    None,
                    {"$round": ["$spend_native_raw", 2]},
                ],
            },
            "spend_sar":    {"$round": ["$spend_sar", 2]},
            "bank_fee_sar": {"$round": ["$bank_fee_sar", 2]},
            "gross_sar":    {"$round": ["$gross_sar", 2]},
            "days_count":   1,
            "latest_date":  1,
            "bank_fee_pct": {
                "$cond": [
                    {"$gt": ["$spend_sar", 0]},
                    {"$round": [
                        {"$multiply": [
                            {"$divide": ["$bank_fee_sar", "$spend_sar"]},
                            100.0,
                        ]}, 3,
                    ]},
                    0.0,
                ],
            },
            "configured_bank_fee_pct": {
                "$round": [
                    {"$multiply": [
                        {"$ifNull": [
                            {"$arrayElemAt": ["$_acct.bank_fee.rate_pct", 0]},
                            0,
                        ]},
                        100.0,
                    ]}, 3,
                ],
            },
        }},
        {"$sort": {"gross_sar": -1}},
    ]
    rows = await db.ads_daily.aggregate(pipeline).to_list(None)
    # Native-currency totals exclude SAR-billed accounts entirely.
    by_ccy: dict[str, float] = {}
    for r in rows:
        ccy = (r.get("currency_native") or "").upper()
        if ccy == "SAR" or r.get("spend_native") is None:
            continue
        by_ccy[ccy] = round(
            by_ccy.get(ccy, 0.0) + float(r.get("spend_native") or 0.0), 2)
    totals = {
        "spend_sar":    round(sum(r["spend_sar"] for r in rows), 2),
        "bank_fee_sar": round(sum(r["bank_fee_sar"] for r in rows), 2),
        "gross_sar":    round(sum(r["gross_sar"] for r in rows), 2),
        "spend_native_by_currency": by_ccy,
    }
    totals["bank_fee_pct"] = (
        round((totals["bank_fee_sar"] / totals["spend_sar"]) * 100.0, 3)
        if totals["spend_sar"] > 0 else 0.0
    )
    return {"data": rows, "totals": totals,
            "meta": _meta("get_spend_by_account")}


async def get_spend_by_provider(
    db, user_id: str, date_from: str, date_to: str,
) -> dict:
    match = _base_match(user_id, date_from, date_to)
    pipeline = [
        {"$match": match},
        # Stage 1: per-account aggregation so we can join with
        # ads_accounts.currency_native (SSOT) for currency classification.
        {"$group": {
            "_id": {"provider": "$provider", "account_id": "$account_id"},
            "spend_native": {"$sum": "$spend_native"},
            "spend_sar":    {"$sum": "$spend_sar"},
            "bank_fee_sar": {"$sum": "$bank_fee_sar"},
            "gross_sar":    {"$sum": "$gross_sar"},
            "days_count":   {"$sum": 1},
        }},
        {"$lookup": {
            "from": "ads_accounts",
            "let": {"a": "$_id.account_id", "u": user_id},
            "pipeline": [
                {"$match": {"$expr": {"$and": [
                    {"$eq": ["$id", "$$a"]},
                    {"$eq": ["$user_id", "$$u"]},
                ]}}},
                {"$project": {"_id": 0, "currency_native": 1}},
            ],
            "as": "_acct",
        }},
        {"$addFields": {
            "_currency": {"$toUpper": {"$ifNull": [
                {"$arrayElemAt": ["$_acct.currency_native", 0]},
                "SAR",
            ]}},
        }},
        # Stage 2: roll up per provider, dropping SAR-billed amounts
        # from the native currency totals (they're already counted in
        # spend_sar; foreign-native columns would be misleading).
        {"$group": {
            "_id": "$_id.provider",
            "spend_native_by_currency": {"$push": {
                "currency": "$_currency",
                "amount": {
                    "$cond": [
                        {"$eq": ["$_currency", "SAR"]}, 0, "$spend_native",
                    ],
                },
            }},
            "spend_sar":    {"$sum": "$spend_sar"},
            "bank_fee_sar": {"$sum": "$bank_fee_sar"},
            "gross_sar":    {"$sum": "$gross_sar"},
            "accounts":     {"$addToSet": "$_id.account_id"},
            "days_count":   {"$sum": "$days_count"},
        }},
        {"$project": {
            "_id": 0,
            "provider": "$_id",
            "spend_sar":    {"$round": ["$spend_sar", 2]},
            "bank_fee_sar": {"$round": ["$bank_fee_sar", 2]},
            "gross_sar":    {"$round": ["$gross_sar", 2]},
            "spend_native_by_currency": {
                "$filter": {
                    "input": "$spend_native_by_currency",
                    "as":    "x",
                    # Hide SAR & zero-amount entries from the per-provider
                    # native breakdown (only foreign currencies remain).
                    "cond":  {"$and": [
                        {"$ne": ["$$x.currency", "SAR"]},
                        {"$gt": ["$$x.amount", 0]},
                    ]},
                },
            },
            "accounts_count": {"$size": "$accounts"},
            "days_count":   1,
            "bank_fee_pct": {
                "$cond": [
                    {"$gt": ["$spend_sar", 0]},
                    {"$round": [
                        {"$multiply": [
                            {"$divide": ["$bank_fee_sar", "$spend_sar"]},
                            100.0,
                        ]}, 3,
                    ]},
                    0.0,
                ],
            },
        }},
        {"$sort": {"gross_sar": -1}},
    ]
    rows = await db.ads_daily.aggregate(pipeline).to_list(None)
    totals = {
        "spend_sar":    round(sum(r["spend_sar"] for r in rows), 2),
        "bank_fee_sar": round(sum(r["bank_fee_sar"] for r in rows), 2),
        "gross_sar":    round(sum(r["gross_sar"] for r in rows), 2),
    }
    totals["bank_fee_pct"] = (
        round((totals["bank_fee_sar"] / totals["spend_sar"]) * 100.0, 3)
        if totals["spend_sar"] > 0 else 0.0
    )
    return {"data": rows, "totals": totals, "meta": _meta("get_spend_by_provider")}


async def get_daily_rows(
    db, user_id: str, date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    account_id: Optional[str] = None,
    provider: Optional[str] = None,
    has_anomalies: Optional[bool] = None,
    review_status: Optional[str] = None,
    limit: int = 500,
) -> dict:
    """Return raw ads_daily rows for the merchant review UI / reports."""
    q = _base_match(user_id, date_from, date_to, account_id, provider)
    if has_anomalies is True:
        q["anomaly_flags"] = {"$ne": []}
    elif has_anomalies is False:
        q["anomaly_flags"] = {"$in": [[], None]}
    if review_status:
        q["review_status"] = review_status
    rows: list[dict] = []
    async for r in db.ads_daily.find(q, {"_id": 0}) \
                                 .sort("date", -1).limit(limit):
        rows.append(r)
    return {"data": rows, "count": len(rows),
            "meta": _meta("get_daily_rows")}


async def get_reconciliation_report(
    db, user_id: str, date_from: str, date_to: str,
    account_id: Optional[str] = None,
) -> dict:
    """Reconciliation report: per (account, date) show spend_daily and
    last sync_run details so the merchant can compare with Ads Manager.

    For each row we expose:
      • spend_native / spend_sar — what V2 SSOT holds
      • platform_manual_value_native / platform_manual_value_sar — what
        the merchant said Ads Manager shows (ground truth)
      • drift_pct_vs_manual — primary drift figure if manual entered
      • drift_pct_vs_previous_sync — drift between consecutive syncs
      • drift_pct — the most meaningful one (manual if present, else prev)
      • drift_reason — structured explanation of why drift exists
      • anomaly_flags + review_status + confidence
    """
    q = _base_match(user_id, date_from, date_to, account_id)
    rows: list[dict] = []
    async for r in db.ads_daily.find(q, {
        "_id": 0, "account_id": 1, "date": 1, "provider": 1,
        "spend_native": 1, "currency_native": 1,
        "spend_sar": 1, "bank_fee_sar": 1, "gross_sar": 1,
        "platform_reported_native": 1, "platform_reported_sar": 1,
        "platform_manual_value_native": 1, "platform_manual_value_sar": 1,
        "platform_manual_entered_at": 1, "platform_manual_entered_by": 1,
        "platform_authoritative_native": 1, "platform_authoritative_sar": 1,
        "platform_authoritative_currency": 1, "platform_last_checked_at": 1,
        "platform_check_error": 1,
        "diff_native": 1, "diff_sar": 1,
        "platform_checked_at": 1,
        "drift_pct": 1, "drift_pct_vs_manual": 1,
        "drift_pct_vs_platform": 1,
        "drift_pct_vs_previous_sync": 1, "drift_reason": 1,
        "anomaly_flags": 1,
        "review_status": 1, "match_status": 1,
        "fx_rate": 1, "fx_source": 1,
        "confidence": 1, "last_synced_at": 1,
    }).sort([("date", -1), ("account_id", 1)]).limit(2000):
        # Attach a flag the UI uses to render "—" vs "0%".
        r["has_manual_value"] = r.get("platform_manual_value_native") is not None
        r["has_platform_check"] = r.get("platform_last_checked_at") is not None
        rows.append(r)

    # Enrich rows with display_name for nicer UI
    acct_ids = list({r["account_id"] for r in rows})
    acct_map: dict[str, dict] = {}
    async for a in db.ads_accounts.find(
        {"user_id": user_id, "id": {"$in": acct_ids}},
        {"_id": 0, "id": 1, "display_name": 1, "external_account_id": 1,
         "currency_native": 1},
    ):
        acct_map[a["id"]] = a
    for r in rows:
        a = acct_map.get(r["account_id"]) or {}
        r["display_name"] = a.get("display_name")
        r["external_account_id"] = a.get("external_account_id")

    summary = {
        "rows_total":             len(rows),
        "rows_with_anomalies":    sum(1 for r in rows if r.get("anomaly_flags")),
        "rows_late_reporting":    sum(
            1 for r in rows if "late_reporting" in (r.get("anomaly_flags") or [])),
        "rows_drift_above_5pct":  sum(
            1 for r in rows if "drift_above_5pct" in (r.get("anomaly_flags") or [])),
        "rows_drift_above_15pct": sum(
            1 for r in rows if "drift_above_15pct" in (r.get("anomaly_flags") or [])),
        "rows_missing_fx":        sum(
            1 for r in rows if "missing_fx" in (r.get("anomaly_flags") or [])),
        "rows_with_manual_value": sum(
            1 for r in rows if r.get("has_manual_value")),
        "rows_pending_manual":    sum(
            1 for r in rows if not r.get("has_manual_value")),
        # Match-status histogram (the user-facing 🟢/🟡/🟠/🔴 indicators)
        "match_matched":          sum(
            1 for r in rows if r.get("match_status") == "matched"),
        "match_pending_platform": sum(
            1 for r in rows if r.get("match_status") == "pending_platform"),
        "match_drift_review":     sum(
            1 for r in rows if r.get("match_status") == "drift_review"),
        "match_sync_failed":      sum(
            1 for r in rows if r.get("match_status") == "sync_failed"),
        "match_no_data":          sum(
            1 for r in rows if r.get("match_status") in (None, "no_data")),
    }
    return {"data": rows, "summary": summary,
            "meta": _meta("get_reconciliation_report")}


async def get_sync_health(db, user_id: str) -> dict:
    """Snapshot of last sync per account + recent heartbeats."""
    accts = [a async for a in db.ads_accounts.find(
        {"user_id": user_id, "soft_deleted": False}, {"_id": 0},
    )]
    recent = [e async for e in db.ads_sync_logs.find(
        {"user_id": user_id, "event": {"$in": [
            "sync_run", "sync_failed", "token_expired", "token_alert",
        ]}}, {"_id": 0},
    ).sort("at", -1).limit(50)]
    return {
        "accounts": [
            {
                "id":               a["id"],
                "display_name":     a.get("display_name"),
                "provider":         a.get("provider"),
                "sync_status":      a.get("sync_status"),
                "sync_enabled":     a.get("sync_enabled"),
                "last_synced_date": a.get("last_synced_date"),
                "last_sync_finished_at": a.get("last_sync_finished_at"),
                "sync_error_message":    a.get("sync_error_message"),
            } for a in accts
        ],
        "recent_events": recent,
        "meta": _meta("get_sync_health", ssot="ads_accounts + ads_sync_logs"),
    }
