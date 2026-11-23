"""Customer side additions do not describe a stored piece or its preparation."""
from copy import deepcopy

import pytest
from fastapi import HTTPException

from inventory_option_configuration import configuration_identity, option_visibility
from operational_purchase_metadata import configuration, option_contract, _products, require_inventory_option_policy
from test_inventory_option_configuration import product_source, product, config


def classified_source():
    row = product_source()
    row.update(mezan_product_id='fur', name='قطعة اختبار')
    row['options'].extend([
        {'id': 'card', 'name': 'هل تريد كرت إهداء؟', 'type': 'radio', 'required': True,
         'values': [{'id': 'yes', 'name': 'نعم'}, {'id': 'no', 'name': 'لا'}]},
        {'id': 'card-text', 'name': 'نص كرت الإهداء', 'type': 'textarea', 'required': True,
         'show_if': [{'option_id': 'card', 'value_id': 'yes'}]},
    ])
    row['mezan_inventory_option_policy'] = {'revision': 1, 'roles': {
        'size': 'physical', 'embroider': 'customization', 'shape': 'customization',
        'name': 'customization', 'note': 'informational', 'optional': 'informational',
        'card': 'informational', 'card-text': 'informational',
    }}
    return row


def test_saved_local_roles_apply_by_id_and_preserve_provider_data():
    source = classified_source(); before = deepcopy(source)
    fields, issues = option_contract(source)
    assert issues == []
    by_id = {row['id']: row for row in fields}
    assert by_id['card']['inventory_role'] == 'informational'
    assert by_id['card-text']['inventory_role'] == 'informational'
    assert not by_id['card']['stock_identity'] and not by_id['card']['defer_allowed']
    assert by_id['size']['role_locked'] is True and by_id['size']['stock_identity']
    assert by_id['name']['defer_allowed']
    assert source == before
    metadata = _products([source])[0]
    assert metadata['inventory_option_policy_revision'] == 1


def test_required_customer_card_fields_are_not_required_for_ready_stock():
    definition = product(classified_source())
    saved, labels = configuration(config(), definition)
    assert saved['state'] == 'ready'
    assert not {'card', 'card-text', 'note', 'optional'} & {row['option_id'] for row in labels}
    assert configuration_identity(saved) == configuration_identity(config())
    # The customer's dependency is still available for order/packing display.
    assert option_visibility(definition['options'], [{'option_id': 'card', 'value_id': 'yes'}])['card-text'] == 'visible'
    assert option_visibility(definition['options'], [{'option_id': 'card', 'value_id': 'no'}])['card-text'] == 'hidden'


def test_only_actual_customization_can_leave_stock_pending():
    body = config(name='', deferred=('name',))
    saved, _ = configuration(body, product(classified_source()))
    assert saved['state'] == 'raw' and saved['deferred_option_ids'] == ['name']
    body = config(answer='no', name='', shape='')
    saved, _ = configuration(body, product(classified_source()))
    assert saved['state'] == 'ready'


@pytest.mark.parametrize('group,entry', [
    ('selections', {'option_id': 'card', 'value_id': 'yes'}),
    ('inputs', {'option_id': 'note', 'value': 'رسالة العميل'}),
    ('deferred_option_ids', 'card'),
])
def test_stale_side_values_cannot_enter_stock_identity_silently(group, entry):
    body = config(); body[group].append(entry)
    with pytest.raises(HTTPException) as exc:
        configuration(body, product(classified_source()))
    assert exc.value.status_code == 422
    assert exc.value.detail['code'] in {'inventory_purchase_side_option', 'inventory_purchase_deferred_option_invalid'}


def test_explicit_physical_roles_protect_mixed_text_connected_group():
    source = classified_source()
    source['variants'] = []
    source['options'][0]['show_if'] = [{'option_id': 'embroider', 'value_id': 'yes'}]
    fields, issues = option_contract(source)
    assert issues == []
    size = next(row for row in fields if row['id'] == 'size')
    assert size['stock_identity'] and not size['defer_allowed']
    assert not size['role_locked']


def test_informational_parent_cannot_hide_an_actual_preparation_requirement():
    source = classified_source()
    source['mezan_inventory_option_policy']['roles']['card-text'] = 'customization'
    _, issues = option_contract(source)
    assert issues and any('جانبي' in issue for issue in issues)


@pytest.mark.parametrize('change', ['variant_demoted', 'missing_role', 'unknown_role', 'removed_option'])
def test_incompatible_policy_or_catalog_requires_review(change):
    source = classified_source(); roles = source['mezan_inventory_option_policy']['roles']
    if change == 'variant_demoted': roles['size'] = 'informational'
    if change == 'missing_role': roles.pop('name')
    if change == 'unknown_role': roles['name'] = 'ignore-everything'
    if change == 'removed_option': roles['removed'] = 'physical'
    _, issues = option_contract(source)
    assert issues


def test_customer_side_text_does_not_infer_preparation_for_unclassified_parent():
    source = {'options': [
        {'id': 'card', 'name': 'كرت', 'type': 'radio', 'required': False,
         'values': [{'id': 'yes', 'name': 'نعم'}]},
        {'id': 'text', 'name': 'رسالة', 'type': 'text', 'required': False,
         'inventory_role': 'informational', 'show_if': [{'option_id': 'card', 'value_id': 'yes'}]},
    ]}
    fields, issues = option_contract(source)
    assert issues == []
    assert not any(field['defer_allowed'] for field in fields)


@pytest.mark.parametrize('revision', [None, 0, 2, True, '1'])
def test_stock_request_requires_the_reviewed_policy_revision(revision):
    definition = _products([classified_source()])[0]
    with pytest.raises(HTTPException) as exc:
        require_inventory_option_policy({'inventory_option_policy_revision': revision}, definition)
    assert exc.value.status_code == 409
    assert exc.value.detail['code'] == 'inventory_option_policy_changed'


def test_current_policy_and_legacy_command_shapes_remain_supported():
    definition = _products([classified_source()])[0]
    require_inventory_option_policy({'inventory_option_policy_revision': 1}, definition)
    require_inventory_option_policy({}, {'kind': 'product'})
    from operational_opening_inventory import OpeningRequest
    body = {'request_id': 'legacy-stock-command', 'lines': [{'kind': 'product', 'item_id': 'fur',
        'quantity': 1, 'unit_cost': '10', 'location_id': 'shelf', 'purchase_configuration': config()}]}
    assert OpeningRequest.model_validate(body).model_dump() == body


def test_optional_physical_field_can_be_empty_but_required_dimensions_cannot():
    source = classified_source()
    source['mezan_inventory_option_policy']['roles']['optional'] = 'physical'
    saved, _ = configuration(config(), product(source))
    assert saved['state'] == 'ready'
    missing_size = config(); missing_size['selections'] = [row for row in missing_size['selections'] if row['option_id'] != 'size']
    with pytest.raises(HTTPException): configuration(missing_size, product(source))


@pytest.mark.parametrize('roles', [[], {'size': []}, None])
def test_malformed_local_policy_is_an_issue_not_an_unhandled_error(roles):
    source = classified_source(); source['mezan_inventory_option_policy']['roles'] = roles
    assert option_contract(source)[1]


def test_cached_classification_is_reused_in_purchases_without_mutating_provider_options():
    source = classified_source()
    source['raw_salla'] = {'options': source.pop('options')}
    before = deepcopy(source)
    purchase = _products([source])[0]
    stock = _products([source], opening_cached_options=True)[0]
    assert purchase == stock and purchase['configuration_issues'] == []
    assert configuration(config(), purchase)[0]['state'] == 'ready'
    assert source == before
    source['options'] = []  # An explicit canonical empty catalog is authoritative.
    assert _products([source])[0]['configuration_issues']
