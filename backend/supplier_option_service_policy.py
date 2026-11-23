"""Project local side-option roles into newly constructed supplier service plans.

This reader does not alter option costs, customer answers, product-level links,
or services already frozen on materialized preparation pieces.
"""
from typing import Any

from fastapi import HTTPException

from inventory_option_configuration import INVENTORY_ROLES, POLICY_FIELD


PRODUCTS = 'mezan_products_v2'
PRODUCT_IDS = ('salla_product_id', 'mezan_product_id', 'id')
MAX_PRODUCTS = 20000


def _text(value: Any) -> str:
    return str(value or '').strip()


def _fail(code: str, product_id: str = '', option_id: str = '') -> None:
    raise HTTPException(409, detail={
        'code': code,
        'message': 'تعذر التحقق من تصنيف خيارات المنتج؛ راجع التصنيف وروابط الخدمات قبل إنشاء خطة تجهيز المورد',
        'product_id': product_id, 'option_id': option_id,
    })


def _validated_roles(product: dict, product_id: str) -> dict[str, str]:
    policy = product.get(POLICY_FIELD)
    roles = policy.get('roles') if isinstance(policy, dict) else None
    revision = policy.get('revision') if isinstance(policy, dict) else None
    if (not isinstance(roles, dict) or type(revision) is not int or revision < 1
            or any(not isinstance(key, str) or not isinstance(role, str) or role not in INVENTORY_ROLES
                   for key, role in (roles or {}).items())):
        _fail('supplier_option_policy_invalid', product_id)
    from operational_opening_options import opening_option_source
    from operational_purchase_metadata import option_contract

    options, issues = option_contract(opening_option_source(product))
    if issues or set(roles) != {option['id'] for option in options}:
        _fail('supplier_option_policy_invalid', product_id)
    return {option['id']: option['inventory_role'] for option in options}


async def supplier_service_option_bindings(
    db: Any,
    *,
    user_id: str,
    bindings: list[dict],
    resources_by_id: dict[str, dict],
) -> list[dict]:
    """Keep legacy plans unchanged; suppress only proven side-option services.

    A resource can become a service after policy approval, so inspect its current
    kind here. Material costs and unconditional product services use their
    original inputs elsewhere. Policy-aware links must identify their option by
    stable ID; display names are insufficient to choose which work to suppress.
    """
    def is_service(binding):
        resource = resources_by_id.get(_text(binding.get('resource_id'))) or {}
        return (_text(binding.get('mode')) == 'resource'
                and _text(resource.get('kind')).casefold() == 'service')

    product_ids = {_text(binding.get('salla_product_id')) for binding in bindings if is_service(binding)}
    product_ids.discard('')
    if not product_ids:
        return list(bindings)
    query_ids: list[str | int] = sorted(product_ids)
    for identity in sorted(product_ids):
        try:
            number = int(identity)
        except ValueError:
            continue
        # Salla IDs may be stored as BSON integers while bindings use strings.
        # Preserve distinct IDs such as "0123" and never send an oversized int.
        if str(number) == identity and -(2 ** 63) <= number < 2 ** 63:
            query_ids.append(number)
    products = await db[PRODUCTS].find({
        'user_id': user_id,
        '$or': [{key: {'$in': query_ids}} for key in PRODUCT_IDS],
    }, {'_id': 0, **{key: 1 for key in PRODUCT_IDS}, POLICY_FIELD: 1,
        'options': 1, 'options_count': 1, 'variants': 1, 'variants_count': 1,
        'raw_salla.options': 1, 'raw_salla_details.options': 1}).to_list(MAX_PRODUCTS + 1)
    if len(products) > MAX_PRODUCTS:
        _fail('supplier_option_policy_catalog_incomplete')
    matches: dict[str, list[dict]] = {identity: [] for identity in product_ids}
    for product in products:
        for identity in {_text(product.get(key)) for key in PRODUCT_IDS} & product_ids:
            matches[identity].append(product)
    policies = {}
    for identity, candidates in matches.items():
        if not any(POLICY_FIELD in candidate for candidate in candidates):
            continue  # Missing/no-policy legacy catalogs keep their existing behavior.
        if len(candidates) != 1:
            _fail('supplier_option_policy_product_ambiguous', identity)
        policies[identity] = _validated_roles(candidates[0], identity)

    result = []
    for binding in bindings:
        product_id = _text(binding.get('salla_product_id'))
        roles = policies.get(product_id)
        if roles is not None and is_service(binding):
            option_id = _text(binding.get('option_id'))
            if not option_id or option_id not in roles:
                _fail('supplier_option_policy_binding_unresolved', product_id, option_id)
            if roles[option_id] == 'informational':
                continue
        result.append(binding)
    return result
