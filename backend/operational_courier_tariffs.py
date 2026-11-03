"""Pin an owner's saved deferred courier terms for the operational report."""
from collections import Counter
from decimal import Decimal, InvalidOperation

from courier_cod_fee_rules import (calculate_courier_cod_fee_decimal, split_tax_amount,
                                  validate_courier_cod_fee_tiers)
from operational_balance_store import audit, digest, fail, mutate, now, read, remember, replay
from shipping_companies import SHIPPING_ALIASES, normalize_shipping_company


def matches(row, key):
    aliases = row.get('salla_carrier_keys') or []
    if row.get('source') == 'operational_courier_tariff':
        return bool(key) and str(key).strip().casefold() in {s.casefold() for s in aliases}
    return key in aliases


def decimal_field(value, upper=None):
    try:
        number = Decimal(str(value))
        if not number.is_finite() or number < 0 or (upper is not None and number > upper):
            raise ValueError
        return number
    except (ValueError, InvalidOperation):
        raise ValueError('courier_tariff_amount_invalid') from None


def terms(row):
    price = decimal_field(row.get('cost_per_order', row.get('cost')))
    vat = decimal_field(row['vat_percent'], 100) if row.get('vat_percent') is not None else decimal_field(row.get('vat_rate'), 1) * 100
    if row.get('vat_rate') is not None and decimal_field(row['vat_rate'], 1) * 100 != vat:
        raise ValueError('courier_tariff_tax_conflict')
    if price <= 0 or price != price.quantize(Decimal('.01')):
        raise ValueError('courier_tariff_base_missing')
    result = {'cost_per_order': str(price), 'vat_percent': str(vat)}
    tiers = row.get('cod_fee_tiers') or []
    if tiers:
        result['cod_fee_tiers'] = validate_courier_cod_fee_tiers(tiers)
    elif all(row.get(k) is not None for k in ('cod_fee_percent', 'cod_fee_fixed_per_order', 'cod_fee_vat_percent')):
        included = row.get('cod_fee_vat_included', False)
        if not isinstance(included, bool):
            raise ValueError('courier_tariff_tax_conflict')
        result.update(cod_fee_percent=str(decimal_field(row['cod_fee_percent'], 1)),
                      cod_fee_fixed_per_order=str(decimal_field(row['cod_fee_fixed_per_order'])),
                      cod_fee_vat_percent=str(decimal_field(row['cod_fee_vat_percent'], 100)),
                      cod_fee_vat_included=included)
    return result


def native_matches(setup, candidate):
    aliases = {s.casefold() for s in candidate['keys']}
    return [c for c in setup.get('couriers', []) if c.get('courier_key') == candidate['id']
            or any(str(k).casefold() in aliases for k in (c.get('salla_carrier_keys') or []))]


async def choices(db, owner, state):
    from operational_balance_sources import external_courier, rows, usable
    records = await db['settings'].find({'user_id': owner}, {'_id': 0, 'user_id': 1, 'shipping_companies': 1}).to_list(2)
    setups = await rows(db, owner, 'mz2_shipping_setup_v2')
    if len(records) > 1 or len(setups) > 1 or any(r.get('user_id') != owner for r in records):
        fail('courier_tariff_source_ambiguous', 'تعددت سجلات إعداد شركات الشحن للمتجر.')
    companies = records[0].get('shipping_companies', []) if records else []
    if not isinstance(companies, list) or any(not isinstance(c, dict) for c in companies):
        fail('courier_tariff_source_invalid', 'تعذر قراءة قائمة الشركات المحفوظة.')
    counts = Counter(normalize_shipping_company(c.get('name'))[0] for c in companies)
    items = []
    for row in companies:
        if not (usable(row) and external_courier(row) and row.get('is_deferred') is True
                and row.get('payment_mode') in (None, 'deferred')):
            continue
        key, label = normalize_shipping_company(row.get('name'))
        if counts[key] != 1 or key == 'unknown':
            continue
        # Exact known aliases only. Never use substring recognition on a waybill.
        keys = sorted({key, label, str(row['name']).strip(), *(alias for k, _, alias in SHIPPING_ALIASES if k == key)})
        candidate = {'id': key, 'name': label, 'keys': keys, 'currency': 'SAR'}
        try:
            candidate['terms'] = terms(row)
        except (ValueError, InvalidOperation, TypeError):
            items.append({**candidate, 'unavailable': 'تحتاج تعرفة الشحن أو ضريبتها إلى تصحيح في الإعدادات.'})
            continue
        if native_matches(setups[0] if setups else {}, candidate):
            items.append({**candidate, 'unavailable': 'لهذه الشركة تعريف محاسبي مستقل. راجع عقدها المعتمد.'})
            continue
        saved = [s for s in state.get('courier_tariffs', []) if s['id'] == key]
        previous = saved[-1] if saved else {}
        items.append({**candidate, 'source_hash': digest([owner, candidate, previous.get('version_id')]),
                      'current_from': previous.get('effective_from'), 'same_terms': previous.get('terms') == candidate['terms']
                      and previous.get('keys') == keys})
    return items


async def catalog(db, owner):
    state = await read(db, owner)
    return {'items': await choices(db, owner, state), 'started_at': state.get('started_at')}


async def approve(db, owner, actor, payload):
    from operational_balance_sources import instant
    if actor != owner or payload.get('confirmed') is not True:
        fail('courier_tariff_owner_required', 'يجب أن يعتمد مالك المتجر التعرفة وتاريخ سريانها.', 403)
    try:
        at = instant(payload['effective_from'])
    except (TypeError, ValueError):
        fail('courier_tariff_date_required', 'اختر وقت سريان التعرفة مع المنطقة الزمنية.')
    action = 'approve_operational_courier_tariff'

    async def save(state):
        old = replay(state, action, payload)
        if old:
            return old
        from operational_balance_service import accounting_inactive
        if state.get('status') not in ('draft', 'active'):
            fail('operational_not_active', 'انتهت فترة تعديل الإعدادات التشغيلية.')
        await accounting_inactive(db, owner)
        candidate = next((c for c in await choices(db, owner, state) if c['id'] == payload['party_id']), None)
        if not candidate or candidate.get('source_hash') != payload['source_hash'] or candidate.get('unavailable'):
            fail('courier_tariff_preview_stale', 'تغيرت التعرفة أو الربط. افتح المعاينة من جديد.')
        if candidate['same_terms'] or (candidate['current_from'] and at <= instant(candidate['current_from'])):
            fail('courier_tariff_period_conflict', 'العقد مطابق أو يتداخل مع نسخة معتمدة.')
        stamp = now()
        versions = state.setdefault('courier_tariffs', [])
        version = {k: candidate[k] for k in ('id', 'name', 'keys', 'currency', 'terms')}
        version.update(version_id=digest([candidate['source_hash'], at.isoformat()]), effective_from=at.isoformat(),
                       confirmed_at=stamp, confirmed_by=actor)
        versions.append(version)
        result = {'id': version['id'], 'version_id': version['version_id'], 'effective_from': version['effective_from']}
        audit(state, actor, action, None, result, payload['reason'], 'settings.shipping_companies', at=stamp)
        remember(state, action, payload, result)
        return result
    return await mutate(db, owner, save)


def with_tariffs(setup, owner, state):
    versions = (state or {}).get('courier_tariffs', [])
    if not versions:
        return setup, []
    if state.get('owner_id') != owner or (setup and setup.get('user_id') != owner):
        raise ValueError('operational_source_owner_mismatch')
    result = {**setup, 'user_id': owner, 'couriers': list(setup.get('couriers', [])), 'contracts': list(setup.get('contracts', []))}
    conflicts = []
    for key in dict.fromkeys(v['id'] for v in versions):
        history = sorted((v for v in versions if v['id'] == key), key=lambda v: v['effective_from'])
        current = history[-1]
        keys = sorted({alias for v in history for alias in v['keys']})
        collision = native_matches(setup, {'id': key, 'keys': keys})  # Native definitions must never stack with an adopted rate.
        if collision or any(v.get('confirmed_by') != owner or not v.get('confirmed_at') for v in history):
            conflicts.append(key)
            result['couriers'] = [c for c in result['couriers'] if c not in collision]
            continue
        result['couriers'].append({'courier_key': key, 'salla_carrier_keys': keys, 'status': 'active',
                                   'source': 'operational_courier_tariff', 'confirmed_by': owner, 'confirmed_at': current['confirmed_at']})
        for index, version in enumerate(history):
            result['contracts'].append({**version, 'id': version['version_id'], 'kind': 'operational_courier_tariff',
                'status': 'approved', 'context': 'delivery', 'party_type': 'courier', 'party_id': key,
                'effective_to': history[index + 1]['effective_from'] if index + 1 < len(history) else None})
    return result, conflicts


def charge(rate, cod_amount, cod_incomplete):
    t = rate['terms']
    base = split_tax_amount(t['cost_per_order'], t['vat_percent'], inclusive=False)['gross']
    parts = [{'id': 'base_shipping', 'amount': str(base), 'complete': True}]
    commission, problem = Decimal('0'), None
    if cod_incomplete:
        problem = 'shipping_cod_amount_unavailable'
    elif cod_amount is not None and cod_amount > 0:
        if not t.get('cod_fee_tiers') and 'cod_fee_percent' not in t:
            problem = 'shipping_cod_tariff_incomplete'
        else:
            fee = calculate_courier_cod_fee_decimal(cod_amount, t)
            if fee['needs_review']:
                problem = 'shipping_cod_tariff_incomplete'
            else:
                commission = fee['fee_total']
    parts.append({'id': 'cod_commission', 'amount': None if problem else str(commission), 'complete': not problem})
    return {'cost': str(base + commission), 'fee_components': parts, 'fee_complete': not problem,
            'component_issues': [{'code': problem, 'component': 'cod_commission'}] if problem else []}
