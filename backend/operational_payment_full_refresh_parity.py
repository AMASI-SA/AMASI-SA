"""Read-only zero-difference contract for a dated first provider capture.

Compare a saved BEFORE state, the direct CAS payment result, and a later full
operational engine reconciliation. No posting, database or Salla API calls.
A synthetic "match" alone is not Preview/Production acceptance: pass actual
independently recomputed full-refresh states before release.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

MONEY_REPORT_FIELDS = (
    "gross", "cancelled", "refunded", "net", "estimated_fees",
    "expected_receivable", "estimated_fee_net", "estimated_fee_tax",
    "confirmed_receivable",
)
MONEY_CLAIM_FIELDS = ("confirmed", "expected")
CLAIM_FIELDS = ("id", "kind", "party_id", "party_type", "currency",
                "direction", "business_date")
MAX_FACTS = 25000


def _money(value):
    if value is None:
        return None
    if isinstance(value,bool):
        raise ValueError("payment_parity_amount_invalid")
    try:
        item=Decimal(str(value))
        if not item.is_finite() or item != item.quantize(Decimal(".01")):
            raise ValueError("payment_parity_amount_invalid")
        return item
    except (InvalidOperation, TypeError, OverflowError):
        raise ValueError("payment_parity_amount_invalid") from None


def compare_first_provider_capture(before, direct, recomputed, *, order_id, provider, reference):
    """Return only safe mismatch codes, never customer data or amounts."""
    if not all(isinstance(s,dict) and isinstance(s.get("engine"),dict)
               for s in (before,direct,recomputed)):
        raise ValueError("payment_parity_snapshot_invalid")
    if not all(isinstance(v,str) and v.strip() and len(v)<=256
               for v in (order_id,provider,reference)):
        raise ValueError("payment_parity_identity_invalid")
    key="provider:"+order_id
    capture="capture:"+reference
    problems=[]
    def add(code):
        if code not in problems:problems.append(code)

    originals=before["engine"]
    new=direct["engine"]
    updated=recomputed["engine"]
    if (before.get("owner_id") != direct.get("owner_id")
            or before.get("owner_id") != recomputed.get("owner_id")):
        add("payment_parity_owner_conflict")
    for name in ("facts","obligations","provider_reports"):
        for snapshot in (originals,new,updated):
            unit=snapshot.get(name)
            if not isinstance(unit,dict) or (name=="facts" and len(unit)>MAX_FACTS):
                add("payment_parity_scope_invalid")
                return {"status":"conflict","issues":sorted(problems),
                        "release_verified":False}
    if key in originals["obligations"] or capture in originals["facts"]:
        add("payment_parity_not_first_capture")
    if (before.get("movements") != direct.get("movements")
            or before.get("openings") != direct.get("openings")):
        add("payment_parity_direct_bank_or_opening_modified")
    if (direct.get("movements") != recomputed.get("movements")
            or direct.get("openings") != recomputed.get("openings")):
        add("payment_parity_refresh_bank_or_opening_changed")
    for label, snapshot in (("direct",new),("refresh",updated)):
        fact=snapshot["facts"].get(capture)
        if (not isinstance(fact,dict) or str(fact.get("order_id"))!=order_id
                or fact.get("provider")!=provider):
            add("payment_parity_"+label+"_capture_missing_or_changed")
        claim=snapshot["obligations"].get(key)
        if (not isinstance(claim,dict) or claim.get("party_id")!=provider
                or claim.get("kind")!="provider"):
            add("payment_parity_"+label+"_provider_claim_missing_or_changed")
        report=snapshot["provider_reports"].get(key)
        if not isinstance(report,dict):
            add("payment_parity_"+label+"_fee_projection_missing")
    a,b=new["facts"].get(capture),updated["facts"].get(capture)
    if isinstance(a,dict) and isinstance(b,dict):
        try:
            if _money(a.get("amount")) is None or _money(a["amount"])!=_money(b.get("amount")):
                add("payment_parity_capture_amount_delta")
        except ValueError:
            add("payment_parity_capture_amount_invalid")
    a,b=new["obligations"].get(key),updated["obligations"].get(key)
    if isinstance(a,dict) and isinstance(b,dict):
        for field in CLAIM_FIELDS:
            if a.get(field)!=b.get(field):
                add("payment_parity_claim_"+field+"_delta")
        for field in MONEY_CLAIM_FIELDS:
            try:
                if _money(a.get(field)) is None or _money(a[field])!=_money(b.get(field)):
                    add("payment_parity_claim_"+field+"_delta")
            except ValueError:
                add("payment_parity_claim_"+field+"_invalid")
    a,b=new["provider_reports"].get(key),updated["provider_reports"].get(key)
    if isinstance(a,dict) and isinstance(b,dict):
        if not isinstance(a.get("policy_id"),str) or a.get("policy_id")!=b.get("policy_id"):
            add("payment_parity_fee_policy_delta")
        for field in MONEY_REPORT_FIELDS:
            try:
                if _money(a.get(field))!=_money(b.get(field)):
                    add("payment_parity_fee_"+field+"_delta")
            except ValueError:
                add("payment_parity_fee_"+field+"_invalid")
    return {"status":"match" if not problems else "conflict",
            "issues":sorted(problems),
            "release_verified":False}
