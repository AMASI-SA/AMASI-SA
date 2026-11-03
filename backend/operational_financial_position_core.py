"""Read-only presentation of the existing operational financial authority.

No source collection, persistence, accounting fallback, exchange rate, or new
obligation belongs here. Prepared cost exposure is a separate, optional input;
its stage buckets replace overlapping estimates rather than being added to
them. A recorded subtotal is not proof that an entire forecast is covered.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from operational_balance_service import report
from operational_delivery_report_coverage import delivery_coverage_diagnostics
from operational_provider_report_coverage import provider_evidence_coverage


ZERO = Decimal("0")
CENT = Decimal("0.01")
UNKNOWN = "غير متاح: "
COD_SCOPE = "لا يغطي المصدر تحصيل جميع الطلبات السابقة لبدء التشغيل؛ الإجمالي المتوقع غير مكتمل."
PARTIES = (
    ("supplier", "suppliers", "الموردون", "actual"),
    ("employee", "employees", "الموظفون", "actual"),
    ("external_person", "external", "الجهات الخارجية", "actual"),
    ("courier", "couriers", "شركات الشحن", "fulfillment"),
    ("store_driver", "drivers", "مناديب المتجر", "fulfillment"),
    ("provider", "providers", "منصات الدفع", "fulfillment"),
)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _money(value):
    value = _number(value)
    if value is None:
        return None
    return format((value or ZERO).quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def _sum(values):
    values = [_number(value) for value in values]
    return None if any(value is None for value in values) else sum(values, ZERO)


def _column(key, label, kind="money", role=None):
    return {"key": key, "label": label, "type": kind, **({"role": role} if role else {})}


def _table(identity, title, section, columns, rows, *, complete=True, notes=None, totals=None):
    calculated = {column["key"]: (
        _money(_sum(row.get(column["key"]) for row in rows)) if column["type"] == "money"
        else None if column["type"] == "count" else "الإجمالي"
    ) for column in columns}
    if totals:
        calculated.update(totals)
    return {"id": identity, "title": title, "section": section,
            "kind": "forecast" if section == "forecast" else "actual", "columns": columns,
            "rows": rows, "totals": calculated, "complete": complete, "notes": notes or []}


def _row(identity, label, amount, emphasis=None, reason=None):
    return {"id": identity, "label": label, "amount": _money(amount),
            **({"emphasis": emphasis} if emphasis else {}), **({"reason": reason} if reason else {})}


def _party_id(row):
    return ":".join(str(row.get(key) or "") for key in ("party_type", "party_id", "currency"))


def _name(row):
    return str(row.get("name") or row.get("party_name") or row.get("party_id") or "جهة غير محددة")


def _shipping_order(row):
    identity = str(row.get("id") or "")
    if not identity.startswith("shipping:"):
        return None
    identity = identity[len("shipping:"):]
    component = row.get("fee_component")
    if component and identity.endswith(":" + str(component)):
        identity = identity[:-(len(str(component)) + 1)]
    return identity or None


def _source_issue(row):
    return {**deepcopy(row), "code": str(row.get("code") or "source_incomplete"),
            "message": row.get("message") or UNKNOWN + "يوجد مصدر تشغيلي يحتاج إلى استكمال أو مراجعة."}


def _balance_origin(parties, obligations, complete):
    """Explain saved balances. Never feed this breakdown into debt totals."""
    kinds = {"provider", "courier", "store_driver"}
    index = {}
    for item in obligations:
        if item.get("party_type") in kinds and item.get("direction") in {"receivable", "payable"}:
            index.setdefault((item["party_type"], item.get("party_id"), item["direction"]), []).append(item)
    rows = []
    for party in parties:
        kind = party["party_type"]
        if kind not in kinds:
            continue
        for direction, label in (("receivable", "لنا"), ("payable", "علينا")):
            items = index.get((kind, party["party_id"], direction), [])
            remaining = party["outstanding_" + direction]
            if not items and _number(remaining) == ZERO:
                continue
            rows.append({"id": _party_id(party) + ":" + direction, "name": _name(party), "side": label,
                         "opening": _money(_sum(r.get("confirmed") for r in items if r["kind"] == "baseline")),
                         "posted": _money(_sum(r.get("confirmed") for r in items if r["kind"] != "baseline")),
                         "allocated": _money(_sum(r.get("settled") for r in items)), "remaining": remaining})
    cols = [_column("name", "الجهة", "text"), _column("side", "الاتجاه", "text"),
            _column("opening", "الافتتاحي"), _column("posted", "المؤكد المسجّل بعد الافتتاح"),
            _column("allocated", "السداد المخصّص"), _column("remaining", "الرصيد المسجّل")]
    breakdown = _table("balance_origin", "تفصيل أرصدة المنصات والشحن", "fulfillment", cols, rows, complete=complete,
                       notes=["تفصيل للأرصدة السابقة؛ لا يُضاف إلى إجمالي الديون. الصفر في المؤكد المسجّل لا ينفي وجود مبيعات أو شحنات تنتظر إثباتها.",
                              "السداد المخصّص يشمل الافتتاحي والقيود اللاحقة. قد يتضمن الرصيد المسجّل أيضًا تسويات أو دفعات غير مخصّصة."])
    breakdown["show_totals"] = False  # Opposite directions must not be added together.
    breakdown["totals"] = {column["key"]: None if column["type"] == "money" else "" for column in cols}
    return breakdown


def _actual_tables(parties, complete):
    tables = []
    columns = [_column("name", "الحساب", "text"), _column("available", "المتاح", role="neutral"),
               _column("unsettled", "بانتظار التسوية", role="receivable")]
    banks = [{"id": _party_id(row), "name": _name(row),
              "available": row["actual"] if row["party_type"] == "bank" else "0.00",
              "unsettled": row["outstanding_receivable"] if row["party_type"] == "pos_receivable" else "0.00"}
             for row in parties if row["party_type"] in {"bank", "pos_receivable"}]
    tables.append(_table("banks_network", "البنوك وأجهزة الشبكة", "actual", columns, banks, complete=complete,
                         notes=["تسويات أجهزة الشبكة حقوق لنا؛ لا تدخل ضمن النقد المتاح ولا تربط ببنك بالاسم."]))
    for kind, identity, title in (("cash", "cash", "الصناديق"),
                                  ("employee_custody", "employee_custody", "العهد لدى الموظفين")):
        key = "custody_remaining" if kind == "employee_custody" else "actual"
        rows = [{"id": _party_id(row), "name": _name(row), "available": row[key]}
                for row in parties if row["party_type"] == kind]
        tables.append(_table(identity, title, "actual", [_column("name", "الاسم", "text"),
                             _column("available", "العهدة المتبقية" if kind == "employee_custody" else "المتاح")],
                             rows, complete=complete))
    debt_columns = [_column("name", "الجهة", "text"), _column("receivable", "لنا", role="receivable"),
                    _column("payable", "علينا", role="payable")]
    for kind, identity, title, section in PARTIES:
        rows = [{"id": _party_id(row), "name": _name(row),
                 "receivable": row["outstanding_receivable"], "payable": row["outstanding_payable"]}
                for row in parties if row["party_type"] == kind]
        notes = []
        if kind in {"courier", "store_driver"}:
            notes = ["التحصيل المتبقي لنا والرسوم المتبقية علينا منفصلان؛ لا يضاف التحصيل مرة ثانية إلى النقد."]
        if kind == "provider":
            notes = ["المطالبات المثبتة ظاهرة قبل الرسوم التقديرية المتبقية؛ تخصم الرسوم المستقبلية مرة واحدة في التوقعات."]
        tables.append(_table(identity, title, section, debt_columns, rows, complete=complete, notes=notes))
    ads = [{"id": _party_id(row), "name": _name(row), "prepaid": row["ad_wallet_balance"],
            "payable": row["outstanding_payable"]} for row in parties if row["party_type"] == "ad_account"]
    tables.append(_table("ads", "حسابات الإعلانات", "actual", [_column("name", "الحساب", "text"),
                         _column("prepaid", "الرصيد المقدم المتبقي"), _column("payable", "علينا", role="payable")],
                         ads, complete=complete, notes=["الرصيد الإعلاني المقدم منفصل عن النقد والديون القابلة للتحصيل."]))
    covered = {kind for kind, *_ in PARTIES} | {"pos_receivable", "ad_account"}
    other = [{"id": _party_id(row), "name": _name(row), "receivable": row["outstanding_receivable"],
              "payable": row["outstanding_payable"]} for row in parties if row["party_type"] not in covered
             and any((_number(row[key]) or ZERO) != ZERO for key in ("outstanding_receivable", "outstanding_payable"))]
    if other:
        tables.append(_table("other_balances", "حقوق والتزامات أخرى مسجلة", "actual", debt_columns,
                             other, complete=complete,
                             notes=["هذه أرصدة موثقة في المصدر التشغيلي؛ لا تعني أن السحب أو التمويل مصروف ربح."]))
    order = ["banks_network", "cash", "suppliers", "employees", "external", "ads", "employee_custody",
             "other_balances", "couriers", "drivers", "providers"]
    return sorted(tables, key=lambda item: order.index(item["id"]))


def _cost_table(exposure, currency, identity, title, buckets, match):
    available = isinstance(exposure, dict) and exposure.get("status") == "preview"
    scoped = (exposure.get("bucket_completeness") or {}) if available else {}
    scoped_valid = isinstance(scoped, dict) and type(scoped.get("version")) is int and scoped["version"] == 1
    complete = available and (scoped[identity] if scoped_valid and type(scoped.get(identity)) is bool
                              else exposure.get("complete") is True)
    summaries = (exposure.get("currencies") or {}).get(currency, {}) if available else {}
    total = _sum(summaries.get(key, "0.00") for key in buckets) if available else None
    groups = [group for group in (exposure.get("groups") or []) if group.get("currency") == currency and match(group)] if available else []
    rows = []
    for group in groups:
        period = "قبل بدء التشغيل" if group.get("period") == "prestart" else "بعد بدء التشغيل"
        stage = {"awaiting_review": "انتظار المراجعة", "under_fulfillment": "قيد التنفيذ"}.get(group.get("stage"))
        rows.append({"id": ":".join(str(group.get(key) or "") for key in ("period", "kind", "stage", "party_id", "currency")),
                     "name": " — ".join(filter(None, (_name(group), stage, period))), "orders": group.get("orders"),
                     "quantity": None, "amount": _money(group.get("amount")),
                     "period": group.get("period"), "stage": group.get("stage")})
    if rows and _sum(row["amount"] for row in rows) != total:
        complete = False
    if not rows and total:
        # The aggregate remains source evidence when party detail is absent.
        rows.append({"id": identity + ":aggregate", "name": "إجمالي المصدر؛ تفصيل الجهات غير متاح",
                     "orders": None, "quantity": None, "amount": _money(total)})
        complete = False
    if total is None or any(row["amount"] is None for row in rows):
        complete = False
    notes = ["لا تجمع مراحل المنتجات مع إجمالي الموردين المتوقع؛ هذه المراحل تفصل مبالغ المصدر نفسه.",
             "عدد الطلبات خاص بكل مجموعة ولا يجمع بين المجموعات. كمية المنتجات الفعلية غير مثبتة في هذا المصدر."]
    if not complete:
        notes.insert(0, UNKNOWN + "مصدر تكاليف الطلبات المفتوحة غير جاهز أو غير مكتمل؛ المبالغ الظاهرة أجزاء موثقة فقط.")
    if total is not None and not complete:
        notes.append("مجموع التقديرات المتاحة من المصدر: " + _money(total) + " " + currency + ".")
    order_count = None
    if complete:
        if "distinct_order_counts" in exposure:
            order_count = exposure["distinct_order_counts"].get(currency, {}).get(identity, 0 if not rows else None)
        elif len(rows) == 1:
            order_count = rows[0]["orders"]
    return _table(identity, title, "forecast", [_column("name", "الجهة والفترة", "text"),
                  _column("orders", "طلبات المجموعة", "count"), _column("quantity", "الكمية", "count"),
                  _column("amount", "التكلفة المتوقعة", role="payable")], rows, complete=complete, notes=notes,
                  totals={"amount": _money(total) if complete else None,
                          "orders": order_count, "quantity": None})


def _cost_tables(exposure, currency, obligations):
    supplier = lambda group: group.get("kind") == "supplier" and group.get("period") in {"prestart", "poststart"}
    tables = [
        _cost_table(exposure, currency, "products_unreceived", "المنتجات التي لم تستلم",
                    ("prestart_supplier_unreceived", "poststart_supplier_unreceived"),
                    lambda group: supplier(group) and group.get("stage") in {"awaiting_review", "under_fulfillment", "unreceived"}),
        _cost_table(exposure, currency, "products_received_uninvoiced", "منتجات مستلمة دون فاتورة معتمدة",
                    ("prestart_supplier_received_uninvoiced", "poststart_supplier_received_uninvoiced"),
                    lambda group: supplier(group) and group.get("stage") == "received_without_approved_invoice"),
        _cost_table(exposure, currency, "shipping_expected", "شحن الطلبات غير المسلمة",
                    ("prestart_shipping_expected", "current_shipping_expected"),
                    lambda group: group.get("kind") in {"courier", "store_driver", "shipping"}
                    and group.get("period") in {"prestart", "poststart"} and group.get("stage") == "not_delivered"),
    ]
    shipping = tables[-1]
    shipping["notes"] = [note for note in shipping["notes"] if "مراحل المنتجات" not in note]
    shipping["notes"].append("الشحن المتوقع يستبعد الرسوم المثبتة؛ قد يتضمن السعر المجمع عمولة التحصيل ولا تضاف مرة ثانية.")
    if not isinstance(exposure, dict) or exposure.get("status") != "preview":
        shipping["rows"] = [{"id": row["id"], "name": _name(row), "orders": 1 if _shipping_order(row) else None,
                             "quantity": None, "amount": _money(row["expected"])} for row in obligations
                            if row.get("kind") == "shipping" and (_number(row.get("expected")) or ZERO) > ZERO]
        shipping["notes"].append("هذه البنود تقديرات التشغيل الحالية فقط؛ تغطية الطلبات السابقة لبدء التشغيل غير متاحة.")
        shipping["totals"].update(orders=None, amount=None)
    return tables


def _provider_tables(obligations, details, issues):
    fees, collections = [], []
    # A failed policy can prevent the obligation from being created at all.
    # Absence of a row is not evidence of zero fees or zero future collection.
    complete = not any(str(issue.get("source_id", "")).startswith("provider:")
                       or str(issue.get("code", "")).startswith("provider_") for issue in issues)
    for row in obligations:
        if row.get("kind") != "provider" or row.get("derived_credit"):
            continue
        projection = (details.get("provider_reports") or {}).get(row["id"])
        fee = pending = None
        if projection:
            net = _number(projection.get("net"))
            captured = _number(projection.get("confirmed_receivable"))
            fee = _number(projection.get("estimated_fees_remaining"))
            if net is not None and captured is not None and net >= ZERO and captured >= ZERO:
                pending = max(net - captured, ZERO)
            if fee is not None and fee < ZERO:
                fee = None
            # A retained fee on an entirely cancelled/refunded uncaptured sale
            # cannot be turned into zero by the report's net-zero denominator.
            if net == ZERO and row.get("direction") == "payable" and (_number(row.get("expected")) or ZERO) > ZERO and fee == ZERO:
                fee = None
        complete = complete and fee is not None and pending is not None and not row.get("incomplete")
        common = {"id": row["id"], "name": _name(row), "orders": 1}
        fees.append({**common, "amount": _money(fee)})
        collections.append({**common, "amount": _money(pending)})
    columns = [_column("name", "منصة الدفع / المطالبة", "text"), _column("orders", "الطلبات", "count"),
               _column("amount", "المبلغ المتوقع")]
    note = "الرسوم الفعلية للجزء المسوى استبدلت تقديراته؛ يعرض هنا تقدير الجزء المتبقي فقط."
    fee_table = _table("provider_fees_expected", "رسوم منصات الدفع المتوقعة", "forecast", columns, fees,
                       complete=complete, notes=[note], totals={"orders": len(fees)})
    collection_table = _table("provider_collections_expected", "تحصيل منصات الدفع غير المثبت بعد", "forecast",
                              columns, collections, complete=complete,
                              notes=["إجمالي التحصيل المتوقع قبل الرسوم، بعد استبعاد الالتقاط المثبت؛ تخصم الرسوم في بند مستقل."],
                              totals={"orders": len(collections)})
    if not complete:
        for table in (fee_table, collection_table):
            table["notes"].append(UNKNOWN + "تفاصيل بعض مطالبات منصات الدفع غير مكتملة.")
            table["totals"]["amount"] = None
    return fee_table, collection_table


def _cod_table(obligations, details):
    facts = details.get("facts") or {}
    metrics = details.get("cod_reports") or {}
    rows = []
    for obligation in obligations:
        if obligation.get("kind") != "cod" or not str(obligation.get("id", "")).startswith("cod:"):
            continue
        identity = obligation["id"]
        order = identity[len("cod:"):]
        if facts.get("shipping:" + order):
            continue
        source = metrics.get(identity, {})
        due, gross, collected = (_number(source.get(key)) for key in ("customer_outstanding", "gross", "collected"))
        balanced = all(value is not None and value >= ZERO for value in (due, gross, collected)) and due + collected == gross
        expected = _number(obligation.get("expected"))
        value = expected if expected is not None and expected > ZERO and obligation.get("direction") == "receivable" else None
        if obligation.get("party_type") == "store_driver" and due is not None and due > ZERO:
            # The native adapter deliberately keeps undelivered driver custody
            # at zero. A positive live shipping estimate proves an uncancelled
            # undelivered order without guessing status from a remaining sum.
            active_shipping = any(_shipping_order(row) == order and row.get("party_id") == obligation.get("party_id")
                                  and row.get("party_type") == "store_driver"
                                  and (_number(row.get("expected")) or ZERO) > ZERO
                                  and _number(row.get("confirmed")) == ZERO for row in obligations)
            value = due if balanced and active_shipping else None
        elif balanced and due == ZERO and expected == ZERO:
            continue
        if value is None and due is None and not source:
            continue
        rows.append({"id": identity, "name": _name(obligation), "orders": 1, "amount": _money(value),
                     "order_id": order, "known_due": _money(due),
                     **({"reason": UNKNOWN + "لا يكفي الرصيد المتبقي وحده لإثبات طلب نشط غير مسلم."} if value is None else {})})
    known = _sum(row["amount"] for row in rows if row["amount"] is not None)
    return _table("cod_expected", "تحصيل متوقع من العملاء للطلبات غير المسلمة", "forecast",
                  [_column("name", "مسار التحصيل / المطالبة", "text"), _column("orders", "الطلبات", "count"),
                   _column("amount", "تحصيل متوقع من العميل", role="receivable")], rows, complete=False,
                  totals={"amount": None, "orders": len(rows)},
                  notes=[UNKNOWN + COD_SCOPE, "المبالغ المتوقعة ليست عهدة نقدية لدى المندوب ولا تضاف إلى رصيد شركة الشحن المثبت.",
                         "مجموع التحصيل المتوقع المثبت في الصفوف المتاحة: " + _money(known) + "."])


def _deliveries(obligations, details, currency, names, source_issues, *, coverage_note=None):
    facts = details.get("facts") or {}
    metrics = {key: value for key, value in (details.get("cod_reports") or {}).items() if value.get("currency") == currency}
    cod_obligations = {row["id"]: row for row in obligations if row.get("kind") == "cod"}
    for key, obligation in cod_obligations.items():
        if key not in metrics and key.startswith("cod:") and (_number(obligation.get("confirmed")) or ZERO) > ZERO:
            # Old confirmed obligations can precede rich collection metadata.
            # Retain the recorded responsibility without guessing its method.
            metrics[key] = {"order_id": key[len("cod:"):], "currency": currency,
                            "party_id": obligation["party_id"], "party_type": obligation["party_type"],
                            "confirmed_custody": obligation["confirmed"], "settled": obligation.get("settled"),
                            "outstanding": obligation.get("outstanding"), "legacy_obligation_only": True}
    shipping = {}
    for row in obligations:
        order = _shipping_order(row)
        if order and ((_number(row.get("confirmed")) or ZERO) > ZERO or facts.get("shipping:" + order)):
            shipping.setdefault(order, []).append(row)
    order_ids = set(shipping) | {str(value.get("order_id") or key.removeprefix("cod:")) for key, value in metrics.items()}
    rows, complete = [], True
    missing_fee_orders = {str(issue.get("source_id", "")).removeprefix("shipping:") for issue in source_issues
                          if str(issue.get("code", "")).startswith("shipping_")
                          or issue.get("component") in {"shipping_fee", "base_shipping", "cod_commission"}}
    for order in sorted(order_ids):
        key = "cod:" + order
        source = metrics.get(key)
        frozen = facts.get("shipping:" + order) or {}
        fee_rows = shipping.get(order, [])
        obligation = cod_obligations.get(key) or (fee_rows[0] if fee_rows else {})
        party_id = frozen.get("id") or obligation.get("party_id") or (source or {}).get("party_id")
        party_type = frozen.get("party_type") or obligation.get("party_type") or (source or {}).get("party_type")
        original = facts.get("cod_collection:" + order) or {}
        proof = facts.get("cod_customer_confirmation:" + order) or original
        evidence = proof.get("evidence") or {}
        proof_valid = bool(original) and original.get("party_id") == party_id and evidence.get("evidence_complete") is True
        method = (source or {}).get("payment_method")
        destination, status = "unavailable", "unavailable"
        store_collection = custody = settled = outstanding = None
        if source is None:
            destination = status = "not_applicable"
        elif party_type == "store_driver":
            if method == "cash":
                destination = "driver_custody"
                status = "confirmed" if proof_valid and evidence.get("collection_confirmed") is True else "not_confirmed"
            elif method in {"bank_transfer", "card_terminal"}:
                destination = "store_bank_account_unknown" if method == "bank_transfer" else "store_pos_account_unknown"
                status = ("confirmed" if proof_valid and evidence.get("collection_confirmed") is True else
                          "pending_review" if proof_valid and evidence.get("collection_confirmed") is False else "unavailable")
                if status == "confirmed":
                    store_collection = _money(source.get("collection_amount"))
                # Receiver identity is not in this operational report. Native
                # approval is display evidence, never a second cash posting.
                complete = False
            else:
                status = "not_confirmed"
            if proof_valid:
                custody = _money(source.get("confirmed_custody"))
        elif party_type == "courier":
            destination = "courier_receivable"
            status = "confirmed" if proof_valid else "not_confirmed"
        if source is not None and proof_valid:
            settled, outstanding = _money(source.get("settled")), _money(source.get("outstanding"))
        elif source is not None and source.get("legacy_obligation_only"):
            settled, outstanding = _money(source.get("settled")), _money(source.get("outstanding"))
        fees = {field: _money(_sum(row.get(key) for row in fee_rows)) if fee_rows else None
                for field, key in (("shipping_confirmed", "confirmed"), ("shipping_settled", "settled"),
                                   ("shipping_outstanding", "outstanding"))}
        if order in missing_fee_orders:
            fees = {key: None for key in fees}
        if any(value is None for value in fees.values()) or status in {"unavailable", "not_confirmed", "pending_review"}:
            complete = False
        rows.append({"id": "delivery:" + order, "order_id": order, "order_number": None,
                     "party_id": party_id, "party_type": party_type,
                     "party_name": names.get((party_type, party_id), party_id),
                     "delivered_at": frozen.get("delivered_at"), "payment_method": method,
                     "collection_destination": destination, "collection_status": status, **fees,
                     "collection_amount": _money((source or {}).get("collection_amount")),
                     "driver_custody": custody, "collection_settled": settled,
                     "collection_outstanding": outstanding, "store_collection": store_collection,
                     "customer_outstanding": _money((source or {}).get("customer_outstanding"))})
    return {"complete": complete, "rows": rows, "notes": [
        "الرسوم من التزامات الشحن التشغيلية فقط؛ لا تضاف أرباح تطبيق المندوب مرة أخرى.",
        "عهدة المندوب هي المسؤولية المثبتة في المصدر وليست إثباتًا لمقدار النقد الفعلي في يده.",
        "التحويل البنكي والشبكة لا ينشئان عهدة نقدية للمندوب. لا يثبت اعتماد التحصيل من حالة تسليم الطلب وحدها.",
        "غير متاح: هوية البنك أو جهاز الشبكة المستقبل غير مثبتة في هذه اللقطة؛ اعتماد المستند لا يثبت قيدًا في رصيد البنك التشغيلي.",
        coverage_note or "التفاصيل تخص الطلبات الممثلة في لقطة التشغيل فقط؛ تغطية التسليم بعد بداية التشغيل غير مثبتة في هذه اللقطة.",
        "المبلغ المتبقي على العميل لا يضاف مرة ثانية إلى عهدة شركة الشحن أو المندوب.",
    ]}


def _profit_rows():
    components = (
        ("net_sales", "صافي المبيعات دون ضريبة القيمة المضافة", "مجموعة مبيعات الفترة بعد الخصومات والمرتجعات والضريبة غير مثبتة هنا."),
        ("cost_of_goods", "تكلفة المنتجات المباعة", "فواتير الموردين وتقديرات الطلبات المفتوحة لا تثبت تكلفة مجموعة المبيعات نفسها."),
        ("shipping", "تكلفة الشحن", "تكلفة الشحن المرتبطة بمجموعة المبيعات غير مكتملة."),
        ("payment_fees", "رسوم الدفع والتحصيل", "تغطية الرسوم لنفس مجموعة المبيعات غير مكتملة."),
        ("advertising", "الإعلانات المستهلكة", "إنفاق الإعلانات للفترة غير معتمد كعنصر كامل في ربح هذا التقرير."),
        ("salaries", "الرواتب", "تغطية الرواتب لنفس فترة الربح لم تثبت في عقد التقرير."),
        ("operating_expenses", "المصاريف التشغيلية", "الحركات المدفوعة وحدها لا تثبت المصروف المستحق للفترة."),
        ("operating_profit", "الربح التشغيلي المتوقع", "بعض عناصر الإيراد والتكلفة غير مكتملة."),
        ("financing", "تكاليف التمويل", "لا يوجد مصدر مكتمل لتكلفة التمويل للفترة."),
        ("tax", "الزكاة والضريبة", "الضريبة الواجبة للفترة غير مثبتة؛ لا تفترض صفرًا."),
        ("net_profit", "صافي الربح المتوقع", "لا يحسب الربح النهائي قبل اكتمال عناصره."),
    )
    return [_row(identity, label, None, "total" if identity in {"operating_profit", "net_profit"} else None,
                 UNKNOWN + reason) for identity, label, reason in components]


def build_financial_position(state, *, cost_exposure=None, generated_at=None):
    """Build JSON v1 from one saved operational snapshot without side effects."""
    projection = report(state)
    result = {"schema_version": 1, "authority": "operational_balance_report", "status": projection["status"],
              "started_at": projection["started_at"], "as_of": projection["as_of"],
              "generated_at": generated_at or projection["as_of"], "currency_order": [], "currencies": {},
              "issues": [_source_issue(row) for row in projection["issues"]], "dependencies": []}
    if projection["status"] != "active":
        return result
    delivery_coverage = delivery_coverage_diagnostics(state, projection)
    cost_ready = isinstance(cost_exposure, dict) and cost_exposure.get("status") == "preview"
    result["dependencies"] = [
        {"code": "open_cost_exposure", "status": "ready" if cost_ready and cost_exposure.get("complete") is True else "incomplete" if cost_ready else "unavailable",
         "message": "تقديرات مراحل الطلبات المفتوحة من مصدرها التشغيلي." if cost_ready else UNKNOWN + "مصدر تكاليف الطلبات المفتوحة غير متاح بعد."},
        {"code": "complete_collection_forecast", "status": "incomplete", "message": UNKNOWN + COD_SCOPE},
        {"code": "profit_period_coverage", "status": "unavailable", "message": UNKNOWN + "مصادر الربح الكامل لنفس فترة المبيعات لم تكتمل."},
        *delivery_coverage["dependencies"],
        *provider_evidence_coverage(projection),
    ]
    if cost_ready:
        result["issues"].extend(_source_issue(row) for row in cost_exposure.get("issues", []))
    currencies = {row["currency"] for row in projection["parties"]}
    if cost_ready:
        currencies.update((cost_exposure.get("currencies") or {}).keys())
    result["currency_order"] = sorted(currencies, key=lambda currency: (currency != "SAR", currency))
    for currency in result["currency_order"]:
        parties = sorted((row for row in projection["parties"] if row["currency"] == currency), key=_party_id)
        obligations = [row for row in projection["obligations"] if row.get("currency") == currency]
        actual_complete = bool(projection["as_of"]) and not any(row.get("currency") in {None, currency} for row in projection["issues"])
        tables = _actual_tables(parties, actual_complete)
        tables.append(_balance_origin(parties, obligations, actual_complete))
        names = {(row["party_type"], row["party_id"]): _name(row) for row in parties}
        debt_rows = []
        categories = [("pos_receivable", "banks_network", "تسويات أجهزة الشبكة"),
                      *((kind, identity, title) for kind, identity, title, _ in PARTIES), ("ad_account", "ads", "حسابات الإعلانات")]
        for kind, identity, title in categories:
            selected = [row for row in parties if row["party_type"] == kind]
            debt_rows.append({"id": identity, "name": title,
                              "receivable": "0.00" if kind == "ad_account" else _money(_sum(row["outstanding_receivable"] for row in selected)),
                              "payable": _money(_sum(row["outstanding_payable"] for row in selected))})
        other = next((table for table in tables if table["id"] == "other_balances"), None)
        if other:
            debt_rows.append({"id": "other_balances", "name": other["title"],
                              **{key: other["totals"][key] for key in ("receivable", "payable")}})
        liquidity = _sum(row["actual"] for row in parties if row["party_type"] in {"bank", "cash"})
        receivable, payable = (_sum(row[key] for row in debt_rows) for key in ("receivable", "payable"))
        prepaid = _sum(row["ad_wallet_balance"] for row in parties if row["party_type"] == "ad_account")
        custody = _sum(row["custody_remaining"] for row in parties if row["party_type"] == "employee_custody")
        position = liquidity + receivable - payable if actual_complete else None
        displayed = position + prepaid + custody if position is not None else None
        unavailable_actual = None if actual_complete else UNKNOWN + "المصادر الحالية تحتاج إلى مراجعة؛ المجاميع الظاهرة تخص الأرصدة المسجلة فقط."
        current_rows = [
            _row("liquidity", "النقد المتاح في البنوك والصناديق", liquidity),
            _row("debts_receivable", "الحقوق المسجلة لنا", receivable, "positive"),
            _row("debts_payable", "الالتزامات المسجلة علينا", payable, "negative"),
            _row("monetary_position", "المركز النقدي والديون الحالي", position, "total", unavailable_actual),
            _row("prepaid_ad_credit", "الرصيد الإعلاني المقدم", prepaid),
            _row("employee_custody", "العهد المتبقية لدى الموظفين", custody),
            _row("displayed_position", "المركز مع الأرصدة المقدمة والعهد المعروضة", displayed, "total", unavailable_actual),
        ]
        costs = _cost_tables(cost_exposure, currency, obligations)
        currency_issues = [row for row in projection["issues"] if row.get("currency") in {None, currency}]
        fees, provider_future = _provider_tables(obligations, projection["details"], currency_issues)
        cod = _cod_table(obligations, projection["details"])
        tables.extend([costs[-1], *costs[:-1], cod, fees, provider_future])
        forecast_rows = [_row(table["id"], table["title"], table["totals"]["amount"],
                              "positive" if table["id"] in {"cod_expected", "provider_collections_expected"} else "negative",
                              None if table["complete"] else UNKNOWN + "تغطية هذا البند غير مكتملة.")
                         for table in (cod, provider_future, *costs, fees)]
        forecast_rows.extend([
            _row("forecast_change", "صافي التغير المتوقع", None, "total", UNKNOWN + COD_SCOPE),
            _row("forecast_monetary_position", "المركز النقدي والديون المتوقع", None, "total", UNKNOWN + "التكاليف والتحصيل المستقبليان غير مكتملين."),
        ])
        result["currencies"][currency] = {
            "tables": tables, "debt_rows": debt_rows,
            "debt_totals": {"receivable": _money(receivable), "payable": _money(payable)},
            "current_rows": current_rows, "forecast_rows": forecast_rows, "profit_rows": _profit_rows(),
            "metrics": {"liquidity": _money(liquidity), "debts_receivable": _money(receivable), "debts_payable": _money(payable),
                        "monetary_position": _money(position), "prepaid_ad_credit": _money(prepaid), "employee_custody": _money(custody),
                        "displayed_position": _money(displayed), "forecast_change": None, "forecast_monetary_position": None,
                        "operating_profit": None, "net_profit": None},
            "completeness": {"actual": actual_complete, "forecast": False, "profit": False},
            "notes": ["كل المبالغ بهذه العملة؛ لا تحويل ولا جمع بين العملات.",
                      "المركز النقدي والديون = النقد المتاح + الحقوق القابلة للتحصيل − الالتزامات المثبتة.",
                      "الرصيد الإعلاني المقدم والعهد يعرضان منفصلين ثم يضافان مرة واحدة إلى مجموع الأرصدة المعروضة.",
                      "التوقعات تغطي البنود المبينة فقط؛ لا تفترض مصروفات مستقبلية أو رواتب أو إعلانات أو ضرائب غير موثقة.",
                      "الربح لا يستنتج من الأرصدة أو التمويل أو سحب المالك أو مجرد اعتماد فاتورة مورد."],
            "deliveries": _deliveries(obligations, projection["details"], currency, names, projection["issues"],
                coverage_note=(delivery_coverage["event_note"] if currency == "SAR" else
                    "تغطية تسليم الطلبات السابقة لبدء التشغيل بهذه العملة غير مثبتة؛ لا تحويل أو إعادة تسمية لمبالغ الشحن.")),
        }
    return result
