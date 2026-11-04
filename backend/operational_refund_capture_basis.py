"""Restore an unchanged, proved capture for a recorded post-capture cancellation.

Pure read-only validation. An order disposition is not a payment void. This
narrow recovery requires the prior executed refund and capture facts to agree
with the current full payment evidence. It does not reconstruct a reduced
order's original total, infer a payment date, or post a settlement/fee credit.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation

from operational_payment_evidence import normalize_payment, _instant


def _cash(value):
    if value is None or isinstance(value, bool):
        raise ValueError('missing monetary evidence')
    result = Decimal(str(value))
    if not result.is_finite() or result < 0 or result != result.quantize(Decimal('.01')):
        raise ValueError('invalid monetary evidence')
    return result


def _captures(payment, facts, order_id, provider):
    rows = payment.get('captures')
    if not isinstance(rows, list) or not rows:
        raise ValueError('missing original capture')
    result = {}
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('invalid capture')
        reference = row.get('id')
        if not isinstance(reference, str) or not reference.strip() or reference in result:
            raise ValueError('invalid capture reference')
        value, timestamp = _cash(row.get('amount')), _instant(row.get('captured_at'))
        if value <= 0 or facts.get('capture:' + reference) != {
                'order_id': order_id, 'provider': provider, 'amount': format(value, '.2f')}:
            raise ValueError('capture fact mismatch')
        result[reference] = (value, timestamp)
    return result


def recorded_capture_for_cancelled_order(raw, state, order_id, provider,
                                         currency, total, created_at, as_of):
    """Return verified current capture or None; never weaken a failed proof.

    Only a generic cancelled order status may be ignored after a same-order
    executed provider refund is proved. All monetary fields and native refund
    observations still pass through the existing strict normalizer unchanged.
    """
    try:
        status = raw.get('status')
        status = (status.get('slug') or status.get('name')) if isinstance(status, dict) else status
        if status not in {'cancelled', 'canceled', 'ملغي', 'ملغى'} or not provider:
            return None
        payment = raw.get('payment')
        if not isinstance(payment, dict) or payment.get('cancellations') or raw.get('cancellations'):
            return None
        if _cash(payment.get('cancelled', '0')) != 0:
            return None
        prior = [row for row in state.get('customer_returns', [])
                 if row.get('order_id') == order_id and row.get('status') == 'refunded'
                 and row.get('case_type', 'item_return') == 'item_return'
                 and row.get('refund_source_type') == 'provider'
                 and row.get('refund_source_id') == provider]
        if not prior:
            return None
        candidate = deepcopy(raw)
        # This is a nonmonetary order-disposition override, not a financial
        # cancellation/refund override. All actual payment hints are retained.
        candidate['status'] = 'under_review'
        normalized, issues = normalize_payment(candidate, currency, total, created_at, as_of)
        if normalized is None or issues:
            return None
        facts = state.get('engine', {}).get('facts', {})
        current_captures = _captures(normalized, facts, order_id, provider)
        for case in prior:
            snapshot = case.get('payment_snapshot')
            if not isinstance(snapshot, dict) or not case.get('confirmed_by'):
                return None
            if (snapshot.get('provider') != provider or snapshot.get('currency') != currency
                    or _cash(snapshot.get('gross')) != _cash(total)
                    or _cash(case.get('order_total')) != _cash(total)
                    or snapshot.get('plan_id') != normalized.get('plan_id')
                    or (snapshot.get('payment_method') and normalized.get('payment_method')
                        and snapshot['payment_method'] != normalized['payment_method'])
                    or _cash(snapshot.get('cancelled', '0')) != 0
                    or snapshot.get('cancellations')
                    or _captures(snapshot, facts, order_id, provider) != current_captures):
                return None
            reference = case.get('refund_reference')
            value = _cash(case.get('amount'))
            if (not isinstance(reference, str) or not reference.strip() or value <= 0
                    or not _instant(created_at) <= _instant(case.get('refunded_at')) <= _instant(as_of)
                    or facts.get('refund:' + provider + ':' + reference) != {
                        'order_id': order_id, 'amount': format(value, '.2f')}):
                return None
        return normalized
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, OverflowError):
        return None
