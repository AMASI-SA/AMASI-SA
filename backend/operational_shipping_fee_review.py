"""Reconcile driver earnings, never customer collection, against delivery fees.

The driver producer denominates the assignment snapshot and earning in SAR.
Its payouts are pooled per driver; they are never allocated to individual
orders by this reader. The output is either corroboration of an existing
delivery policy or a nonposting, operational-review row.
"""
from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation

from operational_delivery_evidence import _instant, _text

NATIVE = "store_delivery_operational"
PENDING = "pending_mz2_driver_balance_link"
FIELDS = {
    "store_delivery_driver_earnings": ("user_id", "id", "driver_id", "assignment_id", "order_id", "order_number",
        "amount", "earned_at", "status", "currency", "accounting_status", "financial_source",
        "financial_handoff_status", "ledger_txn_group_id", "accounting_operation_id"),
    "store_delivery_driver_settlements": ("user_id", "driver_id", "settlement_type", "status"),
}
REASONS = {
    "driver_fee_history_review_required": "أجرة مثبتة في التطبيق تخص فترة سبق احتسابها؛ طابق الافتتاح والتسديد قبل اعتمادها.",
    "driver_fee_policy_review_required": "أجرة مثبتة في التطبيق؛ لا يوجد عقد توصيل واحد معتمد وساري يطابقها.",
    "driver_fee_payout_review_required": "توجد تسوية أجور مجمعة أو معلّقة لهذا المندوب؛ لا يمكن تخصيصها لهذا الطلب تلقائيًا.",
    "driver_fee_evidence_conflict": "تعذر إثبات تطابق الإسناد والاستحقاق أو الجهة أو العملة أو وقت التسليم.",
    "driver_fee_amount_conflict": "مبلغ الإسناد أو الأجرة المثبتة لا يطابق قيمة العقد؛ راجع المصادر.",
    "driver_fee_saved_conflict": "الأجرة أو المستفيد يختلف عن قيد أو تخصيص سابق؛ حُفظ القيد السابق دون تعديل.",
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
        self.allocation_keys = set()
        self.state = (state or {}).get("engine", {})
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
        for p in data["store_delivery_driver_settlements"]:
            if (p.get("status") != "rejected" and
                    p.get("settlement_type") != "cod_remittance"):
                # Unknown settlement states or malformed amounts cannot prove unpaid.
                self.payout_drivers.add(p.get("driver_id"))
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

    def check(self, a, order, quote, matching_rates):
        """None for original contract-only producers. No collection method input."""
        earning_rows = self.by_assignment.get(a.get("id"), [])
        if not self.has_other_assignment(order, a.get('id')) and not earning_rows and not (a.get("financial_source") == NATIVE or a.get("delivery_fee_snapshot") is not None):
            return None
        oid, num = order["id"], order["order_number"]
        key = "shipping:" + oid
        info = dict(order_id=oid, order_number=num, party_id=a.get("driver_id"),
                    party_name=a.get("driver_name_snapshot") or a.get("driver_id"),
                    assignment_id=a.get("id"), currency="SAR", delivered_at=None, candidate_amount=None)
        try:
            if (a.get("user_id") != self.owner or a.get("active") is not True or a.get("status") != "delivered"
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
            delivered = _instant(a.get("delivered_at"))
            if (not self.start < delivered <= self.as_of or delivered < _instant(order["created_at"])
                or _instant(e.get("earned_at")) != delivered):
                raise ValueError("driver_fee_evidence_conflict")
            n = amount(e.get("amount"))
            if n != amount(a.get("delivery_fee_snapshot")):
                raise ValueError("driver_fee_amount_conflict")
            info.update(earning_id=e["id"], candidate_amount=format(n, ".2f"), delivered_at=delivered.isoformat())
            prior = self.state.get("obligations", {})
            previous = [prior[k] for k in (key, key + ":base_shipping") if k in prior and amount(prior[k].get("confirmed")) > 0]
            if (len(previous) > 1 or any(r.get("party_type") != "store_driver" or r.get("party_id") != a["driver_id"]
                    or r.get("currency") != "SAR" or amount(r.get("confirmed")) != n for r in previous)
                    or (not previous and {key, key + ":base_shipping"} & self.allocation_keys)):
                raise ValueError("driver_fee_saved_conflict")
            frozen = self.state.get("facts", {}).get(key)
            if frozen and (frozen.get("party_type") != "store_driver" or frozen.get("id") != a["driver_id"]
                        or _instant(frozen.get("delivered_at")) != delivered):
                raise ValueError("driver_fee_saved_conflict")
            stored = self.state.get("facts", {}).get("native_driver_earning:" + oid)
            if stored and stored != {k:v for k,v in info.items() if k != "party_name"}:
                raise ValueError("driver_fee_saved_conflict")
            if a["driver_id"] in self.payout_drivers:
                raise ValueError("driver_fee_payout_review_required")
            if not previous and not stored and self.state.get("as_of") and delivered <= _instant(self.state["as_of"]):
                raise ValueError("driver_fee_history_review_required")
            if matching_rates != 1 or not quote:
                raise ValueError("driver_fee_policy_review_required")
            base = [p for p in quote.get("fee_components", []) if p.get("id") == "base_shipping"]
            if len(base) != 1 or not base[0].get("complete") or amount(base[0].get("amount")) != n:
                raise ValueError("driver_fee_amount_conflict")
            return info, None
        except (ValueError, ArithmeticError, TypeError, KeyError) as exc:
            code = str(exc) if str(exc) in REASONS else "driver_fee_evidence_conflict"
            return info, code


def courier_issue(out, code, raw, source, *, candidate=None, event=None):
    """Expose source identity for investigation, never infer the delivery date or debt."""
    current = source.get("salla_shipping_current") or raw.get("salla_shipping_current") or {}
    if not isinstance(current, dict):
        current = {}
    shipping = raw.get("shipping") if isinstance(raw.get("shipping"), dict) else {}
    name = current.get("company_name") or current.get("company_code") or shipping.get("company_code")
    oid, number = _text(raw.get("id")), _text(raw.get("reference_id") or raw.get("order_number"))
    if not (name and oid and number):
        return
    info = {"party_name":str(name), "order_number":number, "order_id":oid,
            "delivered_at":(event or {}).get("delivered_at"), "candidate_amount":candidate,
            "currency":(event or {}).get("currency") or ("SAR" if raw.get("currency") == "SAR" else None)}
    actions = {
        "operational_delivery_timestamp_missing":"يلزم تاريخ تسليم مؤرّخ للبوليصة الحالية؛ حالة الطلب أو تاريخ تعديله لا يثبتان التسليم بعد البداية.",
        "operational_delivery_confirmed_evidence_changed":"بيانات البوليصة الحالية تختلف عن التسليم المثبت؛ راجع البوليصتين قبل أي تصحيح.",
        "operational_delivery_order_shipment_status_conflict":"حالة الطلب وحالة البوليصة الحالية مختلفتان؛ طابق دليل التسليم قبل الاحتساب.",
        "operational_delivery_shipment_source_missing":"يلزم مستند الشحنة الأصلية المطابق للبوليصة الحالية.",
        "operational_delivery_courier_identity_ambiguous":"يلزم ربط شركة الشحنة بهوية تشغيلية معتمدة واحدة.",
        "courier_fee_historical_review_required":"تسليم سابق لم يظهر كقيد مؤكد عند احتسابه؛ راجع الافتتاح والمدفوعات قبل اعتماد الأجرة.",
        "shipping_identity_or_rate_incomplete":"يلزم مطابقة الشركة وعقد توصيل معتمد وساري بتاريخ التسليم.",
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
    engine = state.get("engine", {})
    saved_at = engine.get("as_of")
    if not saved_at or _instant(event["delivered_at"]) > _instant(saved_at):
        return False
    key = "shipping:" + event["order_id"]
    frozen = engine.get("facts", {}).get(key)
    if not frozen:
        return True
    obs = engine.get("obligations", {})
    return (carrier or {}).get("cost") is not None and not any(
        amount(obs[k].get("confirmed", 0)) > 0 for k in (key, key + ":base_shipping") if k in obs)
