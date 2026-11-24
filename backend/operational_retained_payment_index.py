"""Private capture/refund routing for one immutable source-collection attempt.

No price, confirmation, date or proof is precomputed. The original validator
reads current rows. A complete boundary check must pass before observations
leave source_orders; unsupported inputs keep the legacy scan and error order.
"""
from decimal import Decimal

from bson.int64 import Int64


_SCALARS = frozenset((type(None), str, int, bool, float, Decimal, Int64))
_IDENTITIES = frozenset((type(None), str, int, bool, Int64))
_CHANGED = 'operational_retained_fact_snapshot_changed'


def _checkpoint():
    from operational_demand_refresh import calculation_checkpoint
    calculation_checkpoint()


def _record_current(row, record):
    original, fields = record
    if row is not original or type(row) is not dict or len(row) != len(fields):
        return False
    # Exact immutable values are compared by identity, never by monetary
    # equality (which could interpret a Decimal NaN before the validator).
    return all(type(key) is str and key == old_key and value is old_value
               for (key, value), (old_key, old_value) in zip(row.items(), fields))


class _RetainedFactIndex:
    def __init__(self, scope, engine, facts, keys, records, captures, refunds):
        self.scope, self.engine, self.facts = scope, engine, facts
        self.keys, self.records = tuple(keys), records
        self.captures, self.refunds = captures, refunds
        self.markers = (scope.state.get('owner_id'), scope.state.get('revision'), engine.get('as_of'))
        self.closed, self.invalidated = False, False

    def _current_scope(self, state, facts):
        return (state is self.scope.state and type(state) is dict
                and state.get('engine') is self.engine and self.engine.get('facts') is self.facts
                and facts is self.facts
                and all(value is prior for value, prior in zip(
                    (state.get('owner_id'), state.get('revision'), self.engine.get('as_of')), self.markers)))

    def rows(self, state, facts, order_id, kind):
        if (self.closed or not self.scope.active or self.invalidated or type(order_id) is not str
                or not self._current_scope(state, facts)):
            return None
        groups = self.captures if kind == 'capture:' else self.refunds
        if len(facts) != len(self.keys):
            self.invalidated = True
            return None
        result = []
        for key in groups.get(order_id, ()):
            _checkpoint()
            row = facts.get(key)
            if not _record_current(row, self.records[key]):
                self.invalidated = True
                return None
            result.append((key, row))
        return result

    def validate(self):
        _checkpoint()
        if (self.invalidated or not self._current_scope(self.scope.state, self.facts)
                or len(self.facts) != len(self.keys)):
            raise ValueError(_CHANGED)
        for position, (key, row) in enumerate(self.facts.items()):
            _checkpoint()
            if type(key) is not str or key != self.keys[position]:
                raise ValueError(_CHANGED)
            if key in self.records and not _record_current(row, self.records[key]):
                raise ValueError(_CHANGED)
        _checkpoint()


class RetainedPaymentFactScope:
    """Use only around source_orders; build/validate via prepare_in_thread.

    Normal exit without the linear final validation fails closed. Exceptional
    exit (including cancellation) always closes the index, so it cannot be
    reused by a later attempt. An index outside this scope uses legacy scans.
    """
    def __init__(self, state):
        self.state, self.index = state, None
        self.active = self.prepared = self.validated = self.closed = False

    def __enter__(self):
        if self.active or self.closed:
            raise ValueError(_CHANGED)
        self.active = True
        return self

    def __exit__(self, kind, _value, _traceback):
        self.active, self.closed = False, True
        if self.index is not None:
            self.index.closed = True
            if kind is None and not self.validated:
                raise ValueError(_CHANGED)

    def build(self):
        _checkpoint()
        if not self.active or self.prepared:
            return None
        self.prepared = True
        if type(self.state) is not dict:
            return None
        engine = self.state.get('engine')
        if type(engine) is not dict:
            return None
        facts = engine.get('facts')
        if type(facts) is not dict or not facts:
            return None
        keys, records, captures, refunds = [], {}, {}, {}
        for key, row in facts.items():
            _checkpoint()
            if type(key) is not str:
                return None
            keys.append(key)
            if not key.startswith(('capture:', 'refund:')):
                continue
            if (type(row) is not dict or any(type(field) is not str or type(value) not in _SCALARS
                                           for field, value in row.items())
                    or type(row.get('order_id')) not in _IDENTITIES):
                return None
            try:
                order_id = str(row.get('order_id'))
            except (ValueError, TypeError, OverflowError):
                return None
            records[key] = row, tuple(row.items())
            groups = captures if key.startswith('capture:') else refunds
            groups.setdefault(order_id, []).append(key)
        _checkpoint()
        self.index = _RetainedFactIndex(self, engine, facts, keys, records, captures, refunds)
        return self.index

    def validate(self):
        if self.index is not None:
            self.index.validate()
            self.index.closed = True
        self.validated = True


def retained_fact_rows(index, state, facts, order_id, kind):
    """None means the unchanged complete scan, including all short circuits."""
    if type(index) is not _RetainedFactIndex or kind not in {'capture:', 'refund:'}:
        return None
    return index.rows(state, facts, order_id, kind)
