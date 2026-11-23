"""Read one uniquely identified saved dashboard header; print no financial amounts or identities."""
import argparse
from datetime import date, datetime, timezone
import json
import os


def reason_list(value):
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 100:
        raise ValueError("invalid_reason_shape")
    if any(not isinstance(v, str) or len(v) > 160 for v in value):
        raise ValueError("invalid_reason_shape")
    return value


def fee_summary(row):
    result = {key: row[key] for key in ("fees_complete", "fees_estimated")
              if type(row.get(key)) is bool}
    for key in ("fee_sources", "fee_review_reasons"):
        if key in row:
            result[key] = reason_list(row[key])
    sources = row.get("fee_estimate_sources") or []
    if not isinstance(sources, list) or len(sources) > 100:
        raise ValueError("invalid_source_shape")
    result["estimate_bases"] = sorted({source["basis"] for source in sources
        if isinstance(source, dict) and source.get("basis") in {
            "weekly_statement_average", "operational_balance_average"}})
    return result


def main(args):
    from dotenv import load_dotenv
    from pymongo import MongoClient

    day = date.fromisoformat(args.day).isoformat()
    start = date.fromisoformat(args.week_start).isoformat()
    end = date.fromisoformat(args.week_end).isoformat()
    after = datetime.fromisoformat(args.after.replace("Z", "+00:00"))
    if after.tzinfo is None or not 0 <= args.estimated <= args.orders or args.sample <= 0:
        raise ValueError("invalid_scope")
    after = after.astimezone(timezone.utc)
    tabby_match = {"key": "tabby", "orders_count": args.orders,
        "estimated_orders_count": args.estimated, "fee_estimate_sources": {"$elemMatch": {
            "basis": "weekly_statement_average", "window_start": start,
            "window_end": end, "eligible_sale_rows": args.sample}}}
    query = {"family": "dashboard_card", "code_version": "demand-read-v1", "status": "ready",
        "spec.resource": "executive", "spec.projection_version": "dashboard-card-v1",
        "spec.filters.from_date": day, "spec.filters.to_date": day,
        "header.range.from_date": day, "header.range.to_date": day,
        "generated_at": {"$gte": after}, "header.payment_breakdown": {"$elemMatch": tabby_match}}
    projection = {"_id": 0, "generated_at": 1,
        "header.totals.payment_fees_complete": 1, "header.totals.payment_fee_review_reasons": 1,
        "header.payment_fee_authority.complete": 1, "header.payment_fee_authority.reason_codes": 1,
        "header.payment_fee_authority.incomplete_buckets": 1,
        "header.payment_breakdown.key": 1}
    for prefix in ("header.payment_breakdown", "header.payment_breakdown.sub_methods"):
        for field in ("fees_complete", "fees_estimated", "fee_sources", "fee_review_reasons",
                      "fee_estimate_sources.basis"):
            projection[prefix + "." + field] = 1
    load_dotenv("/app/backend/.env", override=False)
    with MongoClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000,
                     connectTimeoutMS=5000, socketTimeoutMS=10000,
                     maxPoolSize=1, retryWrites=False, tz_aware=True,
                     appname="tabby-saved-snapshot-readonly") as client:
        rows = list(client[os.environ["DB_NAME"]]["mezan_demand_read_jobs_v1"].find(
            query, projection).max_time_ms(5000).limit(2))
    if len(rows) != 1:
        raise ValueError("snapshot_not_unique_or_absent")
    row = rows[0]
    if not isinstance(row.get("generated_at"), datetime) or row["generated_at"] < after:
        raise ValueError("snapshot_time_unverified")
    header = row["header"]
    groups = [group for group in (header.get("payment_breakdown") or [])
              if isinstance(group, dict) and group.get("key") == "tabby"]
    if len(groups) != 1 or groups[0].get("key") != "tabby":
        raise ValueError("tabby_scope_unverified")
    group = groups[0]
    children = group.get("sub_methods") or []
    if not isinstance(children, list) or len(children) > 100:
        raise ValueError("invalid_submethod_shape")
    authority, totals = header.get("payment_fee_authority") or {}, header.get("totals") or {}
    output = {"read_only": True, "matched_unique_snapshot": True, "date": day,
        "generated_at": row["generated_at"].isoformat(),
        "totals": {"payment_fees_complete": totals.get("payment_fees_complete"),
                   "reason_codes": reason_list(totals.get("payment_fee_review_reasons"))},
        "payment_fee_authority": {"complete": authority.get("complete"),
            "reason_codes": reason_list(authority.get("reason_codes")),
            "incomplete_buckets": reason_list(authority.get("incomplete_buckets"))},
        "tabby": {**fee_summary(group), "sub_methods": [fee_summary(child) for child in children]}}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("day", "week-start", "week-end", "after"):
        parser.add_argument("--" + name, required=True)
    for name in ("orders", "estimated", "sample"):
        parser.add_argument("--" + name, required=True, type=int)
    args = parser.parse_args()
    try:
        main(args)
    except Exception as exc:
        safe = str(exc) if isinstance(exc, ValueError) and str(exc) in {
            "invalid_scope", "snapshot_not_unique_or_absent", "snapshot_time_unverified",
            "tabby_scope_unverified", "invalid_reason_shape", "invalid_source_shape",
            "invalid_submethod_shape"} else "probe_read_failed"
        print(json.dumps({"read_only": True, "error": safe, "error_type": type(exc).__name__}))
        raise SystemExit(1) from None
