"""Customer-side Salla amounts. This summary does not create cash, liabilities or collections."""
from decimal import Decimal

from operational_balance_bank_orders import transfer_bank
from operational_payment_evidence import _amount, provider_for
from order_currency import SUPPORTED_ORDER_CURRENCIES
from salla_payment_collection import provider_order_page_balance, provider_order_page_current


NAMES = {
    "bank": "تحويل بنكي حسب سلة", "wallet": "محفظة العميل", "cod": "الدفع عند الاستلام",
    "awaiting": "انتظار الدفع", "unclassified": "طريقة دفع غير محددة",
}


def category(method):
    from operational_prestart_cod_forecast import COD
    if isinstance(method, dict):
        names = {category(method[k]) for k in ("code", "slug") if method.get(k)}
        return names.pop() if len(names) == 1 else "unclassified"
    name = str(method or "").strip().lower()
    if provider_for(name):
        return "provider"
    if name in ("bank", "تحويل بنكي", "حوالة بنكية") or transfer_bank(name)[0]:
        return "bank"
    if name == "customer_wallet":
        return "wallet"
    if name in COD:
        return "cod"
    return "awaiting" if name == "waiting" else "unclassified"


def new_coverage(started_at, as_of):
    return {"source_order_count":0, "uninspected_source_orders":0, "unknown_currency_orders":0,
            "started_at":started_at, "as_of":as_of, "by_currency":{}}


def _add(summary, currency, method, paid=None, remaining=None):
    rows = summary["by_currency"].setdefault(currency, [])
    row = next((r for r in rows if r["method"] == method), None)
    if row is None:
        row = {"method":method, "name":NAMES[method], "orders":0, "verified_orders":0, "incomplete_orders":0,
               "paid":None, "remaining":None}
        rows.append(row)
        rows.sort(key=lambda r:r["method"])
    row["orders"] += 1
    if paid is None or remaining is None:
        row["incomplete_orders"] += 1
        return
    row["verified_orders"] += 1
    for k,v in (("paid",paid), ("remaining",remaining)):
        row[k] = format(Decimal(row[k] or "0") + v, ".2f")


def _single_balance(raw, kind, currency, total, methods):
    if kind == "unclassified" or total <= 0:
        return None
    # Bank and COD lines commonly lack a transaction reference. They may
    # corroborate the order-page balance, but never identify a receiving bank.
    if len(methods) > 1 or any(row.get("currency") not in (None, currency) for row in methods):
        return None
    page = provider_order_page_balance(raw, currency, total)
    if page is None and kind == "cod" and len(methods) == 1:
        # Native COD closes its checkout link even when the whole price is due.
        # Require both explicit paid observations and a matching zero-valued tender.
        actions = raw.get("payment_actions") or {}
        action, refund = actions.get("remaining_action") or {}, actions.get("refund_action") or {}
        if (action.get("has_remaining_amount") is False and action.get("remaining_amount") is None
                and _amount(action["paid_amount"], currency) == _amount(refund["paid_amount"], currency) == 0
                and _amount(methods[0]["amount"], currency) == 0):
            checked = {**raw, "payment_actions":{**actions, "remaining_action":{**action,
                       "has_remaining_amount":True, "remaining_amount":total}}}
            page = provider_order_page_balance(checked, currency, total)
    if not page:
        return None
    paid = _amount(page["paid"], currency)
    if methods and _amount(methods[0].get("amount"), currency) != paid:
        return None
    if kind == "awaiting" and paid:
        return None
    return paid, _amount(page["remaining"], currency)


def observe_nonprovider(summary, raw, source, order, total, payment, owner, *, blocked=False):
    """Called once per in-window source row already accepted by its canonical identity."""
    summary["uninspected_source_orders"] -= 1
    detail = raw.get("payment") or {}
    header = category(raw.get("payment_method") or (detail.get("method") if isinstance(detail, dict) else None))
    methods = raw.get("payment_methods") or []
    if not isinstance(methods, list) or len(methods) > 100 or any(not isinstance(row,dict) for row in methods):
        methods, blocked = [], True
    kinds = {header, *(category(row.get("payment_method")) for row in methods)}
    if isinstance(detail,dict) and detail.get("method"):
        kinds.add(category(detail["method"]))
    groups = kinds - {"provider"}
    if not groups:
        return
    currency = order["currency"]
    if not isinstance(currency, str) or currency not in SUPPORTED_ORDER_CURRENCIES | {"USD"}:
        summary["unknown_currency_orders"] += 1
        return
    split = (payment or {}).get("split")
    if split and not blocked:
        wallet = [Decimal(t["amount"]) for t in split["tenders"] if t["method"] == "customer_wallet"]
        if wallet:
            _add(summary, currency, "wallet", sum(wallet), Decimal(0))
        return
    amounts = None
    try:
        if (not blocked and len(kinds) == 1 and provider_order_page_current(source, raw, owner=owner,
                order_number=order["order_number"], provider_order_id=order["id"])):
            amounts = _single_balance(raw, header, currency, _amount(total, currency), methods)
    except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
        amounts = None
    for group in sorted(groups):
        _add(summary, currency, group, *(amounts or (None,None)))
