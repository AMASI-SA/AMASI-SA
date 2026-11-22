"""Configuration input uses provider identities and explicit deferred fields."""
from copy import deepcopy
import asyncio
import pytest
from fastapi import HTTPException
from inventory_option_configuration import (
    configuration_identity, configuration_specifications, option_visibility,
)
from operational_purchase_metadata import option_contract, combinations, configuration, describe


def product_source():
    fields = [
        {'id': 'size', 'name': 'المقاس', 'type': 'radio', 'required': True,
         'values': [{'id': 'two', 'name': 'سنتان'}, {'id': 'three', 'name': 'ثلاث سنوات'}]},
        {'id': 'embroider', 'name': 'هل تريد تطريز الاسم', 'type': 'radio', 'required': True,
         'values': [{'id': 'yes', 'name': 'نعم'}, {'id': 'no', 'name': 'لا'}]},
        {'id': 'shape', 'name': 'شكل التطريز', 'type': 'select', 'required': True,
         'values': [{'id': 'heart', 'name': 'قلب'}, {'id': 'crown', 'name': 'تاج'}]},
        {'id': 'name', 'name': 'الاسم على الفروة', 'type': 'text', 'required': True},
        {'id': 'note', 'name': 'ملاحظة', 'type': 'textarea', 'required': False},
        {'id': 'optional', 'name': 'خيار إضافي', 'type': 'select', 'required': False,
         'values': [{'id': 'one', 'name': 'واحد'}]},
    ]
    raw = deepcopy(fields)
    for field in raw[2:4]:
        field.update(visibility='on_condition', visibility_condition_type='=',
                     visibility_condition_option='embroider', visibility_condition_value='yes')
    return {'options': fields, 'raw_salla_details': {'options': raw},
            'variants': [{'id': 'size-two', 'selections': [{'option_id': 'size', 'value_id': 'two'}]},
                         {'id': 'size-three', 'selections': [{'option_id': 'size', 'value_id': 'three'}]}]}


def product(source=None):
    options, issues = option_contract(source or product_source())
    return {'id': 'fur', 'kind': 'product', 'options': options, 'configuration_issues': issues,
            'selection_combinations': [[{'option_id': 'size', 'value_id': value}] for value in ('two', 'three')]}


def config(answer='yes', name='فاطمة', shape='heart', deferred=()):
    selections = [{'option_id': 'size', 'value_id': 'two'}]
    if answer:
        selections.append({'option_id': 'embroider', 'value_id': answer})
    if shape:
        selections.append({'option_id': 'shape', 'value_id': shape})
    return {'state': 'ready', 'selections': selections,
            'inputs': [{'option_id': 'name', 'value': name}] if name else [],
            'deferred_option_ids': list(deferred)}


def reject(body, definition=None, code=None):
    with pytest.raises(HTTPException) as exc:
        configuration(body, definition or product())
    assert exc.value.status_code == 422
    if code:
        assert exc.value.detail['code'] == code


def test_saved_provider_conditions_merge_by_id_without_mutating_source():
    source = product_source(); before = deepcopy(source)
    result = product(source)
    assert result['configuration_issues'] == []
    fields = {row['id']: row for row in result['options']}
    assert fields['name']['visibility_rules'] == [{'option_id': 'embroider', 'value_id': 'yes', 'operator': '='}]
    assert {key for key, row in fields.items() if row['defer_allowed']} == {'embroider', 'shape', 'name', 'note'}
    assert fields['size']['stock_identity'] is True
    assert fields['optional']['defer_allowed'] is False
    assert source == before


def test_literal_no_hides_children_without_requiring_name_and_stays_fixed():
    saved, labels = configuration(config(answer='no', name='', shape=''), product())
    assert saved['state'] == 'ready' and saved['deferred_option_ids'] == []
    assert {row['option_id'] for row in labels} == {'size', 'embroider'}
    assert saved['selections'][0] == {'option_id': 'embroider', 'value_id': 'no'}


@pytest.mark.parametrize('deferred', [('embroider',), ('name',), ('shape',)])
def test_deferral_is_per_option_and_keeps_other_fixed_values(deferred):
    body = config()
    body['selections'] = [row for row in body['selections'] if row['option_id'] not in deferred]
    body['inputs'] = [row for row in body['inputs'] if row['option_id'] not in deferred]
    if deferred == ('embroider',):
        body['selections'] = [row for row in body['selections'] if row['option_id'] != 'shape']
        body['inputs'] = []
    body['deferred_option_ids'] = list(deferred)
    saved, labels = configuration(body, product())
    assert saved['state'] == 'raw' and saved['deferred_option_ids'] == list(deferred)
    assert 'size' in {row['option_id'] for row in labels}
    if deferred == ('name',):
        assert {'option_id': 'shape', 'value_id': 'heart'} in saved['selections']


def test_optional_unrelated_omission_does_not_create_preparation():
    saved, labels = configuration(config(), product())
    assert saved['state'] == 'ready'
    assert not {'note', 'optional'} & {row['option_id'] for row in labels}


@pytest.mark.parametrize('body', [
    config(answer='no'), config(answer='', deferred=('embroider',)),
    config(deferred=('name',)), config(deferred=('size',)),
    config(deferred=('optional',)), config(deferred=('missing',)),
    config(deferred=('note', 'note')), config(name=''),
])
def test_forged_hidden_conflicting_unknown_physical_and_missing_required_are_rejected(body):
    reject(body)


def test_physical_variant_dimension_cannot_be_unspecified_even_when_raw():
    body = config(answer='', name='', shape='', deferred=('embroider',))
    body['state'] = 'raw'; body['selections'] = []
    reject(body)


def test_new_configuration_requires_a_unique_full_physical_combination():
    definition = product()
    definition['selection_combinations'].append(deepcopy(definition['selection_combinations'][0]))
    reject(config(), definition, 'inventory_purchase_combination_invalid')
    legacy = config(); legacy.pop('deferred_option_ids')
    assert configuration(legacy, definition)[0]['state'] == 'ready'


def conditional_physical_product(*, mapped_variants=True):
    source = {'options': [
        {'id': 'kind', 'name': 'نوع القطعة', 'type': 'radio', 'required': mapped_variants,
         'values': [{'id': 'plain', 'name': 'قطعة عادية'}, {'id': 'chain', 'name': 'سلسلة'}]},
        {'id': 'length', 'name': 'طول السلسلة', 'type': 'select', 'required': True,
         'values': [{'id': 'long', 'name': 'طويلة'}], 'inventory_role': 'physical',
         'show_if': [{'option_id': 'kind', 'value_id': 'chain'}]},
    ], 'variants': [
        {'id': 'plain-variant', 'selections': [{'option_id': 'kind', 'value_id': 'plain'}]},
        {'id': 'chain-variant', 'selections': [{'option_id': 'kind', 'value_id': 'chain'},
                                             {'option_id': 'length', 'value_id': 'long'}]},
    ] if mapped_variants else []}
    options, issues = option_contract(source)
    assert issues == []
    return {'kind': 'product', 'options': options, 'configuration_issues': issues,
            'selection_combinations': combinations(source, options)}


@pytest.mark.parametrize('kind', ['plain', 'chain'])
def test_physical_requirements_follow_visibility_for_each_complete_variant(kind):
    definition = conditional_physical_product()
    selections = [{'option_id': 'kind', 'value_id': kind}]
    if kind == 'chain': selections.append({'option_id': 'length', 'value_id': 'long'})
    body = {'state': 'ready', 'selections': selections, 'inputs': [], 'deferred_option_ids': []}
    assert option_visibility(definition['options'], selections)['length'] == ('hidden' if kind == 'plain' else 'visible')
    saved, labels = configuration(body, definition)
    assert saved == body
    assert {row['option_id'] for row in labels} == ({'kind'} if kind == 'plain' else {'kind', 'length'})


def test_hidden_physical_values_and_incomplete_visible_dimensions_are_rejected():
    definition = conditional_physical_product()
    hidden = {'state': 'ready', 'selections': [{'option_id': 'kind', 'value_id': 'plain'},
              {'option_id': 'length', 'value_id': 'long'}], 'inputs': [], 'deferred_option_ids': []}
    reject(hidden, definition)
    missing = {**hidden, 'selections': [{'option_id': 'kind', 'value_id': 'chain'}]}
    reject(missing, definition)


def test_pending_physical_dimension_requires_parent_answer_even_without_variant_maps():
    definition = conditional_physical_product(mapped_variants=False)
    body = {'state': 'ready', 'selections': [], 'inputs': [], 'deferred_option_ids': []}
    assert option_visibility(definition['options'], [])['length'] == 'pending'
    reject(body, definition, 'inventory_purchase_physical_option_required')


def test_deferred_parent_is_pending_not_false_and_hidden_parent_cascades():
    definition = product()
    states = option_visibility(definition['options'], [{'option_id': 'size', 'value_id': 'two'}], ['embroider'])
    assert states['name'] == states['shape'] == 'pending'
    source = product_source()
    source['options'][3]['show_if'] = [{'option_id': 'shape', 'value_id': 'heart'}]
    fields = product(source)['options']
    assert option_visibility(fields, config(answer='no', name='', shape='')['selections'])['name'] == 'hidden'


def test_not_equals_requires_an_answer_and_evaluates_with_parent_visibility():
    source = product_source()
    source['raw_salla_details']['options'][3]['visibility_condition_type'] = '!='
    definition = product(source)
    assert option_visibility(definition['options'], [])['name'] == 'pending'
    assert option_visibility(definition['options'], [{'option_id': 'embroider', 'value_id': 'no'}])['name'] == 'visible'
    assert option_visibility(definition['options'], [{'option_id': 'embroider', 'value_id': 'yes'}])['name'] == 'hidden'


@pytest.mark.parametrize('fault', ['cycle', 'missing_parent', 'missing_value', 'operator', 'bad_schema'])
def test_invalid_conditional_catalog_fails_closed(fault):
    source = product_source()
    field = source['raw_salla_details']['options'][3]
    if fault == 'cycle':
        source['options'][1]['show_if'] = [{'option_id': 'shape', 'value_id': 'heart'}]
    elif fault == 'missing_parent': field['visibility_condition_option'] = 'removed'
    elif fault == 'missing_value': field['visibility_condition_value'] = 'removed'
    elif fault == 'operator': field['visibility_condition_type'] = 'contains'
    else: source['options'][3]['show_if'] = {'option_id': 'embroider', 'value_id': 'yes'}
    definition = product(source)
    assert definition['configuration_issues']
    assert not any(row['defer_allowed'] for row in definition['options'])
    reject(config(), definition)
    legacy = config(); legacy.pop('deferred_option_ids')
    reject(legacy, definition)


def test_customization_inference_does_not_cross_a_physical_variant_dimension():
    source = product_source()
    source['options'][1]['show_if'] = [{'option_id': 'size', 'value_id': 'two'}]
    definition = product(source)
    assert not any(row['defer_allowed'] for row in definition['options'] if row['id'] != 'note')
    source['options'][3]['inventory_role'] = 'customization'
    assert next(row for row in product(source)['options'] if row['id'] == 'name')['defer_allowed']


def test_canonical_condition_takes_precedence_over_saved_provider_copy():
    source = product_source()
    source['options'][3].update(visibility='always')
    assert next(row for row in product(source)['options'] if row['id'] == 'name')['visibility_rules'] == []


def test_legacy_request_shape_survives_and_raw_text_policy_is_unchanged():
    body = config(); body.pop('deferred_option_ids')
    saved, _ = configuration(body, product())
    assert set(saved) == {'state', 'selections', 'inputs'}
    body['state'] = 'raw'
    reject(body, code='inventory_purchase_raw_customization')


def test_stable_identity_separates_question_and_name_even_when_labels_collapse():
    saved, _ = configuration(config(), product())
    identity = configuration_identity(saved)
    assert identity['version'] == 2 and len(identity['fixed_options']) == 4
    specs = configuration_specifications(saved)
    assert len(specs) == 4 and 'فاطمة' in specs.values()
    changed = deepcopy(saved); changed['inputs'][0]['value'] = 'عبير'
    assert configuration_identity(changed) != identity
    source = product_source(); source['options'][3]['name'] = 'اسم جديد'
    renamed, _ = configuration(config(), product(source))
    assert configuration_identity(renamed) == identity


def test_receipt_tracking_can_defer_location_without_accepting_a_forged_location():
    definition = product()
    body = {'kind': 'product', 'item_id': 'fur', 'purchase_configuration': config()}
    args = (None, 'owner', body, {('product', 'fur'): definition}, [])
    with pytest.raises(HTTPException): asyncio.run(describe(*args))
    result = asyncio.run(describe(*args, allow_missing_location=True))
    assert not {'location', 'location_id', 'location_name'} & set(result)
    body['location_id'] = 'forged'
    with pytest.raises(HTTPException): asyncio.run(describe(*args, allow_missing_location=True))
