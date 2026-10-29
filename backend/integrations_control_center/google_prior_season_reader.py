"""Bounded saved prior-interval observations; no backfill, providers or writes."""
from datetime import date, timedelta

from .google_plan_store_observations import MAX_DOCUMENTS, PROJECTION
from .google_store_product_observations import observe_prior_season_lines


async def read_prior_season_observations(db, *, owner_id, product_ids, as_of, date_from, date_to):
    empty = observe_prior_season_lines([], owner_id=owner_id, product_ids=product_ids, as_of=as_of,
                                      date_from=date_from, date_to=date_to)
    if not product_ids:
        return empty
    # Only this explicit prior interval is discovered. Never scan the year gap
    # between prior dates and the independent current90day observations.
    lower = (date.fromisoformat(date_from) - timedelta(days=1)).isoformat()
    upper = (date.fromisoformat(date_to) + timedelta(days=1)).isoformat()
    query = {"user_id": owner_id, "order_date": {"$gte": lower, "$lte": upper}, "$expr": {"$and": [
        {"$lte": [{"$bsonSize": "$$ROOT"}, 32768]},
        {"$in": [{"$type": "$raw_by_source.salla_direct.payment_actions"}, ["missing", "object"]]},
        {"$in": [{"$type": "$raw_by_source.salla_direct.payment_actions.refund_action"}, ["missing", "object"]]},
    ]}}
    cursor = (db["unified_orders"].find(query, PROJECTION).hint([("user_id", 1), ("order_date", -1)])
              .limit(MAX_DOCUMENTS + 1).max_time_ms(3000))
    rows = await cursor.to_list(length=MAX_DOCUMENTS + 1)
    if any(not isinstance(r, dict) or r.get("user_id") != owner_id or not isinstance(r.get("order_date"), str)
           or not lower <= r["order_date"] <= upper for r in rows):
        raise ValueError("prior read scope unproved")
    result = observe_prior_season_lines(rows, owner_id=owner_id, product_ids=product_ids, as_of=as_of,
        date_from=date_from, date_to=date_to, truncated=len(rows) > MAX_DOCUMENTS)
    if sum(len(p["currency_groups"]) for p in result["products"]) > 200:
        raise ValueError("prior output scope limit")
    result["limitations"] += ["normalized_date_plus_minus_one_is_candidate_discovery_only", "oversized_documents_excluded_unquantified",
        "malformed_refund_containers_excluded_before_projection", "index_required_no_fallback", "owner_calendar_alignment_unverified"]
    return result
