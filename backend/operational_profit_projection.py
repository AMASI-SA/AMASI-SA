"""Display a frozen, period-bound profit source without using debt/cash as revenue."""
from copy import deepcopy
from decimal import Decimal

from operational_forecast_projection import money, number
from operational_payment_evidence import _instant
from operational_profit_payroll import recorded_salary_subtotals

VERSION = "operational_profit_snapshot_v1"
SOURCE = "mezan_profit_components_same_order_cohort"
LABELS = {
    "net_sales": "إجمالي مبيعات الفترة حسب الطلبات",
    "cost_of_goods": "تكلفة منتجات مجموعة المبيعات",
    "shipping": "تكلفة شحن مجموعة المبيعات",
    "payment_fees": "رسوم الدفع والتحصيل للمجموعة",
    "advertising": "الإعلانات المستهلكة للفترة",
    "salaries": "الرواتب المستحقة للفترة",
    "operating_expenses": "المصاريف التشغيلية الدورية للفترة",
    "operating_profit": "ربح الفترة قبل التمويل والزكاة والضريبة",
    "financing": "تكاليف التمويل",
    "tax": "الزكاة وضريبة الدخل",
    "net_profit": "صافي الربح بعد التمويل والزكاة والضريبة",
}
OPERATING = ("net_sales", "cost_of_goods", "shipping", "payment_fees", "advertising", "salaries", "operating_expenses")


def valid_snapshot(state):
    snapshot = state.get("engine", {}).get("profit_snapshot")
    if not isinstance(snapshot, dict) or snapshot.get("version") != VERSION or snapshot.get("source") != SOURCE:
        return None
    try:
        period = snapshot["period"]
        if (snapshot.get("owner_id") != state.get("owner_id") or snapshot.get("currency") != "SAR"
                or period.get("timezone") != "Asia/Riyadh"
                or _instant(period["from"]) != _instant(state["started_at"])
                or _instant(period["to"]) != _instant(snapshot["as_of"])
                or _instant(snapshot["as_of"]) != _instant(state["engine"]["as_of"])):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    return snapshot


def project_profit(state, currency):
    snapshot = valid_snapshot(state) if currency == "SAR" else None
    components = snapshot.get("components", {}) if snapshot else {}
    rows, amounts = [], {}
    for identity, label in LABELS.items():
        if identity in {"operating_profit", "net_profit"}:
            continue
        source = components.get(identity) or {}
        amount, known = number(source.get("amount")), number(source.get("known_subtotal"))
        complete = source.get("complete") is True and amount is not None and amount >= 0
        if complete:
            known = amount
        if known is not None and known < 0:
            known = None
        amounts[identity] = amount if complete else None
        rows.append({"id": identity, "label": label, "amount": money(amount) if complete else None,
            "known_subtotal": money(known), "complete": complete,
            "source": source.get("source"), "reason": source.get("reason") or (
                "المصدر المحفوظ يخص مجموعة الطلبات والفترة المعروضة." if complete else "مصدر هذا البند لنفس الفترة غير مكتمل؛ لا يفترض صفرًا."),
            "coverage": deepcopy(source.get("coverage") or {})})
    if not snapshot:
        recorded = recorded_salary_subtotals(state).get(currency)
        if recorded:
            salary = next(row for row in rows if row["id"] == "salaries")
            salary.update(recorded_subtotal=recorded, known_subtotal=recorded["amount"],
                reason=f"المجموع الجزئي لأيام الرواتب المسجلة {recorded['amount']} {currency} عن {recorded['employee_days']} يوم موظف؛ تغطية الفترة الكاملة غير مثبتة.")
    operating = (amounts["net_sales"] - sum((amounts[key] for key in OPERATING if key != "net_sales"), Decimal(0))
                 if all(amounts[key] is not None for key in OPERATING) else None)
    net = operating - amounts["financing"] - amounts["tax"] if operating is not None and amounts["financing"] is not None and amounts["tax"] is not None else None
    rows.extend([
        {"id": "operating_profit", "label": LABELS["operating_profit"], "amount": money(operating), "complete": operating is not None,
         "emphasis": "total", "reason": "مبيعات الطلبات ناقص تكاليف المجموعة والفترة؛ أساس المبيعات هو أساس المصدر وليس مبلغ التحصيل البنكي."},
        {"id": "net_profit", "label": LABELS["net_profit"], "amount": money(net), "complete": net is not None,
         "emphasis": "total", "reason": "يلزم اكتمال الربح وتكلفة التمويل والزكاة وضريبة الدخل للفترة."},
    ])
    order = list(LABELS)
    rows.sort(key=lambda row: order.index(row["id"]))
    return {"rows": rows, "operating_profit": money(operating), "net_profit": money(net), "complete": net is not None,
        "operating_complete": operating is not None, "source_available": snapshot is not None,
        "quality": deepcopy(snapshot.get("quality", {})) if snapshot else {"complete": False, "reason_codes": ["profit_snapshot_unavailable"]}}
