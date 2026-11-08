"""Read-only COD expected from the customer before delivery, independent of a shipping tariff."""
from collections import Counter, defaultdict
from decimal import Decimal

from assembly_status_policy import status_values
from operational_balance_sources import MAX_ROWS, instant
from operational_delivery_evidence import _inactive, _text
from operational_payment_evidence import _amount
from operational_prestart_cod_forecast import COD
from order_currency import SUPPORTED_ORDER_CURRENCIES
from salla_payment_collection import order_page_source_current, provider_order_page_balance
from salla_shipping import SHIPMENT_FIELDS

BEFORE_DISPATCH = {
    "under_review", "waiting_review", "waiting_for_review", "pending_review", "in_progress",
    "pending", "waiting", "processing", "reviewed", "ready", "بانتظار_المراجعة", "بإنتظار_المراجعة",
    "انتظار_المراجعة", "تم_المراجعة", "تمت_المراجعة", "جاري_التنفيذ",
}
WITH_DRIVER = BEFORE_DISPATCH | {"out_for_delivery", "delivering", "جاري_التوصيل"}
LABELS = {"unassigned": "طلبات مفتوحة قبل إسناد التوصيل", "assigned": "طلبات مفتوحة مسندة لمندوب"}
PAID = {"paid", "captured", "completed", "مدفوع", "تم الدفع"}
TERMINAL_PAY = {"refunded", "partially_refunded", "voided", "cancelled",
                "canceled", "reversed", "failed", "مسترجع"}


def _page_due(raw):
    payment = raw.get("payment") or {}
    methods = set()
    for value in (raw.get("payment_method"), payment.get("method")):
        if value:
            names = [value.get(k) for k in ("code", "slug") if value.get(k)] if isinstance(value, dict) else [value]
            if not names:
                return None
            methods.update(str(name).strip().lower() for name in names)
    if not methods or not methods <= COD or raw.get("payment_methods") or payment.get("methods"):
        return None
    amounts = raw.get("amounts") or {}
    gross = next((v for v in (amounts.get("total"), raw.get("total"), raw.get("total_amount")) if v is not None), None)
    currency = (gross.get("currency") if isinstance(gross, dict) else None) or amounts.get("currency") or raw.get("currency")
    if currency not in SUPPORTED_ORDER_CURRENCIES:
        return None
    states = {str(v).strip().lower() for v in (raw.get("payment_status"), raw.get("payment_collection_status"),
              payment.get("status"), payment.get("collection_status")) if v}
    if (states & TERMINAL_PAY or any(r.get(k) for r in (raw, payment) for k in
            ("refunds", "refunded_at", "cancelled_at", "returned_at", "refunded_transactions"))
            or any(_amount(r[k], currency) for r in (raw, payment) for k in
                   ("refund_amount", "refunded_amount") if r.get(k) is not None)):
        return None
    action = (raw.get("payment_actions") or {}).get("remaining_action") or {}
    # The order page also recognizes a closed checkout on an unpaid SAR COD
    # order awaiting review. Validate every promoted value before trusting it.
    if (currency == "SAR" and action.get("has_remaining_amount") is False
            and action.get("remaining_amount") is None and not states.intersection(PAID | {"partial"})
            and all(_amount(r[k], currency) == 0 for r in (raw, payment, action,
                (raw.get("payment_actions") or {}).get("refund_action") or {}) for k in ("paid_amount", "remaining_amount")
                if r.get(k) is not None)
            and all(r.get("has_remaining_amount") in (None, False) for r in (raw, payment))):
        from order_engine.mapper import map_salla_order
        page = map_salla_order(raw).payment
        if page.collection_status == "unpaid" and page.paid_amount == 0 and _amount(page.remaining_amount, currency) == _amount(gross, currency):
            # Reuse the shared amount/currency/refund checks on the gross and
            # explicit unpaid state, after the page has resolved its null action.
            due = {**action, "remaining_amount": gross, "has_remaining_amount": True}
            normalized = {**raw, "remaining_amount": gross, "has_remaining_amount": True,
                          "payment": {**payment, "remaining_amount": gross, "has_remaining_amount": True},
                          "payment_actions": {**raw["payment_actions"], "remaining_action": due}}
            raw = normalized
    balance = provider_order_page_balance(raw, currency, gross)
    if not balance or (states & PAID and Decimal(balance["remaining"]) > 0):
        return None
    return currency, Decimal(balance["remaining"])


def _without_waybill(source, raw):
    # An incomplete or replaced waybill is still shipping evidence and must
    # never fall back to the unassigned-order path.
    if raw.get("shipments") or raw.get("shipment"):
        return False
    for row in (source, raw, raw.get("shipping") or {}, source.get("salla_shipping_current") or {},
                raw.get("salla_shipping_current") or {}):
        if (not isinstance(row, dict) or _inactive(row) or row.get("shipment") or row.get("superseded_shipment_ids")
                or row.get("source_kind") == "shipment" or any(row.get(k) for k in
                (*SHIPMENT_FIELDS, "shipment_id", "waybill", "awb", "awb_number", "tracking", "label_url",
                 "delivered_at", "store_delivery_delivered_at", "store_delivery_collection_amount"))):
            return False
        if row is not source and row is not raw and row.get("status") not in (None, ""):
            return False
    return True


async def _recorded_deliveries(db, owner):
    # A partially persisted native collection/earning must also exclude an
    # order. The preview does not need receipts, contact details, or amounts.
    records = []
    for name in ("store_delivery_collections", "store_delivery_driver_earnings"):
        projection = {"_id": 0, "user_id": 1, "order_id": 1, "order_number": 1, "assignment_id": 1}
        rows = await db[name].find({"user_id": owner}, projection).to_list(MAX_ROWS + 1)
        if len(rows) > MAX_ROWS:
            raise ValueError("operational_source_scope_too_large")
        if any(r.get("user_id") != owner for r in rows):
            raise ValueError("operational_source_owner_mismatch")
        records.extend(rows)
    return records


async def open_customer_cod(db, owner, raw_orders, data, state, started, stamp, ids, numbers):
    recorded = await _recorded_deliveries(db, owner)
    linked = [*recorded, *state.get("movements", []), *state.get("customer_returns", []),
              *(state.get("driver_payment_review_facts") or {}).values()]
    linked_ids = {_text(row.get("order_id")) for row in linked}
    linked_numbers = {_text(row.get("order_number")) for row in linked}
    linked_assignments = {_text(row.get("assignment_id")) for row in recorded}
    engine = state.get("engine", {})
    booked = set(engine.get("facts", {})) | {a.get("obligation_id") for row in state.get("movements", [])
                                             for a in row.get("allocations", [])}
    obs = engine.get("obligations", {})
    linked_ids.update(_text(row.get("order_id")) for key, row in engine.get("facts", {}).items()
                      if key.startswith(("capture:", "refund:")))
    assignments = defaultdict(list)
    assignment_ids = Counter()
    for a in data["store_delivery_assignments"]:
        assignments[("number", _text(a.get("order_number")))].append(a)
        assignments[("id", _text(a.get("order_id")))].append(a)
        assignment_ids[_text(a.get("id"))] += 1
    outer_numbers = Counter(_text(s.get("order_number")) for s in data["unified_orders"])
    outer_ids = Counter(_text(s.get("salla_order_id") or s.get("order_id")) for s in data["unified_orders"])
    totals = defaultdict(lambda: defaultdict(lambda: [0, Decimal(0)]))
    replaced = defaultdict(list)
    for source, raw in raw_orders:
        try:
            oid, num = _text(raw.get("id")), _text(raw.get("reference_id") or raw.get("order_number"))
            if (not oid or not num or ids[oid] != 1 or numbers[num] != 1 or outer_numbers[num] != 1
                    or _text(source.get("order_number")) != num or outer_ids[oid] > 1
                    or source.get("order_id") is not None and str(source["order_id"]) != oid
                    or not order_page_source_current(source, raw) or not _without_waybill(source, raw)
                    or num in linked_numbers or oid in linked_ids or any(prefix + oid in obs for prefix in
                        ("credit:cod:", "provider:", "credit:provider:"))
                    or any(prefix + oid in booked for prefix in ("cod:", "credit:cod:", "shipping:",
                        "delivery_event:", "cod_collection:", "cod_customer_confirmation:", "native_driver_earning:",
                        "provider:", "credit:provider:", "provider_order_balance:", "provider_split:"))):
                continue
            created = instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
            version = instant(raw.get("updated_at") or raw.get("date_updated") or created)
            saved_version = engine.get("order_versions", {}).get(oid)
            if not created <= version <= stamp or (saved_version and instant(saved_version) > version):
                continue
            old = created <= started
            if old and (version <= started or not (source.get("g47_salla_snapshot") or {}).get("source_updated_at")):
                continue
            rows = assignments[("number", num)]
            if rows != assignments[("id", oid)] or len(rows) > 1:
                continue
            a = rows[0] if rows else {}
            driver = _text(a.get("driver_id"))
            if rows and (not driver or not _text(a.get("id")) or assignment_ids[a["id"]] != 1
                    or a["id"] in linked_assignments or a.get("active") is not True
                    or a.get("status") not in {"assigned", "out_for_delivery"} or a.get("delivered_at")
                    or not created <= instant(a.get("assigned_at")) <= stamp):
                continue
            if (any(source.get(k) and source[k] != v for k, v in (("store_delivery_assignment_id", a.get("id")),
                     ("store_delivery_driver_id", driver), ("store_delivery_status", a.get("status"))))
                    or source.get("store_delivery_payment_status")):
                continue
            statuses = status_values(raw.get("status")) | status_values(raw.get("status_slug"))
            if not statuses or not (statuses | status_values(source.get("order_status"))
                    | status_values(source.get("order_status_slug"))) <= (WITH_DRIVER if rows else BEFORE_DISPATCH):
                continue
            payment = _page_due(raw)
            if not payment:
                continue
            currency, due = payment
            cod_key = "cod:" + oid
            metrics = engine.get("cod_reports", {}).get(cod_key, {})
            if (metrics.get("evidence_ids") or _amount(metrics.get("confirmed_custody", 0), currency)
                    or metrics.get("currency", currency) != currency or metrics.get("order_id", oid) != oid
                    or metrics.get("party_id") and metrics["party_id"] != driver):
                continue
            prior = obs.get(cod_key)
            if prior:
                if (not driver or prior.get("party_id") != driver or prior.get("party_type") != "store_driver"
                        or prior.get("currency") != currency or prior.get("direction") != "receivable"
                        or _amount(prior.get("confirmed"), currency) != 0):
                    continue
                replaced[currency].append(cod_key)
            if due:
                key = ("prestart_" if old else "current_") + ("assigned" if rows else "unassigned")
                totals[currency][key][0] += 1
                totals[currency][key][1] += due
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            continue
    return {ccy: {"rows": [{"id": key, "name": LABELS[key.split("_")[1]] + (
                    " من قبل بداية التشغيل" if key.startswith("prestart_") else " منذ بداية التشغيل"),
                "orders": count, "amount": format(value, ".2f")} for key, (count, value) in sorted(totals[ccy].items())],
            "replaced": sorted(replaced[ccy])} for ccy in totals.keys() | replaced.keys()}
