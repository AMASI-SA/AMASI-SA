from copy import deepcopy
from decimal import Decimal
import pytest
from current_bundle_reporting_estimate import _build_line_cost, _Unresolved


def product(name, cost, options=None):
    return {'name': name, 'cost_price': cost, 'options': options or []}


def test_detail_cells_are_per_parent_unit_with_nested_multipliers():
    products = {'p': product('Shawl', 15), 'child': product('Gift box', 3)}
    definitions = {'p': {'items': [{'id': 'gift', 'kind': 'product', 'product_id': 'child', 'quantity': 2}]}}
    resources = {'bag': {'name': 'Bag', 'unit_cost': 1.5, 'kind': 'component'}}
    bindings = {'child': [{'id': 'bag-binding', 'resource_id': 'bag', 'quantity': 2}]}
    result = _build_line_cost({'quantity': 4}, 'p', definitions, products, {}, {}, bindings, resources)
    cells = {c['key']: c for c in result['cost_detail_cells']}
    assert cells['root:base']['unit_cost'] == 15
    assert cells['root:recipe:gift']['unit_cost'] == 6
    assert cells['root:recipe:gift:component:bag-binding']['unit_cost'] == 6
    assert sum(Decimal(str(c['unit_cost'])) for c in cells.values()) == 27
    assert result['line_total'] == 108


def test_selected_and_declined_services_share_stable_column_identity():
    option = {'id': 'name', 'values': [{'id': 'yes', 'name': 'Yes'}, {'id': 'no', 'name': 'No'}]}
    products = {'p': product('Shawl', 15, [option])}
    binding = {'id': 'embroidery', 'option_id': 'name', 'value_id': 'yes',
               'mode': 'direct', 'direct_amount': 10, 'quantity': 1, 'name': 'Embroidery'}
    results = [_build_line_cost({'quantity': 3, 'options_raw': [{'option_id': 'name', 'values': [{'value_id': choice}]}]},
                               'p', {}, products, {}, {'p': [binding]}, {}, {}) for choice in ('yes', 'no')]
    yes, no = [next(c for c in r['cost_detail_cells'] if c['key'] == 'root:option:embroidery') for r in results]
    assert yes == {'key': 'root:option:embroidery', 'label': 'Embroidery', 'kind': 'service', 'status': 'costed', 'unit_cost': 10}
    assert no['status'] == 'not_required' and no['unit_cost'] is None
    assert [r['line_total'] for r in results] == [75, 45]


def test_missing_optional_answer_is_never_labeled_not_required():
    opts = [{'id': oid, 'values': [{'id': 'yes', 'name': 'Yes'}, {'id': 'no', 'name': 'No'}]} for oid in ('name', 'other')]
    products = {'p': product('Shawl', 15, opts)}
    result = _build_line_cost({'quantity': 1, 'options_raw': [{'option_id': 'other', 'values': [{'value_id': 'yes'}]}]},
                         'p', {}, products, {}, {'p': [{'id': 'embroidery', 'option_id': 'name', 'value_id': 'yes', 'mode': 'direct', 'direct_amount': 10}]}, {}, {})

    assert result['calculation_cost_available'] is False
    assert result['line_total'] == 15
    assert result['cost_detail_cells'][-1]['status'] == 'unresolved'
    assert result['missing_cost_reasons']


def test_missing_selected_cost_still_fails_closed():
    result = _build_line_cost({'quantity': 1}, 'p', {'p': {'items': [{'id': 'bag', 'kind': 'resource', 'resource_id': 'bag'}]}},
                         {'p': product('Shawl', 15)}, {}, {}, {}, {'bag': {'name': 'Bag'}})

    assert result['calculation_cost_available'] is False
    assert result['line_total'] == 15
    assert result['cost_detail_cells'][-1]['status'] == 'missing_cost'
    assert result['missing_cost_reasons']


def test_missing_included_answer_does_not_make_incremental_cost_unknown():
    opts = [{'id': oid, 'values': [{'id': 'yes', 'name': 'Yes'}, {'id': 'no', 'name': 'No'}]} for oid in ('name', 'other')]
    result = _build_line_cost({'quantity': 2, 'options_raw': [{'option_id': 'other', 'values': [{'value_id': 'yes'}]}]},
                             'p', {}, {'p': product('Shawl', 15, opts)}, {}, {'p': [
                                 {'id': 'embroidery', 'option_id': 'name', 'value_id': 'yes', 'mode': 'direct', 'direct_amount': 10, 'cost_treatment': 'included_in_base'}]}, {}, {})
    assert result['calculation_cost_available'] is True
    assert result['line_total'] == 30
    assert result['cost_detail_cells'][1]['status'] == 'included_in_base'
    assert result['cost_detail_cells'][1]['unit_cost'] == 0


def test_unanswered_recipe_condition_fails_even_when_other_option_answered():
    opts = [{'id': oid, 'values': [{'id': 'yes', 'name': 'Yes'}, {'id': 'no', 'name': 'No'}]} for oid in ('rose', 'other')]
    result = _build_line_cost({'quantity': 1, 'options_raw': [{'option_id': 'other', 'values': [{'value_id': 'yes'}]}]},
                         'p', {'p': {'items': [{'kind': 'resource', 'resource_id': 'rose', 'condition': {'option_id': 'rose', 'value_id': 'yes'}}]}},
                         {'p': product('Shawl', 15, opts)}, {}, {}, {}, {'rose': {'unit_cost': 40}})

    assert result['calculation_cost_available'] is False
    assert result['line_total'] == 15
    assert result['cost_detail_cells'][-1]['status'] == 'unresolved'
    assert result['missing_cost_reasons']


def test_required_missing_answer_retains_known_evidence():
    result = _build_line_cost({'quantity': 2}, 'p', {}, {'p': product('Shawl', 15, [
        {'id': 'name', 'required': True, 'values': [{'id': 'yes'}]}])}, {},
        {'p': [{'id': 'embroidery', 'option_id': 'name', 'value_id': 'yes', 'mode': 'direct', 'direct_amount': 10}]}, {}, {})
    assert result['line_total'] == 30
    assert not result['calculation_cost_available']
    assert any(c['status'] == 'unresolved' for c in result['cost_detail_cells'])


def test_missing_fx_remains_structurally_unresolved():
    with pytest.raises(_Unresolved, match='fx_invalid'):
        _build_line_cost({'quantity': 1}, 'p', {'p': {'items': [
            {'kind': 'resource', 'resource_id': 'bag'}]}}, {'p': product('Shawl', 15)}, {}, {}, {},
            {'bag': {'unit_cost': 2, 'cost_currency': 'USD'}})


@pytest.mark.asyncio
async def test_loader_retains_incomplete_cost_evidence():
    from current_bundle_reporting_estimate import load_current_bundle_estimates
    class Collection:
        def __init__(self, rows): self.rows = rows
        def find(self, *args, **kwargs): return self
        async def to_list(self, length): return self.rows
    data = {
        'mezan_bundle_definitions_v2': [{'product_id': 'p', 'items': [{'id': 'bag', 'kind': 'resource', 'resource_id': 'bag'}]}],
        'mezan_products_v2': [{**product('Shawl', 15), 'salla_product_id': 'p'}],
        'mezan_cost_resources_v2': [{'id': 'bag', 'name': 'Bag'}],
    }
    class DB:
        def __getitem__(self, key): return Collection(data.get(key, []))
    result = await load_current_bundle_estimates(DB(), 'owner', [
        {'order_number': 'order', 'products': [{'order_item_id': 'line', 'product_id': 'p', 'quantity': 2}]}],
        {'composite_products': ['p'], 'lines': {}})
    cost = result[('order', 'line')]
    assert cost['line_total'] == 30
    assert not cost['calculation_cost_available']
    assert cost['cost_detail_cells'][-1]['status'] == 'missing_cost'


@pytest.mark.parametrize('missing_child', [False, True])
def test_missing_base_retains_priced_components(missing_child):
    products = {'p': product('Shawl', None)}
    definitions = {}
    bindings = {'p': [{'id': 'bag-binding', 'resource_id': 'bag'}]}
    if missing_child:
        products = {'p': product('Shawl', 15), 'child': product('Box', None)}
        definitions = {'p': {'items': [{'id': 'gift', 'kind': 'product', 'product_id': 'child', 'quantity': 2}]}}
        bindings = {'child': [{'id': 'bag-binding', 'resource_id': 'bag'}]}
    result = _build_line_cost({'quantity': 3}, 'p', definitions, products, {}, {}, bindings,
                             {'bag': {'name': 'Bag', 'unit_cost': 2}})
    assert not result['calculation_cost_available']
    assert result['line_total'] == (57 if missing_child else 6)
    assert any(c['status'] == 'missing_cost' for c in result['cost_detail_cells'])
    assert any('base_missing' in reason for reason in result['missing_cost_reasons'])
    if not missing_child:
        assert not result['uses_salla_fallback']  # absent cost is not a Salla price


@pytest.mark.parametrize('base', ['invalid', -1])
def test_recorded_invalid_base_stays_structurally_unresolved(base):
    with pytest.raises(_Unresolved, match='base_cost_or_fx_invalid'):
        _build_line_cost({'quantity': 1}, 'p', {}, {'p': product('Shawl', base)}, {}, {}, {}, {})


def conditional_message_bundle(answer='no', text=None, field_type='text'):
    """Synthetic saved provider condition omitted by normalized catalog rows."""
    question = {'id': 'customize', 'name': 'Change message', 'type': 'radio',
                'required': True, 'values': [
                    {'id': 'yes', 'name': 'Yes'}, {'id': 'no', 'name': 'No'}]}
    field = {'id': 'message', 'name': 'Message', 'type': field_type,
             'required': True, 'values': []}
    saved_field = {**field, 'visibility': 'on_condition',
                   'visibility_condition_option': 'customize',
                   'visibility_condition_value': 'yes',
                   'visibility_condition_type': '='}
    catalog = product('Synthetic bundle', None, [question, field])
    catalog['raw_salla'] = {'options': [deepcopy(question), deepcopy(saved_field)]}
    catalog['raw_salla_details'] = deepcopy(catalog['raw_salla'])
    rows = [] if answer is None else [{
        'id': 'checkout-parent-row', 'product_option_id': 'customize',
        'value': {'id': answer, 'name': answer.title(), 'price': 0},
    }]
    if text is not None:
        rows.append({'id': 'checkout-message-row', 'product_option_id': 'message',
                     'value': text, 'price': 0})
    return {
        'item': {'quantity': 2, 'options_raw': rows}, 'root': 'p',
        'definitions': {'p': {'items': [
            {'id': 'insert', 'kind': 'resource', 'resource_id': 'insert'}]}},
        'products': {'p': catalog}, 'profiles': {'p': {'base_cost': 12}},
        'options': {},
        'bindings': {'p': [{'id': 'bag-link', 'resource_id': 'bag'}]},
        'resources': {'insert': {'name': 'Insert', 'kind': 'component', 'unit_cost': 7},
                      'bag': {'name': 'Bag', 'kind': 'component', 'unit_cost': 3}},
    }


def message_binding(marker='filled'):
    return {'id': 'message-work', 'option_id': 'message', 'value_id': marker,
            'mode': 'direct', 'direct_amount': 5, 'quantity': 1,
            'option_name': 'Message', 'name': 'Message work'}


def message_cost(args):
    before = deepcopy(args)
    result = _build_line_cost(**args)
    assert args == before
    assert 'PRIVATE_FIXTURE_TEXT' not in repr(result)
    return result


@pytest.mark.parametrize('field_type', ['text', 'textarea'])
@pytest.mark.parametrize('answer,text', [
    ('no', None), ('yes', 'PRIVATE_FIXTURE_TEXT'), ('yes', None),
])
def test_unpriced_required_conditional_text_is_not_a_missing_service(answer, text, field_type):
    result = message_cost(conditional_message_bundle(answer, text, field_type))
    # Two bundles, each with base 12 + insert 7 + bag 3. Text changes no cost.
    assert result['line_total'] == 44
    assert result['calculation_cost_available'] is True
    assert result['mezan_cost_complete'] is True
    assert result['missing_cost_reasons'] == []
    assert result['selected_options_total'] == 0
    cells = {cell['key']: cell for cell in result['cost_detail_cells']}
    assert {key: cell['unit_cost'] for key, cell in cells.items()} == {
        'root:base': 12, 'root:recipe:insert': 7, 'root:component:bag-link': 3,
    }
    assert all(cell['kind'] != 'service' for cell in cells.values())


@pytest.mark.parametrize('marker', ['filled', '__option__'])
@pytest.mark.parametrize('answer,text,expected_status,complete,total', [
    ('no', None, 'not_required', True, 44),
    ('yes', 'PRIVATE_FIXTURE_TEXT', 'costed', True, 54),
    ('yes', None, 'unresolved', False, 44),
    ('no', 'PRIVATE_FIXTURE_TEXT', 'unresolved', False, 44),
    (None, None, 'unresolved', False, 44),
])
def test_actual_conditional_text_cost_uses_choice_evidence(
        marker, answer, text, expected_status, complete, total):
    args = conditional_message_bundle(answer, text)
    args['options'] = {'p': [message_binding(marker)]}
    result = message_cost(args)
    actual = next(cell for cell in result['cost_detail_cells']
                  if cell['key'] == 'root:option:message-work')
    assert actual['status'] == expected_status
    assert actual['unit_cost'] == (5 if expected_status == 'costed' else None)
    assert result['line_total'] == total
    assert result['calculation_cost_available'] is complete
    assert bool(result['missing_cost_reasons']) is not complete
    assert not any(cell['key'] == 'root:choice:message'
                   for cell in result['cost_detail_cells'])


@pytest.mark.parametrize('marker,expected_status,total', [
    ('filled', 'not_required', 44), ('__option__', 'costed', 54),
])
@pytest.mark.parametrize('text', ['', '   ', {'value': ''}, {'id': 'provider-row'}])
def test_present_empty_text_keeps_actual_binding_presence_semantics(marker, expected_status, total, text):
    args = conditional_message_bundle('yes', text)
    args['options'] = {'p': [message_binding(marker)]}
    result = message_cost(args)
    assert result['line_total'] == total
    assert result['calculation_cost_available'] is True
    assert next(cell for cell in result['cost_detail_cells']
                if cell['key'] == 'root:option:message-work')['status'] == expected_status


@pytest.mark.parametrize('marker', ['filled', '__option__'])
@pytest.mark.parametrize('included_source', ['binding', 'profile'])
def test_conditional_text_included_in_base_needs_no_incremental_cost(marker, included_source):
    args = conditional_message_bundle('yes')
    binding = message_binding(marker)
    args['options'] = {'p': [binding]}
    if included_source == 'binding':
        binding['cost_treatment'] = 'included_in_base'
    else:
        args['profiles']['p']['composition_components'] = {
            'option_resource:message-work': {'cost_treatment': 'included_in_base'}}
    result = message_cost(args)
    assert result['line_total'] == 44
    assert result['calculation_cost_available'] is True
    assert result['missing_cost_reasons'] == []
    actual = next(cell for cell in result['cost_detail_cells']
                  if cell['key'] == 'root:option:message-work')
    assert actual['status'] == 'included_in_base'
    assert actual['unit_cost'] == 0


@pytest.mark.parametrize('answer,complete,total,status', [
    ('yes', True, 54, 'costed'), (None, False, 44, 'unresolved'),
])
def test_unpriced_text_does_not_hide_an_actual_enum_service_cost(answer, complete, total, status):
    args = conditional_message_bundle(answer)
    args['options'] = {'p': [{**message_binding(), 'option_id': 'customize',
                              'value_id': 'yes', 'option_name': 'Change message'}]}
    result = message_cost(args)
    # A zero customer surcharge does not erase the independently recorded cost.
    assert result['line_total'] == total
    assert result['calculation_cost_available'] is complete
    assert next(cell for cell in result['cost_detail_cells']
                if cell['key'] == 'root:option:message-work')['status'] == status


def test_unique_legacy_value_only_answer_preserves_enum_cost_identity():
    args = conditional_message_bundle('yes')
    args['item']['options_raw'] = [{'value': 'Yes'}]
    args['options'] = {'p': [{**message_binding(), 'option_id': 'customize',
                              'value_id': 'yes', 'option_name': 'Change message'}]}
    result = message_cost(args)
    assert result['line_total'] == 54
    assert result['calculation_cost_available'] is True
    assert result['missing_cost_reasons'] == []
    actual = next(cell for cell in result['cost_detail_cells']
                  if cell['key'] == 'root:option:message-work')
    assert actual['status'] == 'costed'
    assert actual['unit_cost'] == 5


def test_conflicting_explicit_enum_value_aliases_cannot_be_canonicalized_away():
    args = conditional_message_bundle('yes')
    args['item']['options_raw'] = [{
        'option_id': 'customize', 'value_id': 'yes',
        'values': [{'value_id': 'no'}],
    }]
    args['options'] = {'p': [{**message_binding(), 'option_id': 'customize',
                              'value_id': 'yes', 'option_name': 'Change message'}]}
    before = deepcopy(args)
    with pytest.raises(_Unresolved, match='conflict'):
        _build_line_cost(**args)
    assert args == before


def test_conflicting_explicit_text_value_aliases_cannot_be_canonicalized_away():
    args = conditional_message_bundle('yes')
    args['item']['options_raw'].append({
        'option_id': 'message', 'value_id': 'value-a',
        'value': {'value_id': 'value-b', 'text': 'PRIVATE_FIXTURE_TEXT'},
    })
    args['options'] = {'p': [message_binding('filled')]}
    before = deepcopy(args)
    with pytest.raises(_Unresolved, match='conflict'):
        _build_line_cost(**args)
    assert args == before


@pytest.mark.parametrize('identity', [
    'invalid_explicit_parent_value', 'invalid_explicit_value_only',
    'retired_catalog_value', 'retired_catalog_option',
])
def test_invalid_or_retired_identities_cannot_be_relabelled_to_waive_actual_text_cost(identity):
    args = conditional_message_bundle('no')
    catalog = args['products']['p']
    if identity == 'invalid_explicit_parent_value':
        args['item']['options_raw'][0]['value_id'] = 'retired-value'
    elif identity == 'invalid_explicit_value_only':
        args['item']['options_raw'] = [{'value_id': 'retired-value', 'value': 'No'}]
    elif identity == 'retired_catalog_value':
        catalog['raw_salla']['options'][0]['values'] = [
            {'id': 'yes', 'name': 'Yes'}, {'id': 'retired-no', 'name': 'No'}]
        args['item']['options_raw'][0]['value'] = {'id': 'retired-no', 'name': 'No'}
    else:
        catalog['raw_salla']['options'][0]['id'] = 'retired-parent'
        args['item']['options_raw'] = [{
            'id': 'retired-parent', 'name': 'Change message',
            'value': {'id': 'no', 'name': 'No'},
        }]
    args['options'] = {'p': [message_binding('filled')]}
    before = deepcopy(args)
    with pytest.raises(_Unresolved):
        _build_line_cost(**args)
    assert args == before


def test_unknown_checkout_row_ids_keep_unique_current_label_recovery():
    args = conditional_message_bundle('no')
    args['item']['options_raw'] = [{
        'id': 'unknown-checkout-option-row', 'name': 'Change message',
        'value': {'id': 'unknown-checkout-value-row', 'name': 'No'},
    }]
    args['options'] = {'p': [message_binding('filled')]}
    result = message_cost(args)
    assert result['line_total'] == 44
    assert result['calculation_cost_available'] is True
    assert result['missing_cost_reasons'] == []
    actual = next(cell for cell in result['cost_detail_cells']
                  if cell['key'] == 'root:option:message-work')
    assert actual['status'] == 'not_required'
    assert actual['unit_cost'] is None


def test_unpriced_text_does_not_waive_an_unanswered_recipe_condition():
    args = conditional_message_bundle('no')
    args['products']['p']['options'].append({
        'id': 'include-insert', 'type': 'radio', 'required': False,
        'values': [{'id': 'yes', 'name': 'Yes'}, {'id': 'no', 'name': 'No'}]})
    args['definitions']['p']['items'][0]['condition'] = {
        'option_id': 'include-insert', 'value_id': 'yes'}
    result = message_cost(args)
    assert result['line_total'] == 30  # Only two bases and two bags are proved.
    assert result['calculation_cost_available'] is False
    assert next(cell for cell in result['cost_detail_cells']
                if cell['key'] == 'root:recipe:insert')['status'] == 'unresolved'
    assert 'current_bundle_sale_selection_unavailable:root:recipe:insert' in result['missing_cost_reasons']


@pytest.mark.parametrize('size,complete', [(None, False), ('small', True)])
def test_required_size_enum_guard_and_selected_variant_cost_are_preserved(size, complete):
    args = conditional_message_bundle('no')
    args['products']['p']['options'].append({
        'id': 'size', 'type': 'select', 'required': True,
        'values': [{'id': 'small'}, {'id': 'large'}]})
    args['item']['variant_id'] = 'small-variant'
    args['profiles']['p']['variant_costs'] = {'small-variant': 17}
    if size is not None:
        args['item']['options_raw'].append({'option_id': 'size', 'values': [{'value_id': size}]})
    result = message_cost(args)
    assert result['line_total'] == 54  # 2 * (variant base 17 + insert 7 + bag 3).
    assert result['calculation_cost_available'] is complete
    assert next(cell for cell in result['cost_detail_cells']
                if cell['key'] == 'root:base')['unit_cost'] == 17
    assert any(reason.endswith(':size') for reason in result['missing_cost_reasons']) is not complete
