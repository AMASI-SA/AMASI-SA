"""Bounded read-only association and committed campaign-context reader.

The shared append-only graph resolver runs over a bounded frozen copy of each
campaign's complete event history. No provider or collector is called here.
"""
from datetime import date, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

from bson import BSON

from .campaign_product_associations import CAMPAIGN_PRODUCT_LINK_COLLECTION, list_effective_campaign_products
from .google_linked_campaign_evidence import _instant, snap_campaign_day

SNAPSHOTS = "mezan_snapchat_committed_totals_v1"
RUNS = "mezan_snapchat_sync_runs_v2"
PROVIDERS = {"snapchat_ads", "meta", "tiktok"}


async def _bounded(collection, query, maximum, *, bytes_limit=32768):
    # Whole-document size is checked before materializing nested arrays.
    rows = await collection.aggregate([
        {"$match": query}, {"$limit": maximum + 1},
        {"$project": {"_id": 0, "size": {"$bsonSize": "$$ROOT"},
                      "row": {"$cond": [{"$lte": [{"$bsonSize": "$$ROOT"}, bytes_limit]}, "$$ROOT", None]}}},
    ], maxTimeMS=2000).to_list(length=maximum + 1)
    if len(rows) > maximum:
        raise ValueError("source_row_limit")
    if any(not isinstance(item.get("row"), dict) or item.get("size", bytes_limit + 1) > bytes_limit for item in rows):
        raise ValueError("source_document_limit")
    return [item["row"] for item in rows]


class _FrozenHistory:
    """Only the resolver's tenant-scoped collection read seam is supported."""
    def __init__(self, rows):
        self.rows = rows

    def __getitem__(self, name):
        if name != CAMPAIGN_PRODUCT_LINK_COLLECTION:
            raise ValueError("unexpected_collection")
        return self

    def find(self, query, projection):
        self.selected = [row for row in self.rows if all(row.get(k) == v for k, v in query.items())]
        return self

    async def to_list(self, length):
        if len(self.selected) >= length:
            raise ValueError("resolver_history_limit")
        return list(self.selected)


async def _effective(history, owner, provider, account, campaign, at):
    return await list_effective_campaign_products(_FrozenHistory(history), owner, provider=provider,
        account_id=account, campaign_id=campaign, as_of=at)


def _ref(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


async def linked_campaign_evidence(db, *, owner_id, product_ids, as_of):
    """Return campaign context once, with per-product references and explicit gaps."""
    now = _instant(as_of)
    if (not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 160
            or not isinstance(product_ids, list) or len(product_ids) > 100
            or any(not isinstance(p, str) or not p or p != p.strip() or len(p) > 240 for p in product_ids)
            or len(set(product_ids)) != len(product_ids)):
        raise ValueError("selection_invalid")
    output = {"as_of": now.isoformat(), "products": [{"product_id": p, "associations": [], "context_refs": []} for p in product_ids],
              "campaign_contexts": [], "campaign_windows": [], "source_coverage_complete": False, "product_attribution_verified": False,
              "independent_store_orders": None, "profitability": None, "execution_authorized": False,
              "rule": "Campaign metrics are shared context, never allocated product results or independent store orders.",
              "limitations": ["No product ranking or Google success inference", "No approved test budget or launch authority", "Native days and attribution settings remain separate"]}
    if not product_ids:
        return output
    discovered = await _bounded(db[CAMPAIGN_PRODUCT_LINK_COLLECTION],
        {"user_id": owner_id, "product_id": {"$in": product_ids}}, 500)
    scopes = sorted({(r.get("provider"), r.get("account_id"), r.get("campaign_id")) for r in discovered
                     if r.get("provider") in PROVIDERS and all(isinstance(r.get(k), str) and r[k].strip() for k in ("account_id", "campaign_id"))})
    if len(scopes) > 6:
        raise ValueError("campaign_scope_limit")
    products = {r["product_id"]: r for r in output["products"]}
    for provider, account, campaign in scopes:
        history = await _bounded(db[CAMPAIGN_PRODUCT_LINK_COLLECTION],
            {"user_id": owner_id, "provider": provider, "account_id": account, "campaign_id": campaign}, 500)
        event_ids = set()
        for event in history:
            event_id, key = event.get("event_id"), event.get("association_key")
            if (not isinstance(event_id, str) or not event_id.strip() or event_id in event_ids
                    or not isinstance(key, str) or not key.strip() or event.get("state") not in {"active", "inactive"}
                    or _instant(event.get("recorded_at")) > now
                    or _instant((event.get("evidence") or {}).get("observed_at")) > now):
                raise ValueError("association_history_unproved")
            event_ids.add(event_id)
            _instant(event.get("valid_from"))
        current = await _effective(history, owner_id, provider, account, campaign, now)
        linked = [link for link in current if link.get("product_id") in products]
        for link in linked:
            products[link["product_id"]]["associations"].append({"provider": provider, "account_id": account,
                "campaign_id": campaign, "event_id": link["event_id"], "source": (link.get("evidence") or {}).get("source"),
                "meaning": "intended_product_association_only", "feed_mapping_verified": False,
                "metrics_status": "saved_context_review" if provider == "snapchat_ads" else "source_fields_unproved",
                "missing": [] if provider == "snapchat_ads" else (["persisted_native_timezone", "atomic_campaign_snapshot_completeness"] if provider == "meta" else ["persisted_native_currency", "persisted_native_timezone"])})
        if provider != "snapchat_ads" or not linked:
            continue
        # The broad UTC +/-1 candidate range is discovery only. Exact native
        # closed-day windows are validated from each snapshot below.
        earliest = (now - timedelta(days=91)).date().isoformat()
        latest = now.date().isoformat()
        snapshots = await _bounded(db[SNAPSHOTS], {"user_id": owner_id, "provider": provider,
            "ad_account_id": account, "report_date": {"$gte": earliest, "$lte": latest}}, 95, bytes_limit=524288)
        if sum(len(BSON.encode(s)) for s in snapshots) > 4 * 1024 * 1024:
            raise ValueError("source_total_bytes_limit")
        runs = {}
        contexts = []
        seen, duplicate, rejected = set(), set(), 0
        for snapshot in snapshots:
            run_id = snapshot.get("sync_run_id")
            try:
                if run_id not in runs:
                    found = await _bounded(db[RUNS], {"user_id": owner_id, "provider": provider,
                        "ad_account_id": account, "sync_run_id": run_id}, 1)
                    runs[run_id] = found[0] if found else {}
                context = snap_campaign_day(snapshot, runs[run_id], owner_id=owner_id,
                    account_id=account, campaign_id=campaign, as_of=now)
                last = now.astimezone(ZoneInfo(context["timezone"])).date() - timedelta(days=1)
                day = date.fromisoformat(context["date"])
                age = (last - day).days
                if not 0 <= age < 90:
                    continue
                identity = (context["date"], context["timezone"], context["attribution_key"])
                if identity in seen:
                    duplicate.add(identity)
                    raise ValueError("duplicate_campaign_day")
                seen.add(identity)
                start, end = _instant(context["window_start_utc"]), _instant(context["window_end_utc"])
                before = await _effective(history, owner_id, provider, account, campaign, start)
                after = await _effective(history, owner_id, provider, account, campaign, end - timedelta(microseconds=1))
                end_ids = {r["event_id"] for r in after}
                proof = []
                for link in before:
                    if (link["event_id"] not in end_ids or link.get("product_id") not in products
                            or _instant(link.get("recorded_at")) > start
                            or _instant((link.get("evidence") or {}).get("observed_at")) > start):
                        continue
                    # An interior event can detach and reattach before end;
                    # reject rather than infer continuity from endpoints.
                    if any(r.get("association_key") == link.get("association_key")
                           and start < _instant(r.get("valid_from")) < end for r in history):
                        continue
                    proof.append({"product_id": link["product_id"], "event_id": link["event_id"]})
                if not proof:
                    continue
                context["context_id"] = _ref([provider, account, campaign, *identity, context["currency"]])
                context["window_ids"] = (["last14"] if age < 14 else ["previous14"] if age < 28 else []) + (["last30"] if age < 30 else []) + ["last90"]
                context["associated_product_count"] = len(before)
                context["association_proofs"] = proof
                contexts.append(context)
            except (ValueError, TypeError, KeyError, OverflowError):
                # Invalid snapshots never turn into zero/complete observations.
                rejected += 1
                continue
        contexts = [c for c in contexts if (c["date"], c["timezone"], c["attribution_key"]) not in duplicate]
        for link in linked:
            entry = next(a for a in products[link["product_id"]]["associations"] if
                a["event_id"] == link["event_id"])
            entry.update(observed_context_days=sum(any(p["product_id"] == link["product_id"] for p in c["association_proofs"]) for c in contexts),
                         rejected_source_snapshots=rejected, source_coverage_complete=False,
                         missing=["complete_period_product_attribution", "independent_store_order_matching", "historical_product_costs"],
                         metrics_status="partial_saved_campaign_context" if contexts else "no_qualified_historical_context")
        output["campaign_contexts"].extend(contexts)
        for context in contexts:
            for proof in context["association_proofs"]:
                refs = products[proof["product_id"]]["context_refs"]
                if context["context_id"] not in refs:
                    refs.append(context["context_id"])
    for product in output["products"]:
        product["status"] = "partial_saved_campaign_context" if product["context_refs"] else "verified_association_only" if product["associations"] else "no_verified_current_campaign_association"
    groups = {}
    for context in output["campaign_contexts"]:
        scope = tuple(context[k] for k in ("provider", "account_id", "campaign_id", "currency", "timezone", "attribution_key"))
        groups.setdefault(scope, []).append(context)
    for scope, contexts in sorted(groups.items()):
        provider, account, campaign, currency, zone, attribution = scope
        last = now.astimezone(ZoneInfo(zone)).date() - timedelta(days=1)
        for name, days, offset in (("last14", 14, 0), ("previous14", 14, 14), ("last30", 30, 0), ("last90", 90, 0)):
            end = last - timedelta(days=offset)
            start = end - timedelta(days=days - 1)
            expected = {(start + timedelta(days=i)).isoformat() for i in range(days)}
            selected = [c for c in contexts if c["date"] in expected]
            output["campaign_windows"].append({"provider": provider, "account_id": account, "campaign_id": campaign,
                "currency": currency, "timezone": zone, "attribution_key": attribution, "window_id": name,
                "date_from": start.isoformat(), "date_to": end.isoformat(), "context_refs": [c["context_id"] for c in selected],
                "observed_context_days": len(selected), "expected_days": days,
                "missing_dates": sorted(expected - {c["date"] for c in selected}), "source_coverage_complete": False,
                "metrics_aggregated": False, "comparison_status": "partial_context_only_no_growth_inference"})
    return output
