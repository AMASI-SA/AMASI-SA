"""Single-invoice, read-only model of gross receiving components in their own currencies."""
from collections import Counter
from decimal import InvalidOperation

from fastapi import HTTPException

from operational_balance_sources import instant, invoice_component_amounts
from operational_supplier_reconciliation import _components, _money


def _result(invoice, reasons, legs=(), proofs=None):
    return {"invoice_id": invoice["id"], "status": "blocked" if reasons else "review_only",
            "reasons": sorted(set(reasons)), "legs": [] if reasons else list(legs),
            "order_evidence": {} if reasons else proofs or {}, "posting_allowed": False, "read_only": True,
            "bank_movements_created": 0, "existing_allocations_changed": 0, "openings_changed": False}


def component_proposal(state, invoice, coverage, *, native_clear, order_proofs, cross_claim):
    """An invoice is indivisible for review. A blocked unit withholds every proposed leg."""
    sid, iid = invoice["supplier_id"], invoice["id"]
    reasons = []
    if not native_clear:
        reasons.append("native_settlement_not_proven_clear")
    if cross_claim:
        reasons.append("other_document_may_cover_receipt")
    if (coverage.get("source_issue") or coverage.get("extra_saved_count")
            or (coverage.get("invoice_id"), coverage.get("supplier_id")) != (iid, sid)
            or state.get("owner_id") != invoice.get("user_id")
            or not isinstance(state.get("openings"), dict) or not isinstance(state.get("movements"), list)):
        return _result(invoice, reasons + ["invoice_evidence_incomplete"])
    differences = coverage["differences"]
    if not differences:
        return _result(invoice, reasons + ["no_proposed_change"])

    target = _components(invoice)
    obligations, facts = state["engine"]["obligations"], state["engine"]["facts"]
    keys = {k for k, row in obligations.items() if row.get("source_invoice_id") == iid
            or target.intersection(row.get("evidence_ids", []))}
    if any(o.get("party_id") == sid and o.get("party_type") == "supplier" and _money(o["amount"]) != 0
           for o in state["openings"].values()):
        reasons.append("supplier_opening_requires_allocation_review")
    if any((m.get("party_id") == sid and m.get("party_type") == "supplier") or
           any(a.get("obligation_id") in keys for a in m.get("allocations", [])) for m in state["movements"]):
        reasons.append("prior_movement_or_unallocated_payment")
    if (any(k.startswith("return:") and f.get("receipt_id") in target for k, f in facts.items())
            or any("receipt:" + str(r.get("receipt_id")) in target for r in state.get("supplier_returns", []))
            or any(r.get("invoice_id") == iid or r.get("supplier_id") == sid for r in state.get("supplier_adjustments", []))):
        reasons.append("supplier_return_or_adjustment")
    if any(v.get("supplier_id") == sid for v in state.get("native_purchase_links", {}).values()):
        reasons.append("native_purchase_link_requires_review")
    purchases = list(state.get("inventory_purchases", []))
    for case in state.get("customer_exchanges", []):
        purchases.extend(case.get("purchases", []))
    if any(p.get("supplier_id") == sid and (p.get("id") == iid or
           p.get("supplier_invoice_number", p.get("invoice_number")) == invoice.get("invoice_number")) for p in purchases):
        reasons.append("other_document_may_cover_receipt")

    # A legacy aggregate, damaged receipt, or unexplained reduction in confirmed debt
    # cannot be assumed to be unrelated to the missing piece.
    for key, saved in obligations.items():
        if saved.get("kind") != "supplier" or saved.get("party_id") != sid:
            continue
        evidence = saved.get("evidence_ids") or []
        if key in keys:
            if (saved.get("superseded") or saved.get("derived_credit") or _money(saved.get("expected", "0"))
                    or _money(saved.get("returned_amount", "0")) or len(evidence) != 1
                    or saved.get("confirmed") != facts.get(evidence[0], {}).get("amount")):
                reasons.append("saved_amount_or_currency_requires_review")
        elif (_money(saved.get("confirmed", "0")) and not saved.get("derived_credit")
              and (len(evidence) != 1 or not str(evidence[0]).startswith("receipt:") or evidence[0] not in facts)):
            reasons.append("other_document_may_cover_receipt")

    # Same physical charge under a different invoice or supplier is still the same charge.
    receipt_items = {}
    for rid, fact in facts.items():
        if rid.startswith("receipt:"):
            receipt_items.setdefault(fact.get("item_id"), set()).add(rid)
    amounts = invoice_component_amounts(invoice)
    legs = []
    for difference in differences:
        piece, component = difference["piece_id"], difference["component"]
        item = piece if component == "product" else f"{piece}:{component}"
        rid = f"receipt:{iid}:{piece}:{component}"
        proof = order_proofs.get(piece)
        if difference["status"] not in {"missing", "wrong_currency"}:
            reasons.append("unsupported_receipt_discrepancy")
            continue
        if not proof:
            reasons.append("missing_finalized_receipt_or_unique_order_line")
            continue
        key = f"supplier:{proof['order_id']}:{item}:{rid}"
        if receipt_items.get(item, set()) - {rid} or "credit:" + key in obligations:
            reasons.append("other_document_may_cover_receipt")
            continue
        if difference["status"] == "wrong_currency":
            row = obligations.get(key, {})
            declared = amounts[piece, component]
            fact = facts.get(rid, {})
            breakdown_conflict = any(
                field in saved and _money(saved[field]) != _money(declared[field])
                for saved in (row, fact) for field in ("net_amount", "tax_amount"))
            if (breakdown_conflict or row.get("superseded") or row.get("evidence_ids") != [rid]
                    or difference["saved_currency"] not in {"QAR", "AED", "KWD", "BHD", "OMR", "USD"}
                    or difference["saved_gross"] != difference["source_amount_sar"]
                    or row.get("confirmed") != difference["saved_gross"]
                    or _money(row.get("returned_amount", "0")) or _money(row.get("expected", "0"))):
                reasons.append("saved_amount_or_currency_requires_review")
                continue
            legs.append(dict(piece_id=piece, component=component, currency=difference["saved_currency"],
                             before=difference["saved_gross"], after="0.00"))
        elif key in obligations or rid in facts:
            reasons.append("other_document_may_cover_receipt")
            continue
        legs.append(dict(piece_id=piece, component=component, currency="SAR",
                         before="0.00", after=difference["source_amount_sar"]))
    return _result(invoice, reasons, legs, {d["piece_id"]: order_proofs[d["piece_id"]]
                                           for d in differences if d["piece_id"] in order_proofs})


def _num(value):
    return str(value) if type(value) in (int, str) and value not in ("", 0) else ""


def _keys(values):
    # Source IDs may be stored as strings or numbers. Never fold leading zeroes.
    return list(values) + [int(v) for v in values if v.isascii() and v.isdigit()
                           and len(v) <= 18 and str(int(v)) == v]


def _raw(order):
    return (order.get("raw_by_source") or {}).get("salla_direct") or {}


def _order_ref(raw):
    return _num(raw.get("reference_id") or raw.get("order_number"))


def order_proofs(invoice, events, orders):
    """Join a finalized scan to one canonical order, order line and physical unit."""
    named = {p for line in invoice["lines"] for p in line["piece_ids"]}
    id_counts = Counter(_num(_raw(o).get("id")) for o in orders)
    ref_counts = Counter(_order_ref(_raw(o)) for o in orders)
    by_ref = {_order_ref(_raw(o)): o for o in orders}
    event_ids = Counter(e.get("id") for e in events)
    pieces = Counter(e.get("piece_id") for e in events)
    units = Counter((e.get("order_item_id"), e.get("unit_index")) for e in events)
    result = {}
    for event in events:
        p, eid = event.get("piece_id"), event.get("id")
        line, unit = event.get("order_item_id"), event.get("unit_index")
        ref = _num(event.get("order_number"))
        doc = by_ref.get(ref, {})
        raw = _raw(doc)
        oid = _num(raw.get("id"))
        if (p not in named or not eid or pieces[p] != 1 or event_ids[eid] != 1 or units[line, unit] != 1
                or type(unit) is not int or unit < 1 or not ref or not oid or ref_counts[ref] != 1 or id_counts[oid] != 1
                or event.get("user_id") != invoice["user_id"] or doc.get("user_id") != invoice["user_id"]
                or (doc.get("g47_salla_snapshot") or {}).get("requires_authoritative_refresh")
                or not invoice.get("session_id") or event.get("session_id") != invoice["session_id"]
                or (event.get("supplier_context") or {}).get("id") != invoice["supplier_id"]
                or event.get("supplier_invoice_id") != invoice["id"] or event.get("event_type") != "supplier_piece_service_recorded"
                or event.get("experiment_mode") is not False or event.get("experiment_run_id")
                or event.get("financial_invoice_created") is not True):
            continue
        try:
            approved = instant(invoice["approved_at"])
            if instant(event.get("finalized_at")) != approved or instant(
                    raw.get("date") or raw.get("created_at") or raw.get("order_date")) > approved:
                continue
        except (TypeError, ValueError, OverflowError):
            continue
        matches = [i for i in raw.get("items", []) if isinstance(i, dict) and
                   (item_id := _num(i.get("id") or i.get("item_id") or i.get("order_item_id"))) and line == f"salla:{ref}:{item_id}"]
        if len(matches) == 1 and type(matches[0].get("quantity")) is int and unit <= matches[0]["quantity"]:
            result[p] = dict(event_id=eid, order_id=oid, order_number=ref, order_item_id=line, unit_index=unit)
    return result


async def _native_clear(scoped, owner, invoice, as_of):
    """Verify the exact MZ2 journal, including its invoice digest, in the same snapshot.

    Existence checks only withhold a proposal. They never contribute an amount.
    Checking all dates also prevents showing a historical correction after later payment.
    """
    from accounting_ledger_v2 import AccountingLedgerV2Error, GENERAL_LEDGER_COLLECTION
    from accounting_supplier_payments_v2 import INVOICE_CONTRACT, OPERATIONS, invoice_projection
    from supplier_native_invoice_v2 import verify_native_invoice

    gid = invoice.get("mz2_txn_group_id")
    if invoice.get("mz2_financial_contract") != INVOICE_CONTRACT or not gid:
        return False
    sid, iid = invoice["supplier_id"], invoice["id"]
    group_match = {"txn_group_id": {"$ne": gid}, "metadata.supplier_invoice_id": iid}
    for collection, criteria in (
            (OPERATIONS, {"supplier_id": sid}),
            ("mz2_supplier_payment_operations", {"supplier_entity_id": sid}),
            (GENERAL_LEDGER_COLLECTION, {"$or": [group_match,
                {"entity_type": "supplier", "entity_id": sid, "entry_type": {"$ne": "supplier_invoice"}}]})):
        if await scoped.db[collection].find_one({"user_id": owner, **criteria}, {"_id": 1}, session=scoped.session):
            return False
    try:
        session = await scoped.db["mezan_supplier_receiving_sessions_v1"].find_one(
            {"user_id": owner, "id": invoice["session_id"]},
            {"status": 1, "supplier_invoice_id": 1, "supplier_id": 1}, session=scoped.session)
        if not session or instant(invoice["mz2_effective_at"]) > instant(as_of):
            return False
        await verify_native_invoice(scoped.db, invoice=invoice, session=session,
                                    mongo_session=scoped.session)
        payable = await scoped.db[GENERAL_LEDGER_COLLECTION].find(
            {"user_id": owner, "txn_group_id": gid, "entity_type": "supplier", "entity_id": sid,
             "sub_account": "payable"}, {"_id": 0}, session=scoped.session).to_list(2)
        return invoice_projection(invoice, payable, [])["financial_eligible"] is True
    except (AccountingLedgerV2Error, HTTPException, KeyError, TypeError, ValueError, InvalidOperation):
        return False


async def load_correction(scoped, owner, state, invoice, row, as_of):
    """All reads use the same owner and snapshot. Truncation or incomplete identity blocks the preview."""
    iid, sid = invoice["id"], invoice["supplier_id"]
    if (scoped.session is None or scoped.owner != owner or state.get("owner_id") != owner or
            invoice.get("user_id") != owner or row.get("invoice_id") != iid):
        raise ValueError("supplier_review_correction_source_scope_invalid")
    if row.get("source_issue") or row.get("extra_saved_count"):
        return _result(invoice, ["invoice_evidence_incomplete"])
    if not row["differences"]:
        return _result(invoice, ["no_proposed_change"])

    async def rows(name, criteria, fields):
        query = {"user_id": owner, **criteria}
        records = await scoped.db[name].find(query, {"_id": 0, "user_id": 1, **dict.fromkeys(fields.split(), 1)},
                                              session=scoped.session).to_list(5001)
        if len(records) > 5000 or any(r.get("user_id") != owner for r in records):
            raise ValueError("supplier_review_correction_source_scope_invalid")
        return records

    pieces = sorted({p for line in invoice["lines"] for p in line["piece_ids"]})
    events = await rows("mezan_supplier_receiving_events_v1", {"supplier_invoice_id": iid},
        "id piece_id order_number order_item_id unit_index session_id supplier_context.id supplier_invoice_id "
        "event_type experiment_mode experiment_run_id finalized_at financial_invoice_created")
    numbers = _keys({_num(e.get("order_number")) for e in events} - {""})
    raw = "raw_by_source.salla_direct."
    criteria = [{raw + k: {"$in": numbers}} for k in ("reference_id", "order_number")]
    fields = "g47_salla_snapshot.requires_authoritative_refresh " + " ".join(raw + k for k in (
        "id", "reference_id", "order_number", "created_at", "date", "order_date",
        "items.id", "items.item_id", "items.order_item_id", "items.quantity"))
    orders = await rows("unified_orders", {"$or": criteria}, fields) if numbers else []
    ids = _keys({_num(_raw(o).get("id")) for o in orders} - {""})
    if ids:
        orders = await rows("unified_orders", {"$or": criteria + [{raw + "id": {"$in": ids}}]}, fields)
    proofs = order_proofs(invoice, events, orders)
    # Also find a second receiving invoice under a different piece ID for the same physical unit.
    other_units = await rows("mezan_supplier_receiving_events_v1", {
        "order_item_id": {"$in": list({p["order_item_id"] for p in proofs.values()})},
        "event_type": "supplier_piece_service_recorded", "supplier_invoice_id": {"$ne": iid},
        "experiment_mode": {"$ne": True}}, "order_item_id unit_index") if proofs else []
    units = {(p["order_item_id"], p["unit_index"]) for p in proofs.values()}
    cross_claim = any((e.get("order_item_id"), e.get("unit_index")) in units for e in other_units)
    # Whole invoice is withheld when another approved document may include its piece (including before cutover).
    for collection, criteria in (
            ("mezan_supplier_invoices_v2", {"id": {"$ne": iid}, "lines.piece_ids": {"$in": pieces},
                "approved_at": {"$ne": None}, "experiment_mode": {"$ne": True}}),
            ("purchase_invoices", {"state": "approved", "invoice_number": invoice.get("invoice_number"),
                "$or": [{k: sid} for k in ("supplier_id", "supplier_counterparty_id", "supplier_entity_id", "supplier_account_id")]})):
        if await scoped.db[collection].find_one({"user_id": owner, **criteria}, {"_id": 1}, session=scoped.session):
            cross_claim = True
    clear = await _native_clear(scoped, owner, invoice, as_of) if not cross_claim else False
    try:
        return component_proposal(state, invoice, row, native_clear=clear, order_proofs=proofs, cross_claim=cross_claim)
    except (ValueError, KeyError, TypeError, AttributeError, InvalidOperation):
        return _result(invoice, ["invoice_evidence_incomplete"])
