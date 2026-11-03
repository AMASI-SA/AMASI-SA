"""Describe payment evidence already present in one saved operational snapshot."""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

PLATFORMS = {"salla": "سلة", "tabby": "تابي", "tamara": "تمارا", "emkan": "إمكان"}
LABELS = {
    "order_page": "لها إثبات مدفوع ومتبقٍ من صفحة الطلب",
    "capture": "لها قيد تحصيل محفوظ", "settlement_allocation": "خُصص لها سداد كلي أو جزئي",
    "missing_time": "ينقصها وقت التحصيل", "fee_review": "تحتاج مطابقة الرسوم",
    "settlement_review": "تحتاج مطابقة التسوية", "capture_review": "تحتاج مراجعة إثبات التحصيل",
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
        counts[party][group].add(order)

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

def order_page_payment_rows(projection):
    """Break down saved page observations and unposted candidates by provider and currency."""
    records = {}
    for key, row in projection["details"].get("facts", {}).items():
        if key.startswith("provider_order_balance:") and isinstance(row, dict) and key == "provider_order_balance:" + str(row.get("order_id")):
            records[("saved", row["order_id"])] = row
    for issue in projection["issues"]:
        row = issue.get("order_page_balance_review")
        if isinstance(row, dict) and row.get("order_id") and issue.get("source_id") == "provider:" + str(row["order_id"]):
            records[("review", row["order_id"])] = {**row, "review_code": issue.get("code")}

    groups = defaultdict(lambda: [0, Decimal(0), Decimal(0)])
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
    return by_currency
