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
    a, b = {**item(2), 'order_item_id': 'a', 'variant_id': 'red'}, {**item(1), 'order_item_id': 'b', 'variant_id': 'blue'}
    def frozen(line, base):
        cost = calculate_catalog_line_cost(line, product={}, profile={'base_cost': base}, product_bindings=[], option_bindings=[], resources={})
        return {'quantity': line['quantity'], 'cost': cost, 'line_fingerprint': line_fingerprint(line)}
    rows = aggregate_sold_products([{'order_number': 'o', 'products': [a, b]}],
        [{'salla_product_id': 'p'}], [{'salla_product_id': 'p', 'base_cost': 999}],
        authority={'lines': {('o', 'a'): frozen(a, 15), ('o', 'b'): frozen(b, 20)}})
    assert rows[0]['total_cost'] == 50
    assert rows[0]['unit_cost_varies']
    assert [g['unit_cost'] for g in rows[0]['cost_groups']] == [15, 20]


def actual_bundle_report(amounts):
    from order_cost_authority import _bundle_cost, line_fingerprint
    line = {**item(len(amounts)), 'order_item_id': 'actual'}
    units = [{'unit_index': index, 'state': 'consumed', 'bundle_leaves': [
        {'leaf_id': 'frozen-shirt', 'kind': 'product', 'product_id': 'shirt',
         'actual_cost': amount, 'name': 'القميص'}]} for index, amount in enumerate(amounts)]
    cost = _bundle_cost({'estimated_cost': 999}, len(amounts), units)
    snapshot = {'quantity': len(amounts), 'cost': cost, 'line_fingerprint': line_fingerprint(line)}
    return aggregate_sold_products([{'order_number': 'o', 'products': [line]}],
        [{'salla_product_id': 'p'}], [{'salla_product_id': 'p', 'base_cost': 9999}],
        authority={'lines': {('o', 'actual'): snapshot}})[0]


def test_actual_units_with_different_frozen_costs_split_without_repricing():
    row = actual_bundle_report([20, 30])
    assert row['total_cost'] == 50
    assert row['unit_cost_varies']
    assert [(g['unit_cost'], g['quantity'], g['total_cost']) for g in row['cost_groups']] == [(20, 1, 20), (30, 1, 30)]
    assert [g['cells'][0]['unit_cost'] for g in row['cost_groups']] == [20, 30]


def test_actual_units_with_identical_frozen_costs_merge():
    row = actual_bundle_report([20, 20])
    assert row['total_cost'] == 40
    assert not row['unit_cost_varies']
    assert [(g['unit_cost'], g['quantity'], g['total_cost']) for g in row['cost_groups']] == [(20, 2, 40)]


def test_unit_details_must_cover_authoritative_quantity_and_amount():
    from decimal import Decimal
    from sold_product_cost_details import detail_groups
    cost = {'calculation_cost_available': True, 'line_total': 50, 'base_total': 0, 'components_total': 50,
            'cost_detail_units': [{'quantity': 1, 'line_total': 20, 'cost_detail_cells': [
                {'key': 'x', 'label': 'x', 'kind': 'product', 'status': 'costed', 'unit_cost': 20}]}]}
    groups = detail_groups(cost, item(2), Decimal(2))
    assert len(groups) == 1
    assert groups[0]['total_cost'] == 50
    assert groups[0]['quantity'] == 2


def test_actual_unit_precision_is_not_rounded_before_group_addition():
    row = actual_bundle_report(['20.005', '20.005'])
    assert row['total_cost'] == 40.01
    assert row['cost_groups'][0]['total_cost'] == 40.01
    assert row['cost_groups'][0]['unit_cost'] == 20.005


def test_variants_share_one_parent_summary_and_equal_cost_group():
    rows = report([{**item(1), 'variant_id': 'red'}, {**item(1), 'variant_id': 'blue'}])
    assert len(rows) == 1
    assert rows[0]['report_key'] == 'product:p'
    assert rows[0]['units_sold'] == 2
    assert len(rows[0]['cost_groups']) == 1
    assert rows[0]['cost_groups'][0]['quantity'] == 2


def test_customer_names_and_unpriced_colours_do_not_split_equal_costs():
    lines = []
    for index in range(17):
        line = item(1, 'yes')
        line['options_raw'] += [
            {'option_id': 'engraved-text', 'value': f'اسم {index}'},
            {'option_id': 'colour', 'value_id': 'red' if index % 2 else 'blue'},
        ]
        lines.append(line)
    row = report(lines)[0]
    assert len(row['cost_groups']) == 1
    group = row['cost_groups'][0]
    assert (group['quantity'], group['unit_cost'], group['total_cost']) == (17, 35, 595)
    assert next(c for c in group['cells'] if c['key'] == 'option:name')['unit_cost'] == 10


def test_selected_name_service_and_without_service_keep_separate_rows():
    row = report([item(3, 'yes'), item(2), item(4, 'yes')])[0]
    assert [(g['quantity'], g['unit_cost'], g['total_cost']) for g in row['cost_groups']] == [(7, 35, 245), (2, 25, 50)]
    assert any(c['kind'] == 'service' and c['key'] == 'option:name' for c in row['cost_columns'])
