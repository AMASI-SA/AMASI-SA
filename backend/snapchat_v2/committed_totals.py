"""Atomic account-day snapshots, published only after the producing run succeeds.

Mutable ingestion facts remain available for diagnostics. Readers keep the last
completed day while the next refresh writes those facts and discovers identities.
"""
from collections import defaultdict
from datetime import date
from typing import Any

from bson import BSON

from .models import SNAPCHAT_PROVIDER, build_attribution_key
from .sync_runs import SNAPCHAT_SYNC_RUNS_COLLECTION
from .empty_hierarchy import valid_proof

COLLECTION = "mezan_snapchat_committed_totals_v1"
FACTS_COLLECTION = "mezan_snapchat_daily_total_facts_v2"
LEVELS = ("campaign", "ad_squad", "ad")
MAX_ROWS = 100_000
MAX_DOCUMENT_BYTES = 12 * 1024 * 1024


async def _rows(cursor, limit):
    cursor = cursor.limit(limit + 1)
    rows = list(await cursor.to_list(length=limit + 1))
    if len(rows) > limit:
        raise ValueError("snapchat_committed_totals_truncated")
    return rows


async def publish_committed_totals(db: Any, sync_run_id: str) -> int:
    run = await db[SNAPCHAT_SYNC_RUNS_COLLECTION].find_one({
        "sync_run_id": str(sync_run_id), "provider": SNAPCHAT_PROVIDER,
        "status": "complete", "financial_sync_status": "complete",
        **{f"{level}_sync_status": "complete" for level in LEVELS},
    }, {"_id": 0})
    if not run:
        return 0
    scope = {"user_id": run["user_id"], "provider": SNAPCHAT_PROVIDER,
             "ad_account_id": run["ad_account_id"]}
    facts = await _rows(db[FACTS_COLLECTION].find({
        **scope, "sync_run_id": str(sync_run_id),
    }, {"_id": 0}), MAX_ROWS)
    grouped = defaultdict(list)
    window = run.get("request_window") or {}
    for fact in facts:
        day = str(fact.get("report_date") or "")
        if (fact.get("entity_type") not in LEVELS
                or (fact.get("coverage") or {}).get("status") != "complete"
                or not window.get("date_from", "~") <= day <= window.get("date_to", "")
                or fact.get("action_report_time") != window.get("action_report_time")):
            raise ValueError("snapchat_committed_totals_invalid_fact")
        grouped[(day, fact["account_timezone"], fact["action_report_time"],
                 fact["attribution_key"])].append(fact)
    documents = []
    for (day, zone, action, attribution), rows in grouped.items():
        # Do not publish a partially populated hierarchy. A provider-confirmed
        # empty level remains on the conservative legacy path until represented.
        if set(LEVELS) != {row["entity_type"] for row in rows}:
            continue
        key = {**scope, "report_date": day, "account_timezone": zone,
               "action_report_time": action, "attribution_key": attribution}
        document = {**key, "sync_run_id": str(sync_run_id),
                    "finished_at": run["finished_at"],
                    "rows": {level: [row for row in rows if row["entity_type"] == level]
                             for level in LEVELS}}
        if len(BSON.encode(document)) > MAX_DOCUMENT_BYTES:
            raise ValueError("snapchat_committed_totals_document_limit")
        documents.append((key, document))
    proof = (run.get("summary") or {}).get("empty_hierarchy_proof")
    if proof:
        days = set(proof.get("report_dates") or [])
        if (facts or run.get("identity_sync_status") != "complete"
                or not valid_proof(proof, account_id=scope["ad_account_id"],
                    currency=proof.get("currency", ""), timezone_name=proof.get("account_timezone", ""),
                    action=window.get("action_report_time"), days=days)
                or not days or not all(window.get("date_from", "~") <= day <= window.get("date_to", "") for day in days)):
            raise ValueError("snapchat_committed_empty_proof_invalid")
        for day in sorted(days):
            key = {**scope, "report_date": day, "account_timezone": proof["account_timezone"],
                   "action_report_time": proof["action_report_time"], "attribution_key": proof["attribution_key"]}
            document = {**key, "sync_run_id": str(sync_run_id),
                "finished_at": run["finished_at"], "rows": {level: [] for level in LEVELS},
                "empty_hierarchy_proof": proof}
            if len(BSON.encode(document)) > MAX_DOCUMENT_BYTES:
                raise ValueError("snapchat_committed_totals_document_limit")
            documents.append((key, document))
    collection = db[COLLECTION]
    await collection.create_index([
        (name, 1) for name in ("user_id", "provider", "ad_account_id", "report_date",
                              "account_timezone", "action_report_time", "attribution_key")
    ], unique=True, name="snapchat_committed_account_day_unique")
    for key, document in documents:
        # One atomic write switches all three levels of a day together. Failed
        # later refreshes never touch this document; the source run stays proven.
        await collection.update_one(key, {"$set": document}, upsert=True)
    return len(documents)


async def committed_period_rows(db: Any, *, user_id: str, account_id: str,
                                date_from: date, date_to: date, timezone_name: str,
                                action: str, windows: dict,
                                entity_type: str) -> dict[str, list[dict]]:
    if entity_type not in LEVELS:
        raise ValueError("snapchat_committed_totals_invalid_level")
    query = {"user_id": str(user_id), "provider": SNAPCHAT_PROVIDER,
             "ad_account_id": str(account_id), "account_timezone": timezone_name,
             "report_date": {"$gte": date_from.isoformat(), "$lte": date_to.isoformat()},
             "action_report_time": action,
             "attribution_key": build_attribution_key(action, windows)}
    snapshots = await _rows(db[COLLECTION].find(query, {
        "_id": 0, "report_date": 1, f"rows.{entity_type}": 1,
    }),
                            (date_to - date_from).days + 1)
    return {snapshot["report_date"]: snapshot["rows"][entity_type] for snapshot in snapshots}
