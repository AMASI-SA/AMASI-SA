"""Recognize an explicitly documented already-net sale without refunding it twice."""
from decimal import Decimal
from operational_balance_sources import instant, number
from operational_customer_returns import observed_salla_refund


def current_total_is_net(raw, cases, as_of):
    """No bank/provider amount is changed here; absent tax/return proof stays unknown.

    Only the existing confirmed Salla-compensation contract proves that the
    saved current order total already includes the refund. Ordinary item-return
    cash payments alone cannot establish the sale's tax-adjusted net amount.
    """
    observed = observed_salla_refund(raw)
    hinted = bool(raw.get('refund_amount') or raw.get('refunded_amount') or
                  observed['status'] != 'not_observed')
    if not cases and not hinted:
        return True
    if raw.get('currency') != 'SAR' or not cases:
        return False
    try:
        if observed['status'] != 'observed_requires_execution_evidence':
            return False
        identities, references = set(), set()
        total = Decimal(0)
        for case in cases:
            if (not case.get('id') or case['id'] in identities
                    or not case.get('refund_reference') or case['refund_reference'] in references
                    or str(case.get('order_id')) != str(raw.get('id'))
                    or case.get('currency') != 'SAR' or case.get('case_type') != 'order_compensation'
                    or case.get('status') != 'salla_refund_confirmed'
                    or case.get('manually_confirmed_source') is not True
                    or case.get('refund_source_type') not in {'bank', 'provider'}
                    or not case.get('refund_source_id')
                    or case.get('financial_effect') != 'confirmed_' + case['refund_source_type'] + '_refund_debit'
                    or instant(case['created_at']) > as_of or instant(case['refunded_at']) > as_of):
                return False
            identities.add(case['id'])
            references.add(case['refund_reference'])
            value = number(case['amount'])
            if value <= 0:
                return False
            total += value
        if total != number(observed['amount']):
            return False
        for key in ('refund_amount', 'refunded_amount'):
            if raw.get(key) is not None and number(raw[key]) != total:
                return False
        latest = max(cases, key=lambda c: instant(c['created_at']))
        gross = number((raw.get('amounts') or {})['total'])
        return number(latest['order_total_after_refund_snapshot']) == gross
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return False
