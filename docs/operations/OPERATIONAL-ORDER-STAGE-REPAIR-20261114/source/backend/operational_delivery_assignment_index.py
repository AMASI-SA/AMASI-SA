"""Attempt-local delivery assignment views over one unchanged source cohort.

Strict delivery proof and the older raw-number filter have different contracts.
Keep both views explicit, retain original row references/order, and defer errors
until the corresponding legacy reader would have been called.
"""
from collections import Counter, defaultdict
from bson.int64 import Int64

import operational_delivery_evidence as legacy


def _checkpoint():
    from operational_demand_refresh import calculation_checkpoint
    calculation_checkpoint()


def _plain_identity(value):
    # BSON identifiers fit here. Exotic objects or huge integers keep the old
    # reader, so building an optional index cannot run arbitrary str/equality
    # methods or move a conversion error ahead of the original short circuit.
    kind = type(value)
    return (value is None or kind is str or kind is bool or kind is float
            or (kind is int or kind is Int64) and value.bit_length() <= 1024)


class DeliveryAssignmentIndex:
    def __init__(self, assignments, owner, by_id, by_number, raw_numbers, ids, invalid_owner):
        self.assignments, self.owner, self.length = assignments, owner, len(assignments)
        self.by_id, self.by_number, self.raw_numbers = by_id, by_number, raw_numbers
        self.ids, self.invalid_owner = ids, invalid_owner

    def matches(self, assignments, owner):
        return (assignments is self.assignments and type(owner) is str
                and owner == self.owner and len(assignments) == self.length)

    def resolve(self, order_id, number):
        _checkpoint()
        if self.invalid_owner:
            legacy._fail('owner_mismatch')
        # OR matching keeps each source occurrence once, including duplicates
        # represented by distinct positions, in the original iteration order.
        positions = {}
        for matches in (self.by_id.get(order_id, ()), self.by_number.get(number, ())):
            for position, row in matches:
                _checkpoint()
                positions[position] = row
        relevant = [positions[position] for position in sorted(positions)]
        for row in relevant:
            _checkpoint()
            if legacy._text(row.get('order_id')) != order_id or legacy._text(row.get('order_number')) != number:
                legacy._fail('assignment_order_conflict')
        active = []
        for row in relevant:
            _checkpoint()
            if (row.get('active') is True and not legacy._inactive(row)
                    and legacy._status(row.get('status')) in {'assigned', 'out_for_delivery', *legacy.DELIVERED}):
                active.append(row)
        if len(active) > 1:
            legacy._fail('assignment_ambiguous')
        if not active:
            for row in relevant:
                _checkpoint()
                if legacy._status(row.get('status')) in legacy.DELIVERED:
                    legacy._fail('assignment_inactive')
            return None
        assignment = active[0]
        identity = legacy._text(assignment.get('id'))
        if not identity or not legacy._text(assignment.get('driver_id')):
            legacy._fail('assignment_identity_missing')
        if self.ids[identity] != 1:
            legacy._fail('assignment_ambiguous')
        return assignment


def build_delivery_assignment_index(assignments, owner):
    """Build in the request-owned prepare_in_thread; rebuild every attempt.

    None means unsupported structure, not missing or valid financial evidence.
    The original cohort and its rows must stay unchanged during this attempt.
    """
    _checkpoint()
    if (type(assignments) is not list and type(assignments) is not tuple) or type(owner) is not str:
        return None
    by_id, by_number, raw_numbers = defaultdict(list), defaultdict(list), defaultdict(list)
    ids, invalid_owner = Counter(), False
    for position, row in enumerate(assignments):
        _checkpoint()
        if type(row) is not dict or any(type(key) is not str for key in row):
            return None
        if any(not _plain_identity(row.get(key)) for key in ('id', 'order_id', 'order_number', 'user_id')):
            return None
        invalid_owner |= row.get('user_id') != owner
        by_id[legacy._text(row.get('order_id'))].append((position, row))
        by_number[legacy._text(row.get('order_number'))].append((position, row))
        raw_numbers[str(row.get('order_number'))].append(row)
        ids[legacy._text(row.get('id'))] += 1
    _checkpoint()
    return DeliveryAssignmentIndex(assignments, owner, by_id, by_number, raw_numbers, ids, invalid_owner)


def indexed_driver_assignment(assignments, owner, order_id, number, *, assignment_index=None):
    if (type(assignment_index) is DeliveryAssignmentIndex and assignment_index.matches(assignments, owner)
            and type(order_id) is str and type(number) is str):
        return assignment_index.resolve(order_id, number)
    return legacy._driver_assignment(assignments, owner, order_id, number)


def active_assignments_for_number(assignments, order_number, *, owner, assignment_index=None):
    """The collector's raw str(number), active-is-not-False, exact-status view."""
    if (type(assignment_index) is DeliveryAssignmentIndex and assignment_index.matches(assignments, owner)
            and type(order_number) is str):
        result = []
        for row in assignment_index.raw_numbers.get(order_number, ()):
            _checkpoint()
            if row.get('active') is not False and row.get('status') in {'assigned', 'out_for_delivery', 'delivered'}:
                result.append(row)
        return result
    return [row for row in assignments if str(row.get('order_number')) == order_number
            and row.get('active') is not False and row.get('status') in {'assigned', 'out_for_delivery', 'delivered'}]
