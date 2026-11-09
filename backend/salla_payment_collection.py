"""Honor an explicit Salla collection-clear without inferring settlement."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation


def settled_payment_snapshot(payload):
    actions = payload.get("payment_actions")
    remaining = actions.get("remaining_action") if isinstance(actions, dict) else None
    if not isinstance(remaining, dict) or remaining.get("has_remaining_amount") is not False:
        return payload
    paid = remaining.get("paid_amount")
    if isinstance(paid, dict):
        paid = paid.get("amount")
    try:
        amount = Decimal(str(paid))
    except (InvalidOperation, ValueError, TypeError):
        return payload
    if not amount.is_finite() or amount <= 0:
        return payload
    result = deepcopy(payload)
    result["payment_actions"]["remaining_action"].update(
        remaining_amount=0, has_remaining_amount=False, checkout_url="")
    result.update(paid_amount=float(amount), remaining_amount=0,
                  has_remaining_amount=False, payment_collection_status="paid",
                  payment_checkout_url="", checkout_url="")
    return result


def _balance_amount(value, currency):
    if isinstance(value, dict):
        if value.get("currency") != currency:
            raise ValueError("provider_payment_currency_conflict")
        value = value.get("amount")
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise ValueError("provider_payment_amount_invalid")
    try:
        number = Decimal(str(value))
        if (not number.is_finite() or not 0 <= number <= Decimal("999999999999.99")
                or number != number.quantize(Decimal(".01"))):
            raise ValueError("provider_payment_amount_invalid")
    except InvalidOperation:
        raise ValueError("provider_payment_amount_invalid") from None
    return number


def provider_order_page_balance(raw, currency, total):
    """Reconcile original-currency paid/due on a single Salla order. Never invent a transaction."""
    payment = raw.get("payment") or {}
    actions = raw.get("payment_actions") or {}
    amounts = raw.get("amounts") or {}
    if not all(isinstance(value, dict) for value in (payment, actions, amounts)):
        raise ValueError("provider_payment_shape_invalid")
    action = actions.get("remaining_action") or {}
    refund = actions.get("refund_action") or {}
    if not isinstance(action, dict) or not isinstance(refund, dict):
        raise ValueError("provider_payment_shape_invalid")

    def one(field, containers):
        values = {_balance_amount(container[field], currency) for container in containers if container.get(field) is not None}
        if len(values) > 1:
            raise ValueError("provider_payment_amount_conflict")
        return next(iter(values), None)

    totals = [v for v in (amounts.get("total"), raw.get("total"), raw.get("total_amount")) if v is not None]
    codes = [raw.get("currency"), amounts.get("currency")]
    codes.extend(v.get("currency") for v in totals if isinstance(v, dict))
    payment_codes = [row.get("currency") for row in (payment, action, refund)]
    if not any(codes) or any(code is not None and code != currency for code in codes + payment_codes):
        raise ValueError("provider_payment_currency_conflict")
    if (refund.get("refunded_transactions") or any(_balance_amount(v, currency) > 0 for v in (
            raw.get("refunded_amount"), payment.get("refunded_amount"), refund.get("refund_amount"))
            if v is not None)):
        raise ValueError("provider_refund_execution_evidence_required")
    gross = _balance_amount(total, currency)
    if any(_balance_amount(v, currency) != gross for v in totals):
        raise ValueError("provider_payment_amount_conflict")
    if gross == 0:
        return None
    # An explicit native clear supersedes stale promoted root values, as on Order Details.
    native_paid = action.get("paid_amount")
    clear = (action.get("has_remaining_amount") is False and native_paid is not None
             and _balance_amount(native_paid, currency) == gross)
    paid = one("paid_amount", (payment, action, refund) if clear else (raw, payment, action, refund))
    if clear and action.get("remaining_amount") is not None:
        # Salla may leave an old numeric checkout balance after closing the link.
        # Validate its currency and shape. An independent payment balance still has to agree.
        _balance_amount(action["remaining_amount"], currency)
    due = one("remaining_amount", (payment,) if clear else (raw, payment, action))
    flags = [r["has_remaining_amount"] for r in ((action, payment) if clear else (raw, payment, action))
             if r.get("has_remaining_amount") is not None]
    if any(type(f) is not bool for f in flags) or len(set(flags)) > 1:
        raise ValueError("provider_payment_amount_conflict")
    states = {str(v).strip().lower() for v in (
        payment.get("status"), raw.get("payment_status"), raw.get("payment_collection_status"),
        payment.get("collection_status"),
    ) if v is not None}
    # A native zero balance and explicit successful payment corroborate the
    # gross when Salla omits paid_amount. A missing/defaulted due never does.
    if paid is None and due == 0 and states and states <= {"paid", "captured", "مدفوع", "تم الدفع"}:
        paid = gross
    if paid is None and due == gross:
        paid = Decimal(0)
    if paid == gross and due is None:
        due = Decimal(0)
    if paid is None or due is None:
        return None
    if paid + due != gross or (flags and flags[0] != (due > 0)):
        raise ValueError("provider_payment_amount_conflict")
    order_status = raw.get("status")
    names = (order_status.get("slug"), order_status.get("name")) if isinstance(order_status, dict) else (order_status,)
    order_states = {value.strip().lower() for value in names if isinstance(value, str)}
    awaiting = raw.get("is_pending_payment") is True or bool(order_states & {
        "awaiting_payment", "pending_payment", "waiting_payment", "بانتظار الدفع", "بإنتظار الدفع",
    })
    awaiting = awaiting or bool(states.intersection({
        "pending", "pending_payment", "awaiting_payment", "waiting_payment", "بانتظار الدفع", "بإنتظار الدفع",
    }))
    if (order_states.intersection({"refunded", "restored", "مسترجع", "cancelled", "canceled", "ملغي", "ملغى"})
            or states.intersection({"failed", "declined", "refunded", "reversed", "voided", "cancelled", "canceled"})
            or (paid > 0 and states.intersection({"unpaid", "authorized", "authorised", "غير مدفوع"}))
            or (paid == gross and (awaiting or "partial" in states))):
        raise ValueError("provider_capture_status_conflict")
    return {"paid": format(paid, ".2f"), "remaining": format(due, ".2f")}


def order_page_source_current(source, raw, *, referenced_provider_payment=False, customer_balance_verified=False):
    """Validate the native order against its stored identity and source watermark."""
    from operational_payment_evidence import _instant

    oid = raw.get("id")
    ref = raw.get("reference_id") or raw.get("order_number")
    watermark = source.get("g47_salla_snapshot") or {}
    if (oid in (None, "") or ref in (None, "")
            or (raw.get("order_number") is not None and str(raw["order_number"]) != str(ref))
            or str(source.get("salla_order_id", oid)) != str(oid)
            or str(source.get("order_number", ref)) != str(ref)
            or not isinstance(watermark, dict)
            or any(watermark.get(k) for k in ("cancelled", "requires_authoritative_refresh"))):
        return False
    if watermark.get("component_pending"):
        # Fulfillment acknowledgement can lag the atomically stored order.
        # Unreferenced customer balances require that exact stored version.
        if not (referenced_provider_payment or customer_balance_verified) or watermark.get("source_updated_at") is None:
            return False
        if str(source.get("order_status_slug") or "").lower() in {
                "cancelled", "canceled", "restored", "refunded", "ملغي", "ملغى", "مسترجع"}:
            return False
        try:
            version = _instant(raw.get("updated_at") or raw.get("date_updated"))
            status_version = source.get("last_salla_direct_status_source_updated_at")
            if customer_balance_verified and not referenced_provider_payment and version != _instant(watermark["source_updated_at"]):
                return False
            return (version >= _instant(watermark["source_updated_at"])
                    and (status_version is None or version >= _instant(status_version)))
        except ValueError:
            return False
    if watermark.get("source_updated_at") is None:
        return True
    try:
        return _instant(watermark["source_updated_at"]) == _instant(raw.get("updated_at") or raw.get("date_updated"))
    except ValueError:
        return False


def order_page_display_source_current(source, raw):
    """Read payment evidence independently of fulfillment acknowledgement.

    This is only the order DTO's display gate, not a financial posting gate.
    Identity, cancellation, refresh and source-version checks still apply;
    the mapper separately reconciles native paid/due amounts and currency.
    Never mutate the persisted component watermark.
    """
    from operational_payment_evidence import _instant

    watermark = source.get("g47_salla_snapshot") or {}
    if not isinstance(watermark, dict):
        return False
    if str(source.get("order_status_slug") or "").lower() in {
            "cancelled", "canceled", "restored", "refunded", "ملغي", "ملغى", "مسترجع"}:
        return False
    status_version = source.get("last_salla_direct_status_source_updated_at")
    if status_version is not None:
        try:
            if _instant(raw.get("updated_at") or raw.get("date_updated")) < _instant(status_version):
                return False
        except ValueError:
            return False
    display_source = {**source, "g47_salla_snapshot": {**watermark, "component_pending": False}}
    return order_page_source_current(display_source, raw)


def provider_order_page_current(source, raw, *, owner, order_number, provider_order_id, customer_balance_verified=False):
    if (not owner or source.get("user_id") != owner or not order_number or not provider_order_id
            or str(raw.get("id")) != provider_order_id or str(raw.get("reference_id") or raw.get("order_number")) != order_number
            or str(source.get("order_id", provider_order_id)) != provider_order_id):
        return False
    methods = raw.get("payment_methods")
    referenced = (isinstance(methods, list) and 0 < len(methods) <= 100
                  and all(isinstance(m, dict) and m.get("transaction_reference") for m in methods)
                  and str(source.get("order_id") or source.get("salla_order_id") or "") == provider_order_id
                  and str(source.get("order_number") or "") == order_number)
    return order_page_source_current(source, raw, referenced_provider_payment=referenced,
                                     customer_balance_verified=customer_balance_verified)
