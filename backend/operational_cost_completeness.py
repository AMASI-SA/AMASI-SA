"""Scope cost evidence gaps without hiding warnings or manufacturing zeroes."""
SCOPES = frozenset({'products_unreceived', 'products_received_uninvoiced', 'shipping_expected'})
PRODUCTS = SCOPES - {'shipping_expected'}
SHIPPING_ONLY = frozenset({
    'shipping_identity_or_rate_incomplete', 'shipping_setup_ambiguous',
    'historical_shipping_assignment_ambiguous', 'historical_shipping_quote_incomplete',
    'historical_shipping_unassigned',
})


def cost_completeness(issues, declared_scopes=None):
    declared_scopes = declared_scopes or {}
    blocked = set()
    for code in issues:
        bare = code.removeprefix('current_')
        if code == 'current_cost_snapshot_time_incomplete':
            blocked.update(SCOPES)
            continue
        if bare == 'historical_current_catalog_estimate_used':
            continue  # A visibly marked estimate, never historical proof.
        if bare == 'bundle_received_purchase_financial_matching_required':
            blocked.add('products_received_uninvoiced')
        elif code in declared_scopes:
            blocked.update(declared_scopes[code])
        elif bare in SHIPPING_ONLY:
            blocked.add('shipping_expected')
        elif (bare.startswith(('bundle_', 'cost_snapshot_', 'order_cost_authority_', 'supplier_',
                              'mz2_product_', 'mz2_cost_', 'mz2_supplier_', 'preparation_',
                              'historical_supplier_', 'historical_received_supplier_'))
              or bare in {'historical_items_incomplete', 'historical_order_item_identity_ambiguous',
                          'prestart_order_postcutover_invoice_needs_reconciliation'}):
            blocked.update(PRODUCTS)
        else:
            # Missing source/date/identity or an unclassified problem may affect
            # any bucket. Do not turn a newly introduced warning into success.
            blocked.update(SCOPES)
    return {'version': 1, **{scope: scope not in blocked for scope in sorted(SCOPES)}}
