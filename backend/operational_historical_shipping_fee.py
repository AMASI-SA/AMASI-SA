"""Owner authorization for one historical delivery fee; no cash or COD effects."""
from collections import Counter
from decimal import Decimal

from operational_balance_store import digest
from operational_shipping_fee_review import amount

APPROVALS = "historical_shipping_fee_approvals"


def saved_for(state, number):
    return (state or {}).get(APPROVALS, {}).get(digest(str(number)))


def register_candidate(out, review_numbers, event, *, rate=None, quote=None, earning=None):
    if not event or event["order_number"] not in review_numbers:
        return
    parts = [p for p in (quote or {}).get("fee_components", []) if p.get("id") == "base_shipping"]
    if earning:
        n = amount(earning["candidate_amount"])
    elif len(parts) == 1 and parts[0].get("complete") is True and rate and rate.get("id"):
        n = amount(parts[0].get("amount"))
    else:
        return
    if n <= 0:
        return
    p = {k: event[k] for k in ("order_id", "order_number", "party_type", "party_id", "currency", "delivered_at")}
    p.update(amount=format(n, ".2f"), obligation_id="shipping:" + event["order_id"] + ":base_shipping",
             delivery_hash=digest(event), source_hash=digest([event, rate, earning]))
    if rate and rate.get("id"):
        p["contract_id"] = rate["id"]
    for k in ("waybill", "shipment_id", "assignment_id"):
        if k in event:
            p[k] = event[k]
    if earning:
        p["earning_id"] = earning["earning_id"]
    out.setdefault("historical_fee_candidates", []).append(p)


def review(state, candidate):
    """Validate source-specific blockers and expose relevant openings and payments."""
    if not candidate:
        return [], [], ["لا توجد أجرة تاريخية مؤهلة لهذا الطلب. طابق التسليم والتعرفة والمدفوعات أولًا."]
    def same_party(row):
        return all(row.get(k) == candidate[k] for k in ("party_id", "party_type", "currency"))
    openings = [{k: r.get(k) for k in ("id", "name", "amount", "currency", "direction")}
                for r in state.get("openings", {}).values() if same_party(r)]
    keys = {"shipping:" + candidate["order_id"], candidate["obligation_id"]}
    engine = state.get("engine", {})
    facts, obs = engine.get("facts", {}), engine.get("obligations", {})
    issues = []
    for key in keys:
        r = obs.get(key)
        if r and (not same_party(r) or r.get("kind") != "shipping" or r.get("direction") != "payable"
                  or amount(r["confirmed"]) != 0 or key != candidate["obligation_id"] and amount(r.get("expected", 0)) > 0):
            issues.append("توجد أجرة سابقة أو ذمة متعارضة على هذا الطلب.")
            break
    shipping_key = "shipping:" + candidate["order_id"]
    frozen = facts.get(shipping_key)
    saved_event = facts.get("delivery_event:" + candidate["order_id"])
    if (saved_event and digest(saved_event) != candidate["delivery_hash"]
            or frozen and (frozen.get("id") != candidate["party_id"] or frozen.get("party_type") != candidate["party_type"]
                    or frozen.get("delivered_at") != candidate["delivered_at"] or frozen.get("cost") is not None)
            or any(k in facts for k in (candidate["obligation_id"], "shipping_fee:" + candidate["order_id"]))):
        issues.append("توجد أجرة محفوظة أو يختلف مستفيد التسليم أو تاريخه عن الدليل.")
    payouts = []
    for m in state.get("movements", []):
        allocations = m.get("allocations") or []
        if any(a.get("obligation_id") in keys for a in allocations):
            issues.append("يوجد سداد مخصص لأجرة هذا الطلب.")
        if not same_party(m) or m.get("direction") != "outgoing":
            continue
        payouts.append({k: m.get(k) for k in ("id", "amount", "currency", "kind", "allocations")})
        if (m.get("kind") != "payment" or not allocations
                or sum((amount(a["amount"]) for a in allocations), Decimal(0)) != amount(m["amount"])
                or any(not same_party(obs.get(a.get("obligation_id"), {}))
                       or obs[a["obligation_id"]].get("direction") != "payable" for a in allocations)):
            issues.append("يوجد دفع غير مخصص بالكامل لهذه الجهة. طابقه أولًا.")
    return sorted(openings, key=lambda r: str(r["id"])), sorted(payouts, key=lambda r: str(r["id"])), list(dict.fromkeys(issues))


def apply_reviewed_fees(state, sources, put, fact, issue):
    approvals = state.get(APPROVALS, {})
    resolved = set()
    if not approvals or not sources.get("delivery_event_coverage"):
        return resolved
    candidates = {p["order_number"]: p for p in sources.get("historical_fee_candidates", [])}
    counts = Counter(p["order_number"] for p in sources.get("historical_fee_candidates", []))
    for key, saved in approvals.items():
        p = saved["candidate"]
        number = p["order_number"]
        recorded = state["engine"].get("facts", {}).get("historical_base_fee:" + p["order_id"])
        if (counts[number] != 1 or key != digest(number) or saved.get("approved_by") != state["owner_id"] or candidates.get(number) != p):
            issue("historical_shipping_fee_source_changed", p["order_id"])
            continue
        reason = "driver_fee_history_review_required" if p["party_type"] == "store_driver" else "courier_fee_historical_review_required"
        if recorded is not None:
            # Do not touch previously released debts or their allocations.
            obs = state["engine"]["obligations"].get(p["obligation_id"], {})
            if (recorded == {"source_hash": p["source_hash"]} and obs.get("confirmed") == p["amount"] and
                    all(obs.get(k) == p[k] for k in ("party_id", "party_type", "currency"))):
                resolved.add((reason, p["order_id"]))
            else:
                issue("historical_shipping_fee_balance_changed", p["order_id"])
            continue
        openings, payouts, issues = review(state, p)
        if issues or digest([openings, payouts]) != saved.get("opening_payout_hash"):
            issue("historical_shipping_fee_balance_changed", p["order_id"])
            continue
        put(p["obligation_id"], "shipping", p["party_id"], p["party_type"], p["currency"],
            confirmed=amount(p["amount"]), business_date=p["delivered_at"][:10], fee_component="base_shipping")
        fact(p["obligation_id"], {"cost": p["amount"]})
        fact("historical_base_fee:" + p["order_id"], {"source_hash": p["source_hash"]})
        resolved.add((reason, p["order_id"]))
    return resolved
