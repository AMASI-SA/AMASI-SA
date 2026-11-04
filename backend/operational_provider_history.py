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


def _context(state, row, *, obligation_key=None, through=None):
    """Check only attributable provider money. A pooled or opening payout always blocks."""
    owner, oid, number, party, ccy = (row[k] for k in ("owner_id", "order_id", "order_number", "provider", "currency"))
    key = obligation_key or "provider:" + oid
    scope = (party, ccy)
    engine = state.get("engine", {})
    issues = []
    try:
        paid = _amount(row["paid"], ccy)
        created = _instant(row["created_at"])
        fresh = through is not None and (not engine.get("as_of") or created > _instant(engine["as_of"]))

        if (not owner or owner != state.get("owner_id") or not oid or not number or party not in {"salla","tabby","tamara","emkan"}
                or paid <= 0 or paid + _amount(row["remaining"], ccy) != _amount(row["gross"], ccy)
                or not _instant(state["started_at"]) < created <= _instant(through or engine["as_of"])):
            issues.append("order")
        claim = engine.get("obligations", {}).get(key)
        if claim and (claim.get("party_type") != "provider" or (claim.get("party_id"), claim.get("currency")) != scope
                or claim.get("direction") != "receivable" or _amount(claim["confirmed"], ccy) != 0):
            issues.append("claim")
        if any(isinstance(f, dict) and f.get("order_id") == oid and (
                name.startswith(("capture:", "refund:")) or
                (name.startswith("provider_order_balance:") and _amount(f["paid"], ccy) > 0))
               for name, f in engine.get("facts", {}).items()):
            issues.append("claim")
        if any(r.get("order_id") == oid or r.get("order_number") == number
               for group in ("customer_returns","customer_exchanges") for r in state.get(group, [])):
            issues.append("return")
        cod = engine.get("facts", {}).get("cod_collection:" + oid)
        if ((cod is not None and (_amount(cod["amount"], ccy) > 0 or cod.get("evidence"))) or
                _amount(engine.get("obligations", {}).get("cod:" + oid, {}).get("confirmed", 0), ccy) > 0):
            issues.append("claim")
        movements = []
        for movement in state.get("movements", []):
            other_split_provider = (obligation_key and movement.get("party_type") == "provider"
                                    and movement.get("party_id") not in (party, "", None))
            same_number = number in (movement.get("order_number"), movement.get("original_order_number"))
            if ((same_number and not other_split_provider) or (movement.get("order_id") == oid and not other_split_provider)
                    or movement.get("request_id") == "order-bank:" + oid
                    or any(a.get("obligation_id") in (key, "credit:" + key) for a in movement.get("allocations", []))):
                issues.append("target")
            # A recorded payout predating a new order could not settle that order.
            # Explicit order/obligation links above still require reconciliation.
            if fresh and movement.get("occurred_at") and _instant(movement["occurred_at"]) < created:
                continue
            if movement.get("party_type") == "provider" and (movement.get("party_id"), movement.get("currency")) == scope:
                movements.append(movement)
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
        for m in movements:
            allocations = m.get("allocations") or []
            ids = [a["obligation_id"] for a in allocations]
            gross = _amount(m["amount"], ccy) + _amount(m.get("actual_fee_amount", "0"), ccy)
            opening_ids = {o["id"] for o in openings}
            if not ids or sum((_amount(a["amount"], ccy) for a in allocations), Decimal(0)) != gross:
                issues.append("pooled")
                break
            if (len(ids) != len(set(ids)) or m.get("direction") != "incoming" or m.get("kind") != "settlement"
                    or any((not k.startswith("provider:") and k not in opening_ids) or k == key for k in ids) or m.get("order_number") == number
                    or any(_amount(a["amount"], ccy) <= 0 for a in allocations)):
                issues.append("movement")
                break
            for other in ids:
                # The owner's opening is the balance at started_at. A complete
                # allocation to that opening cannot also cover a uniquely
                # evidenced order created strictly after that boundary.
                if other in opening_ids:
                    opening = next(o for o in openings if o["id"] == other)
                    used = sum((_amount(a["amount"], ccy) for movement in movements
                                for a in movement.get("allocations", []) if a.get("obligation_id") == other), Decimal(0))
                    if opening["direction"] != "for_us" or used > _amount(opening["amount"], ccy):
                        issues.append("movement")
                    continue
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
            or (page["provider"], page["currency"]) != (payment.get("provider"), payment.get("currency"))
            or _amount(page["gross"], page["currency"]) != _amount(payment["gross"], page["currency"])
            or order.get("bank_credit") or payment.get("refunds") or payment.get("cancellations")
            or _amount(payment.get("cancelled", 0), page["currency"]) != 0
            or (str(order["id"]), order["order_number"]) != (page["order_id"], page["order_number"])):
        return None
    return {**{k: page[k] for k in fields}, "created_at": order["created_at"]}


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
    if not row:
        return False
    row["proof_hash"] = proof_hash(order, payment)
    page = payment["order_page_balance"]
    if saved.get("proof_hash") != row["proof_hash"] and "transaction_reference" in page:
        # Older review records predate preservation of the source's transaction
        # reference. Every field reviewed then must still match the saved hash.
        legacy_page = {k: v for k, v in page.items() if k != "transaction_reference"}
        row["proof_hash"] = proof_hash(order, {**payment, "order_page_balance": legacy_page})
    if saved.get("proof_hash") != row["proof_hash"] or not saved.get("evidence_id"):
        return False
    preview = preview_row(state, row, HISTORICAL)
    return not preview["blocks"] and saved.get("preview_hash") == preview["preview_hash"]


def _sar_payout_overlap(state, order, payment, key):
    """An unbooked foreign sale may already be covered by its provider's SAR payout."""
    fx, party = order.get("customer_payment_sar") or {}, payment.get("provider")
    if order.get("currency") == "SAR" or party not in (fx.get("shares") or {}):
        return False
    oid, number = str(order["id"]), order.get("order_number")
    created = _instant(order["created_at"])
    claims = state.get("engine", {}).get("obligations", {})
    for m in state.get("movements", []):
        if (m.get("party_type"), m.get("party_id"), m.get("currency")) != ("provider", party, "SAR"):
            continue
        lines = m.get("allocations") or []
        ids = [a.get("obligation_id") for a in lines]
        if ((number and number in (m.get("order_number"), m.get("original_order_number")))
                or m.get("order_id") == oid or key in ids or "credit:" + key in ids):
            return True
        if m.get("direction") != "incoming":
            continue
        try:
            if m.get("occurred_at") and _instant(m["occurred_at"]) < created:
                continue
            total = sum((_amount(a["amount"], "SAR") for a in lines), Decimal(0))
            if (not lines or len(set(ids)) != len(ids) or m.get("kind") != "settlement"
                    or total != _amount(m["amount"], "SAR") + _amount(m.get("actual_fee_amount", "0"), "SAR")):
                return True
            # Only a valid allocation to a different specific order excludes overlap.
            for identity in ids:
                opening = state.get("openings", {}).get(identity, {})
                if ((opening.get("party_type"), opening.get("party_id"), opening.get("currency")) == ("provider", party, "SAR")
                        and opening.get("direction") == "for_us"
                        and _instant(state["started_at"]) < created):
                    used = sum((_amount(a["amount"], "SAR") for movement in state.get("movements", [])
                                for a in movement.get("allocations", []) if a.get("obligation_id") == identity), Decimal(0))
                    if used > _amount(opening.get("amount"), "SAR"):
                        return True
                    continue
                claim = claims.get(identity, {})
                if (not isinstance(identity, str) or not identity.startswith("provider:")
                        or (claim.get("kind"), claim.get("party_id"), claim.get("currency")) != ("provider", party, "SAR")):
                    return True
        except (KeyError, TypeError, ValueError, ArithmeticError):
            return True
    return False


def late_payment_blocks(state, order, payment, *, obligation_key=None, through=None):
    """An older snapshot alone cannot hide post-start money. Check possible overlaps."""
    if _sar_payout_overlap(state, order, payment, obligation_key or "provider:" + str(order["id"])):
        return True
    if state.get(REVIEWS) and digest(str(order["id"])) in state[REVIEWS]:
        # Revalidate any pending owner decision against its original evidence.
        return not approved_context(state, order, payment)
    if payment.get("order_page_balance") is not None:
        row = make_review_row(order, payment)
        if not row:
            return True
    else:
        currency = payment["currency"]
        captures = payment.get("captures", [])
        if (not captures or order.get("bank_credit") or payment.get("refunds") or payment.get("cancellations")
                or _amount(payment.get("cancelled", 0), currency)):
            return True
        paid = sum((_amount(c["amount"], currency) for c in captures), Decimal(0))
        gross = _amount(payment["gross"], currency)
        row = {"owner_id": state.get("owner_id"), "order_id": str(order["id"]),
               "order_number": order.get("order_number"), "created_at": order["created_at"],
               "provider": payment["provider"], "currency": currency, "gross": gross,
               "paid": paid, "remaining": gross - paid}
    blocks, _, _ = _context(state, row, obligation_key=obligation_key, through=through)
    return bool(blocks)
