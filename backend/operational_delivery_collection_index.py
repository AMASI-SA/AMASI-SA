"""Attempt-local candidate rows for the existing driver collection predicate.

The caller still checks every financial and identity condition. This optional
view only avoids revisiting receipts from unrelated assignments; it preserves
source order, duplicate occurrences and row references without caching evidence.
"""
from collections import defaultdict
import math

from bson.int64 import Int64


def _checkpoint():
    from operational_demand_refresh import calculation_checkpoint
    calculation_checkpoint()


def _plain_key(value):
    # Only built-in equality/hash behavior is safe to use ahead of the original
    # predicate. Unsupported keys keep the full scan and its error ordering.
    kind = type(value)
    return (value is None or kind is str or kind is bool
            or kind is float and not math.isnan(value)
            or (kind is int or kind is Int64) and value.bit_length() <= 1024)


class DeliveryCollectionIndex:
    def __init__(self, rows, owner, by_assignment):
        self.rows, self.owner, self.length = rows, owner, len(rows)
        self.by_assignment = by_assignment

    def matches(self, rows, owner):
        return (rows is self.rows and type(owner) is str
                and owner == self.owner and len(rows) == self.length)


def build_delivery_collection_index(rows, owner):
    """Build once in the owned preparation worker, anew for every attempt.

    The source cohort must remain unchanged for the attempt, as with the
    assignment index. None is an optimization fallback, never valid evidence.
    """
    _checkpoint()
    if (type(rows) is not list and type(rows) is not tuple) or type(owner) is not str:
        return None
    by_assignment = defaultdict(list)
    for row in rows:
        _checkpoint()
        if type(row) is not dict or any(type(key) is not str for key in row):
            return None
        identity = row.get('assignment_id')
        if not _plain_key(identity):
            return None
        by_assignment[identity].append(row)
    _checkpoint()
    return DeliveryCollectionIndex(rows, owner, by_assignment)


def delivery_collection_rows(rows, assignment, *, owner, collection_index=None):
    """Return candidate rows; the unchanged caller predicate remains decisive."""
    _checkpoint()
    if (type(collection_index) is DeliveryCollectionIndex
            and collection_index.matches(rows, owner) and type(assignment) is dict
            and all(type(key) is str for key in assignment)):
        assignment_id = assignment.get('id')
        if _plain_key(assignment_id):
            return collection_index.by_assignment.get(assignment_id, ())
    return rows
