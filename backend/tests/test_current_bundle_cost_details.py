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
    with pytest.raises(_Unresolved, match='sale_selection_unavailable'):
        _build_line_cost({'quantity': 1, 'options_raw': [{'option_id': 'other', 'values': [{'value_id': 'yes'}]}]},
                         'p', {}, products, {}, {'p': [{'id': 'embroidery', 'option_id': 'name', 'value_id': 'yes', 'mode': 'direct', 'direct_amount': 10}]}, {}, {})


def test_missing_selected_cost_still_fails_closed():
    with pytest.raises(_Unresolved, match='resource_cost_missing'):
        _build_line_cost({'quantity': 1}, 'p', {'p': {'items': [{'id': 'bag', 'kind': 'resource', 'resource_id': 'bag'}]}},
                         {'p': product('Shawl', 15)}, {}, {}, {}, {'bag': {'name': 'Bag'}})


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
    with pytest.raises(_Unresolved, match='sale_selection_unavailable'):
        _build_line_cost({'quantity': 1, 'options_raw': [{'option_id': 'other', 'values': [{'value_id': 'yes'}]}]},
                         'p', {'p': {'items': [{'kind': 'resource', 'resource_id': 'rose', 'condition': {'option_id': 'rose', 'value_id': 'yes'}}]}},
                         {'p': product('Shawl', 15, opts)}, {}, {}, {}, {'rose': {'unit_cost': 40}})
