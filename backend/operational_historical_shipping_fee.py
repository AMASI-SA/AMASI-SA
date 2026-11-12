"""Owner authorization for one historical delivery fee; no cash or COD effects."""
from collections import Counter, defaultdict
from decimal import Decimal

from operational_balance_store import digest
from operational_shipping_fee_review import amount
from dashboard_cpu_budget import CPUWorkBudget, PARSER_BUDGET_MS
from operational_financial_activity_runtime import checked_rows, preparation_checkpoint

APPROVALS = "historical_shipping_fee_approvals"
NATIVE_EVENTS = "mz2_shipping_accounting_events"
NATIVE_V2 = "mz2_shipping_native_events_v2"


def saved_for(state, number):
    return (state or {}).get(APPROVALS, {}).get(digest(str(number)))


def register_candidate(out, review_numbers, event, *, rate=None, quote=None, earning=None):
    if not event or event["order_number"] not in review_numbers:
        return
    parts = [p for p in (quote or {}).get("fee_components", []) if p.get("id") == "base_shipping"]
    if earning:
        n, currency = amount(earning["candidate_amount"]), earning.get("currency")
    elif len(parts) == 1 and parts[0].get("complete") is True and rate and rate.get("id"):
        n, currency = amount(parts[0].get("amount")), quote.get("currency")
    else:
        return
    # This approval surface supports SAR fees only. Delivery and COD evidence
    # may be denominated in a different customer currency after startup.
    if n <= 0 or currency != "SAR" or (rate and rate.get("currency") != currency):
        return
    p = {k: event[k] for k in ("order_id", "order_number", "party_type", "party_id", "delivered_at")}
    p.update(currency=currency, amount=format(n, ".2f"), obligation_id="shipping:" + event["order_id"] + ":base_shipping",
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
    preparation_checkpoint()
    if not candidate:
        return [], [], ["لا توجد أجرة تاريخية مؤهلة لهذا الطلب. طابق التسليم والتعرفة والمدفوعات أولًا."]
    def same_party(row):
        return all(row.get(k) == candidate[k] for k in ("party_id", "party_type", "currency"))
    openings = [{k: r.get(k) for k in ("id", "name", "amount", "currency", "direction")}
                for r in checked_rows(state.get("openings", {}).values()) if same_party(r)]
    keys = {"shipping:" + candidate["order_id"], candidate["obligation_id"]}
    engine = state.get("engine", {})
    facts, obs = engine.get("facts", {}), engine.get("obligations", {})
    issues = []
    from operational_completed_shipping_admission import recorded_base_fee
    if recorded_base_fee(state, candidate['order_id']):
        issues.append("توجد أجرة أساسية مثبتة لهذا الطلب؛ لا تُعتمد مرة ثانية من مسار التسليم التاريخي.")
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
    payable_ids = {k for k, row in checked_rows(obs.items()) if same_party(row) and row.get("direction") == "payable"}
    payable_ids.update(r["id"] for r in openings if r["direction"] == "for_party")
    payouts = []
    for m in checked_rows(state.get("movements", [])):
        allocations = m.get("allocations") or []
        if any(a.get("obligation_id") in keys for a in checked_rows(allocations)):
            issues.append("يوجد سداد مخصص لأجرة هذا الطلب.")
        if not same_party(m) or m.get("direction") != "outgoing":
            continue
        payouts.append({k: m.get(k) for k in ("id", "amount", "currency", "kind", "allocations")})
        if (m.get("kind") != "payment" or not allocations
                or sum((amount(a["amount"]) for a in checked_rows(allocations)), Decimal(0)) != amount(m["amount"])
                or any(a["obligation_id"] not in payable_ids for a in checked_rows(allocations))):
            issues.append("يوجد دفع غير مخصص بالكامل لهذه الجهة. طابقه أولًا.")
    return sorted(openings, key=lambda r: str(r["id"])), sorted(payouts, key=lambda r: str(r["id"])), list(dict.fromkeys(issues))


async def native_conflicts(db, owner, candidates, *, max_rows):
    """Existing fee postings and pooled payable offsets block a historical claim."""
    conflicts = {}
    budget = CPUWorkBudget(PARSER_BUDGET_MS)
    for offset in range(0, len(candidates), 250):
        batch = candidates[offset:offset + 250]
        numbers, parties, waybills, assignments = defaultdict(set), defaultdict(set), defaultdict(set), defaultdict(set)
        for p in batch:
            n = p["order_number"]
            numbers[n].add(n)
            parties[(p["party_type"], p["party_id"])].add(n)
            if p.get("waybill"):
                waybills[p["waybill"]].add(n)
            if p.get("assignment_id"):
                assignments[p["assignment_id"]].add(n)
        refs = [{"facts.order_number": {"$in": sorted(numbers)}}]
        if waybills:
            refs.append({"facts.waybill": {"$in": sorted(waybills)}})
        if assignments:
            refs.append({"facts.assignment_id": {"$in": sorted(assignments)}})
        query = {"user_id": owner, "$or": [
            {"kind": {"$in": ["courier_fee", "store_driver_cod"]}, "$or": refs},
            {"kind": "shipping_settlement", "facts.counterparty_id": {"$in": sorted({i for _, i in parties})}}]}
        fields = ("order_number", "waybill", "assignment_id", "counterparty_type", "counterparty_id",
                  "settlement_type", "offset_amount")
        projection = {"_id": 0, "user_id": 1, "kind": 1, **{"facts." + f: 1 for f in fields}}
        events = await db[NATIVE_EVENTS].find(query, projection).to_list(max_rows + 1)
        if len(events) > max_rows:
            conflicts.update({n: "تجاوزت التسويات حد المعاينة؛ يلزم حصرها قبل الاعتماد." for n in numbers})
            continue
        for event in events:
            preparation_checkpoint()
            await budget.checkpoint()
            if event.get("user_id") != owner:
                raise ValueError("operational_source_owner_mismatch")
            f = event["facts"]
            if event["kind"] == "shipping_settlement":
                if f.get("settlement_type") == "cod_remittance" and f.get("offset_amount") == "0.00":
                    continue
                matched = parties.get((f.get("counterparty_type"), f.get("counterparty_id")), set())
                reason = "توجد تسوية شحن أو مقاصة أصلية لهذه الجهة؛ طابق الأجرة مع التسوية قبل اعتمادها."
            else:
                matched = (numbers.get(str(f.get("order_number")), set()) | waybills.get(f.get("waybill"), set())
                           | assignments.get(f.get("assignment_id"), set()))
                reason = "يوجد قيد شحن أصلي على الطلب أو البوليصة أو إسناد المندوب."
            conflicts.update({n: reason for n in matched})
        v2 = await db[NATIVE_V2].find({"user_id": owner, "$or": [
            {"kind": "fee", "order_number": {"$in": sorted(numbers)}},
            {"kind": "settlement", "party_id": {"$in": sorted({i for _, i in parties})}}
        ]}, {"_id": 0, "user_id": 1, "kind": 1, "order_number": 1, "party_type": 1, "party_id": 1, "action": 1}).to_list(max_rows + 1)
        if len(v2) > max_rows:
            conflicts.update({n: "يلزم حصر جميع قيود الشحن الأصلية قبل الاعتماد." for n in numbers})
            continue
        for event in v2:
            preparation_checkpoint()
            await budget.checkpoint()
            if event.get("user_id") != owner:
                raise ValueError("operational_source_owner_mismatch")
            if event["kind"] == "fee":
                matched = numbers.get(str(event.get("order_number")), set())
            elif event.get("action") != "receive_cod":
                matched = parties.get((event.get("party_type"), event.get("party_id")), set())
            else:
                continue
            conflicts.update({n: "يوجد قيد أجرة أو سداد سابق في محاسبة الشحن الأصلية." for n in matched})
    return conflicts


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
        if number in sources.get("historical_fee_native_conflicts", {}):
            issue("historical_shipping_fee_native_overlap", p["order_id"])
            continue
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
        try:
            openings, payouts, issues = review(state, p)
        except (ValueError, KeyError, TypeError, ArithmeticError):
            issue("historical_shipping_fee_balance_changed", p["order_id"])
            continue
        if issues or digest([openings, payouts]) != saved.get("opening_payout_hash"):
            issue("historical_shipping_fee_balance_changed", p["order_id"])
            continue
        put(p["obligation_id"], "shipping", p["party_id"], p["party_type"], p["currency"],
            confirmed=amount(p["amount"]), business_date=p["delivered_at"][:10], fee_component="base_shipping")
        fact(p["obligation_id"], {"cost": p["amount"], "currency": p["currency"]})
        fact("historical_base_fee:" + p["order_id"], {"source_hash": p["source_hash"]})
        resolved.add((reason, p["order_id"]))
    return resolved
