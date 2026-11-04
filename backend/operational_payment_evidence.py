"""Pure normalization of canonical Salla payment evidence.

Payment routing, an observed paid amount and a dated captured transaction are
separate facts. Neither fulfillment status nor order dates establish capture.
This module reads no database, writes no balances and enables no fee policy.
"""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo
from operational_balance_bank_orders import transfer_bank
from operational_fee_methods import order_fee_rail

# Exact aliases only: fuzzy substring matching is unsafe for financial routing.
PAYMENT_METHODS = {
    'salla': {'applepay', 'apple pay', 'apple_pay', 'googlepay', 'google pay', 'google_pay',
              'checkout', 'stcpay', 'stc pay', 'stc_pay', 'mada', 'مدى', 'visa', 'mastercard',
              'credit_card', 'credit card', 'debit_card', 'debit card', 'salla', 'سلة',
              'salla_pay', 'salla_payments', 'salla payments'},
    'tabby': {'tabby', 'tabby_installment', 'تابي'},
    'tamara': {'tamara', 'tamara_installment', 'تمارا'},
    'emkan': {'emkaninstallment', 'emkan', 'imkan', 'إمكان', 'امكان'},
}
CENT = Decimal('.01')
LIMIT = Decimal('999999999999.99')
NON_PROVIDER_METHODS = {
    'bank', 'bank_transfer', 'bank transfer', 'wire transfer', 'تحويل بنكي', 'حوالة بنكية',
    'cod', 'cash_on_delivery', 'cashondelivery', 'cash on delivery',
    'الدفع عند الاستلام', 'دفع عند الاستلام', 'دفع عند الإستلام',
}
DIAGNOSTICS = {
    'provider_payment_method_unknown': 'طريقة الدفع غير معروفة في قارئ الأرصدة',
    'provider_payment_method_conflict': 'تعارضت جهة الدفع بين حقول الطلب',
    'provider_split_payment_review_required': 'الطلب يحتوي عدة دفعات أو جهات دفع ويلزم تفصيلها',
    'provider_payment_shape_invalid': 'بنية بيانات الدفع غير مكتملة أو غير صالحة',
    'provider_payment_currency_conflict': 'عملة إثبات الدفع لا تطابق عملة الطلب',
    'provider_payment_amount_invalid': 'قيمة إثبات الدفع غير صالحة',
    'provider_payment_amount_conflict': 'المبلغ المدفوع أو المتبقي متعارض بين المصادر',
    'provider_payment_reference_missing': 'مرجع عملية الدفع غير مكتمل',
    'provider_payment_reference_conflict': 'مرجع الدفع مكرر بقيم مختلفة أو متعارض',
    'provider_payment_plan_invalid': 'هوية خطة الدفع غير صالحة',
    'provider_payment_plan_conflict': 'خطة الدفع متعارضة بين المصادر',
    'provider_capture_original_basis_required': 'التحصيل الأصلي أكبر من قيمة الطلب الحالية؛ يلزم أصل التحصيل والاسترداد',
    'provider_capture_timestamp_invalid': 'تاريخ التحصيل غير صالح أو بلا منطقة زمنية',
    'provider_capture_conflict': 'مبلغ التحصيل أو تاريخه خارج نطاق الطلب',
    'provider_capture_status_conflict': 'إثبات التحصيل الكامل يتعارض مع انتظار الدفع',
    'provider_transaction_order_conflict': 'إثبات المعاملة لا يطابق الطلب؛ لم يثبت تحصيل جديد',
    'provider_transaction_history_conflict': 'سجلات المعاملة متعارضة أو تشمل أكثر من دفعة؛ يلزم فحص المصدر',
    'provider_transaction_binding_conflict': 'تعذرت مطابقة معرّف المعاملة أو جهة الدفع أو عملتها مع الطلب',
    'provider_transaction_not_captured': 'حالة معاملة الدفع ليست تحصيلًا مؤكدًا',
    'provider_transaction_amount_conflict': 'مبلغ معاملة الدفع لا يطابق المبلغ المثبت للطلب',
    'provider_capture_timestamp_conflict': 'تاريخ التحصيل لا يطابق فترة الطلب أو سجل دفعه',
    'provider_capture_evidence_incomplete': 'المبلغ المدفوع ظاهر لكن إثبات التحصيل المؤرخ غير مكتمل',
    'provider_refund_execution_evidence_required': 'توجد إشارة استرداد؛ يلزم مطابقة التنفيذ ومصدر المبلغ قبل الاحتساب',
    'provider_cancellation_execution_evidence_required': 'حالة الإلغاء لا تثبت إلغاء التحصيل أو تنفيذ الاسترداد',
}



class PaymentEvidenceError(ValueError):
    """A stable diagnostic code, never a raw source value."""


def provider_for(method):
    if isinstance(method, dict):
        values = [method[k] for k in ("code", "slug") if method.get(k)]
        if not values or not all(isinstance(v, str) for v in values):
            return None
        routes = {provider_for(v) for v in values}
        return routes.pop() if len(routes) == 1 else None
    if not isinstance(method, str):
        return None
    method = method.strip().lower()
    return next((key for key, aliases in PAYMENT_METHODS.items() if method in aliases), None)


def _object(value):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise PaymentEvidenceError('provider_payment_shape_invalid')
    return value


def _identity(value, code):
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise PaymentEvidenceError(code)
    text = str(value).strip()
    if not text or len(text) > 256 or any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise PaymentEvidenceError(code)
    return text


def _amount(value, currency):
    if isinstance(value, dict):
        if value.get('currency') != currency:
            raise PaymentEvidenceError('provider_payment_currency_conflict')
        value = value.get('amount')
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        raise PaymentEvidenceError('provider_payment_amount_invalid')
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or result > LIMIT or result != result.quantize(CENT):
            raise ValueError()
    except (ValueError, InvalidOperation):
        raise PaymentEvidenceError('provider_payment_amount_invalid') from None
    return result


def _instant(value):
    try:
        zone = None
        if isinstance(value, dict):
            zone, value = value.get('timezone'), value.get('date')
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None and zone:
            parsed = parsed.replace(tzinfo=ZoneInfo(zone))
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, KeyError, OverflowError):
        raise PaymentEvidenceError('provider_capture_timestamp_invalid') from None


def _single(values, code):
    if len(set(values)) > 1:
        raise PaymentEvidenceError(code)
    return values[0] if values else None


def _method_rows(raw, provider, currency, *, page_balance=False):
    rows = raw.get('payment_methods')
    if rows is None:
        return []
    if not isinstance(rows, list) or len(rows) > 100:
        raise PaymentEvidenceError('provider_payment_shape_invalid')
    unique = {}
    for row in rows:
        if not isinstance(row, dict):
            raise PaymentEvidenceError('provider_payment_shape_invalid')
        if provider_for(row.get('payment_method')) != provider:
            raise PaymentEvidenceError('provider_split_payment_review_required')
        if row.get('currency') is not None and row['currency'] != currency:
            raise PaymentEvidenceError('provider_payment_currency_conflict')
        value = _amount(row.get('amount'), currency)
        reference = row.get('transaction_reference')
        if reference is not None or not (page_balance and len(rows) == 1):
            reference = _identity(reference, 'provider_payment_reference_missing')
        plan = row.get('plan_id')
        plan = _identity(plan, 'provider_payment_plan_invalid') if plan is not None else None
        item = (value, plan)
        if reference in unique and unique[reference] != item:
            raise PaymentEvidenceError('provider_payment_reference_conflict')
        unique[reference] = item
    if len(unique) > 1:
        # The present engine models a single provider payment per order.
        raise PaymentEvidenceError('provider_split_payment_review_required')
    return [(reference, *values) for reference, values in unique.items()]


def resolve_provider(raw, *, require_known=False):
    """Resolve only the payment party; do not validate an already-refunded total.

    Returns pages need party identity even when current total differs from the
    original capture. Unknown routing leaves that read available with no provider.
    Financial normalization requires known routing; contradictory sources always
    fail closed, including read-only views.
    """
    raw = _object(raw)
    payment = _object(raw.get('payment'))
    methods = [value for value in (payment.get('method'), raw.get('payment_method'))
               if value is not None and value != '']
    routes = [provider_for(value) for value in methods]
    known = [route for route in routes if route is not None]
    if not known:
        if require_known and any(not isinstance(value, str) or (value.strip().lower() not in NON_PROVIDER_METHODS
               and not transfer_bank(value)[0]) for value in methods):
            raise PaymentEvidenceError('provider_payment_method_unknown')
        if require_known and not methods and raw.get('payment_methods'):
            raise PaymentEvidenceError('provider_payment_reference_missing')
        return None
    provider = known[0]
    if any(route != provider for route in routes):
        raise PaymentEvidenceError('provider_payment_method_conflict')
    return provider


def normalize_payment(raw, currency, total, created_at, as_of, *, page_balance=False):
    """Return (payment-or-None, diagnostics), without mutating the supplied order.

    Native payment_methods/payment_actions fields are retained as observations.
    Only the existing complete canonical payment capture contract creates a
    capture; no reference/status/date is manufactured from an order status.
    Refund/cancellation ambiguity withholds recognition for independent review.
    """
    provider = None
    try:
        raw = _object(raw)
        payment = _object(raw.get('payment'))
        provider = resolve_provider(raw, require_known=True)
        if provider is None:
            return None, []
        gross = _amount(total, currency)
        native = _method_rows(raw, provider, currency, page_balance=page_balance)
        plans = [_identity(payment['plan_id'], 'provider_payment_plan_invalid')] if payment.get('plan_id') is not None else []
        plans += [item[2] for item in native if item[2] is not None]
        plan = _single(plans, 'provider_payment_plan_conflict')
        actions = _object(raw.get('payment_actions'))
        refund_action = _object(actions.get('refund_action'))
        remaining_action = _object(actions.get('remaining_action'))
        refunded = [container.get('refunded_amount') for container in (payment, raw)]
        refunded += [refund_action.get('refund_amount')]
        refunded = [_amount(value, currency) for value in refunded if value is not None]
        refunds = refund_action.get('refunded_transactions')
        if refunds is not None and not isinstance(refunds, list):
            raise PaymentEvidenceError('provider_payment_shape_invalid')
        status_value = raw.get('status')
        if isinstance(status_value, dict) and any(status_value.get(key) is not None and not isinstance(status_value[key], str) for key in ('slug', 'name')):
            raise PaymentEvidenceError('provider_payment_shape_invalid')
        status = (status_value.get('slug') or status_value.get('name')) if isinstance(status_value, dict) else status_value
        if status is not None and not isinstance(status, str):
            raise PaymentEvidenceError('provider_payment_shape_invalid')
        if any(value > 0 for value in refunded) or refunds or status in {'refunded', 'restored', 'مسترجع'}:
            raise PaymentEvidenceError('provider_refund_execution_evidence_required')
        if status in {'cancelled', 'canceled', 'ملغي', 'ملغى'}:
            # An order cancellation is not proof of a pre-capture void or refund.
            raise PaymentEvidenceError('provider_cancellation_execution_evidence_required')
        paid_values = [_amount(container['paid_amount'], currency)
                       for container in (payment, refund_action, remaining_action)
                       if container.get('paid_amount') is not None]
        observed = _single(paid_values, 'provider_payment_amount_conflict')
        if observed is not None and observed > gross:
            raise PaymentEvidenceError('provider_capture_original_basis_required')
        if native:
            native_value = native[0][1]
            if native_value > gross or (observed is not None and native_value != observed):
                raise PaymentEvidenceError('provider_payment_amount_conflict')
        remaining = remaining_action.get('remaining_amount')
        if remaining is not None and observed is not None and _amount(remaining, currency) + observed != gross:
            raise PaymentEvidenceError('provider_payment_amount_conflict')
        result = {'provider': provider, 'gross': str(gross), 'currency': currency,
                  'cancelled': '0', 'refunds': []}
        rail = order_fee_rail(raw) if provider == 'salla' else None
        if rail:
            result['payment_method'] = rail
        if plan is not None:
            result['plan_id'] = plan
        if observed is not None:
            result['paid_observation'] = {'amount': format(observed, '.2f'),
                                         'source': 'canonical_salla_order', 'is_capture': False}
        reference_values = [_identity(payment[key], 'provider_payment_reference_missing')
                            for key in ('reference', 'transaction_reference') if payment.get(key) is not None]
        reference_values += [native[0][0]] if native and native[0][0] is not None else []
        reference = _single(reference_values, 'provider_payment_reference_conflict')
        if page_balance and reference and observed and observed > 0:
            result['order_page_reference'] = reference
        capture_status = payment.get('status')
        if capture_status is not None and not isinstance(capture_status, str):
            raise PaymentEvidenceError('provider_payment_shape_invalid')
        if (capture_status in {'paid', 'captured'} and reference
                and observed is not None and payment.get('paid_at') is not None):
            captured = observed
            at, created, now = _instant(payment['paid_at']), _instant(created_at), _instant(as_of)
            if captured <= 0 or captured > gross or at < created or at > now:
                raise PaymentEvidenceError('provider_capture_conflict')
            if raw.get('is_pending_payment') is True and captured == gross:
                raise PaymentEvidenceError('provider_capture_status_conflict')
            result['captures'] = [{'id': reference, 'amount': str(captured), 'captured_at': at.isoformat()}]
            return result, []
        if capture_status in {'paid', 'captured'} or observed is not None and observed > 0:
            missing = []
            if capture_status not in {'paid', 'captured'}:
                missing.append('capture_status')
            if not reference:
                missing.append('capture_reference')
            if observed is None:
                missing.append('captured_amount')
            if payment.get('paid_at') is None:
                missing.append('capture_timestamp')
            return result, [{'code': 'provider_capture_evidence_incomplete', 'provider': provider,
                             'missing_fields': missing}]
        return result, []
    except PaymentEvidenceError as error:
        return None, [{'code': str(error), 'provider': provider}]
