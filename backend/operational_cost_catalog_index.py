"""Attempt-local catalogue lookups, without pricing or option interpretation.

Only ordinary source containers and scalar identities use the index. Returning
None leaves validation to the old per-item path, in its original error order.
The caller owns the immutable input for one attempt and rebuilds on every retry.
"""
from bson.int64 import Int64


RESOURCES = 'mezan_cost_resources_v2'
PRODUCT_BINDINGS = 'mezan_product_resource_bindings_v2'
OPTION_BINDINGS = 'mezan_product_option_cost_bindings_v2'


def _checkpoint():
    from operational_demand_refresh import calculation_checkpoint
    calculation_checkpoint()


class _CheckedRows:
    """A repeatable, cancellable view for the existing two-pass unique index."""
    def __init__(self, rows):
        self.rows = rows

    def __iter__(self):
        for row in self.rows:
            _checkpoint()
            yield row


def _plain_record(value):
    return type(value) is dict and all(type(key) is str for key in value)


def _plain_identity(value):
    kind = type(value)
    return value is None or kind is str or kind is int or kind is bool or kind is Int64


def _group(rows):
    grouped = {}
    for row in _CheckedRows(rows):
        # No truthiness normalization: legacy bindings distinguish None/0/False.
        grouped.setdefault(str(row.get('salla_product_id')), []).append(row)
    return grouped


class CostCatalogIndex:
    def __init__(self, data, resources, product_rows, option_rows):
        self.resources = resources
        self._data = data
        self._products, self._options = product_rows, option_rows

    def _rows(self, product_id, grouped, collection):
        _checkpoint()
        if type(product_id) is str:
            # Fresh ordered lists preserve duplicate rows and caller isolation.
            return list(_CheckedRows(grouped.get(product_id, ())))
        # An unusual query identity retains the old comparison and error order.
        return [row for row in _CheckedRows(self._data[collection])
                if str(row.get('salla_product_id')) == product_id]

    def product_rows(self, product_id):
        return self._rows(product_id, self._products, PRODUCT_BINDINGS)

    def option_rows(self, product_id):
        return self._rows(product_id, self._options, OPTION_BINDINGS)


def build_cost_catalog_index(data, unique_index):
    """Build via prepare_in_thread; never evaluate an amount or a cost policy.

The supplied existing unique_index preserves its exact duplicate/falsey-ID
semantics. Unsupported inputs fall back before per-item validation is reached.
"""
    _checkpoint()
    if not _plain_record(data):
        return None
    fields = ((RESOURCES, 'id'), (PRODUCT_BINDINGS, 'salla_product_id'),
              (OPTION_BINDINGS, 'salla_product_id'))
    for collection, field in fields:
        rows = data.get(collection)
        if type(rows) is not list:
            return None
        for row in _CheckedRows(rows):
            if not _plain_record(row) or not _plain_identity(row.get(field)):
                return None
    try:
        resources = unique_index(_CheckedRows(data[RESOURCES]), 'id')
        products = _group(data[PRODUCT_BINDINGS])
        options = _group(data[OPTION_BINDINGS])
    except (ValueError, TypeError, KeyError, AttributeError, OverflowError):
        # For example, an oversized integer may fail Python's str conversion.
        # The legacy cost path must raise only if/when it reaches that catalogue.
        return None
    _checkpoint()
    return CostCatalogIndex(data, resources, products, options)
