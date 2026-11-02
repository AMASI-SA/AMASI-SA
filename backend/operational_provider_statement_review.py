"""Pure statement arithmetic and duplicate checks, NEVER a posting command.

Adapters must supply actual statement columns; no rates, FX, opening-balance
basis, or bank receipt are inferred by this review. No DB or network access.
"""
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json

CENT = Decimal('0.01')
ZERO = Decimal('0')
FIELDS = ('order_amount', 'refundable_fee', 'nonrefundable_fee', 'fixed_fee',
          'total_fee', 'vat_amount', 'transferred_amount')


def _cash(value):
    if value is None or isinstance(value, bool):
        raise ValueError('statement_amount_missing')
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise ValueError('statement_amount_invalid') from None
    if not result.is_finite() or abs(result) > Decimal('999999999999.99') or result != result.quantize(CENT):
        raise ValueError('statement_amount_invalid')
    return result


def _fmt(value):
    return format(value, '.2f')


def review_statement(*, provider, account_id, statement_id, currency, rows, reported_payout,
                     recorded_statements=None, bank_received=None):
    """Return inspectable evidence only; callers cannot mistake it for a cash write."""
    if not all(isinstance(v, str) and v.strip() for v in (provider, account_id, statement_id, currency)):
        raise ValueError('statement_identity_missing')
    if not isinstance(rows, list) or not rows:
        raise ValueError('statement_rows_missing')
    seen, normalized = set(), []
    totals = {k: ZERO for k in ('orders', 'order_fee_net', 'order_fee_tax',
                               'payout_fee_net', 'payout_fee_tax', 'transferred')}
    for row in rows:
        identity = row.get('id')
        if not isinstance(identity, str) or not identity.strip() or identity in seen:
            raise ValueError('statement_row_identity_conflict')
        seen.add(identity)
        if row.get('currency') != currency:
            raise ValueError('statement_currency_conflict')
        kind = row.get('type')
        if kind not in {'sale', 'refund', 'partial_refund', 'payout_fee'}:
            raise ValueError('statement_row_type_unsupported')
        amounts = {key: _cash(row.get(key)) for key in FIELDS}
        fee = amounts['refundable_fee'] + amounts['nonrefundable_fee'] + amounts['fixed_fee']
        if fee != amounts['total_fee']:
            raise ValueError('statement_fee_component_mismatch')
        if kind == 'sale' and (amounts['order_amount'] <= 0 or any(amounts[k] < 0 for k in ('refundable_fee','nonrefundable_fee','fixed_fee','vat_amount'))):
            raise ValueError('statement_sale_sign_invalid')
        if kind in {'refund','partial_refund'} and (amounts['order_amount'] >= 0 or any(amounts[k] > 0 for k in ('refundable_fee','nonrefundable_fee','fixed_fee','vat_amount'))):
            # Additional charges require their own explicitly classified row.
            raise ValueError('statement_refund_sign_invalid')
        if kind == 'payout_fee' and (amounts['order_amount'] != 0 or fee < 0 or amounts['vat_amount'] < 0):
            raise ValueError('statement_payout_fee_invalid')
        deduction = fee + amounts['vat_amount']
        if row.get('total_deduction') is not None and _cash(row['total_deduction']) != deduction:
            raise ValueError('statement_deduction_mismatch')
        if amounts['order_amount'] - deduction != amounts['transferred_amount']:
            raise ValueError('statement_row_transfer_mismatch')
        prefix = 'payout_fee' if kind == 'payout_fee' else 'order_fee'
        totals[prefix + '_net'] += fee
        totals[prefix + '_tax'] += amounts['vat_amount']
        totals['orders'] += amounts['order_amount']
        totals['transferred'] += amounts['transferred_amount']
        normalized.append({'id': identity, 'type': kind, 'currency': currency,
                           **{k: _fmt(v) for k,v in amounts.items()}})
    if totals['transferred'] != _cash(reported_payout):
        raise ValueError('statement_reported_payout_mismatch')
    normalized.sort(key=lambda r: r['id'])
    payload = {'provider': provider, 'account_id': account_id, 'statement_id': statement_id, 'currency':currency,
               'rows':normalized, 'reported_payout':_fmt(_cash(reported_payout))}
    fingerprint = sha256(json.dumps(payload,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    key = json.dumps([provider,account_id,statement_id,currency],separators=(',',':'))
    previous = (recorded_statements or {}).get(key)
    if previous is not None and previous != fingerprint:
        raise ValueError('statement_reference_content_conflict')
    bank = _cash(bank_received) if bank_received is not None else None
    return {'account_id':account_id, 'statement_id':statement_id, 'currency':currency,
        'row_count':len(rows), 'transaction_count':sum(r['type']!='payout_fee' for r in normalized),
        'totals':{k:_fmt(v) for k,v in totals.items()}, 'statement_arithmetic_matches':True,
        'statement_key':key, 'statement_fingerprint':fingerprint, 'already_recorded':previous is not None,
        'bank_receipt_matches':None if bank is None else bank==totals['transferred'],
        'bank_difference':None if bank is None else _fmt(bank-totals['transferred']),
        'posting_allowed':False, 'opening_balance_review_required':True}
