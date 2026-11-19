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


async def native_pos_candidates(db, owner):
    from accounting_onboarding_ssot import FACTS
    from operational_balance_store import fail

    records = await db[FACTS].find({'user_id': owner, 'status': 'active',
                                    'category': 'other_receivable'}).limit(1001).to_list(1001)
    if len(records) > 1000:
        fail('operational_source_scope_too_large')
    return pos_candidates(records, owner)


def bound_identity(state, owner):
    """Only an explicit owner designation supplies a default. Never imports an amount."""
    from operational_balance_store import digest, fail

    binding = state.get('pos_receivable_binding')
    if binding is None:
        return None
    if (not isinstance(binding, dict) or binding.get('owner_id') != owner or binding.get('approved_by') != owner
            or binding.get('category') != 'other_receivable' or binding.get('currency') != 'SAR'
            or not binding.get('id') or not binding.get('approved_at') or not binding.get('name')
            or binding.get('source') not in {'native', 'operational'}
            or binding.get('seal') != digest({k: v for k, v in binding.items() if k != 'seal'})
            or (binding['source'] == 'operational' and binding['id'] != digest([owner, 'operational-pos-receivable-v1']))):
        fail('operational_pos_binding_invalid', 'تعذر التحقق من ربط ذمة الشبكة')
    return binding


async def configure_pos(db, owner, actor, selection, scope):
    from operational_balance_service import active_gate
    from operational_balance_store import audit, digest, fail, mutate, now

    if actor != owner or scope != digest([owner, actor]):
        fail('operational_pos_owner_required', 'ربط ذمة الشبكة متاح للمالك فقط', 403)

    async def apply(state):
        await active_gate(db, owner, state)
        candidates = await native_pos_candidates(db, owner)
        existing = bound_identity(state, owner)
        if existing:
            if selection != (existing['id'] if existing['source'] == 'native' else None):
                fail('operational_pos_binding_exists', 'ذمة الشبكة مرتبطة بالفعل')
            return {'id': existing['id'], 'name': existing['name']}
        selected = next((row for row in candidates if row['id'] == selection), None)
        if (selection is not None and not selected) or (selection is None and candidates):
            fail('operational_pos_selection_required', 'اختر ذمة مدينة موثقة من الحسابات الحالية')
        identity = selected['id'] if selected else digest([owner, 'operational-pos-receivable-v1'])
        name = selected['name'] if selected else 'ذمة الشبكة'
        binding = dict(id=identity, name=name, category='other_receivable', currency='SAR',
                       owner_id=owner, approved_by=actor, approved_at=now(), source='native' if selected else 'operational')
        binding['seal'] = digest(binding)
        state['pos_receivable_binding'] = binding
        audit(state, actor, 'pos_receivable_binding', None, binding, 'ربط تحصيلات الشبكة المستقبلية', 'mezan2')
        return {'id': identity, 'name': name}

    return await mutate(db, owner, apply)
