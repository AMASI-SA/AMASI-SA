"""Read native documented receivables for explicit POS opening selection.

No name heuristic, balance import, new accounting fact, or bank arrival proof.
The owner explicitly designates the selected receivable as the terminal balance.
"""
from pydantic import ValidationError
from accounting_onboarding_ssot import TypedFactCreate, _key


def pos_candidates(records, owner):
    result = []
    for row in records:
        if row.get('user_id') != owner:
            raise ValueError('operational_source_owner_mismatch')
        if row.get('category') != 'other_receivable' or row.get('status') != 'active':
            continue
        try:
            contract = TypedFactCreate.model_validate({k: row[k] for k in TypedFactCreate.model_fields if k in row})
        except (ValidationError, ValueError):
            continue
        identity = _key(owner, 'other_receivable', contract.reference, str(contract.cutover_date))
        if (row.get('_id') != identity or row.get('id') != identity or row.get('entity_id') != identity
                or (row.get('entity_type'), row.get('sub_account'), row.get('side')) != ('asset', 'other_receivable', 'debit')
                or contract.currency != 'SAR' or row.get('source') != 'documented_opening_fact'
                or not row.get('created_by') or not row.get('created_at') or row.get('version') != 1):
            continue
        # Exclude native amount: the operational baseline is explicitly entered.
        result.append({'id': identity, 'name': contract.display_name, 'currency': 'SAR', 'status': 'active'})
    return result
