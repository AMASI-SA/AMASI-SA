"""Read-only order exceptions for authorized operational resources.

The workspace navigation only consumes existing counters. The first explicit
review request projects the saved evidence, with one batched canonical order
number lookup. Subsequent pages can reuse the same short-lived scoped index.
"""
import asyncio
from collections import OrderedDict, defaultdict
from decimal import Decimal
from math import ceil
from time import monotonic

from operational_balance_completeness import annotate_report_coverage
from operational_balance_service import report
from operational_balance_sources import entities
from operational_balance_store import STATES, fail
from operational_delivery_register import PREFIX as DELIVERY_PREFIX
from operational_financial_activity_runtime import checked_rows, prepare_in_thread
from operational_financial_order_labels import attach_order_numbers
from operational_financial_position_state import read_financial_position_state

KINDS = {"provider", "store_driver", "courier"}
PAGE_SIZE = 8
DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


def source_key(state):
    engine = state.get("engine") or {}
    return state.get("revision"), engine.get("as_of"), state.get("started_at")


def navigation_counts(computed):
    """Read provider counters already computed for the balance, never scan orders."""
    counts = {}
    for rows in computed.get("details", {}).get("provider_balances", {}).values():
        for row in rows:
            key = ("provider", row["provider"])
            # Several currencies may refer to the same order.
            counts[key] = None if key in counts else row.get("pending_orders")
    return counts


class ReviewSnapshots:
    def __init__(self):
        # A burst of simultaneous review opens must not occupy the worker pool.
        self.preparation = asyncio.Lock()
        self.pages = OrderedDict()
        self.counts = OrderedDict()

    def get(self, key):
        saved = self.pages.get(key)
        if saved and monotonic() - saved[0] < 45:
            self.pages.move_to_end(key)
            return saved[1]
        self.pages.pop(key, None)
        return None

    def save(self, key, result, counts):
        if len(result["orders"]) <= 2500:
            self.pages[key] = (monotonic(), result)
            while len(self.pages) > 4:
                self.pages.popitem(last=False)
        owner, revision, *_ = key
        slot = self.counts.setdefault((owner, revision), {})
        slot.update(counts)
        self.counts.move_to_end((owner, revision))
        while len(self.counts) > 12:
            self.counts.popitem(last=False)


def issue_badge(kind, identity, access, source_known, has_issues, computed_counts, reviewed_counts):
    if kind not in KINDS or access["movements"] != "all":
        return {}
    key = kind, identity
    default = 0 if source_known and (kind == "provider" or not has_issues) else None
    return {"order_issues_count": reviewed_counts.get(key, computed_counts.get(key, default))}


def known_evidence(state, kind, party, oid):
    engine = state.get("engine") or {}
    if kind in {"courier", "store_driver"}:
        delivery = (engine.get("delivery_register") or {}).get(DELIVERY_PREFIX + oid) or {}
        if (delivery.get("party_type"), delivery.get("party_id"), delivery.get("order_id")) == (kind, party, oid):
            return "سبق أن ثبت توصيل الطلب مع هذه الجهة في سجل التوصيل."
    if kind == "provider":
        fact = (engine.get("facts") or {}).get("provider_order_balance:" + oid) or {}
        if ((fact.get("provider"), str(fact.get("order_id")), fact.get("owner_id")) == (party, oid, state["owner_id"])
                and fact.get("paid") is not None and fact.get("currency")):
            return "المدفوع المثبت في صفحة الطلب: " + str(fact["paid"]) + " " + fact["currency"]
        for key in ("provider:" + oid + ":" + party, "provider:" + oid):
            claim = (engine.get("obligations") or {}).get(key) or {}
            if ((claim.get("party_type"), claim.get("party_id"), claim.get("direction")) == ("provider", party, "receivable")
                    and not claim.get("superseded") and Decimal(claim.get("confirmed") or "0") > 0 and claim.get("currency")):
                return "أصل المستحق المسجل للمتجر لدى المنصة: " + str(claim["confirmed"]) + " " + claim["currency"]
    return None


def review_groups(state, resources, include_unassigned):
    result = report(state)
    needed = {kind for kind, _ in resources}
    names = dict(resources)
    candidates = []
    if needed.intersection({"courier", "store_driver"}) or include_unassigned:
        annotate_report_coverage(result)
        candidates.extend((row["party_type"], row["party_id"], row) for row in result["parties"]
                          if row["party_type"] in {"courier", "store_driver"})
    if "provider" in needed:
        for rows in result["details"]["provider_balances"].values():
            candidates.extend(("provider", row["provider"], row) for row in rows)
    if include_unassigned:
        key = ("unassigned", "")
        names[key] = "جهة التوصيل غير محددة"
        for kinds in result.get("unassigned_order_details", {}).values():
            for details in kinds.values():
                candidates.append((*key, {"pending_order_details": details}))

    groups, unknown = {}, defaultdict(int)
    for kind, party, row in checked_rows(candidates):
        key = kind, party
        if key not in names:
            continue
        if kind == "provider":
            unknown[key] += row.get("unidentified_pending_orders", 0)
        for item in checked_rows(row.get("pending_order_details") or ()):
            oid = item.get("order_id")
            if not oid:
                continue
            oid = str(oid)
            group = groups.setdefault(key, {})
            if oid not in group:
                group[oid] = {"order_id": oid, "reasons": [], "known": known_evidence(state, kind, party, oid)}
            order = group[oid]
            for reason in item.get("reasons") or ():
                if reason.get("message") and reason not in order["reasons"]:
                    order["reasons"].append({"code": reason.get("code") or "", "message": reason["message"]})

    # The shared number resolver only receives rows visible to this caller.
    packets = [{"kind": kind, "id": party, "name": name, "unidentified": unknown[kind, party],
                "pending_order_details": list(groups.get((kind, party), {}).values())}
               for (kind, party), name in names.items()]
    return {"parties": packets, "as_of": result["as_of"], "started_at": result["started_at"]}


def indexed(packets):
    orders, missing, counts = {}, [], {}
    for party in checked_rows(packets["parties"]):
        count = len(party["pending_order_details"]) + party["unidentified"]
        counts[party["kind"], party["id"]] = count
        if party["unidentified"]:
            missing.append({"name": party["name"], "count": party["unidentified"]})
        for item in checked_rows(party["pending_order_details"]):
            row = orders.setdefault(item["order_id"], {"order_id": item["order_id"],
                "order_number": item.get("order_number"), "groups": []})
            row["groups"].append({key: party[key] for key in ("kind", "id", "name")} |
                                 {"known": item["known"], "reasons": item["reasons"]})
    return {"orders": sorted(orders.values(), key=lambda row: (row["order_number"] or "", row["order_id"]), reverse=True),
            "unidentified": missing, "as_of": packets["as_of"], "started_at": packets["started_at"]}, counts


def page_of(index, query, page):
    term = query.strip().translate(DIGITS).casefold().lstrip("#")
    def matches(row):
        if not term:
            return True
        values = [row["order_number"], row["order_id"]]
        for group in row["groups"]:
            values.append(group["name"])
            values.extend(reason["message"] for reason in group["reasons"])
        return any(term in str(value).translate(DIGITS).casefold() for value in values if value)
    rows = [row for row in checked_rows(index["orders"]) if matches(row)]
    pages = max(1, ceil(len(rows) / PAGE_SIZE))
    page = min(page, pages)
    return {k: index[k] for k in ("as_of", "started_at", "unidentified")} | {
        "items": rows[(page - 1) * PAGE_SIZE:page * PAGE_SIZE], "page_size": PAGE_SIZE,
        "page": page, "total_pages": pages, "total": len(rows), "available": len(index["orders"])}


async def authorized_names(db, owner, policy, kind, identity):
    if (kind is None) != (identity is None) or kind is not None and (not kind.strip() or not identity.strip()):
        fail("operational_account_filter_invalid", "اختر الجهة ونوعها معًا", 422)
    if kind and (kind not in KINDS or policy.resource(kind, identity)["movements"] != "all"):
        fail("operational_workspace_not_found", "طلبات هذه الجهة غير متاحة", 404)
    kinds = {kind} if kind else KINDS
    if not policy.owner_override:
        kinds = kinds.intersection(row["kind"] for row in policy.resources if row["movements"] == "all")
    names = {}
    for category in sorted(kinds):
        for row in await entities(db, owner, category):
            if identity and row["id"] != identity or row.get("ready") is False or row.get("settings_complete") is False:
                continue
            if policy.resource(category, row["id"])["movements"] == "all":
                names[category, row["id"]] = row.get("name") or row["id"]
    if kind and not names:
        fail("operational_workspace_not_found", "طلبات هذه الجهة غير متاحة", 404)
    return names


async def read_order_issues(db, principal, load, cache, kind, identity, query, page):
    actor, owner, _, policy = await load(principal)
    names = await authorized_names(db, owner, policy, kind, identity)
    include_unassigned = kind is None and policy.owner_override
    identity_key = (actor["id"], owner, policy.version, policy.owner_override)
    def key(revision):
        return owner, revision, identity_key, tuple(sorted(names.items())), include_unassigned
    if not names and not include_unassigned:
        return page_of({"orders": [], "as_of": None, "started_at": None, "unidentified": []}, query, page)

    marker = await db[STATES].find_one({"_id": owner, "owner_id": owner},
                                      {"revision": 1, "engine.as_of": 1, "started_at": 1})
    cache_key = key(source_key(marker or {}))
    snapshot = cache.get(cache_key)
    if snapshot is None:
        async with cache.preparation:
            snapshot = cache.get(cache_key)
            if snapshot is None:
                state = await read_financial_position_state(db, owner)
                packets = await prepare_in_thread(review_groups, state, names, include_unassigned)
                labelled = await attach_order_numbers(db, owner, packets)
                snapshot, counts = await prepare_in_thread(indexed, labelled)
                cache.save(key(source_key(state)), snapshot, counts)
    response = await prepare_in_thread(page_of, snapshot, query, page)
    current_actor, current_owner, _, current = await load(principal)
    if identity_key != (current_actor["id"], current_owner, current.version, current.owner_override):
        fail("operational_access_changed", "تغيرت صلاحياتك؛ أعد فتح الصفحة", 409)
    return response
