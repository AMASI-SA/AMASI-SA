"""Product indexing RCA only: original statements wrapped by test timers."""
import ast
import inspect
from contextlib import contextmanager
from unittest.mock import patch

import dashboard_v2_routes as dash
import product_catalog_cost_resolution as catalog
from dashboard_b_ads_instrumentation import Recorder
from dashboard_b_ads_cooperative_experiment import candidate as fixed_candidate
from dashboard_b_request_scope_experiment import compile_function


def block(name, body, count='0'):
    return ast.With(items=[ast.withitem(context_expr=ast.parse(
        f'_index_probe.span({name!r}, {count})', mode='eval').body)], body=body)


def instrumented(probe, fine=False):
    function = catalog.index_current_catalog_products
    node = ast.parse(inspect.getsource(function)).body[0]
    outer = next(i for i, n in enumerate(node.body) if isinstance(n, ast.For))
    assert len(node.body) == outer + 3
    products_loop, names_loop, returned = node.body[outer:]
    assert ast.unparse(products_loop.iter) == 'products'
    assert ast.unparse(names_loop.iter) == 'names.items()'
    namespace = {**function.__globals__, '_index_probe': probe, '_input_count': lambda rows: len(rows) if isinstance(rows, (list, tuple)) else 0}
    if fine:
        body = products_loop.body
        assert len(body) == 7
        assert ast.unparse(body[0]) == 'product = enrich_current_salla_cost(raw_product)'
        assert isinstance(body[1], ast.For) and isinstance(body[6], ast.For)
        products_loop.body = [block('enrichment', body[:1], '1'),
            block('product_id_index', body[1:2], '1'),
            block('sku_index', body[2:4], '1'),
            block('normalized_name_grouping', body[4:6], '1'),
            block('variant_index', body[6:], 'len(_list(product.get("variants")))')]
        enrich = catalog.enrich_current_salla_cost
        enriched = ast.parse(inspect.getsource(enrich)).body[0]
        def position(name):
            return next(i for i, n in enumerate(enriched.body) if isinstance(n, ast.Assign)
                        and any(isinstance(t, ast.Name) and t.id == name for t in n.targets))
        cv, byid, bysku = (position(name) for name in ('current_variants', 'by_id', 'by_sku'))
        raw_loop = next(i for i, n in enumerate(enriched.body) if isinstance(n, ast.For))
        assert byid == cv + 1 and bysku == byid + 1 and raw_loop == bysku + 1
        original = enriched.body
        enriched.body = [original[0], block('shallow_product_copy_and_base_cost', original[1:cv], '1'),
            block('variant_shallow_copies', original[cv:byid], 'len(_list(row.get("variants")))'),
            block('variant_lookup_maps', original[byid:raw_loop], 'len(current_variants)'),
            block('raw_variant_reconciliation', original[raw_loop:raw_loop+1], '1'),
            block('enrichment_finalize', original[raw_loop+1:], '1')]
        namespace['enrich_current_salla_cost'] = compile_function(enriched,
            {**enrich.__globals__, '_index_probe': probe, '_input_count': lambda rows: len(rows) if isinstance(rows, (list, tuple)) else 0})
    node.body = [node.body[0], block('index_accumulator_preparation', node.body[1:outer], '_input_count(products)'),
        block('ordered_product_traversal', [products_loop], '_input_count(products)'),
        block('name_alias_finalization', [names_loop], 'len(names)'),
        block('return_maps', [returned], '3')]
    compiled = compile_function(node, namespace)
    def run(products):
        with probe.span('whole_index', len(products) if isinstance(products, (list, tuple)) else 0):
            return compiled(products)
    return run


@contextmanager
def candidate(db, scope=None, mode='coarse', probe=None, capture=None, **kwargs):
    assert mode in {'current', 'coarse', 'fine'}
    probe = probe if probe is not None else Recorder()
    fn = catalog.index_current_catalog_products if mode == 'current' else instrumented(probe, fine=mode == 'fine')
    def index(products):
        if capture is not None:
            capture(products)
        return fn(products)
    # Cost's private compiler copies these globals during setup only. Keep all
    # reads, cost5, parser5 and ads1 fixed; no production function is rewritten.
    with patch.object(dash, '_index_products', index):
        with fixed_candidate(db, scope, ads_budget_ms=1, **kwargs) as configured:
            yield configured
