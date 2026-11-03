"""Describe payment evidence already present in one saved operational snapshot."""
from collections import defaultdict
from decimal import Decimal

PLATFORMS = {"salla": "سلة", "tabby": "تابي", "tamara": "تمارا", "emkan": "إمكان"}
LABELS = {
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
