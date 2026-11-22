"""Read-only customer adjustment disclosures alongside provider balances.

The report's party projection owns confirmed gross money. These helpers expose
pending commercial differences and price only the unmatched receipt's fee.
"""
from collections import defaultdict
from decimal import Decimal

from operational_configured_provider_fees import number
from operational_customer_collection_effects import (
    collection_reference_match, provider_collection_effects, provider_refund_recognized,
    source_uncovered_collected_amount,
)
from operational_provider_balance_sar import sar_amount


ZERO = Decimal(0)


def collection_records(state):
    scopes = {(case["destination_id"], case["currency"])
              for case in state.get("customer_collections", [])
              if case.get("status") == "confirmed" and case.get("destination_type") == "provider"}
    return [row for provider, currency in sorted(scopes)
            for row in provider_collection_effects(state, provider, currency)]


def pending_adjustments(state, records, aliases):
    """Amounts awaiting employee confirmation, with no financial effect."""
    from operational_customer_adjustments import refund_totals

    results = defaultdict(lambda: {"refund_ids": set(), "collection_ids": set(),
                                    "refund_amount": ZERO, "collection_amount": ZERO})
    refund_facts, returns, collections = defaultdict(dict), defaultdict(list), defaultdict(list)
    for key, fact in state.get("engine", {}).get("facts", {}).items():
        if key.startswith("refund:") and isinstance(fact, dict):
            refund_facts[str(fact.get("order_id"))][key] = fact
    for case in state.get("customer_returns", []):
        returns[str(case.get("order_id"))].append(case)
    for case in state.get("customer_collections", []):
        collections[str(case.get("order_id"))].append(case)
    for identity, row in records.items():
        adjustment = (row.get("payment") or {}).get("customer_adjustment")
        if identity in aliases or not row.get("paid_proven") or (not adjustment and not returns[row["order_id"]]):
            continue
        gross = row["native_gross"]
        hint = number((adjustment or {}).get("refund_hint_amount"))
        total = number((adjustment or {}).get("order_total"))
        customer_state = {**state, "customer_collections": collections[row["order_id"]]}
        received = source_uncovered_collected_amount(customer_state, {"id": row["order_id"], "provider": row["provider"],
            "paid_amount": str(gross), "_payment_snapshot": row.get("payment") or {}})
        amounts = refund_totals({"customer_returns": returns[row["order_id"]],
            "engine": {"facts": refund_facts[row["order_id"]]}}, row["order_id"])
        refund = (max(max(gross + received - total, hint, ZERO) - amounts["executed"], ZERO)
                  if total is not None and hint is not None else ZERO)
        refund = max(refund, amounts["unconfirmed"], amounts["reserved"])
        # Executing a refund cannot create a new customer debt. Only a change
        # in the commercial amount establishes a collection suggestion.
        collection = max(total - gross - received, ZERO) if total is not None else ZERO
        if row["currency"] != row["native_currency"]:
            if row.get("fee_fx_basis") is None:
                continue
            refund, collection = (sar_amount(value, row["fee_fx_basis"]) for value in (refund, collection))
        target = results[row["currency"], row["provider"]]
        for name, value in (("refund", refund), ("collection", collection)):
            if value > ZERO and row["order_id"] not in target[name + "_ids"]:
                target[name + "_ids"].add(row["order_id"])
                target[name + "_amount"] += value
    return results


def apply_manual_totals(state, collections, *, group, pending):
    """Add disclosure/fees once; confirmed party principal is already applied."""
    from operational_customer_adjustments import provider_refund_match

    for case in collections:
        if case["matched_evidence_id"]:
            continue
        currency, provider, oid = case["currency"], case["destination_id"], str(case["order_id"])
        amount = case["principal_delta"]
        total = group(currency, provider)
        total["orders"].add(oid)
        total["paid_orders"].add(oid)
        total["confirmed_orders"].add(oid)
        total["gross"] += amount
        total["known_gross"] += amount
        total["manual_collected_amount"] += amount
        total["_observed_pending_basis"] = total.get("_observed_pending_basis", ZERO) + amount
        total["_gross_available"] = True
        if case.get("order_number"):
            total["_order_numbers"][oid] = str(case["order_number"])
        if case.get("review_code"):
            total["_gross_complete"] = False
            pending(total, oid, case["review_code"], case.get("order_number"))
        fee = number(case.get("fee_amount"))
        if fee is None:
            unknown_principal = Decimal(case["amount"])
            total["fees_complete"] = total["complete"] = False
            total["_excluded_net"] += unknown_principal
            total["uncomputed_principal"] = total.get("uncomputed_principal", ZERO) + unknown_principal
            total.setdefault("_uncomputed_ids", set()).add(oid)
            total["net_confirmed_orders"].discard(oid)
            total["_estimated_incomplete"]["customer_collection_fee_unavailable"].add(oid)
            pending(total, oid, "customer_collection_fee_unavailable", case.get("order_number"))
            continue
        total["fees"] += fee
        total["known_fees"] += fee
        total["remaining_fees"] += fee
        total["customer_collection_fees"] += fee
        total["_fees_available"] = total["_net_available"] = True
        total["_fee_bases"].add("plan_estimate")
        total["estimated_fees"] += fee
        total["_estimated_calculated_orders"].add(oid)
        total["plan_estimated_orders"].add(oid)
    for case in state.get("customer_returns", []):
        if (case.get("status") != "refunded" or case.get("refund_source_type") != "provider"
                or case.get("financial_effect") != "confirmed_provider_refund_debit"):
            continue
        if provider_refund_match(state, case) and provider_refund_recognized(state, case):
            continue
        total = group(case["currency"], case["refund_source_id"])
        amount = Decimal(case["amount"])
        total["refunded"] += amount
        total["manual_refunded_amount"] += amount
        total["_net_available"] = True


def original_collection_fee_context(row, matched, context):
    """Price the original portion until the new transaction's fee is matched."""
    keys = {case["matched_evidence_id"] for case in matched}
    amount = sum((Decimal(case["amount"]) for case in matched), ZERO)
    principal = row["gross"] - amount
    if principal < ZERO or row["currency"] != row["native_currency"]:
        raise ValueError("customer_collection_evidence_conflict")
    payment = {**(row.get("payment") or {})}
    payment["captures"] = [capture for capture in payment.get("captures", [])
                           if "capture:" + str(capture["id"]) not in keys]
    payment["gross"] = str(principal)
    pair = row["order_id"], row["provider"]
    base_context = {**context, "events": {**context["events"], pair: [
        event for event in context["events"].get(pair, [])
        if (event.get("covered_event") or {}).get("id") not in keys]}}
    return {**row, "gross": principal, "native_gross": principal, "payment": payment}, base_context, amount


def preserve_unrecognized_bridges(state, records, claims, collections):
    """Retain the original priced portion while a late fact awaits recognition.

    A fact can arrive before its aggregate claim is refreshed. It must not
    count once in source gross and again as an accountant's receipt, nor make
    the unchanged original payment's fee/net disappear.
    """
    from operational_customer_adjustments import provider_refund_match

    excluded = defaultdict(set)
    by_pair, unapplied_refunds = defaultdict(list), defaultdict(Decimal)
    for identity, row in records.items():
        by_pair[row["order_id"], row["provider"]].append((identity, row))
    for case in state.get("customer_returns", []):
        if (case.get("status") == "refunded" and case.get("refund_source_type") == "provider"
                and case.get("financial_effect") == "confirmed_provider_refund_debit"
                and provider_refund_match(state, case) and not provider_refund_recognized(state, case)):
            unapplied_refunds[str(case["order_id"]), case["refund_source_id"]] += Decimal(case["amount"])
    for case in collections:
        if case["matched_evidence_id"]:
            continue
        pair = str(case["order_id"]), case["destination_id"]
        related = [(key, row) for key, row in by_pair[pair] if row["currency"] == case["currency"]]
        if len(related) != 1:
            continue
        identity, row = related[0]
        if row["currency"] != row["native_currency"]:
            continue
        claim = claims.get(identity) or {}
        original = claim.get("native_balance") or claim
        known = Decimal(original.get("confirmed", "0")) + max(row["native_refunded"] - unapplied_refunds[pair], ZERO)
        reference = collection_reference_match(state, case)
        # Only an exact receipt or a larger cumulative observation can overlap
        # the manual record. Unrelated source money remains independently held.
        source = (row.get("payment") or {}).get("order_page_balance") or {}
        same_page = source.get("provider") == case["destination_id"]
        if reference or same_page:
            extra = min(max(row["native_gross"] - known, ZERO), Decimal(case["amount"]))
            if extra:
                row["native_gross"] -= extra
                row["gross"] -= extra
            if reference:
                excluded[pair].add(reference)
        overlap = case.get("source_overlap_amount", ZERO)
        if overlap:
            row["customer_collection_pricing_overlap"] = row.get("customer_collection_pricing_overlap", ZERO) + overlap
        if reference:
            payment = {**(row.get("payment") or {})}
            payment["captures"] = [capture for capture in payment.get("captures", [])
                                   if "capture:" + str(capture["id"]) != reference]
            row["payment"] = payment
    for case in state.get("customer_returns", []):
        if (case.get("status") != "refunded" or case.get("refund_source_type") != "provider"
                or case.get("financial_effect") != "confirmed_provider_refund_debit"):
            continue
        reference = provider_refund_match(state, case)
        if not reference or provider_refund_recognized(state, case):
            continue
        pair = str(case["order_id"]), case["refund_source_id"]
        for identity, row in by_pair[pair]:
            if row["currency"] != row["native_currency"]:
                continue
            amount = Decimal(case["amount"])
            row["refunded"] = max(row["refunded"] - amount, ZERO)
            row["native_refunded"] = max(row["native_refunded"] - amount, ZERO)
            claim = claims.get(identity) or {}
            original = claim.get("native_balance") or claim
            # _records normally reconstructs original gross as net claim plus
            # executed refunds. Until this fact reduces that claim, adding it
            # back would manufacture gross principal that was never collected.
            gross = max(row.get("source_gross", ZERO),
                        Decimal(original.get("confirmed", "0")) + row["native_refunded"])
            row["gross"] = row["native_gross"] = gross
            row["refund_events"] = [event for event in row["refund_events"]
                                    if "refund:" + row["provider"] + ":" + event["id"] != reference]
            excluded[pair].add(reference)
    return excluded
