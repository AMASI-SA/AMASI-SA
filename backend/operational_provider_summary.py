"""One provider balance view, with per-order fees and unchanged allocation identities."""
from collections import defaultdict
from decimal import Decimal

from operational_configured_provider_fees import cash, estimate_fee, number
from operational_provider_balance_sar import sar_amount
from operational_provider_report_coverage import PLATFORMS

ZERO = Decimal(0)


def _money(value):
    return None if value is None else format(cash(value), ".2f")


def _settlements(state, valuations):
    allocated = defaultdict(lambda: {"gross": ZERO, "fees": ZERO})
    scope_fees = defaultdict(Decimal)
    scope_cash = defaultdict(Decimal)
    native = defaultdict(lambda: {"gross": ZERO, "cash": ZERO, "returned": ZERO})
    for movement in state.get("movements", []):
        if movement.get("party_type") != "provider" or movement.get("kind") == "correction":
            continue
        incoming = movement.get("direction") == "incoming"
        received = Decimal(movement["amount"])
        total = received + Decimal(movement.get("actual_fee_amount", "0"))
        if total <= 0:
            continue
        cumulative, previous = ZERO, ZERO
        assigned_fees = ZERO
        for line in movement.get("allocations", []):
            identity = line["obligation_id"]
            gross = Decimal(line["amount"])
            cumulative += gross
            boundary = cash(received * cumulative / total)
            net = boundary - previous
            fee = gross - net
            previous = boundary
            assigned_fees += fee
            row = native[identity, movement["currency"], movement["party_id"]]
            if incoming:
                row["gross"] += gross
                row["cash"] += net
            else:
                row["returned"] += net
        remainder = Decimal(movement.get("actual_fee_amount", "0")) - assigned_fees
        scope = movement["currency"], movement["party_id"]
        if incoming:
            scope_fees[scope] += remainder
        scope_cash[scope] += (received - previous) * (1 if incoming else -1)
    for (identity, currency, provider), values in native.items():
        basis = valuations.get(identity.removeprefix("credit:"))
        if basis:
            values = {key: sar_amount(value, basis) for key, value in values.items()}
            currency = "SAR"
        fee = values["gross"] - values["cash"]
        allocated[identity].update(gross=values["gross"], fees=fee)
        scope_fees[currency, provider] += fee
        scope_cash[currency, provider] += values["cash"] - values["returned"]
    return allocated, scope_fees, scope_cash


def _records(state, obligations, valuations):
    engine = state.get("engine", {})
    inputs = engine.get("provider_fee_inputs", {}).get("orders", {})
    records = {}
    refunds = defaultdict(Decimal)
    captured = defaultdict(Decimal)
    pages = {}
    page_rows = {}
    for key, fact in engine.get("facts", {}).items():
        oid = str(fact.get("order_id") or (fact.get("evidence") or {}).get("order_id") or "")
        if key.startswith("refund:") and oid:
            refunds[oid, key.split(":", 2)[1]] += Decimal(fact["amount"])
        elif key.startswith("capture:") and oid and fact.get("provider"):
            captured[oid, fact["provider"]] += Decimal(fact["amount"])
        elif key == "provider_order_balance:" + oid:
            pages[oid, fact["provider"]] = Decimal(fact["paid"])
            page_rows[oid, fact["provider"]] = fact
        elif key == "provider_split:" + oid:
            split = fact.get("evidence", {})
            for leg in split.get("tenders", []):
                if leg.get("provider") in fact.get("booked_providers", []):
                    captured[oid, leg["provider"]] += Decimal(leg["amount"])
    for pair, paid in pages.items():
        captured[pair] = max(captured[pair], paid)
    claims = {row["id"]: row for row in obligations
              if row.get("kind") == "provider" and not row.get("derived_credit")}
    for oid, source in inputs.items():
        payment = source.get("payment") or {}
        fx = source.get("customer_payment_sar") or {}
        native = {key: Decimal(value) for key, value in fx.get("native", {}).items() if key in PLATFORMS}
        split = payment.get("split")
        methods = {}
        if split:
            legs = defaultdict(list)
            for leg in split.get("tenders", []):
                if leg.get("provider") in PLATFORMS:
                    legs[leg["provider"]].append(leg)
            for provider, values in legs.items():
                native.setdefault(provider, sum((Decimal(v["amount"]) for v in values), ZERO))
                methods[provider] = values[0].get("method") if len(values) == 1 else None
        elif payment.get("provider") in PLATFORMS:
            provider = payment["provider"]
            page = payment.get("order_page_balance") or {}
            paid = number(page.get("paid"))
            if paid is None and payment.get("captures"):
                paid = sum((Decimal(c["amount"]) for c in payment["captures"]), ZERO)
            if paid is not None:
                native.setdefault(provider, paid)
            methods[provider] = payment.get("payment_method")
        for provider, paid in native.items():
            identity = "provider:" + oid + (":" + provider if split else "")
            records[identity] = {"order_id": oid, "provider": provider, "currency": source["currency"],
                                 "gross": max(paid, captured[oid, provider]), "method": methods.get(provider),
                                 "fx": fx, "multiple_legs": bool(split and len(legs[provider]) != 1)}
    for identity, claim in claims.items():
        provider = claim["party_id"]
        oid = identity.removeprefix("provider:").removesuffix(":" + provider)
        original = claim.get("native_balance") or claim
        gross = max(captured[oid, provider], Decimal(original["confirmed"]) + refunds[oid, provider])
        if identity not in records:
            records[identity] = {"order_id": oid, "provider": provider, "currency": original["currency"],
                                 "gross": gross, "method": None, "fx": {}}
        else:
            records[identity]["gross"] = max(records[identity]["gross"], gross)
    for (oid, provider), page in page_rows.items():
        records.setdefault("provider:" + oid, {"order_id": oid, "provider": provider,
            "currency": page["currency"], "gross": captured[oid, provider], "method": None, "fx": {}})
    for issue in engine.get("issues", []):
        page = issue.get("order_page_balance_review")
        if not isinstance(page, dict) or page.get("provider") not in PLATFORMS:
            continue
        identity = "provider:" + str(page["order_id"])
        record = records.setdefault(identity, {"order_id": str(page["order_id"]), "provider": page["provider"],
            "currency": page["currency"], "gross": Decimal(page["paid"]), "method": None, "fx": {}})
        if (record["provider"], record["currency"]) == (page["provider"], page["currency"]):
            record["gross"] = max(record["gross"], Decimal(page["paid"]))
    for identity, row in records.items():
        original_currency, native_paid = row["currency"], row["gross"]
        row["refunded"] = refunds[row["order_id"], row["provider"]]
        row["native_gross"] = native_paid
        row["native_refunded"] = row["refunded"]
        basis = valuations.get(identity)
        fx = row["fx"]
        if not basis and identity not in claims and fx.get("currency") == original_currency:
            paid = number(fx.get("native", {}).get(row["provider"]))
            sar = number(fx.get("shares", {}).get(row["provider"]))
            if paid == native_paid and paid and sar and fx.get("basis") in {"salla_order_fx", "salla_list_paid"}:
                basis = {"native_paid": str(paid), "sar_paid": str(sar)}
        if original_currency != "SAR" and basis:
            row.update(currency="SAR", gross=sar_amount(native_paid, basis), refunded=sar_amount(row["refunded"], basis))
    return records, claims


def provider_balances(state, details, obligations, parties, valuations, *, sar_view=False):
    """Project fees once on the unpaid gross claim. Never change saved financial state."""
    records, claims = _records(state, obligations, valuations)
    allocated, actual_fees, settled_cash = _settlements(state, valuations)
    settings = state.get("engine", {}).get("provider_fee_inputs", {}).get("settings", {})
    groups = {}
    resolved = set()

    def group(currency, provider):
        return groups.setdefault((currency, provider), {"provider": provider, "name": PLATFORMS.get(provider, provider),
            "orders": set(), "gross": ZERO, "fees": ZERO, "refunded": ZERO, "remaining_fees": ZERO,
            "complete": True, "fees_complete": True, "held_orders": 0, "opening": ZERO, "settled": ZERO,
            "known_net": ZERO})

    for identity, row in records.items():
        currency, provider = row["currency"], row["provider"]
        total = group(currency, provider)
        if row["gross"] > 0:
            total["orders"].add(row["order_id"])
        total["gross"] += row["gross"]
        total["refunded"] += row["refunded"]
        claim = claims.get(identity)
        original = (claim.get("native_balance") or claim) if claim else {}
        held = (not claim or claim["currency"] != currency or claim.get("incomplete")
                or row["native_gross"] > Decimal(original.get("confirmed", "0")) + row["native_refunded"])
        if held and row["gross"]:
            total["complete"] = False
            total["held_orders"] += 1
        projection = details.get("provider_reports", {}).get(identity)
        fee = None
        if projection and not held:
            fee = number(projection.get("estimated_fees_original", projection.get("estimated_fees")))
            # Only paid captures belong in the current balance. Future fees stay
            # inside the future net collection, rather than being charged here.
            projected_gross = number(projection.get("gross"))
            if projected_gross and row["gross"] < projected_gross:
                fee = None
        if fee is None and currency == "SAR" and not row.get("multiple_legs"):
            fee = estimate_fee(settings, provider, row["gross"], method=row["method"], refunded=row["refunded"])
        retained_uncaptured = (claim and claim.get("direction") == "payable"
                               and Decimal(claim.get("expected", "0")) > 0 and row["gross"] == 0)
        if row["gross"] == 0 and not retained_uncaptured:
            fee = ZERO
        if retained_uncaptured:
            fee = None
        applied = allocated[identity]["gross"]
        fully_settled = not held and row["gross"] > 0 and applied >= row["gross"]
        if row["refunded"] and applied > 0 and not fully_settled:
            # A refund overlapping a partial payout needs an actual fee-credit
            # allocation. The reduced principal cannot establish that split.
            fee = None
        if fee is None and fully_settled:
            # The allocation proves its full cash/fee split. A missing estimate
            # cannot turn an entirely settled actual fee back into uncertainty.
            fee = ZERO
        if fee is None:
            total["complete"] = total["fees_complete"] = False
            continue
        if not held:
            resolved.add(identity)
        covered = ZERO
        if not held and row["gross"] > 0 and applied > 0:
            covered = cash(fee * min(applied, row["gross"]) / row["gross"])
        remaining = max(fee - covered, ZERO)
        total["fees"] += remaining
        if not held:
            total["remaining_fees"] += remaining
    if sar_view:
        coverage = details.get("customer_tender_coverage", {}).get("sar", {})
        for source in coverage.get("by_method", []):
            provider = source.get("method")
            if provider not in PLATFORMS:
                continue
            total = group("SAR", provider)
            paid = number(source.get("paid"))
            known = number(source.get("known_paid", source.get("paid")))
            if known is not None and known > total["gross"]:
                total["gross"] = known
                total["missing_order_count"] = max(source.get("orders", 0) - len(total["orders"]), 0)
                total["complete"] = total["fees_complete"] = False
            if paid is None:
                total["known_gross"] = total["gross"]
                total["gross"] = None
                total["complete"] = total["fees_complete"] = False
    for party in parties.values():
        if party["party_type"] != "provider":
            continue
        key = party["currency"], party["party_id"]
        total = group(*key)
        total["opening"] += party["opening"]
        total["settled"] = settled_cash[key]
        # Allocated settlement fees already reduced the gross debt. Only the
        # estimated fee on its remaining share is still owed to the platform.
        deduction = total["remaining_fees"]
        taken = min(party["outstanding_receivable"], deduction)
        party["outstanding_receivable"] -= taken
        party["outstanding_payable"] += deduction - taken
        party["provider_fees_remaining"] = _money(deduction)
        party["provider_net_complete"] = total["complete"]
        total["known_net"] += party["outstanding_receivable"] - party["outstanding_payable"]
    result = defaultdict(list)
    for (currency, provider), total in sorted(groups.items()):
        total["fees"] += actual_fees[currency, provider]
        total["orders"] = len(total["orders"]) + total.pop("missing_order_count", 0)
        total["net"] = total["known_net"] if total["complete"] else None
        if not total["fees_complete"]:
            total["fees"] = None
        result[currency].append({k: _money(v) if isinstance(v, Decimal) else v for k, v in total.items()
                                 if k != "remaining_fees"})
    return dict(result), resolved
