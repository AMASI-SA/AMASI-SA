"""One-order release of a reviewed cumulative claim. No opening or settlement writes."""
from decimal import Decimal

from operational_balance_store import digest
from operational_payment_evidence import _amount, _instant

HISTORICAL = "provider_historical_capture_review_required"
SETTLED = "provider_settled_capture_review_required"
REVIEWS = "provider_history_reviews"
BLOCKS = {
    "order": "إثبات الطلب أو هويته أو تاريخ إنشائه غير مكتمل.",
    "claim": "يوجد إثبات تحصيل سابق أو ذمة متعارضة لهذا الطلب.",
    "return": "توجد حالة استرداد أو استبدال تحتاج إلى مطابقة.",
    "opening": "الرصيد الافتتاحي للمنصة غير محدد بهوية واحدة.",
    "movement": "توجد حركة تحصيل أو سداد لا يمكن استبعاد ارتباطها بهذا الطلب.",
    "opening_settlement": "سُوّي مبلغ على افتتاحي المنصة. يلزم كشف الحوالة بسطور طلباتها أو تاريخ إقفال موثّق من المنصة يسبق إنشاء الطلب.",
    "pooled": "توجد حوالة للمنصة بلا توزيع كامل على طلبات محددة. يلزم كشف يطابق سطورها.",
    "target": "توجد حركة أو تسوية مرتبطة بهذا الطلب. راجعها قبل إثبات أي مستحق إضافي.",
    "settled": "سبق إحالة الطلب لمطابقة سداد.",
}


def proof_hash(order, payment):
    return digest([order.get("id"), order.get("order_number"), order.get("created_at"),
                   order.get("updated_at"), payment])


def _context(state, row):
    """Check only attributable provider money. A pooled or opening payout always blocks."""
    owner, oid, number, party, ccy = (row[k] for k in ("owner_id", "order_id", "order_number", "provider", "currency"))
    key = "provider:" + oid
    scope = (party, ccy)
    engine = state.get("engine", {})
    issues = []
    try:
        paid = _amount(row["paid"], ccy)
        if (not owner or owner != state.get("owner_id") or not oid or not number or party not in {"salla","tabby","tamara","emkan"}
                or paid <= 0 or paid + _amount(row["remaining"], ccy) != _amount(row["gross"], ccy)
                or not _instant(state["started_at"]) < _instant(row["created_at"]) <= _instant(engine["as_of"])):
            issues.append("order")
        claim = engine.get("obligations", {}).get(key)
        if claim and (claim.get("party_type") != "provider" or (claim.get("party_id"), claim.get("currency")) != scope
                or claim.get("direction") != "receivable" or _amount(claim["confirmed"], ccy) != 0):
            issues.append("claim")
        if any(isinstance(f, dict) and f.get("order_id") == oid and name.startswith(("capture:", "refund:", "provider_order_balance:"))
               for name, f in engine.get("facts", {}).items()):
            issues.append("claim")
        if any(r.get("order_id") == oid or r.get("order_number") == number
               for group in ("customer_returns","customer_exchanges") for r in state.get(group, [])):
            issues.append("return")
        if ("cod_collection:" + oid in engine.get("facts", {}) or
                _amount(engine.get("obligations", {}).get("cod:" + oid, {}).get("confirmed", 0), ccy) > 0):
            issues.append("claim")
        for movement in state.get("movements", []):
            if (movement.get("order_number") == number or movement.get("original_order_number") == number
                    or movement.get("request_id") == "order-bank:" + oid
                    or any(a.get("obligation_id") in (key, "credit:" + key) for a in movement.get("allocations", []))):
                issues.append("target")
            if (movement.get("party_type") == "provider" and movement.get("party_id") in (None, "", party)
                    and movement.get("currency") in (None, "", ccy) and (not movement.get("party_id") or not movement.get("currency"))):
                issues.append("movement")
        openings = [o for o in state.get("openings", {}).values()
                    if o.get("party_type") == "provider" and (o.get("party_id"), o.get("currency")) == scope]
        if (len(openings) > 1 or any(not o.get("id") or o.get("direction") not in ("for_us", "for_party")
                                     or _amount(o.get("amount"),ccy) < 0 for o in openings)
                or any(o.get("party_type") == "provider" and o.get("party_id") in (None, "", party)
                       and o.get("currency") in (None, "", ccy) and (not o.get("currency") or not o.get("party_id"))
                       for o in state.get("openings",{}).values())):
            issues.append("opening")
        movements = [m for m in state.get("movements", []) if m.get("party_type") == "provider"
                     and (m.get("party_id"), m.get("currency")) == scope]
        for m in movements:
            allocations = m.get("allocations") or []
            ids = [a["obligation_id"] for a in allocations]
            gross = _amount(m["amount"], ccy) + _amount(m.get("actual_fee_amount", "0"), ccy)
            if any(k in {o["id"] for o in openings} for k in ids):
                issues.append("opening_settlement")
                break
            if not ids or sum((_amount(a["amount"], ccy) for a in allocations), Decimal(0)) != gross:
                issues.append("pooled")
                break
            if (len(ids) != len(set(ids)) or m.get("direction") != "incoming" or m.get("kind") != "settlement"
                    or any(not k.startswith("provider:") or k == key for k in ids) or m.get("order_number") == number
                    or any(_amount(a["amount"], ccy) <= 0 for a in allocations)):
                issues.append("movement")
                break
            for other in ids:
                ob = engine.get("obligations", {}).get(other, {})
                if (ob.get("kind"), ob.get("party_type"), ob.get("party_id"), ob.get("currency")) != ("provider","provider",party,ccy):
                    issues.append("movement")
                    break
        return sorted(set(issues)), openings, movements
    except (KeyError, TypeError, ValueError, ArithmeticError):
        return ["order"], [], []


def make_review_row(order, payment):
    page = payment.get("order_page_balance") or {}
    fields = ("owner_id", "order_id", "order_number", "provider", "currency", "gross", "paid", "remaining")
    if (page.get("source") != "canonical_salla_order_page" or not all(page.get(k) for k in fields)
            or order.get("bank_credit") or payment.get("refunds") or payment.get("cancellations")
            or _amount(payment.get("cancelled", 0), page["currency"]) != 0
            or (str(order["id"]), order["order_number"]) != (page["order_id"], page["order_number"])):
        return None
    return {**{k: page[k] for k in fields}, "created_at": order["created_at"], "proof_hash": proof_hash(order, payment)}


def preview_row(state, row, code):
    blocks, openings, movements = _context(state, row)
    if code != HISTORICAL:
        blocks.append("settled")
    return {**{k: row[k] for k in ("order_id","order_number","provider","currency","gross","paid","remaining","created_at")},
        "openings": [{k:o.get(k) for k in ("id","direction","amount")} for o in openings],
        "movement_count": len(movements), "blocks": [BLOCKS[b] for b in blocks],
        "preview_hash": digest([state.get("owner_id"), state.get("started_at"), code, row, openings, movements, blocks])}


def approved_context(state, order, payment):
    oid = str(order["id"])
    saved = state.get(REVIEWS, {}).get(digest(oid))
    if not saved or saved.get("owner_id") != state.get("owner_id") or saved.get("confirmed_by") != state.get("owner_id"):
        return False
    row = make_review_row(order, payment)
    if not row or saved.get("proof_hash") != row["proof_hash"] or not saved.get("evidence_id"):
        return False
    preview = preview_row(state, row, HISTORICAL)
    return not preview["blocks"] and saved.get("preview_hash") == preview["preview_hash"]
