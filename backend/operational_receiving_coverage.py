"""Owner-scoped, read-only comparison of native receiving components to SAVED receipt evidence.

This comparison measures gross component coverage, not what is owed. It cannot approve,
project or post a difference, replay historic invoices, or convert a foreign balance.
"""
from collections import defaultdict
from decimal import InvalidOperation

from operational_balance_sources import invoice_component_amounts
from operational_supplier_reconciliation import _components, _id, _money, _time


def _saved_component(expected, candidates, fact, invoice, piece, component, rid):
    if len(candidates) > 1:
        return {"status": "ambiguous_evidence"}
    if not candidates:
        return {"status": "fact_only" if fact is not None else "missing"}
    key, row = candidates[0]
    item = piece if component == "product" else piece + ":" + component
    if (not isinstance(fact, dict) or not fact.get("order_id") or
            fact.get("item_id") != item or fact.get("supplier_id") != invoice["supplier_id"] or
            row.get("party_id") != invoice["supplier_id"] or row.get("party_type") != "supplier" or
            row.get("kind") != "supplier" or row.get("direction") != "payable" or row.get("derived_credit") or
            row.get("evidence_ids") != [rid] or row.get("id") != key or
            key != "supplier:" + str(fact["order_id"]) + ":" + item + ":" + rid or
            row.get("source_invoice_id") not in (None, "", invoice["id"])):
        return {"status": "conflicting_evidence"}
    try:
        if _time(fact.get("accepted_at")) != _time(invoice["approved_at"]):
            return {"status": "conflicting_evidence"}
        gross = _money(fact["amount"])
        if (gross != _money(fact.get("gross_amount", fact["amount"])) or
                gross != _money(row.get("gross_amount", fact["amount"]))):
            return {"status": "conflicting_evidence"}
        if ("net_amount" in fact or "tax_amount" in fact) and (
                _money(fact.get("net_amount")) + _money(fact.get("tax_amount")) != gross):
            return {"status": "conflicting_evidence"}
    except (KeyError, ValueError, InvalidOperation):
        return {"status": "conflicting_evidence"}
    currency = row.get("currency")
    if not isinstance(currency, str) or not currency:
        return {"status": "conflicting_evidence"}
    status = ("wrong_currency" if currency != "SAR" else
              "amount_mismatch" if gross != expected else
              "missing_provenance" if not row.get("source_invoice_id") else "matched")
    return {"status": status, "saved_currency": currency, "saved_gross": format(gross, ".2f")}


def _validate_native_line_totals(invoice, amounts):
    # Some legacy invoices omit per-line totals. When supplied they must agree
    # with the canonical per-unit net allocation (VAT is checked separately).
    for line in invoice["lines"]:
        pieces = line["piece_ids"]
        product = sum(_money(amounts[(p, "product")]["net_amount"]) for p in pieces)
        if line.get("product_total_halalas") is not None and _money(line["product_total_halalas"], halalas=True) != product:
            raise ValueError("supplier_review_product_amount_conflict")
        subtotal = product
        for service in line.get("services", []):
            key = "service:" + service["service_id"]
            net = sum(_money(amounts[(p, key)]["net_amount"]) for p in pieces)
            if service.get("total_halalas") is not None and _money(service["total_halalas"], halalas=True) != net:
                raise ValueError("supplier_review_service_amount_conflict")
            subtotal += net
        if line.get("total_halalas") is not None and _money(line["total_halalas"], halalas=True) != subtotal:
            raise ValueError("supplier_review_line_amount_conflict")


def compare_receiving(state, invoices, *, owner, observed_at=None):
    """Exact invoice+piece+component IDs; no joins by nominal value, supplier name or date."""
    if (not isinstance(owner, str) or not owner or state.get("owner_id") != owner
            or state.get("status") != "active"):
        raise ValueError("supplier_review_owner_or_state_invalid")
    start, cutoff = _time(state.get("started_at")), _time(state.get("engine", {}).get("as_of"))
    if observed_at is not None and cutoff > _time(observed_at):
        raise ValueError("supplier_review_stale_snapshot")
    if cutoff < start:
        raise ValueError("supplier_review_cutoff_changed")
    if len(invoices) > 20000:
        raise ValueError("supplier_review_invoice_list_invalid")
    engine = state["engine"]
    by_evidence, by_invoice = defaultdict(list), defaultdict(list)
    for key, row in engine.get("obligations", {}).items():
        if not isinstance(row, dict):
            raise ValueError("supplier_review_saved_obligation_invalid")
        ids = row.get("evidence_ids", [])
        if (row.get("kind") == "supplier" and row.get("direction") == "payable" and
                not row.get("derived_credit") and isinstance(row.get("source_invoice_id"), str)):
            by_invoice[row["source_invoice_id"]].append(ids)
        if isinstance(ids, list):
            for rid in set(e for e in ids if isinstance(e, str) and e.startswith("receipt:")):
                by_evidence[rid].append((key, row))
    facts = engine.get("facts", {})
    rows, seen, after_snapshot = [], set(), 0
    for inv in invoices:
        if not isinstance(inv, dict) or inv.get("user_id") != owner:
            raise ValueError("supplier_review_invoice_owner_mismatch")
        if (inv.get("experiment_mode") or inv.get("experiment_run_id") or not inv.get("approved_at") or
                inv.get("status") not in (None, "payable_posted")):
            continue
        iid = _id(inv.get("id"))
        if iid in seen:
            raise ValueError("supplier_review_duplicate_invoice")
        seen.add(iid)
        approved = _time(inv["approved_at"])
        if approved < start:
            continue
        if approved > cutoff:
            if observed_at is None or approved <= _time(observed_at):
                after_snapshot += 1
            continue
        row = {"invoice_id": iid, "invoice_number": inv.get("invoice_number"), "approved_at": approved.isoformat(),
               "supplier_id": inv.get("supplier_id"), "invoice_gross_sar": None, "matched_components": 0,
               "component_count": None, "differences": [], "source_issue": None, "extra_saved_count": 0}
        rows.append(row)
        try:
            _id(inv.get("supplier_id"))
            if inv.get("currency", "SAR") != "SAR":
                raise ValueError("supplier_review_invoice_currency_unverified")
            expected = _components(inv)
            amounts = invoice_component_amounts(inv)
            _validate_native_line_totals(inv, amounts)
            gross = _money(inv.get("total_halalas"), halalas=True)
            parts = []
            for (piece, component), money in amounts.items():
                rid = "receipt:" + iid + ":" + piece + ":" + component
                if rid not in expected:
                    continue  # already-invoiced product; newly approved services remain independent
                parts.append((piece, component, rid, _money(money["gross_amount"])))
            if (not expected or len(parts) != len(expected) or
                    sum(p[3] for p in parts) != gross):
                raise ValueError("supplier_review_invoice_amount_conflict")
            row.update(invoice_gross_sar=format(gross, ".2f"), component_count=len(parts),
                extra_saved_count=sum(not isinstance(refs, list) or len(refs) != 1 or not isinstance(refs[0], str) or refs[0] not in expected
                                      for refs in by_invoice.get(iid, [])))
            for piece, component, rid, amount in sorted(parts):
                evidence = _saved_component(amount, by_evidence.get(rid, []), facts.get(rid),
                                            inv, piece, component, rid)
                if evidence["status"] == "matched":
                    row["matched_components"] += 1
                else:
                    row["differences"].append({"piece_id": piece, "component": component,
                                               "source_amount_sar": format(amount, ".2f"), **evidence})
        except (TypeError, ValueError, KeyError, InvalidOperation, AttributeError, OverflowError):
            # Invalid line/tax/currency evidence is never turned into a zero-valued missing unit.
            row.update(source_issue="supplier_review_invoice_source_invalid", matched_components=0,
                       invoice_gross_sar=None, component_count=None, differences=[])
    rows.sort(key=lambda r: (r["approved_at"], r["invoice_id"]), reverse=True)
    return {"schema": "receiving_component_coverage_v1", "read_only": True, "posting_allowed": False,
            "started_at": start.isoformat(), "saved_balance_as_of": cutoff.isoformat(),
            "after_snapshot_count": after_snapshot, "rows": rows,
            "invoice_count": len(rows), "review_count": sum(bool(r["source_issue"] or r["differences"] or r["extra_saved_count"]) for r in rows)}
