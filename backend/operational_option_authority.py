"""Successful option-catalog preparation for one immutable source attempt.

No price, quantity, selected binding or per-order failure is cached. Plain native
containers are observed without conversions, then checked once before source
observations may leave the collector. Unsupported objects keep the default lazy
validator, including its original error order and custom access behavior.
"""
from datetime import datetime, timezone
from decimal import Decimal

from bson import ObjectId
from bson.int64 import Int64


# Native binding reads retain timestamps. Exact standard values are observed
# without conversion; custom time zones and subclasses keep the default path.
# ObjectId's replaceable byte slot is separately observed below.
_ATOMIC = frozenset((type(None), str, int, bool, float, Decimal, Int64, datetime))
_CHANGED = 'operational_option_authority_snapshot_changed'
_MISSING = object()
_PRODUCTS = 'mezan_products_v2'
_BINDINGS = 'mezan_product_option_cost_bindings_v2'
_ERRORS = '_option_catalog_errors'


def _checkpoint():
    from operational_demand_refresh import calculation_checkpoint
    calculation_checkpoint()


def _observe_containers(roots):
    """Record references, not serialized values or interpreted money."""
    pending, seen, records = list(roots), set(), []
    while pending:
        _checkpoint()
        value = pending.pop()
        kind = type(value)
        if kind is datetime and value.tzinfo is not None and type(value.tzinfo) is not timezone:
            return None
        if kind in _ATOMIC:
            continue
        identity = id(value)
        if identity in seen:
            continue
        if kind is ObjectId:
            fields = value.binary
            if type(fields) is not bytes:
                return None
        elif kind is dict:
            if any(type(key) is not str for key in value):
                return None
            fields = tuple(value.items())
            pending.extend(item for _key, item in fields)
        elif kind is list:
            fields = tuple(value)
            pending.extend(fields)
        else:
            return None
        seen.add(identity)
        records.append((value, fields))
    return records


def _unchanged(value, fields):
    if type(value) is ObjectId:
        return value.binary is fields
    if len(value) != len(fields):
        return False
    if type(value) is dict:
        return all(type(key) is str and key == old_key and item is old_item
                   for (key, item), (old_key, old_item) in zip(value.items(), fields))
    return all(item is old_item for item, old_item in zip(value, fields))


class OptionAuthorityScope:
    """Explicit source_orders lifetime; build/validate via prepare_in_thread."""
    def __init__(self, data):
        self.data = data
        self.active = self.closed = self.prepared = self.ready = self.validated = False
        self.invalidated = False
        self.records, self.cache, self.binding_rows = [], {}, {}

    def __enter__(self):
        if self.active or self.closed:
            raise ValueError(_CHANGED)
        self.active = True
        return self

    def __exit__(self, kind, _value, _traceback):
        self.active, self.closed = False, True
        self.cache.clear()
        self.records.clear()
        self.binding_rows.clear()
        if kind is None and self.ready and not self.validated:
            raise ValueError(_CHANGED)

    def build(self):
        _checkpoint()
        if not self.active or self.prepared:
            return None
        self.prepared = True
        if type(self.data) is not dict or any(type(key) is not str for key in self.data):
            return None
        products, bindings = self.data.get(_PRODUCTS), self.data.get(_BINDINGS)
        errors = self.data.get(_ERRORS)
        if (type(products) is not list or type(bindings) is not list
                or errors is not None and (type(errors) is not set or any(type(value) is not str for value in errors))):
            return None
        records = _observe_containers((products, bindings))
        if records is None:
            return None
        self.products, self.bindings, self.errors = products, bindings, errors
        self.error_values = frozenset(errors) if errors is not None else None
        self.records = records
        self.binding_rows = {id(row): row for row in bindings if type(row) is dict}
        self.ready = True
        _checkpoint()
        return self

    def _same_roots(self, data):
        return (data is self.data and type(data) is dict
                and data.get(_PRODUCTS) is self.products and data.get(_BINDINGS) is self.bindings
                and data.get(_ERRORS) is self.errors)

    def prepare(self, data, product_id, bindings, builder):
        _checkpoint()
        if (not self.ready or not self.active or self.closed or self.invalidated
                or type(product_id) is not str or type(bindings) not in (list, tuple)):
            return builder(data, product_id, bindings)
        if not self._same_roots(data):
            self.invalidated = True
            return builder(data, product_id, bindings)
        if any(type(row) is not dict or self.binding_rows.get(id(row)) is not row for row in bindings):
            return builder(data, product_id, bindings)
        key = product_id, tuple(id(row) for row in bindings)
        saved = self.cache.get(key, _MISSING)
        if saved is _MISSING:
            # Failed preparation is never remembered. Line-specific validation
            # already ran at the original boundary before entering this method.
            saved = builder(data, product_id, bindings)
            self.cache[key] = saved
        return saved

    def validate(self):
        _checkpoint()
        if self.ready:
            if self.invalidated or not self.active or self.closed or not self._same_roots(self.data):
                raise ValueError(_CHANGED)
            if self.errors is not None and (
                    any(type(value) is not str for value in self.errors) or frozenset(self.errors) != self.error_values):
                raise ValueError(_CHANGED)
            for value, fields in self.records:
                _checkpoint()
                if not _unchanged(value, fields):
                    raise ValueError(_CHANGED)
        self.validated = True
