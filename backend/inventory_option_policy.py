"""Owner-local option roles, independent of Salla content and stock movements."""
from copy import deepcopy
from datetime import datetime, timezone

from inventory_option_binding_conflicts import informational_option_service_conflicts
from operational_balance_store import digest, fail


PRODUCTS = 'mezan_products_v2'
POLICY_FIELD = 'mezan_inventory_option_policy'
ROLES = frozenset({'physical', 'customization', 'informational'})
MAX_OPTIONS = 100
MAX_BINDINGS = 2000

# Compare the exact stored definitions, including absence versus null. Provider
# refreshes must not let a reviewed option map silently bind to a different graph.
DEFINITION_FIELDS = (
    'mezan_product_id', 'salla_product_id', 'options', 'options_count',
    'variants', 'variants_count', 'raw_salla', 'raw_salla_details',
    'status', 'active', 'is_active', 'archived', 'deleted', 'is_archived',
    'is_deleted', 'deleted_at', 'product_type', 'type',
)


def _revision(row):
    policy = row.get(POLICY_FIELD)
    if policy is None and POLICY_FIELD not in row:
        return 0
    if (not isinstance(policy, dict) or type(policy.get('revision')) is not int
            or policy['revision'] < 1 or not isinstance(policy.get('roles'), dict)):
        fail('inventory_option_policy_invalid', 'تصنيف خيارات المنتج يحتاج مراجعة', 409)
    return policy['revision']


def _fingerprint(row):
    return digest(['inventory-option-catalog-v1', [
        [key, key in row, row.get(key)] for key in DEFINITION_FIELDS]])


def _catalog(row):
    from operational_opening_options import opening_option_source
    from operational_purchase_metadata import _products

    projected = _products([opening_option_source(row)])
    if len(projected) != 1:
        fail('inventory_option_product_unavailable', 'المنتج غير متاح لتصنيف خيارات المخزون', 404)
    product = projected[0]
    if len(product.get('options', [])) > MAX_OPTIONS:
        fail('inventory_option_catalog_incomplete', 'تعريف خيارات المنتج يتجاوز الحد المدعوم', 409)
    product['inventory_option_policy_revision'] = _revision(row)
    return product


def _view(row):
    product = _catalog(row)
    options = product.get('options', [])
    roles = {option['id']: option['inventory_role'] for option in options}
    return {
        'product_id': row['mezan_product_id'], 'revision': _revision(row),
        'catalog_fingerprint': _fingerprint(row), 'roles': roles,
        'options': options, 'product': product,
    }


async def _product(db, owner, product_id):
    if not isinstance(product_id, str) or not product_id or len(product_id) > 200:
        fail('inventory_option_product_missing', 'المنتج غير موجود', 404)
    rows = await db[PRODUCTS].find(
        {'user_id': owner, 'mezan_product_id': product_id}).limit(2).to_list(2)
    if len(rows) != 1:
        fail('inventory_option_product_missing', 'المنتج غير موجود أو هويته غير مكتملة', 404)
    return rows[0]


def _candidate(row, roles, revision, actor):
    return {**deepcopy(row), POLICY_FIELD: {
        'roles': deepcopy(roles), 'revision': revision,
        'updated_by': actor,
        'updated_at': datetime.now(timezone.utc).isoformat(),
    }}


def _validate_roles(row, roles, candidate):
    current = _catalog(row)
    options = current.get('options', [])
    ids = {option['id'] for option in options}
    if not isinstance(roles, dict) or set(roles) != ids or len(roles) > MAX_OPTIONS:
        fail('inventory_option_catalog_changed', 'تغيرت خيارات المنتج؛ أعد تحميل التصنيف كاملًا', 409)
    if any(role not in ROLES for role in roles.values()):
        fail('inventory_option_role_invalid', 'تصنيف أحد خيارات المنتج غير صالح', 422)
    for option in options:
        if option.get('role_locked') and roles[option['id']] != 'physical':
            fail('inventory_option_physical_locked', 'خيار نسخة المنتج يحدد المخزون ويجب أن يبقى مواصفة قطعة', 422)

    projected = _catalog(candidate)
    if projected.get('configuration_issues'):
        fail('inventory_option_policy_conflict', 'تعذر اعتماد التصنيف: ' +
             ' · '.join(projected['configuration_issues']), 422)
    for option in projected.get('options', []):
        if roles[option['id']] == 'informational':
            continue
        for rule in option.get('visibility_rules', []):
            if roles.get(rule['option_id']) == 'informational':
                fail('inventory_option_side_dependency',
                     'الخيار الجانبي لا يتحكم في مواصفة قطعة أو تجهيز؛ راجع تصنيف الخيارات التابعة', 422)


async def _validate_service_bindings(db, owner, row, roles):
    informational = {identity for identity, role in roles.items() if role == 'informational'}
    if not informational:
        return
    bindings = await db['mezan_product_option_cost_bindings_v2'].find({
        'user_id': owner,
        'salla_product_id': str(row.get('salla_product_id') or row['mezan_product_id']),
        'mode': 'resource',
    }, {'_id': 0}).to_list(MAX_BINDINGS + 1)
    if len(bindings) > MAX_BINDINGS:
        fail('inventory_option_bindings_incomplete', 'تحتاج روابط خدمات الخيارات إلى مراجعة', 409)
    resource_ids = list({binding['resource_id'] for binding in bindings
                         if type(binding.get('resource_id')) in (str, int) and binding['resource_id']})
    resources = await db['mezan_cost_resources_v2'].find({
        'user_id': owner, 'id': {'$in': resource_ids},
    }, {'_id': 0}).to_list(MAX_BINDINGS + 1) if resource_ids else []
    if len(resources) > MAX_BINDINGS:
        fail('inventory_option_bindings_incomplete', 'تحتاج روابط خدمات الخيارات إلى مراجعة', 409)
    conflicts = informational_option_service_conflicts(
        informational_option_ids=informational, bindings=bindings,
        resources_by_id={str(resource['id']): resource for resource in resources})
    if conflicts:
        from fastapi import HTTPException
        raise HTTPException(409, detail={
            'code': 'inventory_option_supplier_service_conflict',
            'message': 'الخيار مرتبط بخدمة مورد؛ راجع الربط قبل تصنيفه إضافة جانبية',
            'conflicts': conflicts,
        })


def _selector(owner, row):
    result = {'_id': row['_id'], 'user_id': owner}
    for key in (*DEFINITION_FIELDS, POLICY_FIELD):
        result[key] = ({'$exists': True, '$eq': deepcopy(row[key])}
                       if key in row else {'$exists': False})
    return result


async def get_policy(db, user, product_id):
    from operational_opening_inventory import owner_actor

    actor = await owner_actor(db, user)
    row = await _product(db, actor['id'], product_id)
    result = _view(row)
    await owner_actor(db, user, actor['id'])
    return result


async def save_policy(db, user, product_id, payload):
    from operational_opening_inventory import owner_actor

    actor = await owner_actor(db, user)
    owner = actor['id']
    row = await _product(db, owner, product_id)
    revision = _revision(row)
    if payload['catalog_fingerprint'] != _fingerprint(row):
        fail('inventory_option_catalog_changed', 'تغيرت خيارات المنتج؛ أعد تحميل التصنيف كاملًا', 409)
    roles = payload['roles']
    identical = (POLICY_FIELD in row and row[POLICY_FIELD]['roles'] == roles
                 and payload['expected_revision'] <= revision)
    if payload['expected_revision'] != revision and not identical:
        fail('inventory_option_policy_changed', 'حُدث تصنيف الخيارات؛ أعد تحميله قبل الحفظ', 409)
    candidate = _candidate(row, roles, revision + 1, owner)
    _validate_roles(row, roles, candidate)
    await _validate_service_bindings(db, owner, row, roles)
    await owner_actor(db, user, owner)

    # A lost response may be retried with the original revision. Only the exact
    # already-persisted complete role map is an idempotent no-op.
    if identical:
        return _view(row)
    result = await db[PRODUCTS].update_one(
        _selector(owner, row), {'$set': {POLICY_FIELD: candidate[POLICY_FIELD]}})
    if result.matched_count != 1:
        # Concurrent identical saves can converge without a second write.
        current = await _product(db, owner, product_id)
        if (_fingerprint(current) == payload['catalog_fingerprint']
                and _revision(current) >= revision + 1
                and current.get(POLICY_FIELD, {}).get('roles') == roles):
            return _view(current)
        fail('inventory_option_policy_changed', 'تغير تعريف المنتج أو تصنيفه أثناء الحفظ؛ أعد تحميله', 409)
    return _view(candidate)
