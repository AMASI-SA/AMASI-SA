"""Exact fee-rail identities. A generic Salla checkout does not identify a card rail."""
from payment_methods import PAYMENT_ALIASES

SALLA_RAILS = {
    alias.lower().replace('_', ' '): key
    for key, display, alias, parent in PAYMENT_ALIASES
    if parent == 'salla' and key != 'salla_generic'
}
SALLA_RAILS.update({key.replace('_', ' '): key for key in SALLA_RAILS.values()})


def fee_rail(method):
    if isinstance(method, dict):
        rails = {fee_rail(method.get(k)) for k in ('code', 'slug') if method.get(k)}
        return rails.pop() if len(rails) == 1 else None
    return SALLA_RAILS.get(method.strip().lower().replace('_', ' ')) if isinstance(method, str) else None


def order_fee_rail(raw):
    methods = [(raw.get('payment') or {}).get('method'), raw.get('payment_method')]
    methods.extend(row.get('payment_method') for row in raw.get('payment_methods') or [])
    rails = set()
    for method in methods:
        fields = [method.get(key) for key in ('code', 'slug')] if isinstance(method, dict) else [method]
        rails.update(rail for value in fields if (rail := fee_rail(value)))
    # Conflicting rails must never borrow a specific rate. Gross proof remains independent.
    return rails.pop() if len(rails) == 1 else None
