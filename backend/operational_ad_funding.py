"""Owner-confirmed operational advertising setup; no native accounting writes."""
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from operational_balance_store import read, mutate, audit, replay, remember, fail, digest, now


def party_id(owner, integration_id):
    return 'operational-ad:' + digest([owner, integration_id])


def same_account(left, right):
    """Integration reconnects may assign a new ID to the same provider account."""
    return (left.get('integration_account_id') == right.get('integration_account_id')
            or (bool(left.get('platform')) and bool(left.get('platform_account_id'))
                and (left.get('platform'), left.get('platform_account_id'))
                == (right.get('platform'), right.get('platform_account_id'))))


def bindings(state, owner, native):
    """Project saved operational identities without reinterpreting native bindings."""
    if state is None:
        return list(native), []
    if state.get('owner_id') != owner:
        raise ValueError('operational_source_owner_mismatch')
    result, conflicts = list(native), []
    settings = state.get('ad_funding', {})
    for identity, setting in settings.items():
        if (setting.get('mode') != 'wallet_first' or identity != party_id(owner, setting.get('integration_account_id'))
                or setting.get('confirmed_by') != owner or not setting.get('confirmed_at')):
            raise ValueError('operational_ad_funding_invalid')
        existing = [row for row in native if same_account(row, setting)
                    and row.get('confirmed_by') and row.get('confirmed_at') and row.get('status', 'active') == 'active']
        duplicate = any(other_id != identity and same_account(other, setting)
                        for other_id, other in settings.items())
        if existing or duplicate:
            # Ambiguous authority cannot create a second spend obligation.
            result = [row for row in result if row not in existing]
            conflicts.append(identity)
            continue
        result.append({**setting, '_id': identity, 'user_id': owner, 'status': 'active',
                       'funding_mode': 'hybrid', 'funding_policy': 'wallet_first'})
    return result, conflicts


async def catalog(db, owner):
    from operational_ad_selection import choices
    from operational_balance_sources import rows, AD_SOURCES
    state = await read(db, owner)
    accounts = await rows(db, owner, 'mezan_integration_accounts_v2')
    native = await rows(db, owner, 'mz2_ad_account_bindings_v2')
    items = []
    for row in choices(accounts, state):
        if not row['selected']:
            continue
        identity = party_id(owner, row['id'])
        setting = state.get('ad_funding', {}).get(identity, {})
        account_key = {'integration_account_id': row['id'], 'platform_account_id': row['external_account_id'],
                       'platform': next(k for k, value in AD_SOURCES.items() if value[0] == row['provider'])}
        bound = any(same_account(b, account_key) and b.get('confirmed_by')
                    and b.get('confirmed_at') and b.get('status', 'active') == 'active' for b in native)
        duplicate = any(other_id != identity and same_account(other, account_key)
                        for other_id, other in state.get('ad_funding', {}).items())
        used = any(r.get('party_id') == identity for r in list(state['openings'].values()) + state['movements'])
        reason = ('operational_ad_setup_closed' if state['status'] != 'draft' else
                  'operational_ad_native_binding_exists' if bound else
                  'operational_ad_duplicate_account' if duplicate else
                  'operational_ad_setup_in_use' if used else None)
        items.append({'integration_account_id': row['id'], 'party_id': identity,
                      'name': row['name'], 'currency': row['currency'],
                      'version': setting.get('version', 0), 'configured': bool(setting),
                      'editable': reason is None, 'reason': reason, 'mode': setting.get('mode')})
    return {'status': state['status'], 'items': items}


async def save(db, owner, actor, payload):
    if actor != owner:
        fail('operational_ad_funding_owner_required', 'إعداد التمويل متاح للمالك فقط', 403)

    async def change(state):
        cached = replay(state, 'ad_funding', payload)
        if cached is not None:
            return cached
        if state['status'] != 'draft':
            fail('operational_ad_setup_closed', 'إعداد التمويل متاح قبل بدء الأرصدة فقط')
        from operational_balance_service import accounting_inactive
        await accounting_inactive(db, owner)
        from operational_balance_sources import rows, AD_SOURCES
        from operational_ad_selection import choices
        accounts = await rows(db, owner, 'mezan_integration_accounts_v2')
        selected = [r for r in choices(accounts, state) if r['id'] == payload['integration_account_id'] and r['selected']]
        if len(selected) != 1:
            fail('operational_ad_funding_account_invalid', 'اختر حسابًا متصلًا ومحددًا للعرض')
        choice = selected[0]
        matches = [r for r in accounts if r.get('mezan_integration_account_id') == choice['id']]
        if len(matches) != 1:
            fail('operational_ad_identity_ambiguous', 'هوية الحساب الإعلاني مكررة')
        account = matches[0]
        try:
            ZoneInfo(account['timezone'])
        except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
            fail('operational_ad_timezone_required', 'أكمل المنطقة الزمنية للحساب في الربط')
        if (not choice.get('external_account_id') or payload['currency'] != choice['currency']
                or payload['mode'] != 'wallet_first'):
            fail('operational_ad_funding_contract_invalid', 'العملة أو طريقة التمويل لا تطابق الحساب')
        native = await rows(db, owner, 'mz2_ad_account_bindings_v2')
        platform = next(k for k, value in AD_SOURCES.items() if value[0] == choice['provider'])
        account_key = {'integration_account_id': choice['id'], 'platform': platform,
                       'platform_account_id': choice['external_account_id']}
        if any(same_account(b, account_key) and b.get('confirmed_by')
               and b.get('confirmed_at') and b.get('status', 'active') == 'active' for b in native):
            fail('operational_ad_native_binding_exists', 'الحساب مرتبط بإعداد مالي قائم؛ لا يمكن استبداله تلقائيًا')
        identity = party_id(owner, choice['id'])
        if any(other_id != identity and same_account(other, account_key)
               for other_id, other in state.get('ad_funding', {}).items()):
            fail('operational_ad_duplicate_account', 'الحساب الإعلاني نفسه مهيأ عبر ربط آخر؛ لا يمكن احتساب صرفه مرتين')
        if (any(r.get('party_id') == identity for r in list(state['openings'].values()) + state['movements'])
                or any(k.startswith('advertising:' + identity + ':') for k in state.get('engine', {}).get('ad_days', {}))):
            fail('operational_ad_setup_in_use', 'حُفظ رصيد لهذا الحساب؛ لا يمكن تغيير إعداد تمويله')
        old = state.get('ad_funding', {}).get(identity, {})
        if payload['version'] != old.get('version', 0):
            fail('operational_ad_funding_stale', 'تغير الإعداد؛ حدّث القائمة قبل الحفظ')
        new = {'mode': 'wallet_first', 'currency': choice['currency'], 'platform': platform,
               'integration_account_id': choice['id'], 'platform_account_id': choice['external_account_id'],
               'timezone': account['timezone'],
               'confirmed_by': owner, 'confirmed_at': now(), 'evidence': payload['evidence'],
               'version': old.get('version', 0) + 1}
        state.setdefault('ad_funding', {})[identity] = new
        audit(state, actor, 'ad_funding_saved', old, new, payload['evidence'], 'mezan2')
        result = {'party_id': identity, 'version': new['version'], 'mode': new['mode']}
        remember(state, 'ad_funding', payload, result)
        return result

    return await mutate(db, owner, change)
