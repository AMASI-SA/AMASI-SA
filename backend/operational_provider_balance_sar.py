"""Historical order valuation for the read-only provider balance report.

Operational allocation identities and bank movements remain in their recorded currencies.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CENT = Decimal(".01")
PROVIDERS = {"salla", "tamara", "tabby", "emkan"}
SOURCES = {"salla_order_fx", "salla_list_paid"}


def positive(value):
    try:
        n = Decimal(str(value))
        return n if n.is_finite() and n > 0 and n == n.quantize(CENT) else None
    except (ValueError, InvalidOperation):
        return None


def captured_originals(engine):
    """Amounts corroborated by the original native claims, keyed by allocation identity."""
    captures = defaultdict(Decimal)
    pages = {}
    for key, fact in engine.get("facts", {}).items():
        if key.startswith("capture:") and fact.get("order_id") and fact.get("provider"):
            captures["provider:" + str(fact["order_id"]), fact["provider"]] += Decimal(fact["amount"])
        elif key.startswith("provider_order_balance:") and fact.get("provider") and fact.get("paid") is not None:
            pages["provider:" + str(fact["order_id"]), fact["provider"]] = Decimal(fact["paid"])
        elif key.startswith("provider_split:"):
            split = fact["evidence"]
            for leg in split["tenders"]:
                party = leg["provider"]
                if party in fact["booked_providers"]:
                    captures["provider:" + str(split["order_id"]) + ":" + party, party] += Decimal(leg["amount"])
    for key, paid in pages.items():
        captures[key] = max(captures[key], paid)
    return captures


def refresh_basis(state, orders, issues):
    foreign = [o for o in orders if o.get("customer_payment_sar") and o.get("payment") and o.get("currency") != "SAR"]
    if not foreign:
        return
    engine = state["engine"]
    claims, facts = engine["obligations"], engine["facts"]
    saved = engine.get("provider_sar_basis", {})
    captures = captured_originals(engine)
    allocated = {a["obligation_id"].removeprefix("credit:") for m in state.get("movements", [])
                 for a in m.get("allocations", [])}
    for order in foreign:
        fx = order.get("customer_payment_sar") or {}
        currency, source = fx.get("currency"), fx.get("basis")
        if currency == "SAR" or currency != order.get("currency") or source not in SOURCES:
            continue
        oid = str(order["id"])
        split = facts.get("provider_split:" + oid)
        for provider, native in fx.get("native", {}).items():
            if provider not in PROVIDERS:
                continue
            key = "provider:" + oid + (":" + provider if split else "")
            claim = claims.get(key, {})
            if (claim.get("currency"), claim.get("party_id"), claim.get("kind")) != (currency, provider, "provider"):
                continue
            paid, sar = positive(native), positive((fx.get("shares") or {}).get(provider))
            if paid is None or sar is None:
                continue
            if captures.get((key, provider)) != paid:
                continue
            current = {"provider": provider, "currency": currency, "native_paid": format(paid, ".2f"),
                       "sar_paid": format(sar, ".2f"), "basis": source}
            old = saved.get(key)
            if old and old.get("conflict"):
                if current == {k: v for k, v in old.items() if k != "conflict"}:
                    saved[key] = current
                    continue
                issues.append({"code": "provider_sar_basis_conflict", "source_id": key, "currency": "SAR"})
                continue
            if old and old != current:
                if old["basis"] == "salla_list_paid" and source == "salla_order_fx" and old["native_paid"] == current["native_paid"]:
                    continue
                if (key in allocated or (old["native_paid"] == current["native_paid"] and old["basis"] == source
                                         and old["sar_paid"] != current["sar_paid"])):
                    saved[key] = {**old, "conflict": True}
                    issues.append({"code": "provider_sar_basis_conflict", "source_id": key, "currency": "SAR"})
                    continue
            saved[key] = current
    if saved:
        engine["provider_sar_basis"] = saved


def usable_bases(state):
    engine = state.get("engine", {})
    bases = engine.get("provider_sar_basis", {})
    if not bases:
        return {}
    captured = captured_originals(engine)
    # A native correction or an unallocated receipt can offset multiple claims.
    # Keep that currency together until a verifiable per-order attribution exists.
    held_scopes = set()
    for m in state.get("movements", []):
        if m["party_type"] != "provider" or m["currency"] == "SAR":
            continue
        allocated = sum((Decimal(a["amount"]) for a in m.get("allocations", [])), Decimal(0))
        if m["kind"] == "correction" or allocated != Decimal(m["amount"]) + Decimal(m.get("actual_fee_amount", "0")):
            held_scopes.add((m["party_id"], m["currency"]))
    conflicted = {i["source_id"] for i in engine.get("issues", []) if i.get("code") == "provider_sar_basis_conflict"}
    return {key: b for key, b in bases.items() if not b.get("conflict") and key not in conflicted
            and (b["provider"], b["currency"]) not in held_scopes
            and positive(b["native_paid"]) and captured.get((key, b["provider"])) == Decimal(b["native_paid"])
            and positive(b["sar_paid"])}


def sar_amount(value, basis):
    return (Decimal(value) * Decimal(basis["sar_paid"]) / Decimal(basis["native_paid"])).quantize(CENT, rounding=ROUND_HALF_UP)


def convert_claim(row, used, basis, original_confirmed=None, original_settled=None):
    """Convert the paid and applied boundaries. This conserves the final halala on replay."""
    native = {k: row.get(k, "0.00") for k in ("confirmed", "expected")}
    native.update(currency=row["currency"], settled=format(used, ".2f"),
                  outstanding=format(max(Decimal(native["confirmed"]) - used, 0), ".2f"))
    confirmed = sar_amount(native["confirmed"], basis)
    if row.get("derived_credit"):
        confirmed = max(sar_amount(original_settled, basis) - sar_amount(original_confirmed, basis), 0)
    row.update(currency="SAR", native_balance=native, sar_basis=basis["basis"],
               confirmed=format(confirmed, ".2f"), expected=format(sar_amount(native["expected"], basis), ".2f"))
    return sar_amount(used, basis)


def unavailable(row, issues):
    if any(Decimal(row.get(k, "0")) != 0 for k in ("confirmed", "expected")):
        issues.append({"code": "provider_balance_sar_unavailable", "source_id": row["id"], "currency": "SAR",
                       "source_currency": row["currency"], "provider": row["party_id"],
                       "message": "قيمة مستحق المنصة بالريال تحتاج إلى مطابقة. يظهر المبلغ بعملته الأصلية."})


# Rates, IDs and policy terms aren't monetary fields.
PROJECTION_MONEY = {"gross", "cancelled", "refunded", "net", "estimated_fees", "expected_receivable", "confirmed_receivable",
                    "estimated_fee_net", "estimated_fee_tax", "refunded_fee_net", "refunded_fee_tax", "estimated_fees_original",
                    "estimated_fees_remaining", "actual_fees", "settled", "outstanding"}


def convert_projection(projection, basis):
    result = {**projection, "native_currency": basis["currency"]}
    for key in PROJECTION_MONEY.intersection(result):
        result[key] = format(sar_amount(result[key], basis), ".2f")
    if "fee_components" in result:
        result["fee_components"] = [
            {k: format(sar_amount(v, basis), ".2f") if k.endswith(("_fee_net", "_fee_tax")) else v for k, v in row.items()}
            for row in result["fee_components"]]
    return result
