"""Invoice references shared by purchases, exchange receipts and adjustments."""


def supplier_reference(invoice):
    # Historical invoice_number was the supplier's own reference.
    return invoice.get('supplier_invoice_number', invoice.get('invoice_number', ''))


def references(invoice):
    return {value for value in (invoice.get('invoice_number'), supplier_reference(invoice)) if value}


def next_purchase_number(state, external_rows):
    # Called inside the state CAS mutation: retries recompute from committed state.
    purchases = state.get('inventory_purchases', [])
    exchanges = [p for c in state.get('customer_exchanges', []) for p in c['purchases']]
    used = {r for p in purchases + exchanges for r in references(p)}
    used.update(str(p.get('invoice_number') or '').strip() for p in external_rows)
    sequence = state.get('inventory_purchase_sequence', 0)
    while True:
        sequence += 1
        number = f'MZ-PI-{sequence:06d}'
        if number not in used:
            state['inventory_purchase_sequence'] = sequence
            return number
