"""Read-only MZ2 routing for operational order bank credits; no writer imports."""
import hashlib
import json
import re
from decimal import Decimal

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
    from operational_payment_evidence import _amount, _identity, _instant
    from salla_payment_collection import provider_order_page_balance, provider_order_page_current

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
    version = _instant(raw.get('updated_at') or raw.get('date_updated'))
    if not _instant(raw.get('date') or raw.get('created_at') or raw.get('order_date')) <= version <= _instant(as_of):
        raise ValueError('operational_order_bank_payment_source_incomplete')
    watermark = source.get('g47_salla_snapshot') or {}
    if (not provider_order_page_current(source, raw, owner=owner, order_number=order_number,
            provider_order_id=provider_order_id, customer_balance_verified=True)
            or (watermark.get('source_updated_at') is not None and _instant(watermark['source_updated_at']) != version)):
        raise ValueError('operational_order_bank_payment_source_incomplete')
    return legs['bank']
