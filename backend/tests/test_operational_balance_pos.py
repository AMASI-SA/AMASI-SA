from copy import deepcopy
import pytest
from operational_pos_identity import pos_candidates
from accounting_onboarding_ssot import _key


def native_fact(owner='owner', reference='terminal-1'):
    identity = _key(owner, 'other_receivable', reference, '2026-10-01')
    return dict(_id=identity, id=identity, entity_id=identity, user_id=owner,
                category='other_receivable', entity_type='asset', sub_account='other_receivable', side='debit',
                reference=reference, cutover_date='2026-10-01', display_name='جهاز الشبكة',
                amount='900', currency='SAR', evidence='synthetic terminal statement',
                source='documented_opening_fact', status='active', created_by=owner,
                created_at='2026-10-01T00:00:00+00:00', version=1)


def test_pos_identity_reuses_native_fact_without_importing_its_amount():
    row = native_fact(); before = deepcopy(row)
    result = pos_candidates([row], 'owner')
    assert result == [{'id': row['id'], 'name': 'جهاز الشبكة', 'currency': 'SAR', 'status': 'active'}]
    assert row == before and 'amount' not in result[0]


@pytest.mark.parametrize('field,value', [('id','forged'), ('_id','forged'), ('entity_id','forged'),
    ('side','credit'), ('currency','USD'), ('category','other_payable'), ('status','inactive'),
    ('version',2), ('source','legacy'), ('created_by',None), ('evidence','')])
def test_pos_rejects_unverified_or_wrong_identity(field, value):
    row = native_fact(); row[field] = value
    assert pos_candidates([row], 'owner') == []


def test_pos_rejects_other_owner():
    with pytest.raises(ValueError, match='owner_mismatch'):
        pos_candidates([native_fact('foreign')], 'owner')
