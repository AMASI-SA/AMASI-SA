"""Attempt-local lookup over an unchanged operational shipping history.

Only ordinary BSON-shaped containers use the index. Unusual structures retain
the legacy reader, including its short circuits and exception order. Amounts
are evaluated only when the original reader would reach the same evidence.
"""
from bisect import bisect_left
from decimal import Decimal
from heapq import merge

from operational_delivery_evidence import recorded_courier_fee


def _checkpoint():
    from operational_demand_refresh import calculation_checkpoint
    calculation_checkpoint()


def _prefix_keys(keys, prefix):
    position = bisect_left(keys, prefix)
    while position < len(keys) and keys[position].startswith(prefix):
        _checkpoint()
        yield keys[position]
        position += 1


class _FeeHistory:
    def __init__(self, state, facts, obligations, allocations):
        self.state, self.facts, self.obligations = state, facts, obligations
        self.fact_keys = sorted(facts)
        self.obligation_keys = sorted(obligations)
        self.positions = {key: position for position, key in enumerate(obligations)}
        self.allocations = allocations

    def __call__(self, order_id):
        if type(order_id) is not str:
            return recorded_courier_fee(self.state, order_id)
        _checkpoint()
        root = 'shipping:' + order_id
        if (self.facts.get(root, {}).get('cost') is not None
                or 'shipping_fee:' + order_id in self.facts
                or next(_prefix_keys(self.fact_keys, root + ':'), None) is not None):
            return True
        candidates = list(_prefix_keys(self.obligation_keys, root + ':'))
        if root in self.obligations:
            candidates.append(root)
        # The old set is constructed in dictionary insertion order. Preserve
        # that order before Decimal evaluation, including colliding hashes.
        candidates.sort(key=self.positions.__getitem__)
        keys = set()
        for key in candidates:
            _checkpoint()
            if self.obligations[key].get('kind') == 'shipping':
                keys.add(key)
        for key in keys:
            _checkpoint()
            if Decimal(self.obligations[key].get('confirmed', '0')) > 0:
                return True
        # Per-key lists retain the original movement/allocation order. Merge
        # only matching lists; malformed amounts in unrelated rows stay unread.
        for _, allocation in merge(*(self.allocations.get(key, ()) for key in keys)):
            _checkpoint()
            if Decimal(allocation.get('amount', '0')) > 0:
                return True
        return False


def build_recorded_courier_fee_lookup(state):
    """Return one callable for this collect attempt; never cache across states.

    Build through the request-owned prepare_in_thread helper. The caller must
    rebuild after any change to the supplied state, including every CAS retry.
    """
    _checkpoint()

    def legacy(order_id):
        return recorded_courier_fee(state, order_id)

    if type(state) is not dict:
        return legacy
    engine = state.get('engine', {})
    if type(engine) is not dict:
        return legacy
    facts, obligations = engine.get('facts', {}), engine.get('obligations', {})
    movements = state.get('movements', [])
    if type(facts) is not dict or type(obligations) is not dict or type(movements) is not list:
        return legacy
    for mapping in (facts, obligations):
        for key, row in mapping.items():
            _checkpoint()
            if type(key) is not str or type(row) is not dict:
                return legacy
    allocations, position = {}, 0
    for movement in movements:
        _checkpoint()
        if type(movement) is not dict:
            return legacy
        rows = movement.get('allocations', [])
        if type(rows) is not list:
            return legacy
        for row in rows:
            _checkpoint()
            if type(row) is not dict:
                return legacy
            key = row.get('obligation_id')
            if key is not None and type(key) is not str:
                return legacy
            if key is not None:
                allocations.setdefault(key, []).append((position, row))
            position += 1
    _checkpoint()
    result = _FeeHistory(state, facts, obligations, allocations)
    _checkpoint()
    return result
