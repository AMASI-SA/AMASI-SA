"""Read-only carrier totals from the same saved projection as its ledger.

These are cumulative snapshot amounts, independent of a ledger page/search.
Recorded COD responsibility is not evidence that the carrier collected cash.
Account-level settlements cannot be attributed to particular COD/fee claims.
"""
from decimal import Decimal

from operational_balance_completeness import GLOBAL_FAILURES, apply_table_coverage
from operational_financial_activity_runtime import checked_rows


ZERO = Decimal("0")


def courier_summary(state, computed, resource):
    access = resource.get("access", {})
    if (resource.get("kind") != "courier" or not access.get("balance")
            or access.get("movements") != "all"):
        return None
    identity = ("courier", resource["id"], resource["currency"])

    def belongs(row):
        return (row.get("party_type"), row.get("party_id"), row.get("currency")) == identity

    totals = {key: ZERO for key in (
        "shipping_confirmed", "return_confirmed", "fees_confirmed", "fees_paid", "fees_outstanding",
        "cod_confirmed", "cod_received", "cod_outstanding", "expected_cod", "expected_shipping",
        "expected_return", "opening_receivable", "opening_payable", "other_receivable", "other_payable",
        "cash_received", "cash_paid", "unallocated_received", "unallocated_paid",
        "cod_excess_received", "fees_overpaid",
    )}
    reasons = set()
    uncertain_expected = set()
    claim_ids = {row["id"] for row in checked_rows(computed.get("obligations", ()))}
    for row in checked_rows(computed.get("obligations", ())):
        if not belongs(row) or row.get("superseded"):
            continue
        confirmed = Decimal(row.get("confirmed", "0"))
        expected = Decimal(row.get("expected", "0"))
        settled = Decimal(row.get("settled", "0"))
        outstanding = Decimal(row.get("outstanding", "0"))
        kind, direction = row.get("kind"), row.get("direction")
        if kind == "shipping" and direction == "payable" and not row.get("derived_credit"):
            is_return = bool(row.get("return_case_id") or row.get("salla_return_shipment_id"))
            totals["return_confirmed" if is_return else "shipping_confirmed"] += confirmed
            expected_key = "expected_return" if is_return else "expected_shipping"
            if confirmed == ZERO:
                totals[expected_key] += expected
            elif expected > ZERO:
                uncertain_expected.add(expected_key)
                reasons.add("carrier_fee_forecast_overlap")
            totals["fees_confirmed"] += confirmed
            totals["fees_paid"] += min(settled, confirmed)
            totals["fees_overpaid"] += max(settled - confirmed, ZERO)
            if "credit:" + row["id"] not in claim_ids:
                totals["other_receivable"] += max(settled - confirmed, ZERO)
            totals["fees_outstanding"] += outstanding
        elif kind == "cod" and direction == "receivable" and not row.get("derived_credit"):
            totals["cod_confirmed"] += confirmed
            totals["cod_received"] += min(settled, confirmed)
            totals["cod_excess_received"] += max(settled - confirmed, ZERO)
            if "credit:" + row["id"] not in claim_ids:
                totals["other_payable"] += max(settled - confirmed, ZERO)
            totals["cod_outstanding"] += outstanding
            if confirmed == ZERO:
                totals["expected_cod"] += expected
        elif kind == "baseline" and direction in {"receivable", "payable"}:
            totals["opening_" + direction] += confirmed
        elif direction in {"receivable", "payable"}:
            totals["other_" + direction] += outstanding

    ambiguous = set()
    for movement in checked_rows(state.get("movements", ())):
        if not belongs(movement):
            continue
        direction = movement.get("direction")
        if movement.get("kind") == "correction":
            ambiguous.update(("receivable", "payable"))
            reasons.add("carrier_account_adjustment_unallocated")
            continue
        if movement.get("bank_kind") not in {"bank", "cash"} or not movement.get("bank_id"):
            continue
        value = Decimal(movement["amount"])
        incoming = direction == "incoming"
        totals["cash_received" if incoming else "cash_paid"] += value
        if movement.get("settlement_scope") == "account":
            ambiguous.add("receivable" if incoming else "payable")
            reasons.add("carrier_account_settlement_unallocated")
        else:
            allocated = sum((Decimal(a["amount"]) for a in movement.get("allocations", ())), ZERO)
            totals["unallocated_received" if incoming else "unallocated_paid"] += max(value - allocated, ZERO)

    parties = [r for r in checked_rows(computed.get("parties", ())) if belongs(r)]
    if len(parties) == 1:
        party = parties[0]
        totals["receivable"] = Decimal(party["outstanding_receivable"])
        totals["payable"] = Decimal(party["outstanding_payable"])
        totals["net_position"] = totals["receivable"] - totals["payable"]
    elif not parties and not any(totals.values()):
        totals.update(receivable=ZERO, payable=ZERO, net_position=ZERO)
    else:
        reasons.add("carrier_balance_unavailable")

    # Reuse the financial-position evidence attribution. In particular, an
    # unpriced carrier with pending proof is unknown, not a new zero balance.
    engine = state.get("engine", {})
    issues = engine.get("issues", computed.get("issues", ()))
    row_id = ":".join(identity)
    table = {"id": "couriers", "rows": [{"id": row_id, "name": resource.get("name"),
        "receivable": totals.get("receivable"), "payable": totals.get("payable")}],
        "columns": [], "totals": {}, "notes": []}
    apply_table_coverage([table], computed.get("parties", ()),
        [r for r in checked_rows(engine.get("obligations", {}).values()) if r.get("currency") == identity[2]],
        issues, engine.get("facts", {}), currency=identity[2], as_of=engine.get("as_of"),
        delivery_register=engine.get("delivery_register"))
    covered = next(row for row in table["rows"] if row["id"] == row_id)
    unknown_setup = any(issue.get("code") in GLOBAL_FAILURES | {"shipping_setup_ambiguous"}
        and issue.get("currency") in {None, identity[2]} for issue in checked_rows(issues))
    if not covered["complete"] or unknown_setup:
        reasons.add("carrier_source_incomplete")

    result = {key: format(value, ".2f") for key, value in totals.items()}
    result.update(dict.fromkeys(uncertain_expected))
    if "receivable" in ambiguous:
        result.update(cod_received=None, cod_outstanding=None)
    if "payable" in ambiguous:
        result.update(fees_paid=None, fees_outstanding=None)
    if "carrier_balance_unavailable" in reasons:
        result.update(receivable=None, payable=None, net_position=None)
    if not parties and "carrier_source_incomplete" in reasons:
        result = dict.fromkeys(result)
    if not state.get("engine", {}).get("as_of") or state.get("status") != "active":
        reasons.add("carrier_snapshot_unavailable")
        result = dict.fromkeys(result)
    return {
        **result, "currency": resource["currency"], "revision": state.get("revision"),
        "as_of": state.get("engine", {}).get("as_of"), "started_at": state.get("started_at"),
        "scope": "saved_snapshot_to_date", "complete": not reasons, "reason_codes": sorted(reasons),
        "source_coverage": "saved_snapshot_only",
        "review_order_count": covered["pending_orders"],
        "unassigned_source_review": bool(table["unassigned_order_details"]) or unknown_setup,
        "expected_coverage": "saved_assigned_obligations_only",
        "customer_collection_confirmed": None,
        "customer_collection_status": "carrier_collection_not_verified",
    }
