"""Reconcile driver earnings, never customer collection, against delivery fees.

The driver producer denominates the assignment snapshot and earning in SAR.
Its payouts are pooled per driver; they are never allocated to individual
orders by this reader. An owner-priced assignment and matching earned fee
support the base delivery cost. Any applicable delivery policy must agree.
"""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from operational_delivery_evidence import STATUS_BASIS, _instant, _text, delivery_time_matches, status_delivery
from operational_delivery_confirmation import BASIS as OWNER_DELIVERY_BASIS

NATIVE = "store_delivery_operational"
PENDING = "pending_mz2_driver_balance_link"
FIELDS = {
    "store_delivery_driver_earnings": ("user_id", "id", "driver_id", "assignment_id", "order_id", "order_number",
        "amount", "earned_at", "status", "currency", "accounting_status", "financial_source",
        "financial_handoff_status", "ledger_txn_group_id", "accounting_operation_id"),
    "store_delivery_driver_settlements": ("id", "user_id", "driver_id", "settlement_type", "status", "currency",
        "posting_scope", "financial_source", "financial_handoff_status", "accounting_status", "posted_at",
        "created_at", "driver_decision", "driver_decision_by", "driver_decision_at", "amount",
        "delivery_fee_settled_amount", "earning_offset", "ledger_txn_group_id", "accounting_operation_id"),
}
REASONS = {
    "driver_fee_history_review_required": "هذه الأجرة مرتبطة بمطابقة محفوظة للافتتاح والسداد؛ تُطبق وفق الاعتماد المسجل.",
    "driver_fee_policy_review_required": "تعذرت مطابقة أجرة التطبيق مع إعدادات عقود التوصيل السارية.",
    "driver_fee_payout_review_required": "توجد تسوية أجور مجمعة أو معلّقة لهذا المندوب؛ لا يمكن تخصيصها لهذا الطلب تلقائيًا.",
    "driver_fee_evidence_conflict": "تعذر إثبات تطابق الإسناد والاستحقاق أو الجهة أو العملة أو وقت التسليم.",
    "driver_fee_amount_conflict": "تختلف أجرة الإسناد عن مبلغ الاستحقاق أو عن التعرفة السارية.",
    "driver_fee_saved_conflict": "الأجرة أو المستفيد يختلف عن قيد أو تخصيص سابق؛ حُفظ القيد السابق دون تعديل.",
    "driver_fee_native_overlap": "يوجد قيد أجرة أو تسوية سابقة في محاسبة الشحن؛ طابقها قبل إضافة الاستحقاق.",
}


def amount(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("driver_fee_evidence_conflict")
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or n != n.quantize(Decimal(".01")):
            raise ValueError("driver_fee_evidence_conflict")
        return n
    except (InvalidOperation, ArithmeticError):
        raise ValueError("driver_fee_evidence_conflict") from None


def _closed_payout_at(p):
    # The native writer caps payouts at earnings already due. A fully approved
    # payout cannot include a delivery that had not happened by its posting time.
    if (p.get("status") != "posted" or p.get("posting_scope") != "operational_balance"
            or p.get("financial_source") != NATIVE or p.get("financial_handoff_status") != PENDING
            or p.get("accounting_status") != "operational_only" or p.get("currency", "SAR") != "SAR"
            or p.get("ledger_txn_group_id") or p.get("accounting_operation_id")
            or p.get("driver_decision") != "approved" or p.get("driver_decision_by") != p.get("driver_id")
            or p.get("settlement_type") not in {"earning_payment", "net_settlement"}):
        return None
    try:
        fee = amount(p.get("delivery_fee_settled_amount"))
        source = p.get("amount") if p["settlement_type"] == "earning_payment" else p.get("earning_offset")
        posted = _instant(p["posted_at"])
        if (fee > 0 and fee == amount(source) and _instant(p["created_at"]) <= posted
                and _instant(p["driver_decision_at"]) == posted):
            return posted
    except (ValueError, ArithmeticError, TypeError, KeyError):
        pass
    return None


class DriverEarnings:
    def __init__(self, data, owner, state, start, as_of):
        self.owner, self.start, self.as_of = owner, start, as_of
        self.by_assignment = defaultdict(list)
        self.native_order_ids, self.native_order_numbers = set(), set()
        self.earning_ids = Counter()
        self.assignments_by_order = defaultdict(set)
        self.assignments_by_number = defaultdict(set)
        self.assignment_ids = Counter()
        self.payout_drivers = set()
        self.latest_closed_payout = {}
        self.allocation_keys = set()
        self.aggregate = state or {}
        self.state = self.aggregate.get("engine", {})
        for a in data["store_delivery_assignments"]:
            self.assignment_ids[a.get("id")] += 1
            if a.get("status") == "delivered" and (a.get("financial_source") == NATIVE or a.get("delivery_fee_snapshot") is not None):
                self.native_order_ids.add(_text(a.get("order_id")))
                self.native_order_numbers.add(_text(a.get("order_number")))
        for e in data["store_delivery_driver_earnings"]:
            self.by_assignment[e.get("assignment_id")].append(e)
            self.earning_ids[e.get("id")] += 1
            self.assignments_by_order[_text(e.get("order_id"))].add(e.get("assignment_id"))
            self.assignments_by_number[_text(e.get("order_number"))].add(e.get("assignment_id"))
        payouts = data["store_delivery_driver_settlements"]
        payout_ids = Counter(p.get("id") for p in payouts)
        for p in payouts:
            driver = p.get("driver_id")
            if p.get("status") == "rejected":
                if p.get("posted_at") or p.get("ledger_txn_group_id") or p.get("accounting_operation_id"):
                    self.payout_drivers.add(driver)
                continue
            if p.get("settlement_type") == "cod_remittance":
                try:
                    if amount(p.get("delivery_fee_settled_amount", 0)) or amount(p.get("earning_offset", 0)):
                        self.payout_drivers.add(driver)
                except ValueError:
                    self.payout_drivers.add(driver)
                continue
            closed = _closed_payout_at(p) if p.get("id") and payout_ids[p["id"]] == 1 else None
            if closed is None:
                self.payout_drivers.add(driver)
            else:
                self.latest_closed_payout[driver] = max(self.latest_closed_payout.get(driver, closed), closed)
        for m in (state or {}).get("movements", []):
            if m.get("party_type") == "store_driver" and m.get("kind") == "payment":
                allocations = m.get("allocations") or []
                if not allocations or sum((amount(v["amount"]) for v in allocations), Decimal(0)) != amount(m["amount"]):
                    self.payout_drivers.add(m.get("party_id"))
            self.allocation_keys.update(v.get("obligation_id") for v in m.get("allocations", []))

    def requires_delivery_proof(self, order):
        return (order["id"] in self.native_order_ids or order["order_number"] in self.native_order_numbers
                or self.has_other_assignment(order))

    def has_other_assignment(self, order, assignment_id=None):
        claims = self.assignments_by_order.get(order["id"], set()) | self.assignments_by_number.get(order["order_number"], set())
        return bool(claims) if assignment_id is None else bool(claims - {assignment_id})

    def check(self, a, order, quote, matching_rates, *, ambiguous_setup=False, delivery_event=None):
        """None for original contract-only producers. No collection method input."""
        earning_rows = self.by_assignment.get(a.get("id"), [])
        if not self.has_other_assignment(order, a.get('id')) and not earning_rows and not (a.get("financial_source") == NATIVE or a.get("delivery_fee_snapshot") is not None):
            return None
        oid, num = order["id"], order["order_number"]
        key = "shipping:" + oid
        info = dict(order_id=oid, order_number=num, party_id=a.get("driver_id"),
                    party_name=a.get("driver_name_snapshot") or a.get("driver_id"),
                    assignment_id=a.get("id"), currency="SAR", delivered_at=None, candidate_amount=None)
        undated = status_delivery(delivery_event)
        try:
            if (a.get("user_id") != self.owner or a.get("active") is not True
                or (a.get("status") not in {"assigned", "out_for_delivery", "delivered"} if undated else a.get("status") != "delivered")
                or not _text(a.get("id")) or not _text(a.get("driver_id"))
                or a.get("financial_source") != NATIVE or a.get("financial_handoff_status") != PENDING
                or a.get("accounting_status") != "operational_only"
                or a.get("ledger_txn_group_id") or a.get("accounting_operation_id")
                or self.has_other_assignment(order, a["id"])
                or self.assignment_ids[a["id"]] != 1 or _text(a.get("order_id")) != oid
                or _text(a.get("order_number")) != num or len(earning_rows) != 1):
                raise ValueError("driver_fee_evidence_conflict")
            e = earning_rows[0]
            if (not _text(e.get("id")) or self.earning_ids[e["id"]] != 1 or e.get("user_id") != self.owner
                or e.get("driver_id") != a["driver_id"] or _text(e.get("order_id")) != oid
                or _text(e.get("order_number")) != num or e.get("status") != "due"
                or e.get("financial_source") != NATIVE or e.get("financial_handoff_status") != PENDING
                or e.get("accounting_status") != "operational_only" or e.get("ledger_txn_group_id")
                or e.get("accounting_operation_id") or order.get("currency") != "SAR"
                or any(row.get("currency", "SAR") != "SAR" for row in [a, e])):
                raise ValueError("driver_fee_evidence_conflict")
            earned = _instant(e.get("earned_at"))
            if undated:
                created, recorded = _instant(order["created_at"]), _instant(delivery_event.get("recorded_at"))
                basis = delivery_event.get('recognition_basis')
                eligible = (self.start < created <= earned <= self.as_of if basis == STATUS_BASIS else
                            max(self.start, created) <= earned <= recorded <= self.as_of if basis == OWNER_DELIVERY_BASIS else False)
                if (not eligible
                        or delivery_event.get("delivered_at") is not None
                        or not created <= recorded <= self.as_of):
                    raise ValueError("driver_fee_evidence_conflict")
                delivered = None
                info.update(recognition_basis=basis, recorded_at=recorded.isoformat(), earned_at=earned.isoformat())
            else:
                delivered = _instant(a.get("delivered_at"))
                if (not self.start <= delivered <= self.as_of or delivered < _instant(order["created_at"])
                        or earned != delivered):
                    raise ValueError("driver_fee_evidence_conflict")
            n = amount(e.get("amount"))
            if n != amount(a.get("delivery_fee_snapshot")):
                raise ValueError("driver_fee_amount_conflict")
            info.update(earning_id=e["id"], candidate_amount=format(n, ".2f"),
                        delivered_at=delivered.isoformat() if delivered is not None else None)
            prior = self.state.get("obligations", {})
            previous = [prior[k] for k in (key, key + ":base_shipping") if k in prior and amount(prior[k].get("confirmed")) > 0]
            if (len(previous) > 1 or any(r.get("party_type") != "store_driver" or r.get("party_id") != a["driver_id"]
                    or r.get("currency") != "SAR" or amount(r.get("confirmed")) != n for r in previous)
                    or (not previous and {key, key + ":base_shipping"} & self.allocation_keys)):
                raise ValueError("driver_fee_saved_conflict")
            saved_facts = self.state.get("facts", {})
            frozen = saved_facts.get(key)
            if frozen and (frozen.get("party_type") != "store_driver" or frozen.get("id") != a["driver_id"]
                        or (not (frozen.get("recognition_basis") == info.get('recognition_basis')
                                 and frozen.get("delivered_at") is None
                                 and frozen.get("recorded_at") == info["recorded_at"]) if undated else
                            not delivery_time_matches(frozen, delivered, order_id=oid, native_driver_earning=info))):
                raise ValueError("driver_fee_saved_conflict")
            if frozen and not undated and _instant(frozen["delivered_at"]) != delivered:
                saved_prices = ((saved_facts.get(key + ":base_shipping"), n),
                                (saved_facts.get("shipping_fee:" + oid), quote.get("cost") if quote else n))
                if any(saved is not None and (expected is None or amount(saved.get("cost")) != amount(expected))
                       for saved, expected in saved_prices):
                    raise ValueError("driver_fee_saved_conflict")
            stored = saved_facts.get("native_driver_earning:" + oid)
            if stored and stored != {k:v for k,v in info.items() if k != "party_name"}:
                raise ValueError("driver_fee_saved_conflict")
            if ambiguous_setup or matching_rates > 1 or (matching_rates and not quote):
                raise ValueError("driver_fee_policy_review_required")
            if quote:
                base = [p for p in quote.get("fee_components", []) if p.get("id") == "base_shipping"]
                if len(base) != 1 or not base[0].get("complete") or amount(base[0].get("amount")) != n:
                    raise ValueError("driver_fee_amount_conflict")
            last_payout = self.latest_closed_payout.get(a["driver_id"])
            # Native earned_at is the source earning's own clock. A later
            # operational recording time cannot prove an earlier payout did
            # not already include this fee.
            payout_boundary = earned if undated else delivered
            if a["driver_id"] in self.payout_drivers or (last_payout and payout_boundary <= last_payout):
                raise ValueError("driver_fee_payout_review_required")
            from operational_historical_shipping_fee import saved_for
            if saved_for(self.aggregate, num):
                raise ValueError("driver_fee_history_review_required")
            return info, None
        except (ValueError, ArithmeticError, TypeError, KeyError) as exc:
            code = str(exc) if str(exc) in REASONS else "driver_fee_evidence_conflict"
            return info, code


def courier_issue(out, code, raw, source, *, carrier=None, event=None, setup=None, owner=None):
    """Expose source identity for investigation, never infer the delivery date or debt."""
    current = source.get("salla_shipping_current") or raw.get("salla_shipping_current") or {}
    if not isinstance(current, dict):
        current = {}
    shipping = raw.get("shipping") if isinstance(raw.get("shipping"), dict) else {}
    name = current.get("company_name") or current.get("company_code") or shipping.get("company_code")
    oid, number = _text(raw.get("id")), _text(raw.get("reference_id") or raw.get("order_number"))
    if not (oid and number):
        return
    party = (event or {}).get('party_id') if (event or {}).get('party_type') == 'courier' else None
    if party is None and setup is not None and owner:
        from operational_delivery_evidence import _courier
        try:
            party, _ = _courier(setup, owner, current)
        except (ValueError, KeyError, TypeError):
            pass
    info = {"party_name":str(name or party or 'شركة شحن غير مطابقة'), "order_number":number, "order_id":oid,
            "party_type":"courier" if party else None, "party_id":party,
            "delivered_at":(event or {}).get("delivered_at"), "candidate_amount":(carrier or {}).get("cost"),
            "currency":(carrier or {}).get("currency")}
    if party and not info['currency']:
        currencies = {rate.get('currency') for rate in (setup or {}).get('contracts', [])
                      if rate.get('party_type') == 'courier' and rate.get('party_id') == party
                      and rate.get('status') == 'approved'}
        if len(currencies) == 1 and None not in currencies:
            info['currency'] = next(iter(currencies))
    actions = {
        "operational_delivery_timestamp_missing":"طابق تاريخ تسليم البوليصة الحالية أو حدّث سجل حركة الطلب من سلة لإثبات وقت انتقاله إلى تم التوصيل.",
        "operational_delivery_order_waybill_unproven":"حركة التسليم بتاريخ الطلب تتطلب بوليصة واحدة مطابقة؛ طابق تاريخ البوليصة الفعلية عند الاستبدال أو تعدد الشحنات.",
        "operational_delivery_timestamp_ambiguous":"تختلف تواريخ التسليم أو توجد حركة حالة لاحقة؛ راجع سجل الطلب والبوليصة.",
        "operational_delivery_confirmed_evidence_changed":"بيانات البوليصة الحالية تختلف عن التسليم المثبت؛ راجع البوليصتين قبل أي تصحيح.",
        "operational_delivery_order_shipment_status_conflict":"حالة الطلب وحالة البوليصة الحالية مختلفتان؛ طابق دليل التسليم قبل الاحتساب.",
        "operational_delivery_shipment_source_missing":"يلزم مستند الشحنة الأصلية المطابق للبوليصة الحالية.",
        "operational_delivery_courier_identity_ambiguous":"يلزم ربط شركة الشحنة بهوية تشغيلية معتمدة واحدة.",
        "courier_fee_historical_review_required":"ترتبط الأجرة بسداد سابق أو مطابقة محفوظة؛ راجع التخصيصات قبل اعتمادها.",
        "shipping_identity_or_rate_incomplete":"يلزم مطابقة الشركة وعقد توصيل معتمد وساري بتاريخ التسليم.",
        "operational_shipping_completion_timestamp_missing":"طابق وقت انتقال الطلب إلى تم التنفيذ من الدليل المعتمد؛ وقت تحديث الطلب العام لا يكفي.",
        "operational_prepaid_shipping_completion_timestamp_missing":"طابق وقت انتقال الطلب إلى تم التنفيذ من الدليل المعتمد؛ وقت تحديث الطلب العام لا يكفي.",
        "operational_shipping_completion_history_review_required":"راجع الأرصدة الافتتاحية والسدادات ثم اعتمد أجرة هذا الطلب من مراجعة أجور تم التنفيذ.",
    }
    msg = actions.get(code, "راجع هوية الشحنة الحالية وتاريخ التسليم وعقد الشركة قبل الاعتماد.")
    entry = {"code":code,"source_id":oid,"status":"incomplete","component":"shipping_fee",
             "courier_fee_review":info, "required_action":msg}
    previous = out["issues"][-1] if out["issues"] else {}
    if previous.get("code") == code and previous.get("source_id") == oid:
        previous.update(entry)
    else:
        out["issues"].append(entry)


def hold_prior_courier_event(state, event, carrier):
    if not state or not event or event["party_type"] != "courier":
        return False
    from operational_historical_shipping_fee import saved_for
    if saved_for(state, event["order_number"]):
        return True
    engine = state.get("engine", {})
    key = "shipping:" + event["order_id"]
    obs = engine.get("obligations", {})
    keys = {key, key + ":base_shipping"}
    # Replays retain their original obligation and its allocations. A previous
    # calculation time alone does not settle a newly discovered delivery.
    if any(amount(obs[k].get("confirmed", 0)) > 0 for k in keys if k in obs):
        return False
    fee_currency = (carrier or {}).get("currency")
    def same_party(row):
        return all(row.get(k) == event[k] for k in ("party_type", "party_id"))
    def same_payable(row):
        return same_party(row) and row.get("currency") == fee_currency
    payable_ids = {k for k, row in obs.items() if same_payable(row) and row.get("direction") == "payable"}
    payable_ids.update(row["id"] for row in state.get("openings", {}).values()
                       if same_payable(row) and row.get("direction") == "for_party")
    for movement in state.get("movements", []):
        allocations = movement.get("allocations") or []
        if any(row.get("obligation_id") in keys for row in allocations):
            return True
        if not same_party(movement) or movement.get("direction") != "outgoing":
            continue
        # Without a priced contract, a pooled payment cannot be cleared by
        # comparing it with the unrelated customer currency.
        if fee_currency is not None and movement.get("currency") != fee_currency:
            continue
        try:
            if (movement.get("kind") != "payment" or not allocations
                    or sum((amount(row["amount"]) for row in allocations), Decimal(0)) != amount(movement["amount"])
                    or any(row.get("obligation_id") not in payable_ids for row in allocations)):
                return True
        except (ValueError, ArithmeticError, TypeError, KeyError):
            return True
    return False


async def hold_native_fee_overlaps(db, owner, out, state, *, max_rows):
    """Check first fee recognition against native postings and pooled payouts."""
    from operational_historical_shipping_fee import native_conflicts
    obligations = (state or {}).get("engine", {}).get("obligations", {})
    new_fees = []
    for order in out["orders"] + out["delivery_events"]:
        event, carrier = order.get("delivery_event"), order.get("carrier") or {}
        if not event:
            continue
        key = "shipping:" + order["id"]
        if any(amount(obligations[k].get("confirmed", 0)) > 0
               for k in (key, key + ":base_shipping") if k in obligations):
            continue
        has_fee = carrier.get("cost") is not None and amount(carrier["cost"]) > 0
        has_fee = has_fee or any(part.get("complete") is True and amount(part.get("amount")) > 0
                                 for part in carrier.get("fee_components", []))
        if has_fee:
            new_fees.append(order)
    candidates = out.get("historical_fee_candidates", []) + [order["delivery_event"] for order in new_fees]
    if not candidates:
        return
    conflicts = await native_conflicts(db, owner, candidates, max_rows=max_rows)
    out["historical_fee_native_conflicts"] = conflicts
    for order in new_fees:
        reason = conflicts.get(order["order_number"])
        if not reason:
            continue
        event, carrier = order["delivery_event"], order["carrier"]
        info = order.pop("native_driver_earning", None)
        if event["party_type"] == "store_driver":
            code, field = "driver_fee_native_overlap", "driver_fee_review"
        else:
            code, field = "courier_fee_native_overlap", "courier_fee_review"
        if info is None:
            info = {k: event[k] for k in ("order_id", "order_number", "party_type", "party_id", "delivered_at")}
            info.update(currency=carrier.get("currency"), party_name=event.get("source_carrier_key") or event["party_id"],
                        candidate_amount=carrier.get("cost"))
        out["issues"].append({"code": code, "source_id": order["id"], "status": "incomplete",
                              "component": "shipping_fee", "recognition_state": "confirmed",
                              field: info, "required_action": reason})
        carrier.update(cost=None, fee_complete=False, fee_recognition_held=True)
        carrier.pop("fee_components", None)
