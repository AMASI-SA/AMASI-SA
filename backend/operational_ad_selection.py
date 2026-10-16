"""Owner catalog preferences only; never change integrations or financial history."""
from operational_balance_sources import rows, usable
from operational_balance_store import read, mutate, audit, replay, remember, fail

EDITABLE = {'google_ads', 'tiktok_ads'}
PROVIDERS = EDITABLE | {'snapchat_ads', 'meta_ads'}


def choices(accounts, state):
    preference = state.get('ad_catalog_selection')
    selected = set(preference['account_ids']) if preference else None
    result, seen = [], set()
    for account in accounts:
        provider = account.get('provider')
        identity = account.get('mezan_integration_account_id')
        if provider not in PROVIDERS or not usable(account) or account.get('connection_status') != 'connected':
            continue
        if not isinstance(identity, str) or not identity or identity in seen:
            fail('operational_ad_identity_ambiguous', 'هوية الحساب الإعلاني ناقصة أو مكررة')
        seen.add(identity)
        enabled = identity in selected if provider in EDITABLE and selected is not None else account.get('mezan_selected') is True
        result.append({'id': identity, 'provider': provider,
                       'name': account.get('display_name') or account.get('external_account_id') or 'حساب بلا اسم',
                       'external_account_id': account.get('external_account_id'),
                       'currency': account.get('currency'), 'selected': enabled,
                       'editable': provider in EDITABLE})
    return sorted(result, key=lambda r: (r['provider'], r['name'], r['id']))


async def catalog(db, owner):
    state = await read(db, owner)
    accounts = await rows(db, owner, 'mezan_integration_accounts_v2')
    return {'version': state.get('ad_catalog_selection', {}).get('version', 0),
            'items': choices(accounts, state)}


async def save(db, owner, actor, payload):
    if actor != owner:
        fail('operational_ad_selection_owner_required', 'اختيار الحسابات متاح للمالك فقط', 403)

    async def change(state):
        cached = replay(state, 'ad_catalog_selection', payload)
        if cached is not None:
            return cached
        old = state.get('ad_catalog_selection', {})
        if payload['version'] != old.get('version', 0):
            fail('operational_ad_selection_stale', 'تغير اختيار الحسابات؛ حدّث القائمة قبل الحفظ')
        accounts = await rows(db, owner, 'mezan_integration_accounts_v2')
        allowed = {r['id'] for r in choices(accounts, state) if r['editable']}
        ids = payload['account_ids']
        if len(ids) != len(set(ids)) or not set(ids) <= allowed:
            fail('operational_ad_selection_invalid', 'الحساب المختار غير متاح أو تابع لمسار اختيار آخر')
        new = {'version': old.get('version', 0) + 1, 'account_ids': sorted(ids)}
        state['ad_catalog_selection'] = new
        audit(state, actor, 'ad_catalog_selection', old, new,
              'اختيار العرض فقط؛ دون تفعيل محاسبة أو تغيير أرصدة', 'mezan2')
        result = {'version': new['version']}
        remember(state, 'ad_catalog_selection', payload, result)
        return result

    return await mutate(db, owner, change)
