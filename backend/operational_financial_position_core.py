"""Read-only presentation of the existing operational financial authority.

No source collection, persistence, accounting fallback, exchange rate, or new
obligation belongs here. Prepared cost exposure is a separate, optional input;
its stage buckets replace overlapping estimates rather than being added to
them. A recorded subtotal is not proof that an entire forecast is covered.
"""
from operational_financial_activity_runtime import preparation_checkpoint
from collections import Counter
from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from operational_balance_service import report
from operational_prestart_cod_forecast import snapshot_cod_preview
from operational_delivery_report_coverage import delivery_coverage_diagnostics
from operational_profit_projection import project_profit, valid_snapshot
from operational_forecast_projection import cod_forecast_coverage, project_forecast
from operational_provider_report_coverage import provider_evidence_coverage
from operational_balance_completeness import GLOBAL_FAILURES, apply_table_coverage, annotate_report_coverage


ZERO = Decimal("0")
CENT = Decimal("0.01")
UNKNOWN = "غير متاح: "
COD_SCOPE = "لا يغطي المصدر تحصيل جميع الطلبات السابقة لبدء التشغيل؛ الإجمالي المتوقع غير مكتمل."
PARTIES = (
    ("supplier", "suppliers", "الموردون", "actual"),
    ("employee", "employees", "الموظفون", "actual"),
    ("external_person", "external", "الجهات الخارجية", "actual"),
    ("customer_wallet", "customer_wallets", "محافظ العملاء", "actual"),
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
    preparation_checkpoint()
    return format((value or ZERO).quantize(CENT, rounding=ROUND_HALF_UP), ".2f")


def _sum(values):
    values = [_number(value) for value in values]
    preparation_checkpoint()
    return None if any(value is None for value in values) else sum(values, ZERO)


def _known_sum(values):
    values = list(values)
    known = [value for value in (_number(value) for value in values) if value is not None]
    preparation_checkpoint()
    return sum(known, ZERO) if known or not values else None


def _column(key, label, kind="money", role=None):
    preparation_checkpoint()
    return {"key": key, "label": label, "type": kind, **({"role": role} if role else {})}


def _table(identity, title, section, columns, rows, *, complete=True, notes=None, totals=None):
    calculated = {column["key"]: (
        _money(_sum(row.get(column["key"]) for row in rows)) if column["type"] == "money"
        else None if column["type"] == "count" else "الإجمالي"
    ) for column in columns}
    if totals:
        calculated.update(totals)
    preparation_checkpoint()
    return {"id": identity, "title": title, "section": section,
            "kind": "forecast" if section == "forecast" else "actual", "columns": deepcopy(columns),
            "rows": rows, "totals": calculated, "complete": complete, "notes": notes or []}


def _row(identity, label, amount, emphasis=None, reason=None):
    preparation_checkpoint()
    return {"id": identity, "label": label, "amount": _money(amount),
            **({"emphasis": emphasis} if emphasis else {}), **({"reason": reason} if reason else {})}


def _party_id(row):
    preparation_checkpoint()
    return ":".join(str(row.get(key) or "") for key in ("party_type", "party_id", "currency"))


def _name(row):
    preparation_checkpoint()
    return str(row.get("name") or row.get("party_name") or row.get("party_id") or "جهة غير محددة")


def _shipping_order(row):
    identity = str(row.get("id") or "")
    prefix = next((prefix for prefix in ("shipping:", "shipping-prepaid:", "shipping-completed:") if identity.startswith(prefix)), None)
    if prefix is None:
        return None
    if row.get("order_id"):
        return str(row["order_id"])
    identity = identity[len(prefix):]
    component = row.get("fee_component")
    if component and identity.endswith(":" + str(component)):
        identity = identity[:-(len(str(component)) + 1)]
    preparation_checkpoint()
    return identity or None


def _source_issue(row):
    preparation_checkpoint()
    return {**deepcopy(row), "code": str(row.get("code") or "source_incomplete"),
            "message": row.get("message") or UNKNOWN + "يوجد مصدر تشغيلي يحتاج إلى استكمال أو مراجعة."}


def _balance_origin(parties, obligations, complete):
    """Explain saved balances. Never feed this breakdown into debt totals."""
    kinds = {"provider", "courier", "store_driver"}
    index = {}
    for item in obligations:
        preparation_checkpoint()
        if item.get("party_type") in kinds and item.get("direction") in {"receivable", "payable"}:
            index.setdefault((item["party_type"], item.get("party_id"), item["direction"]), []).append(item)
    rows = []
    for party in parties:
        preparation_checkpoint()
        kind = party["party_type"]
        if kind not in kinds:
            continue
        for direction, label in (("receivable", "لنا"), ("payable", "علينا")):
            preparation_checkpoint()
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
    preparation_checkpoint()
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
        preparation_checkpoint()
        key = "custody_remaining" if kind == "employee_custody" else "actual"
        rows = [{"id": _party_id(row), "name": _name(row), "available": row[key]}
                for row in parties if row["party_type"] == kind]
        tables.append(_table(identity, title, "actual", [_column("name", "الاسم", "text"),
                             _column("available", "العهدة المتبقية" if kind == "employee_custody" else "المتاح")],
                             rows, complete=complete))
    debt_columns = [_column("name", "الجهة", "text"), _column("receivable", "لنا", role="receivable"),
                    _column("payable", "علينا", role="payable")]
    for kind, identity, title, section in PARTIES:
        preparation_checkpoint()
        if kind in {"provider", "customer_wallet"}:
            continue
        rows = [{"id": _party_id(row), "name": _name(row),
                 "receivable": row["outstanding_receivable"], "payable": row["outstanding_payable"]}
                for row in parties if row["party_type"] == kind]
        notes = []
        if kind == "courier":
            notes = ["لنا هو رصيد ذمة الشركة بعد السداد المخصص، وعلينا أجرة الشحن المتبقية. يلزم كشف الشركة لإثبات تحصيلها من العميل."]
        if kind == "store_driver":
            notes = ["التحصيل المتبقي لنا والرسوم المتبقية علينا منفصلان؛ لا يضاف التحصيل مرة ثانية إلى النقد."]
        tables.append(_table(identity, title, section, debt_columns, rows, complete=complete, notes=notes))
    converted_ads = []
    for row in parties:
        preparation_checkpoint()
        valuation = row.get("ad_sar_valuation")
        if row["party_type"] != "ad_account" or not valuation:
            continue
        native = row["native_balance"]
        converted_ads.append({"id": _party_id(row), "name": _name(row),
                              "amount_usd": _money(_number(native["outstanding_payable"]) - _number(native["ad_wallet_balance"])),
                              "amount_sar": _money(_number(valuation["amount_sar"]) - _number(row["ad_wallet_balance"])),
                              "bank_commission": valuation["bank_commission"],
                              "bank_commission_vat": valuation["bank_commission_vat"],
                              "receivable": row["ad_wallet_balance"], "payable": row["outstanding_payable"]})
    if converted_ads:
        tables.append(_table("ads_currency_conversion", "تحويل أرصدة الإعلانات الدولارية إلى الريال", "actual", [
            _column("name", "الحساب الإعلاني", "text"), _column("amount_usd", "المبلغ بالدولار"),
            _column("amount_sar", "المبلغ بالريال"), _column("bank_commission", "العمولة البنكية"),
            _column("bank_commission_vat", "ضريبة العمولة"), _column("receivable", "لنا بالريال", role="receivable"),
            _column("payable", "علينا بالريال شامل الرسوم", role="payable")], converted_ads, complete=complete,
            notes=["سعر الصرف والعمولة من الإعداد المحفوظ لكل حساب. ضريبة العمولة 15٪ من العمولة البنكية على المبلغ المتبقي علينا.",
                   "المبلغ الموجب يمثل المستحق علينا، والسالب يمثل الرصيد الإعلاني المقدم لنا. تُعرض قيمة الرصيد المقدم أيضًا ضمن أرصدة الإعلانات."]))
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
    order = ["banks_network", "cash", "suppliers", "employees", "external", "ads_currency_conversion", "ads", "employee_custody",
             "other_balances", "couriers", "drivers", "providers"]
    preparation_checkpoint()
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
        preparation_checkpoint()
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
    preparation_checkpoint()
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
    preparation_checkpoint()
    return tables


def _provider_table(rows, issues):
    columns = [_column("name", "المنصة", "text"), _column("orders", "طلبات مدفوعة مثبتة", "count"),
               _column("gross", "المدفوع المثبت"), _column("fees", "العمولات المحتسبة مع ضريبتها"),
               _column("refunded", "الاستردادات"), _column("net", "صافي محتسب متبقٍ لنا", role="receivable"),
               _column("pending_orders", "طلبات تحتاج مراجعة", "exceptions")]
    complete = all(row["complete"] for row in rows) and not any(
        str(issue.get("source_id", "")).startswith("provider:") or str(issue.get("code", "")).startswith("provider_")
        for issue in issues)
    displayed = [{"id": row["provider"], **row,
                  "orders": row.get("paid_orders", row.get("confirmed_orders", row["orders"])),
                  **{field: row.get("known_" + field, row.get(field)) for field in ("gross", "fees", "net")}}
                 for row in rows]
    result = _table("providers", "منصات الدفع", "fulfillment", columns, displayed,
                  complete=complete,
                  totals={"orders": (sum(row["orders"] for row in displayed if row["orders"] is not None)
                                     if not displayed or any(row["orders"] is not None for row in displayed) else None),
                          "pending_orders": len({item["order_id"] for row in rows for item in row.get("pending_order_details", [])})
                              + sum(row.get("unidentified_pending_orders", 0) for row in rows)},
                  notes=["الصافي يشمل الافتتاحي المدخل والتسويات المثبتة. تقدير العمولة يخص المدفوع بعد البداية فقط، والعمولة الفعلية تحل محل تقدير الجزء المسوّى.",
                         "الصافي المحتسب يجمع المبالغ التي اكتمل مدفوعها ورسومها؛ الطلبات المعلقة لا تلغي هذا الجزء المثبت. اضغط عدد الطلبات لمراجعة الأرقام والأسباب.",
                         "المدفوع للمنصة مستقل عن وصول حوالتها إلى البنك؛ التحويل البنكي يثبت بتسوية منفصلة."])
    if not complete and not rows:
        result["totals"].update(gross=None, fees=None, refunded=None, net=None)
    elif not complete:
        result["totals"].update({key: _money(_known_sum(row.get(key) for row in displayed))
                                for key in ("gross", "fees", "refunded", "net")})
        result["totals_basis"] = "known_subtotals"
    preparation_checkpoint()
    return result


def _fully_paid_provider_claim(row, facts):
    if row.get("incomplete") or row.get("party_type") != "provider":
        return False
    native = row.get("native_balance") or row
    confirmed = _number(native.get("confirmed"))
    if confirmed is None or confirmed <= ZERO:
        return False
    identity, provider = str(row["id"]), row["party_id"]
    order = identity.removeprefix("provider:")
    page = facts.get("provider_order_balance:" + order, {})
    if ((page.get("order_id"), page.get("provider"), page.get("currency")) == (order, provider, native["currency"])
            and _number(page.get("paid")) == _number(page.get("gross")) == confirmed
            and _number(page.get("remaining")) == ZERO):
        return True
    if not order.endswith(":" + provider):
        return False
    order = order.removesuffix(":" + provider)
    fact = facts.get("provider_split:" + order, {})
    split = fact.get("evidence", {})
    if ((split.get("order_id"), split.get("currency")) != (order, native["currency"])
            or provider not in fact.get("booked_providers", [])):
        return False
    tenders = split.get("tenders", [])
    preparation_checkpoint()
    return (bool(tenders) and all((_number(leg.get("amount")) or ZERO) > ZERO for leg in tenders)
            and _sum(leg["amount"] for leg in tenders) == _number(split.get("gross"))
            and _sum(leg["amount"] for leg in tenders if leg.get("provider") == provider) == confirmed)


def _provider_collection_table(obligations, details, issues):
    collections = []
    # A failed policy can prevent the obligation from being created at all.
    # Absence of a row is not evidence of zero fees or zero future collection.
    complete = not any(str(issue.get("source_id", "")).startswith("provider:")
                       or str(issue.get("code", "")).startswith("provider_") for issue in issues)
    for row in obligations:
        preparation_checkpoint()
        if row.get("kind") != "provider" or row.get("derived_credit"):
            continue
        if _fully_paid_provider_claim(row, details.get("facts") or {}):
            continue
        projection = (details.get("provider_reports") or {}).get(row["id"])
        pending = None
        if projection:
            net = _number(projection.get("net"))
            captured = _number(projection.get("confirmed_receivable"))
            fee = _number(projection.get("estimated_fees_original", projection.get("estimated_fees")))
            if net is not None and captured is not None and net >= ZERO and captured >= ZERO:
                if captured >= net:
                    pending = ZERO
                elif captured == ZERO and fee is not None:
                    pending = max(net - fee, ZERO)
        if pending == ZERO and not row.get("incomplete"):
            continue
        complete = complete and pending is not None and not row.get("incomplete")
        common = {"id": row["id"], "name": _name(row), "orders": 1}
        collections.append({**common, "amount": _money(pending)})
    columns = [_column("name", "منصة الدفع / المطالبة", "text"), _column("orders", "الطلبات", "count"),
               _column("amount", "المبلغ المتوقع")]
    collection_table = _table("provider_collections_expected", "تحصيل منصات الدفع غير المثبت بعد", "forecast",
                              columns, collections, complete=complete,
                              notes=["صافي التحصيل المستقبلي بعد رسومه، مع استبعاد المدفوع المثبت. عمولات المدفوع المثبت مخصومة في جدول المنصات."],
                              totals={"orders": len(collections)})
    if not complete:
        collection_table["notes"].append(UNKNOWN + "تفاصيل بعض مطالبات منصات الدفع غير مكتملة.")
        collection_table["totals"]["amount"] = None
    preparation_checkpoint()
    return collection_table


def _cod_table(obligations, details, prestart=None, customer=None, *, coverage=None):
    facts = details.get("facts") or {}
    metrics = details.get("cod_reports") or {}
    customer = customer or {}
    replaced = set(customer.get("replaced", []))
    rows = []
    for obligation in obligations:
        preparation_checkpoint()
        if obligation.get("kind") != "cod" or not str(obligation.get("id", "")).startswith("cod:"):
            continue
        identity = obligation["id"]
        order = identity[len("cod:"):]
        if identity in replaced or facts.get("shipping:" + order):
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
    if prestart:
        rows.append({"id":"prestart_customer_cod", "name":"طلبات سابقة لبدء التشغيل ببوليصة خارجية جارية",
                     "orders":prestart["orders"], "amount":prestart["amount"]})
    rows.extend(customer.get("rows", []))
    known = [row["amount"] for row in rows if row["amount"] is not None]
    complete = bool(coverage and coverage.get("complete")) and all(row["amount"] is not None for row in rows)
    result = _table("cod_expected", "تحصيل متوقع من العملاء للطلبات غير المسلمة", "forecast",
                  [_column("name", "مسار التحصيل / المطالبة", "text"), _column("orders", "الطلبات", "count"),
                   _column("amount", "تحصيل متوقع من العميل", role="receivable")], rows, complete=complete,
                  totals={"amount": _money(_sum(known)) if complete else None,
                          "orders": sum(row.get("orders", 0) for row in rows) if complete else None},
                  notes=([UNKNOWN + COD_SCOPE] if not complete else []) + [
                         "تظهر مبالغ الطلبات المفتوحة المؤيدة بصفحة الطلب قبل الإسناد وبعده. تتطلب الطلبات السابقة دليلًا مؤرخًا بعد بداية التشغيل.",
                         "المبالغ المتوقعة ليست عهدة نقدية لدى المندوب ولا تضاف إلى رصيد شركة الشحن المثبت.",
                         ("مجموع التحصيل المتوقع في الصفوف المتاحة " + _money(_sum(known)) + ".") if known
                         else "لم يثبت مبلغ تحصيل متوقع في الصفوف المتاحة."])
    result["coverage"] = coverage or {"complete": False}
    result["known_totals"] = {"amount": _money(_sum(known)) if known or complete else None}
    preparation_checkpoint()
    return result


def _deliveries(obligations, details, currency, names, source_issues, *, coverage_note=None):
    facts = details.get("facts") or {}
    metrics = {key: value for key, value in (details.get("cod_reports") or {}).items() if value.get("currency") == currency}
    cod_obligations = {row["id"]: row for row in obligations if row.get("kind") == "cod"}
    for key, obligation in cod_obligations.items():
        preparation_checkpoint()
        if key not in metrics and key.startswith("cod:") and (_number(obligation.get("confirmed")) or ZERO) > ZERO:
            # Old confirmed obligations can precede rich collection metadata.
            # Retain the recorded responsibility without guessing its method.
            metrics[key] = {"order_id": key[len("cod:"):], "currency": currency,
                            "party_id": obligation["party_id"], "party_type": obligation["party_type"],
                            "confirmed_custody": obligation["confirmed"], "settled": obligation.get("settled"),
                            "outstanding": obligation.get("outstanding"), "legacy_obligation_only": True}
    shipping = {}
    for row in obligations:
        preparation_checkpoint()
        order = _shipping_order(row)
        if order and ((_number(row.get("confirmed")) or ZERO) > ZERO or facts.get("shipping:" + order)):
            shipping.setdefault((order, row.get("party_type"), row.get("party_id")), []).append(row)
    for key, proof in facts.items():
        preparation_checkpoint()
        if (not key.startswith("delivery_event:") or not isinstance(proof, dict)
                or proof.get("schema") != "operational_delivery_status_v1"):
            continue
        order = str(proof.get("order_id") or "")
        frozen = facts.get("shipping:" + order) or {}
        if (order and proof.get("party_type") in {"courier", "store_driver"} and proof.get("party_id")
                and (frozen.get("fee_currency") or proof.get("currency")) == currency):
            shipping.setdefault((order, proof["party_type"], proof["party_id"]), [])
    collections = {}
    for key, source in metrics.items():
        preparation_checkpoint()
        order = str(source.get("order_id") or key.removeprefix("cod:"))
        cod = cod_obligations.get(key) or {}
        context = (order, source.get("party_type") or cod.get("party_type"), source.get("party_id") or cod.get("party_id"))
        if not context[1] or not context[2]:
            known = [candidate for candidate in shipping if candidate[0] == order
                     and (not context[1] or candidate[1] == context[1])
                     and (not context[2] or candidate[2] == context[2])]
            if len(known) == 1:
                # Customer amounts belong to the order. Display them alongside
                # its sole known fee without assigning collection to that carrier.
                context = known[0]
        collections[context] = source
    contexts = set(shipping) | set(collections)
    order_contexts = Counter(context[0] for context in contexts)
    rows = []
    coverage_issues = sorted({row["code"] for row in source_issues if row.get("code") in GLOBAL_FAILURES
                             and row.get("currency") in {None, currency}})
    missing_fee_orders = {str(issue.get("source_id", "")).removeprefix("shipping:") for issue in source_issues
                          if str(issue.get("code", "")).startswith("shipping_")
                          or issue.get("component") in {"shipping_fee", "base_shipping", "cod_commission"}}
    for context in sorted(contexts, key=lambda row: tuple(str(value or "") for value in row)):
        preparation_checkpoint()
        incomplete_reasons = set()
        order, party_type, party_id = context
        key = "cod:" + order
        source = collections.get(context)
        frozen = facts.get("shipping:" + order) or {}
        if frozen.get("id") != party_id or frozen.get("party_type") != party_type:
            frozen = {}  # Evidence and fees stay with the carrier that owns them.
        fee_rows = shipping.get(context, [])
        completed_rows = [row for row in fee_rows if row.get("recognition_event") == "order_completed"]
        prepaid_rows = [row for row in fee_rows if row.get("payment_mode") == "prepaid"]
        prepaid = prepaid_rows[0] if prepaid_rows else None
        completed = completed_rows[0] if completed_rows else None
        completed_times = {row.get("completed_at") for row in completed_rows}
        completed_at = next(iter(completed_times)) if len(completed_times) == 1 else None
        cash_accounts = {row.get("cash_account_id") for row in prepaid_rows}
        cash_account_id = next(iter(cash_accounts)) if len(cash_accounts) == 1 else None
        if prepaid and (not completed_at or not cash_account_id):
            incomplete_reasons.add("prepaid_payment_evidence_incomplete")
        original = facts.get("cod_collection:" + order) or {}
        proof = facts.get("cod_customer_confirmation:" + order) or original
        evidence = proof.get("evidence") or {}
        proof_valid = bool(original) and original.get("party_id") == party_id and evidence.get("evidence_complete") is True
        method = (source or {}).get("payment_method")
        destination, status = "unavailable", "unavailable"
        store_collection = custody = settled = outstanding = courier_responsibility = None
        if source is None:
            # Paying the courier at execution says nothing about whether the
            # customer owes COD or has already paid for the order.
            destination = status = "unavailable" if completed or prepaid else "not_applicable"
        elif not source.get("party_id") or not source.get("party_type"):
            incomplete_reasons.add("collection_party_unproven")
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
                incomplete_reasons.add("collection_destination_unresolved")
            else:
                status = "not_confirmed"
            if proof_valid:
                custody = _money(source.get("confirmed_custody"))
        elif party_type == "courier":
            # The order balance and delivered waybill establish responsibility. Neither
            # independently attests cash received from the customer, including after a remittance.
            destination, status = "courier_receivable", "courier_collection_unverified"
            cod = cod_obligations.get(key) or {}
            if (cod.get("direction") == "receivable" and cod.get("party_type") == "courier"
                    and cod.get("party_id") == party_id and cod.get("currency") == currency
                    and (_number(cod.get("confirmed")) or ZERO) > ZERO):
                courier_responsibility = _money(cod["confirmed"])
            incomplete_reasons.add("courier_collection_unverified")
        if source is not None and proof_valid:
            settled, outstanding = _money(source.get("settled")), _money(source.get("outstanding"))
        elif source is not None and source.get("legacy_obligation_only"):
            settled, outstanding = _money(source.get("settled")), _money(source.get("outstanding"))
        fees = {field: _money(_sum(row.get(key) for row in fee_rows)) if fee_rows else None
                for field, key in (("shipping_confirmed", "confirmed"), ("shipping_settled", "settled"),
                                   ("shipping_outstanding", "outstanding"))}
        if order in missing_fee_orders:
            incomplete_reasons.add("shipping_fee_incomplete")
        if any(value is None for value in fees.values()):
            incomplete_reasons.add("shipping_fee_unknown")
        if status in {"unavailable", "not_confirmed", "pending_review"}:
            incomplete_reasons.add("collection_unconfirmed")
        identity = "delivery:" + order
        if order_contexts[order] > 1:
            identity += ":" + str(party_type) + ":" + str(party_id)
        rows.append({"id": identity, "order_id": order, "complete": not incomplete_reasons,
                     "incomplete_reasons": sorted(incomplete_reasons),
                     "order_number": (completed or prepaid or {}).get("order_number"),
                     "party_id": party_id, "party_type": party_type,
                     "party_name": names.get((party_type, party_id), party_id),
                     "payment_mode": "prepaid" if prepaid else "deferred" if completed else None,
                     "recognition_event": "order_completed" if completed else None,
                     "completed_at": completed_at, "cash_account_id": cash_account_id,
                     "cash_account_name": names.get(("cash", cash_account_id)) if cash_account_id else None,
                     "delivered_at": frozen.get("delivered_at"), "payment_method": method,
                     **({key: frozen.get(key) for key in ("recognition_basis", "recorded_at", "business_date_basis")}
                        if frozen.get("recognition_basis") == "delivered_order_created_after_start" else {}),
                     "collection_destination": destination, "collection_status": status, **fees,
                     "courier_responsibility": courier_responsibility,
                     "collection_amount": None if party_type == "courier" else _money((source or {}).get("collection_amount")),
                     "driver_custody": custody, "collection_settled": settled,
                     "collection_outstanding": outstanding, "store_collection": store_collection,
                     "customer_outstanding": _money((source or {}).get("customer_outstanding"))})
    preparation_checkpoint()
    return {"complete": not coverage_issues and all(row["complete"] for row in rows),
            "coverage_contract_version": 1, "coverage_issues": coverage_issues, "rows": rows, "notes": [
        "الرسوم من التزامات الشحن التشغيلية فقط؛ لا تضاف أرباح تطبيق المندوب مرة أخرى.",
        "الشحن المدفوع مقدمًا يُخصم من الصندوق عند تم التنفيذ. تاريخ التنفيذ مستقل عن تاريخ التسليم، والتسليم اللاحق لا يكرر الخصم. دفع أجرة الشحن لا يثبت تحصيل قيمة الطلب من العميل.",
        "ذمة شركة الشحن تتبع مبلغ الدفع عند الاستلام المسجل عليها. السداد المخصص يخفّض المتبقي، وإثبات تحصيل الشركة من العميل يحتاج مستندًا مستقلًا.",
        "عهدة المندوب هي المسؤولية المثبتة في المصدر وليست إثباتًا لمقدار النقد الفعلي في يده.",
        "التحويل البنكي والشبكة لا ينشئان عهدة نقدية للمندوب. لا يثبت اعتماد التحصيل من حالة تسليم الطلب وحدها.",
        "غير متاح: هوية البنك أو جهاز الشبكة المستقبل غير مثبتة في هذه اللقطة؛ اعتماد المستند لا يثبت قيدًا في رصيد البنك التشغيلي.",
        coverage_note or "التفاصيل تخص الطلبات الممثلة في لقطة التشغيل فقط؛ تغطية التسليم بعد بداية التشغيل غير مثبتة في هذه اللقطة.",
        "المبلغ المتبقي على العميل لا يضاف مرة ثانية إلى عهدة شركة الشحن أو المندوب.",
    ]}


def _profit_rows(recorded_salary):
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
    rows = [_row(identity, label, None, "total" if identity in {"operating_profit", "net_profit"} else None,
                 UNKNOWN + reason) for identity, label, reason in components]
    if recorded_salary:
        row = next(row for row in rows if row["id"] == "salaries")
        row["recorded_subtotal"] = recorded_salary
        row["reason"] = (f"المجموع الجزئي لأيام الرواتب المسجلة بهذه العملة {recorded_salary['amount']} "
                         f"{recorded_salary['currency']} عن {recorded_salary['employee_days']} يوم موظف، "
                         f"بين {recorded_salary['first_day']} و{recorded_salary['last_day']} بتوقيت الرياض. "
                         "يتطلب إجمالي الرواتب مطابقة جميع الموظفين وأيام الاستحقاق للفترة.")
    preparation_checkpoint()
    return rows


def build_financial_position(state, *, cost_exposure=None, generated_at=None):
    """Build JSON v1 from one saved operational snapshot without side effects."""
    preparation_checkpoint()
    projection = annotate_report_coverage(report(state, provider_balances_sar=True, ad_balances_sar=True, provider_balances_net=True))
    result = {"schema_version": 1, "authority": "operational_balance_report", "status": projection["status"],
              "started_at": projection["started_at"], "as_of": projection["as_of"],
              "generated_at": generated_at or projection["as_of"], "currency_order": [], "currencies": {},
              "issues": [_source_issue(row) for row in projection["issues"]], "dependencies": []}
    if projection["status"] != "active":
        return result
    delivery_coverage = delivery_coverage_diagnostics(state, projection)
    cod_coverage = cod_forecast_coverage(cost_exposure, projection["started_at"], projection["as_of"],
        [*projection["issues"], *(cost_exposure.get("issues", []) if isinstance(cost_exposure, dict) else [])])
    profit_source = valid_snapshot(state)
    cost_ready = isinstance(cost_exposure, dict) and cost_exposure.get("status") == "preview"
    result["dependencies"] = [
        {"code": "open_cost_exposure", "status": "ready" if cost_ready and cost_exposure.get("complete") is True else "incomplete" if cost_ready else "unavailable",
         "message": "تقديرات مراحل الطلبات المفتوحة من مصدرها التشغيلي." if cost_ready else UNKNOWN + "مصدر تكاليف الطلبات المفتوحة غير متاح بعد."},
        {"code": "complete_collection_forecast", "status": "ready" if cod_coverage["complete"] else "incomplete",
         "message": "تغطية مصادر التحصيل المتوقع للفترة المعروضة." if cod_coverage["complete"] else UNKNOWN + COD_SCOPE,
         "reason_codes": cod_coverage["reason_codes"]},
        {"code": "profit_period_coverage", "status": "incomplete" if profit_source else "unavailable",
         "message": "مصادر الربح محفوظة لنفس الفترة؛ يظهر كل عنصر معلوم مع حالة اكتماله." if profit_source else UNKNOWN + "مصادر الربح الكامل لنفس فترة المبيعات لم تكتمل."},
        *delivery_coverage["dependencies"],
        *provider_evidence_coverage(projection),
    ]
    if cost_ready:
        result["issues"].extend(_source_issue(row) for row in cost_exposure.get("issues", []))
    currencies = {row["currency"] for row in projection["parties"]}
    prestart_cod = snapshot_cod_preview(cost_exposure, projection["started_at"], projection["as_of"])
    currencies.update(prestart_cod)
    customer_cod = snapshot_cod_preview(cost_exposure, projection["started_at"], projection["as_of"], "open_customer_cod")
    currencies.update(customer_cod)
    page_payments = projection["details"]["provider_order_payments"]
    paid_totals = projection["details"]["provider_paid_totals"]
    tender_coverage = projection["details"].get("customer_tender_coverage", {})
    payment_sar = tender_coverage.get("sar")
    other_tenders = tender_coverage.get("by_currency", {})
    customer_wallets = projection["details"].get("customer_wallet_balances", {})
    wallet_statement_missing = any(row.get("code") == "customer_wallet_balance_missing"
                                   for row in projection["issues"])
    currencies.update(page_payments)
    currencies.update(paid_totals)
    currencies.update(projection["details"]["provider_balances"])
    currencies.update(other_tenders)
    currencies.update(customer_wallets)
    if payment_sar is not None:
        currencies.add("SAR")
    if profit_source:
        currencies.add("SAR")
    if cost_ready:
        currencies.update((cost_exposure.get("currencies") or {}).keys())
    result["currency_order"] = sorted(currencies, key=lambda currency: (currency != "SAR", currency))
    for currency in result["currency_order"]:
        preparation_checkpoint()
        parties = sorted((row for row in projection["parties"] if row["currency"] == currency), key=_party_id)
        obligations = [row for row in projection["obligations"] if row.get("currency") == currency]
        actual_complete = bool(projection["as_of"]) and not any(row.get("currency") in {None, currency} for row in projection["issues"])
        tables = _actual_tables(parties, actual_complete)
        tables.append(_provider_table(projection["details"]["provider_balances"].get(currency, []),
                                      [row for row in projection["issues"] if row.get("currency") in {None, currency}]))
        wallet = customer_wallets.get(currency)
        if wallet is not None:
            wallet_table = _table("customer_wallets", "محافظ العملاء المستحقة علينا", "actual", [
                _column("name", "الجهة", "text"), _column("remaining", "الرصيد المستحق للعملاء", role="payable"),
                _column("as_of", "تاريخ كشف سلة", "text"), _column("source_reference", "مرجع الكشف", "text")],
                [{"id": "customer_wallet", **wallet}], complete=wallet["complete"],
                notes=["الرصيد الختامي التزام علينا بتاريخ كشف سلة المحفوظ. يشمل الاستخدامات المثبتة فيه، وتحتفظ التحديثات التشغيلية بتاريخ هذا الكشف."])
            wallet_table.update(show_totals=False, totals={})
            tables.append(wallet_table)
        tables.append(_balance_origin(parties, obligations, actual_complete))
        apply_table_coverage(tables, parties, obligations, projection["issues"], projection["details"].get("facts", {}),
                             currency=currency, as_of=projection["as_of"])
        if other_tenders.get(currency):
            rows = [{"id": row["method"], **row} for row in other_tenders[currency]]
            for row in rows:
                preparation_checkpoint()
                if row["method"] == "wallet":
                    row["name"] = "المستخدم من أرصدة محافظ العملاء"
            tender_table = _table("customer_other_tenders", "تفصيل طرق الدفع الأخرى بعملة الطلب", "fulfillment",
                [_column("name", "طريقة الدفع", "text"), _column("orders", "الطلبات", "count"),
                 _column("paid", "مسدد للطلبات حسب سلة"), _column("remaining", "متبقٍ على العملاء"),
                 _column("incomplete_orders", "طلبات تحتاج مطابقة", "count")], rows,
                complete=not any(row["incomplete_orders"] for row in rows),
                notes=["تعرض هذه المبالغ ما تثبته صفحة الطلب. يعتمد توريد الأموال للبنك على حركة البنك المطابقة."])
            if payment_sar is not None:
                tender_table.update(show_totals=False, totals={})
            tables.append(tender_table)
        names = {(row["party_type"], row["party_id"]): _name(row) for row in parties}
        debt_rows = []
        categories = [("pos_receivable", "banks_network", "تسويات أجهزة الشبكة"),
                      *((kind, identity, title) for kind, identity, title, _ in PARTIES), ("ad_account", "ads", "حسابات الإعلانات")]
        for kind, identity, title in categories:
            preparation_checkpoint()
            if kind == "customer_wallet":
                if wallet is not None:
                    debt_rows.append({"id": identity, "name": title, "receivable": "0.00", "payable": wallet["remaining"]})
                continue
            selected = [row for row in parties if row["party_type"] == kind]
            debt_rows.append({"id": identity, "name": title,
                              "receivable": "0.00" if kind == "ad_account" else _money(_known_sum(
                                  row.get("known_outstanding_receivable", row.get("outstanding_receivable")) for row in selected)),
                              "payable": _money(_known_sum(row.get("known_outstanding_payable", row.get("outstanding_payable")) for row in selected))})
        other = next((table for table in tables if table["id"] == "other_balances"), None)
        if other:
            debt_rows.append({"id": "other_balances", "name": other["title"],
                              **{key: other["totals"][key] for key in ("receivable", "payable")}})
        liquidity = _sum(row["actual"] for row in parties if row["party_type"] in {"bank", "cash"})
        receivable, payable = (_sum(row[key] for row in debt_rows) for key in ("receivable", "payable"))
        known_receivable, known_payable = (_known_sum(row[key] for row in debt_rows)
                                         for key in ("receivable", "payable"))
        if wallet_statement_missing:
            # Order tender currency does not establish the wallet ledger's
            # currency or closing liability. Preserve recorded subtotals, but
            # never certify a zero aggregate from the absent statement.
            payable = None
        prepaid = _sum(row["ad_wallet_balance"] for row in parties if row["party_type"] == "ad_account")
        custody = _sum(row["custody_remaining"] for row in parties if row["party_type"] == "employee_custody")
        known_position = (liquidity + known_receivable - known_payable
                          if all(value is not None for value in (liquidity, known_receivable, known_payable)) else None)
        position = (liquidity + receivable - payable
                    if actual_complete and all(value is not None for value in (liquidity, receivable, payable)) else None)
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
        for identity, total, subtotal in (("debts_receivable", receivable, known_receivable),
                                          ("debts_payable", payable, known_payable)):
            if total is None:
                next(row for row in current_rows if row["id"] == identity).update(
                    known_subtotal=_money(subtotal), reason=UNKNOWN + (
                        "رصيد محافظ العملاء وعملته يحتاجان إلى كشف سلة موثق؛ المجموع المعلوم يخص الالتزامات المسجلة فقط."
                        if identity == "debts_payable" and wallet_statement_missing else
                        "المجموع المعلوم يخص الأرصدة المتاحة فقط."))
        if not actual_complete:
            next(row for row in current_rows if row["id"] == "monetary_position")["known_subtotal"] = _money(known_position)
            next(row for row in current_rows if row["id"] == "displayed_position")["known_subtotal"] = _money(
                known_position + prepaid + custody if known_position is not None and prepaid is not None and custody is not None else None)
        costs = _cost_tables(cost_exposure, currency, obligations)
        currency_issues = [row for row in projection["issues"] if row.get("currency") in {None, currency}]
        provider_future = _provider_collection_table(obligations, projection["details"], currency_issues)
        cod = _cod_table(obligations, projection["details"], prestart_cod.get(currency), customer_cod.get(currency), coverage=cod_coverage)
        tables.extend([costs[-1], *costs[:-1], cod, provider_future])
        forecast = project_forecast(tables, current_position=_money(position), actual_complete=actual_complete)
        profit = project_profit(state, currency)
        result["currencies"][currency] = {
            "tables": tables, "debt_rows": debt_rows,
            "debt_totals": {"receivable": _money(receivable), "payable": _money(payable)},
            "known_debt_totals": {"receivable": _money(known_receivable), "payable": _money(known_payable)},
            "current_rows": current_rows, "forecast_rows": forecast["rows"], "profit_rows": profit["rows"],
            "metrics": {"liquidity": _money(liquidity), "debts_receivable": _money(receivable), "debts_payable": _money(payable),
                        "monetary_position": _money(position), "prepaid_ad_credit": _money(prepaid), "employee_custody": _money(custody),
                        "displayed_position": _money(displayed), "known_monetary_position": _money(known_position),
                        "forecast_change": forecast["forecast_change"], "forecast_monetary_position": forecast["forecast_monetary_position"],
                        "operating_profit": profit["operating_profit"], "net_profit": profit["net_profit"]},
            "completeness": {"actual": actual_complete, "forecast": forecast["complete"], "profit": profit["complete"]},
            "profit_quality": profit["quality"], "forecast_scope": forecast["scope"],
            "notes": ["كل المبالغ بهذه العملة؛ لا تحويل ولا جمع بين العملات.",
                      "المركز النقدي والديون = النقد المتاح + الحقوق القابلة للتحصيل − الالتزامات المثبتة.",
                      "الرصيد الإعلاني المقدم والعهد يعرضان منفصلين ثم يضافان مرة واحدة إلى مجموع الأرصدة المعروضة.",
                      "التوقعات تغطي البنود المبينة فقط؛ لا تفترض مصروفات مستقبلية أو رواتب أو إعلانات أو ضرائب غير موثقة.",
                      "الربح لا يستنتج من الأرصدة أو التمويل أو سحب المالك أو مجرد اعتماد فاتورة مورد."],
            "deliveries": _deliveries(obligations, projection["details"], currency, names, projection["issues"],
                coverage_note=(delivery_coverage["event_note"] if currency == "SAR" else
                    "تغطية تسليم الطلبات السابقة لبدء التشغيل بهذه العملة غير مثبتة؛ لا تحويل أو إعادة تسمية لمبالغ الشحن.")),
        }
    preparation_checkpoint()
    return result
