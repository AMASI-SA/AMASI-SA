from sold_products_report_v2 import aggregate_sold_products, summarize


def report(lines, embroidery=10):
    product = {'salla_product_id': 'p', 'name': 'شال', 'sku': 'SHAWL'}
    context = {
        'product_binding_map': {'p': [{'id': 'bag', 'resource_id': 'bag'}, {'id': 'box', 'resource_id': 'box'}]},
        'option_map': {'p': [
            {'id': 'name', 'option_id': 'name', 'value_id': 'yes', 'mode': 'direct', 'direct_amount': embroidery, 'option_name': 'تطريز الاسم'},
            {'id': 'palm', 'option_id': 'palm', 'value_id': 'yes', 'mode': 'direct', 'direct_amount': 5, 'option_name': 'تطريز نخلة'}]},
        'resources': {'bag': {'name': 'كيس', 'unit_cost': 2}, 'box': {'name': 'بكس', 'unit_cost': 8}},
    }
    return aggregate_sold_products([{'products': lines}], [product], [{'salla_product_id': 'p', 'base_cost': 15}], catalog_context=context)


def item(q, name='no', palm='no'):
    return {'product_id': 'p', 'sku': 'SHAWL', 'quantity': q, 'options_raw': [
        {'option_id': 'name', 'value_id': name}, {'option_id': 'palm', 'value_id': palm}]}


def test_user_example_grouping_and_unit_costs_sum_255():
    row = report([item(2, 'yes'), item(2, 'yes'), item(3, palm='yes'), item(1)])[0]
    assert row['total_cost'] == 255
    assert row['units_sold'] == 8
    assert row['unit_cost_varies'] is True
    assert [(g['unit_cost'], g['quantity'], g['total_cost']) for g in row['cost_groups']] == [(35, 4, 140), (30, 3, 90), (25, 1, 25)]
    assert next(c for c in row['cost_groups'][0]['cells'] if c['key'] == 'option:palm')['status'] == 'not_required'
    assert sum(c['unit_cost'] or 0 for c in row['cost_groups'][0]['cells']) == 35


def test_missing_price_retains_known_amount_and_not_complete():
    row = report([item(4, 'yes')], embroidery=None)[0]
    assert row['total_cost'] is None
    assert row['known_cost_total'] == 100
    assert summarize([row])['total_cost'] is None
    assert summarize([row])['known_cost_total'] == 100
    assert next(c for c in row['cost_groups'][0]['cells'] if c['key'] == 'option:name')['status'] == 'missing_cost'


def test_absent_answers_are_unresolved_not_not_required():
    row = report([{'product_id': 'p', 'quantity': 2}])[0]
    assert row['cost_status'] == 'incomplete'
    assert row['known_cost_total'] == 50
    assert {c['status'] for c in row['cost_groups'][0]['cells'] if c['key'].startswith('option:')} == {'unresolved'}


def test_different_native_choices_do_not_merge_even_when_same_cost():
    row = report([item(1, 'yes'), item(1, palm='yes')], embroidery=5)[0]
    assert len(row['cost_groups']) == 2
    assert row['total_cost'] == 60


def test_frozen_price_difference_preserved_and_separately_grouped():
    from order_cost_authority import calculate_catalog_line_cost, line_fingerprint
    a, b = {**item(2), 'order_item_id': 'a'}, {**item(1), 'order_item_id': 'b'}
    def frozen(line, base):
        cost = calculate_catalog_line_cost(line, product={}, profile={'base_cost': base}, product_bindings=[], option_bindings=[], resources={})
        return {'quantity': line['quantity'], 'cost': cost, 'line_fingerprint': line_fingerprint(line)}
    rows = aggregate_sold_products([{'order_number': 'o', 'products': [a, b]}],
        [{'salla_product_id': 'p'}], [{'salla_product_id': 'p', 'base_cost': 999}],
        authority={'lines': {('o', 'a'): frozen(a, 15), ('o', 'b'): frozen(b, 20)}})
    assert rows[0]['total_cost'] == 50
    assert rows[0]['unit_cost_varies']
    assert [g['unit_cost'] for g in rows[0]['cost_groups']] == [15, 20]
