"""Read-only MZ2 routing for operational order bank credits; no writer imports."""
import hashlib
import json
import re

QUALIFYING = {'reviewed', 'تم المراجعة', 'تمت المراجعة', 'in_progress', 'processing', 'قيد التنفيذ'}


def transfer_bank(method):
    text = ' '.join(str(method or '').strip().strip("'").split())
    pattern = r'^(?:حوال[هة]\s*بنكي[هة]|تحويل\s*بنكي|bank[ _]*transfer|wire_transfer)\s*'
    match = re.match(pattern, text, re.I)
    return (True, text[match.end():].strip()) if match else (False, '')


def resolve(data, owner, method, currency):
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
