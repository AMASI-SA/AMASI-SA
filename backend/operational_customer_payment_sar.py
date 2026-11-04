"""Value one customer payment at its own Salla SAR basis before grouping orders."""
from decimal import Decimal, ROUND_HALF_UP

from order_currency import salla_order_currency_fields

CENT = Decimal('.01')


def value_customer_payment(raw, source, owner, currency, native):
    """Allocate exact rounded SAR across verified native tender shares once per order."""
    if not native or any(not v.is_finite() or v < 0 for v in native.values()):
        return None
    native_paid = sum(native.values(), Decimal(0))
    if currency == 'SAR':
        paid, basis = native_paid, 'native_sar'
    elif native_paid == 0:
        paid, basis = Decimal(0), 'zero_paid'
    else:
        from salla_paid_sar_evidence import matching_sar_payment
        paid = matching_sar_payment(source, raw, owner=owner, native_paid=native_paid)
        basis = 'salla_list_paid'
        if paid is None:
            if source.get('salla_paid_sar_evidence') is not None:
                return None  # A conflicting receipt must not be overridden by a rate.
            conversion = salla_order_currency_fields(raw)
            if conversion['currency_conversion_status'] != 'verified' or conversion['original_currency'] != currency:
                return None
            paid = (native_paid * Decimal(conversion['exchange_rate_to_sar'])).quantize(CENT, rounding=ROUND_HALF_UP)
            basis = 'salla_order_fx'
        if not paid.is_finite() or paid <= 0 or paid != paid.quantize(CENT):
            return None
    allocated = cumulative = Decimal(0)
    shares = {}
    for method, amount in sorted(native.items()):
        cumulative += amount
        target = (paid * cumulative / native_paid).quantize(CENT, rounding=ROUND_HALF_UP) if native_paid else Decimal(0)
        shares[method] = format(target - allocated, '.2f')
        allocated = target
    return {'currency': currency, 'native': {k: format(v, '.2f') for k, v in native.items()},
            'shares': shares, 'paid': format(paid, '.2f'), 'basis': basis}


def remaining_sar(raw, currency, value):
    if value is None:
        return None
    if value == 0 or currency == 'SAR':
        return value
    conversion = salla_order_currency_fields(raw)
    if conversion['currency_conversion_status'] == 'verified' and conversion['original_currency'] == currency:
        return (value * Decimal(conversion['exchange_rate_to_sar'])).quantize(CENT, rounding=ROUND_HALF_UP)
    return None
