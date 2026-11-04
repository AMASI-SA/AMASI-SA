"""Describe payment evidence already present in one saved operational snapshot."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from operational_split_payment import provider_shares, split_reference_owners

PLATFORMS = {"salla": "سلة", "tabby": "تابي", "tamara": "تمارا", "emkan": "إمكان"}
LABELS = {
    "order_page": "لها إثبات مدفوع ومتبقٍ من صفحة الطلب",
    "capture": "لها قيد تحصيل محفوظ", "settlement_allocation": "خُصص لها سداد كلي أو جزئي",
    "missing_time": "ينقصها وقت التحصيل", "fee_review": "تحتاج مطابقة الرسوم",
    "settlement_review": "تحتاج مطابقة التسوية", "capture_review": "تحتاج مراجعة إثبات التحصيل",
}


def _split_payment_rows(facts):
    for key, saved in facts.items():
        if not key.startswith("provider_split:") or not isinstance(saved, dict):
            continue
        evidence = saved.get("evidence") or {}
        if key != "provider_split:" + str(evidence.get("order_id")):
            continue
        for provider, legs in provider_shares(evidence).items():
            paid = format(sum(Decimal(leg["amount"]) for leg in legs), ".2f")
            applied = provider in saved["booked_providers"]
            yield ("saved" if applied else "review"), {
                "provider": provider, "currency": evidence["currency"], "order_id": evidence["order_id"],
                "paid": paid, "gross": paid, "remaining": "0.00",
                "review_code": "" if applied else "provider_settled_capture_review_required",
            }


def provider_evidence_coverage(projection):
    counts = defaultdict(lambda: defaultdict(set))
    party_for_order = {}
    for row in projection["obligations"]:
        key = row["id"]
        if row.get("kind") == "provider" and row.get("party_type") == "provider" and key.startswith("provider:"):
            order, party = key[len("provider:"):], row["party_id"]
            party_for_order[order] = party
            if Decimal(row["settled"]) > 0:
                counts[party]["settlement_allocation"].add(order)
    for key, fact in projection["details"].get("facts", {}).items():
        if key.startswith("capture:") and isinstance(fact, dict) and fact.get("order_id") and fact.get("provider"):
            counts[fact["provider"]]["capture"].add(fact["order_id"])
        elif isinstance(fact, dict) and fact.get("provider") in PLATFORMS and key == "provider_order_balance:" + str(fact.get("order_id")):
            counts[fact["provider"]]["order_page"].add(fact["order_id"])

    for status, row in _split_payment_rows(projection["details"].get("facts", {})):
        party, oid = row["provider"], row["order_id"]
        party_for_order[oid + ":" + party] = party
        counts[party]["order_page" if status == "saved" else "settlement_review"].add(oid)

    for issue in projection["issues"]:
        order = issue.get("source_id") or ""
        if issue.get("scope") != "provider_payment":
            if not order.startswith("provider:"):
                continue
            order = order[len("provider:"):]
        if not order:
            continue
        party = issue.get("provider") or party_for_order.get(order) or "unassigned"
        code = str(issue.get("code") or "")
        if "capture_timestamp" in (issue.get("missing_fields") or []):
            group = "missing_time"
        elif "fee" in code:
            group = "fee_review"
        elif "settled" in code or "settlement" in code:
            group = "settlement_review"
        else:
            group = "capture_review"
        counts[party][group].add(order.removesuffix(":" + party))

    if not counts:
        return [{"code":"provider_evidence_coverage", "status":"unavailable", "observed_order_counts":{},
                 "message":"لا تتوفر باللقطة أدلة تكفي لقياس تغطية تحصيل المنصات وتسوياتها."}]
    rows = []
    for party, groups in sorted(counts.items()):
        observed = {key: len(groups[key]) for key in LABELS if groups.get(key)}
        text = "، ".join(f"{n} {LABELS[key]}" for key, n in observed.items())
        rows.append({"code":"provider_evidence_coverage_"+party, "status":"incomplete",
                     "observed_order_counts":observed,
                     "message":f"طلبات {PLATFORMS.get(party, 'المنصة غير المحددة')} في اللقطة المحفوظة (كل العملات): {text}. "
                               "قد يظهر الطلب في أكثر من فئة. اكتمال التغطية يتطلب كشف المنصة."})
    return rows

def provider_customer_payments(projection):
    """Return the page breakdown and distinct evidenced gross payments in each native currency."""
    records = {}
    for key, row in projection["details"].get("facts", {}).items():
        if key.startswith("provider_order_balance:") and isinstance(row, dict) and key == "provider_order_balance:" + str(row.get("order_id")):
            records[("saved", row["order_id"])] = row
    for status, row in _split_payment_rows(projection["details"].get("facts", {})):
        records[(status, row["order_id"] + ":" + row["provider"])] = row
    for issue in projection["issues"]:
        row = issue.get("order_page_balance_review")
        if isinstance(row, dict) and row.get("order_id") and issue.get("source_id") == "provider:" + str(row["order_id"]):
            records[("review", row["order_id"])] = {**row, "review_code": issue.get("code")}

    groups = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
    paid_by_order = defaultdict(Decimal)
    for (status, _), row in records.items():
        provider, currency = row.get("provider"), row.get("currency")
        if provider not in PLATFORMS or not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha() or not currency.isupper():
            continue
        try:
            paid, due, gross = (Decimal(str(row[k])) for k in ("paid", "remaining", "gross"))
            if not all(n.is_finite() and n >= 0 for n in (paid, due, gross)) or paid + due != gross:
                continue
        except (KeyError, ValueError, InvalidOperation):
            continue
        identity = (currency, provider, row["order_id"])
        paid_by_order[identity] = max(paid_by_order[identity], paid)
        reason = row.get("review_code", "") if status == "review" else ""
        totals = groups[currency, provider, status, reason]
        totals[0] += 1
        totals[1] += paid
        totals[2] += due
    by_currency = defaultdict(list)
    for (currency, provider, status, reason), (orders, paid, due) in sorted(groups.items()):
        by_currency[currency].append({
            "id": f"{provider}-{status}-{reason}", "name": PLATFORMS[provider],
            "status": ("إثبات محفوظ" if status == "saved" else {
                "provider_historical_capture_review_required": "للمراجعة — طلب سابق لآخر احتساب",
                "provider_settled_capture_review_required": "للمراجعة — سداد يحتاج مطابقة",
            }.get(reason, "للمراجعة قبل الاحتساب")),
            "orders": orders, "paid": format(paid, ".2f"), "remaining": format(due, ".2f"),
        })
    # A later page can corroborate one or more captures for the same order.
    # Both observations describe the same customer money.
    claims = {row["id"]: row for row in projection["obligations"] if row.get("kind") == "provider"}
    captured = defaultdict(Decimal)
    for key, fact in projection["details"].get("facts", {}).items():
        if not key.startswith("capture:") or not isinstance(fact, dict):
            continue
        oid, party = fact.get("order_id"), fact.get("provider")
        claim = claims.get("provider:" + str(oid))
        if claim and party in PLATFORMS and claim["party_id"] == party:
            native_currency = (claim.get("native_balance") or {}).get("currency", claim["currency"])
            captured[(native_currency, party, oid)] += Decimal(fact["amount"])
    for identity, value in captured.items():
        paid_by_order[identity] = max(paid_by_order[identity], value)

    # A closed foreign checkout may prove its charged tender even when the
    # original order total disagrees. It is never an order balance or a claim.
    observations = [i["paid_tender_observation"] for i in projection["issues"]
                    if i.get("code") == "provider_closed_tender_total_mismatch"
                    and i.get("scope") == "provider_payment" and isinstance(i.get("paid_tender_observation"), dict)
                    and i["paid_tender_observation"].get("order_id") == i.get("source_id")]
    if observations:
        facts = projection["details"].get("facts", {})
        owners = split_reference_owners(facts)
        scopes = defaultdict(set)
        for currency, provider, order in paid_by_order:
            scopes[order].add((currency, provider))
        for row in observations:
            owners[row["reference"]].add(row["order_id"])
            scopes[row["order_id"]].add((row["currency"], row["provider"]))
        for row in observations:
            oid = row["order_id"]
            if (owners[row["reference"]] == {oid} and len(scopes[oid]) == 1
                    and "provider_split:" + oid not in facts):
                identity = (row["currency"], row["provider"], oid)
                paid_by_order[identity] = max(paid_by_order[identity], Decimal(row["paid"]))

    totals = defaultdict(lambda: [0, Decimal(0)])
    for (currency, provider, _), paid in paid_by_order.items():
        if paid > 0:
            group = totals[currency, provider]
            group[0] += 1
            group[1] += paid
    summary = defaultdict(list)
    for (currency, provider), (orders, paid) in sorted(totals.items()):
        summary[currency].append({"provider": provider, "name": PLATFORMS[provider],
                                  "orders": orders, "paid": format(paid, ".2f")})
    return by_currency, summary
