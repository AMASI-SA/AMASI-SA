"""Customer-side Salla amounts. This summary does not create cash, liabilities or collections."""
from decimal import Decimal

from operational_balance_bank_orders import transfer_bank
from operational_customer_payment_sar import remaining_sar, value_customer_payment
from operational_payment_evidence import _amount, _instant, provider_for
from operational_provider_report_coverage import PLATFORMS
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
            "started_at":started_at, "as_of":as_of, "by_currency":{},
            "sar":{"paid":None, "known_paid":"0.00", "complete":False, "orders":0, "verified_orders":0,
                   "missing_orders":0, "missing_order_ids":[], "proof_lookups":[], "foreign_order_fx_count":0, "by_method":[]}}


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


def _record_sar(summary, order, methods, valued, due_sar):
    sar = summary["sar"]
    if valued:
        sar["verified_orders"] += 1
        sar["known_paid"] = format(Decimal(sar["known_paid"]) + Decimal(valued["paid"]), ".2f")
        sar["foreign_order_fx_count"] += int(valued["basis"] == "salla_order_fx")
    elif len(sar["missing_order_ids"]) < 100:
        sar["missing_order_ids"].append(str(order["id"]))
    for method in sorted(methods):
        row = next((r for r in sar["by_method"] if r["method"] == method), None)
        if row is None:
            name = NAMES.get(method) or PLATFORMS.get(method, method)
            row = {"method":method, "name":name, "orders":0, "incomplete_orders":0, "known_paid":"0.00",
                   "paid":None, "known_remaining":"0.00", "unknown_remaining_orders":0, "remaining":None}
            sar["by_method"].append(row)
        row["orders"] += 1
        if valued and method in valued["shares"]:
            row["known_paid"] = format(Decimal(row["known_paid"]) + Decimal(valued["shares"][method]), ".2f")
        else:
            row["incomplete_orders"] += 1
        row["paid"] = None if row["incomplete_orders"] else row["known_paid"]
        if method in due_sar and due_sar[method] is not None:
            row["known_remaining"] = format(Decimal(row["known_remaining"]) + due_sar[method], ".2f")
        else:
            row["unknown_remaining_orders"] += 1
        row["remaining"] = None if row["unknown_remaining_orders"] else row["known_remaining"]


def finish_coverage(summary, uninspected=()):
    sar = summary["sar"]
    sar["orders"] = summary["source_order_count"]
    sar["missing_orders"] = sar["orders"] - sar["verified_orders"]
    sar["complete"] = sar["missing_orders"] == 0
    sar["paid"] = sar["known_paid"] if sar["complete"] else None
    room = 100 - len(sar["missing_order_ids"])
    if room > 0:
        sar["missing_order_ids"].extend(sorted(uninspected)[:room])
    sar["by_method"].sort(key=lambda row: row["method"])
    pending = sar["proof_lookups"]
    if len(pending) > 128:
        pending.sort(key=lambda r: r["order_id"])
        offset = (int(_instant(summary["as_of"]).timestamp()) // 300 * 128) % len(pending)
        sar["proof_lookups"] = (pending[offset:] + pending[:offset])[:128]


def observe_nonprovider(summary, raw, source, order, total, payment, owner, *, blocked=False, payment_issues=()):
    """Collect customer-side money for a unique, in-window Salla order without posting cash."""
    summary["uninspected_source_orders"] -= 1
    detail = raw.get("payment") or {}
    primary = raw.get("payment_method") or (detail.get("method") if isinstance(detail, dict) else None)
    header = category(primary)
    methods = raw.get("payment_methods") or []
    if not isinstance(methods, list) or len(methods) > 100 or any(not isinstance(row, dict) for row in methods):
        methods, blocked = [], True
    codes = [primary] + [r.get("payment_method") for r in methods]
    if isinstance(detail,dict) and detail.get("method"):
        codes.append(detail["method"])
    kinds = {category(code) for code in codes}
    groups = kinds - {"provider"}
    parties = {provider_for(code) or category(code) for code in codes}
    currency = order["currency"]
    if not isinstance(currency, str) or not (len(currency) == 3 and currency.isascii() and currency.isalpha() and currency.isupper()):
        summary["unknown_currency_orders"] += 1
        _record_sar(summary, order, parties, None, {})
        return
    native, due = {}, {}
    payment = payment or {}
    split = payment.get("split")
    if split and not blocked:
        for leg in split["tenders"]:
            party = leg["provider"] or category(leg["method"])
            native[party] = native.get(party, Decimal(0)) + Decimal(leg["amount"])
            due[party] = Decimal(0)
        if "wallet" in native:
            _add(summary, currency, "wallet", native["wallet"], Decimal(0))
    elif not groups and not blocked:
        page = payment.get("order_page_balance")
        obs = next((i.get("paid_tender_observation") for i in payment_issues if
                    i.get("code") == "provider_closed_tender_total_mismatch"), None)
        if page:
            party = page["provider"]
            native[party], due[party] = Decimal(page["paid"]), Decimal(page["remaining"])
        elif obs:
            native[obs["provider"]] = Decimal(obs["paid"])
        elif payment.get("captures") and payment.get("provider"):
            captures = payment["captures"]
            created = _instant(raw.get("date") or raw.get("created_at") or raw.get("order_date"))
            if all(created <= _instant(c["captured_at"]) <= _instant(summary["as_of"]) for c in captures):
                values = {c["id"]: _amount(c["amount"], currency) for c in captures}
                native[payment["provider"]] = sum(values.values(), Decimal(0))
    else:
        amounts = None
        try:
            if (not blocked and len(kinds) == 1 and provider_order_page_current(source, raw, owner=owner,
                    order_number=order["order_number"], provider_order_id=order["id"])):
                amounts = _single_balance(raw, header, currency, _amount(total, currency), methods)
        except (KeyError, TypeError, ValueError, ArithmeticError, AttributeError):
            pass
        if amounts:
            native[header], due[header] = amounts
        for group in sorted(groups):
            _add(summary, currency, group, *(amounts or (None, None)))
    valued = value_customer_payment(raw, source, owner, currency, native)
    if currency != "SAR" and any(v > 0 for v in native.values()) and (not valued or valued["basis"] == "salla_order_fx"):
        summary["sar"]["proof_lookups"].append({"order_id":str(order["id"]), "order_number":str(order["order_number"])})
    if valued:
        order["customer_payment_sar"] = valued
    _record_sar(summary, order, parties, valued, {k:remaining_sar(raw, currency, v) for k,v in due.items()})
