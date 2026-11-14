"""Attempt-local selection of the legacy courier hold reader's evidence.

The state stays unchanged for the lifetime of a lookup. Only ordinary container
and identity shapes are indexed; unusual input retains the original reader.
Amounts are never interpreted while building or selecting history.
"""
from decimal import Decimal
from heapq import merge
from bson.int64 import Int64

from operational_shipping_fee_review import amount, hold_prior_courier_event


def _checkpoint():
    from operational_demand_refresh import calculation_checkpoint
    calculation_checkpoint()


def _checked(rows):
    for row in rows:
        _checkpoint()
        yield row


def _plain_scalar(value):
    # Keep native equality/hash semantics (including True == 1 and Int64 == int).
    # Float/Decimal NaNs and custom equality methods do not have that contract.
    return value is None or type(value) in (str, int, bool, Int64)


def _plain_record(value):
    return type(value) is dict and all(type(key) is str for key in value)


def _plain_identity(row):
    return _plain_record(row) and all(
        _plain_scalar(row.get(key))
        for key in ('party_type', 'party_id', 'currency', 'direction'))


def _party(row):
    return row.get('party_type'), row.get('party_id')


class _HoldHistory:
    def __init__(self, state, obligations, payable, allocated, outgoing, outgoing_currency):
        self.state, self.obligations = state, obligations
        self.payable, self.allocated = payable, allocated
        self.outgoing, self.outgoing_currency = outgoing, outgoing_currency

    def __call__(self, event, carrier):
        _checkpoint()
        if (not _plain_record(event) or any(type(event.get(key)) is not str
                for key in ('party_type', 'order_id', 'order_number'))
                or 'party_id' not in event or not _plain_scalar(event['party_id'])
                or carrier is not None and not _plain_record(carrier)
                or type(carrier) is dict and not _plain_scalar(carrier.get('currency'))):
            return hold_prior_courier_event(self.state, event, carrier)
        if not self.state or not event or event['party_type'] != 'courier':
            return False
        from operational_historical_shipping_fee import saved_for
        if saved_for(self.state, event['order_number']):
            return True
        root = 'shipping:' + event['order_id']
        keys = {root, root + ':base_shipping'}
        # Retain the exact set construction/iteration and lazy amount checks.
        if any(amount(self.obligations[key].get('confirmed', 0)) > 0
               for key in _checked(keys) if key in self.obligations):
            return False
        currency = (carrier or {}).get('currency')
        party = event['party_type'], event['party_id']
        payable = self.payable.get((*party, currency), ())
        pooled = (self.outgoing.get(party, ()) if currency is None
                  else self.outgoing_currency.get((*party, currency), ()))
        # All streams retain their original movement positions. The 0 tag for
        # an exact allocation sorts before the 1 tag for a pooled payment in
        # the same movement, exactly as the original short circuit does.
        for position, pooled_only in merge(self.allocated.get(root, ()),
                                           self.allocated.get(root + ':base_shipping', ()), pooled):
            _checkpoint()
            if not pooled_only:
                return True
            movement = self.state['movements'][position]
            allocations = movement.get('allocations') or []
            try:
                if (movement.get('kind') != 'payment' or not allocations
                        or sum((amount(row['amount']) for row in _checked(allocations)), Decimal(0))
                        != amount(movement['amount'])
                        or any(row.get('obligation_id') not in payable for row in _checked(allocations))):
                    return True
            except (ValueError, ArithmeticError, TypeError, KeyError):
                return True
        return False


def build_courier_hold_lookup(state):
    """Select evidence once for one unchanged collection/CAS attempt.

    Build through request-owned ``prepare_in_thread`` and rebuild for every
    attempt. This neither caches decisions nor changes the supplied objects.
    """
    _checkpoint()

    def legacy(event, carrier):
        return hold_prior_courier_event(state, event, carrier)

    if not _plain_record(state):
        return legacy
    engine = state.get('engine', {})
    if not _plain_record(engine):
        return legacy
    obligations = engine.get('obligations', {})
    openings, movements = state.get('openings', {}), state.get('movements', [])
    if type(obligations) is not dict or type(openings) is not dict or type(movements) is not list:
        return legacy
    payable, allocated, outgoing, outgoing_currency = {}, {}, {}, {}
    for key, row in obligations.items():
        _checkpoint()
        if type(key) is not str or not _plain_identity(row):
            return legacy
        if row.get('direction') == 'payable':
            payable.setdefault((*_party(row), row.get('currency')), set()).add(key)
    for row in openings.values():
        _checkpoint()
        if not _plain_identity(row):
            return legacy
        if row.get('direction') == 'for_party':
            # A missing/unhashable identity can raise in the old reader after
            # saved/confirmed short circuits; never surface that error early.
            if 'id' not in row or not _plain_scalar(row['id']):
                return legacy
            payable.setdefault((*_party(row), row.get('currency')), set()).add(row['id'])
    for position, movement in enumerate(movements):
        _checkpoint()
        if not _plain_identity(movement):
            return legacy
        rows = movement.get('allocations')
        rows = [] if rows is None else rows
        if type(rows) is not list:
            return legacy
        seen = set()
        for row in rows:
            _checkpoint()
            if not _plain_record(row):
                return legacy
            key = row.get('obligation_id')
            if not _plain_scalar(key):
                return legacy
            if key is not None and key not in seen:
                allocated.setdefault(key, []).append((position, 0))
                seen.add(key)
        if movement.get('direction') == 'outgoing':
            party = _party(movement)
            outgoing.setdefault(party, []).append((position, 1))
            outgoing_currency.setdefault((*party, movement.get('currency')), []).append((position, 1))
    _checkpoint()
    return _HoldHistory(state, obligations, payable, allocated, outgoing, outgoing_currency)
