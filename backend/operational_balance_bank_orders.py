"""Read-only MZ2 routing for operational order bank credits; no writer imports."""
import hashlib
import json
import re
from decimal import Decimal
from urllib.parse import urlsplit

QUALIFYING = {'reviewed', 'تم المراجعة', 'تمت المراجعة', 'in_progress', 'processing', 'قيد التنفيذ'}
NATIVE_BANKS = {
    'bank_rajhi': {'مصرف الراجحي', 'بنك الراجحي', 'الراجحي', 'al rajhi bank', 'al rajhi', 'rajhi'},
    'bank_inma': {'مصرف الإنماء', 'مصرف الانماء', 'بنك الإنماء', 'بنك الانماء', 'الإنماء', 'الانماء', 'alinma bank', 'alinma', 'al inma', 'inma'},
    'bank_ahli': {'البنك الأهلي السعودي', 'البنك الاهلي السعودي', 'البنك الأهلي', 'البنك الاهلي', 'الأهلي', 'الاهلي', 'saudi national bank', 'snb', 'ncb'},
}


def transfer_bank(method):
    text = ' '.join(str(method or '').strip().strip("'").split())
    pattern = r'^(?:حوال[هة]\s*بنكي[هة]|تحويل\s*بنكي|bank(?:$|[ _]*transfer)|wire_transfer)\s*'
    match = re.match(pattern, text, re.I)
    return (True, text[match.end():].strip()) if match else (False, '')


def resolve(data, owner, method, currency, *, raw=None, banks=()):
    if str(method or '').strip().lower() == 'bank':
        native = raw.get('bank') if isinstance(raw, dict) else None
        name = native.get('bank_name') if isinstance(native, dict) else None
        name = ' '.join(name.split()).casefold() if isinstance(name, str) else ''
        code = next((code for code, labels in NATIVE_BANKS.items() if name in labels), None)
        eligible = [bank for bank in banks if bank.get('user_id') == owner
                    and str(bank.get('id') or '').startswith('account:')
                    and bank.get('account_type') == 'bank' and bank.get('status') == 'active'
                    and currency == 'SAR' and bank.get('currency') == currency]
        if not owner or not code or any(not isinstance(bank.get('bank_transfer_aliases', []), list) for bank in eligible):
            raise ValueError('operational_order_bank_binding_incomplete')
        matches = [bank for bank in eligible if code in bank.get('bank_transfer_aliases', [])]
        if len(matches) != 1:
            raise ValueError('operational_order_bank_binding_incomplete')
        return matches[0]
    _, selected = transfer_bank(method)
    source = 'salla.payment_method_bank'
    key = hashlib.sha256(json.dumps([owner, source, selected], ensure_ascii=False).encode()).hexdigest()
    bindings = [b for b in data['mz2_bank_transfer_bindings'] if selected and
        b.get('_id') == key and b.get('upstream_source') == source and b.get('upstream_value') == selected
        and b.get('status') == 'active' and b.get('confirmed') is True
        and b.get('identity_contract_version') == 1 and b.get('bank_account_source') == 'mz2_financial_accounts']
    banks = [b for b in data['mz2_financial_accounts'] if len(bindings) == 1
        and b.get('id') == bindings[0].get('financial_account_id')
        and b.get('account_type') == 'bank' and b.get('status') == 'active'
        and currency == 'SAR' and b.get('currency') == currency]
    if len(banks) != 1:
        raise ValueError('operational_order_bank_binding_incomplete')
    return banks[0]


def native_paid_amount(raw, source, *, owner, order_number, provider_order_id, currency, total, as_of):
    """Prove only the native SAR bank leg against the current customer balance."""
    from operational_payment_evidence import _amount, _identity
    from salla_payment_collection import provider_order_page_balance

    error = 'operational_order_bank_payment_incomplete'
    if not isinstance(raw, dict) or not isinstance(source, dict):
        raise ValueError(error)
    payment = raw.get('payment') or {}
    methods = raw.get('payment_methods')
    if (currency != 'SAR' or not isinstance(payment, dict)
            or raw.get('payment_method') != 'bank' or payment.get('method') not in (None, 'bank')
            or not isinstance(methods, list) or len(methods) not in (1, 2)
            or any(not isinstance(leg, dict) for leg in methods)):
        raise ValueError(error)
    page = provider_order_page_balance(raw, currency, total)
    if not page or not 0 < _amount(page['paid'], currency) <= _amount(total, currency):
        raise ValueError(error)
    legs = {}
    references = set()
    for leg in methods:
        method = leg.get('payment_method')
        if method not in ('bank', 'customer_wallet') or method in legs or leg.get('currency') not in (None, currency):
            raise ValueError(error)
        value = _amount(leg.get('amount'), currency)
        if value <= 0:
            raise ValueError(error)
        legs[method] = value
        if len(methods) > 1:
            reference = _identity(leg.get('transaction_reference'), error)
            if reference in references:
                raise ValueError(error)
            references.add(reference)
    if 'bank' not in legs or sum(legs.values(), Decimal(0)) != _amount(page['paid'], currency):
        raise ValueError(error)
    _require_current(raw, source, owner, order_number, provider_order_id, as_of)
    return legs['bank']


def _require_current(raw, source, owner, order_number, provider_order_id, as_of):
    from operational_payment_evidence import _instant
    from salla_payment_collection import provider_order_page_current

    version = _instant(raw.get('updated_at') or raw.get('date_updated'))
    if not _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date')) <= version <= _instant(as_of):
        raise ValueError('operational_order_bank_payment_source_incomplete')
    watermark = source.get('g47_salla_snapshot') or {}
    if (not provider_order_page_current(source, raw, owner=owner, order_number=order_number,
            provider_order_id=provider_order_id, customer_balance_verified=True)
            or (watermark.get('source_updated_at') is not None and _instant(watermark['source_updated_at']) != version)):
        raise ValueError('operational_order_bank_payment_source_incomplete')


def is_mada_bank_split(raw):
    methods = raw.get('payment_methods') if isinstance(raw, dict) else None
    return (isinstance(methods, list) and len(methods) == 2
            and raw.get('payment_method') in ('mada', 'bank')
            and all(isinstance(leg, dict) for leg in methods)
            and any(leg.get('payment_method') == 'bank' for leg in methods)
            and any(leg.get('payment_method') == 'mada' for leg in methods))


def is_closed_bank_underpayment(raw, currency, total):
    """Identify a bank-only closed checkout whose explicit paid amount fell below gross."""
    from operational_payment_evidence import _amount

    methods = raw.get('payment_methods')
    actions = raw.get('payment_actions')
    action = actions.get('remaining_action') if isinstance(actions, dict) else None
    return (raw.get('payment_method') == 'bank' and isinstance(methods, list) and len(methods) == 1
            and isinstance(methods[0], dict) and methods[0].get('payment_method') == 'bank'
            and isinstance(action, dict) and action.get('has_remaining_amount') is False
            and action.get('paid_amount') is not None and 0 < _amount(action['paid_amount'], currency) < _amount(total, currency))


def _has_receipt(raw, payment):
    for media in (raw.get('receipt_image'), raw.get('payment_receipt_url'),
                  payment.get('receipt_image'), payment.get('receipt_url')):
        if isinstance(media, dict):
            media = media.get('url') or media.get('original') or media.get('src')
        if isinstance(media, str):
            try:
                parts = urlsplit(media.strip())
                hosted = parts.scheme == 'https' and parts.hostname and parts.path.strip('/') and not parts.username
            except ValueError:
                hosted = False
            if hosted:
                return True
    return False


def _closed_order_paid_only(raw, payment, actions, action, currency, total):
    """Validate paid independently when closing checkout did not settle the whole price."""
    from operational_payment_evidence import _amount
    from salla_payment_collection import provider_order_page_balance

    def omit_closed_remainder(row):
        if ((row.get('has_remaining_amount') is not None and row['has_remaining_amount'] is not False)
                or (row.get('remaining_amount') is not None and _amount(row['remaining_amount'], currency) != 0)):
            raise ValueError('operational_order_bank_payment_incomplete')
        return {k: v for k, v in row.items() if k not in ('remaining_amount', 'has_remaining_amount')}

    checked = {**omit_closed_remainder(raw), 'payment': omit_closed_remainder(payment),
               'payment_actions': {**actions, 'remaining_action': omit_closed_remainder(action)}}
    return provider_order_page_balance(checked, currency, total, allow_paid_only=True)


def _validate_card_transactions(raw, rows, card_ref, card_amount, owner, order_number, provider_order_id, currency, as_of):
    from operational_payment_evidence import _instant
    from operational_split_payment import _transaction_conflicts

    tenders = {card_ref: {'provider': 'salla', 'amount': str(card_amount)}}
    if any(row.get('provider_order_id') != provider_order_id or row.get('source') != 'salla_transactions'
           or row.get('user_id') != owner or row.get('order_number') != order_number
           or not row.get('native_transaction_evidence') for row in rows):
        raise ValueError('provider_transaction_binding_conflict')
    created = _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date'))
    if _transaction_conflicts(rows, tenders, owner, order_number, provider_order_id, currency, created, _instant(as_of)):
        raise ValueError('provider_transaction_binding_conflict')


def bank_ledger_paid_amount(raw, source, *, owner, order_number, provider_order_id, currency, total,
                            as_of, payment_issues=(), transaction_rows=(), frozen_provider=False):
    """Recognize only the separately evidenced bank tender; never recognize a provider here."""
    from operational_payment_evidence import _amount, _identity
    from salla_payment_collection import provider_order_page_balance

    error = 'operational_order_bank_payment_incomplete'
    payment = raw.get('payment') or {}
    actions = raw.get('payment_actions') or {}
    if not isinstance(payment, dict) or not isinstance(actions, dict):
        raise ValueError(error)
    action, refund = actions.get('remaining_action') or {}, actions.get('refund_action') or {}
    if not isinstance(action, dict) or not isinstance(refund, dict):
        raise ValueError(error)
    mixed = is_mada_bank_split(raw)
    methods = raw.get('payment_methods')
    if not mixed and not is_closed_bank_underpayment(raw, currency, total):
        return native_paid_amount(raw, source, owner=owner, order_number=order_number, provider_order_id=provider_order_id,
                                  currency=currency, total=total, as_of=as_of)

    # The provider reader cannot describe a direct-bank leg, even when the bank is independently proved.
    allowed = {'provider_split_payment_review_required', 'provider_payment_reference_missing'} if mixed else set()
    if frozen_provider or any(issue.get('code') not in allowed for issue in payment_issues):
        raise ValueError('operational_order_bank_credit_conflict')
    if (currency != 'SAR' or raw.get('payment_method') not in ('bank', 'mada')
            or payment.get('method') not in (None, raw['payment_method'])
            or action.get('has_remaining_amount') is not False
            or any(not isinstance(leg, dict) or leg.get('currency') not in (None, currency) for leg in methods)):
        raise ValueError(error)
    bank = next((leg for leg in methods if leg.get('payment_method') == 'bank'), None)
    if bank is None:
        raise ValueError(error)
    paid = _amount(action.get('paid_amount'), currency)
    bank_paid = _amount(bank.get('amount'), currency)
    if (bank_paid <= 0 or not isinstance(action.get('paid_amount'), dict)
            or not isinstance(refund.get('paid_amount'), dict) or _amount(refund['paid_amount'], currency) != paid
            or sum((_amount(leg.get('amount'), currency) for leg in methods), Decimal(0)) != paid):
        raise ValueError(error)
    bank_ref = bank.get('transaction_reference')
    if bank_ref is not None:
        bank_ref = _identity(bank_ref, error)
    if not bank_ref and not _has_receipt(raw, payment):
        raise ValueError('operational_order_bank_receipt_evidence_missing')

    status = raw.get('status')
    names = (status.get('slug'), status.get('name')) if isinstance(status, dict) else (status,)
    waiting = {'payment_pending', 'awaiting_payment', 'pending_payment', 'waiting_payment', 'بانتظار الدفع', 'بإنتظار الدفع'}
    payment_states = (payment.get('status'), payment.get('collection_status'),
                      raw.get('payment_status'), raw.get('payment_collection_status'))
    if (raw.get('is_pending_payment') is True or any(str(name).strip().lower() in waiting for name in names)
            or any(state not in (None, '', 'paid', 'captured', 'partial', 'مدفوع', 'تم الدفع') for state in payment_states)
            or any(row.get(key) for row in (raw, payment, refund) for key in ('refunds', 'cancellations', 'refunded_transactions'))
            or any(_amount(row[key], currency) for row in (raw, payment, refund) for key in ('pending_refund_amount', 'cancelled')
                   if row.get(key) is not None)):
        raise ValueError(error)
    _require_current(raw, source, owner, order_number, provider_order_id, as_of)

    if mixed:
        page = provider_order_page_balance(raw, currency, total)
        if not page or paid != _amount(total, currency) or _amount(page['paid'], currency) != paid:
            raise ValueError(error)
        card = next(leg for leg in methods if leg['payment_method'] == 'mada')
        card_ref = _identity(card.get('transaction_reference'), error)
        card_amount = _amount(card.get('amount'), currency)
        if (card_amount <= 0 or card_ref == bank_ref
                or any(_identity(payment[key], error) not in (card_ref, bank_ref)
                       for key in ('reference', 'transaction_reference') if payment.get(key) is not None)):
            raise ValueError(error)
        if transaction_rows:
            _validate_card_transactions(raw, transaction_rows, card_ref, card_amount, owner, order_number,
                                        provider_order_id, currency, as_of)
    else:
        page = _closed_order_paid_only(raw, payment, actions, action, currency, total)
        if not page or page['remaining'] is not None or _amount(page['paid'], currency) != bank_paid:
            raise ValueError(error)
    return bank_paid
