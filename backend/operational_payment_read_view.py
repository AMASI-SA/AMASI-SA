"""A short-lived payment input for the pure operational payment normalizer.

Items, customer and shipping payloads are not read by that normalizer. Their
standard BSON containers may be borrowed for this synchronous call; the view
must never escape or be used by a writer. All financial fields share one
deepcopy memo, preserving their aliases and cycles. General snapshot consumers
continue to use settled_payment_snapshot's complete independent copy.
"""
from copy import deepcopy

from salla_payment_collection import _settled_payment_payload


_BORROWED = frozenset({'items', 'customer', 'shipping', 'shipments'})
_ATOMIC = frozenset({str, int, float, bool, bytes, type(None)})


def _plain_payment_graph(payload):
    """Custom financial containers/hooks retain the original copy behavior."""
    if type(payload) is not dict or any(type(key) is not str for key in payload):
        return False
    if any(type(value) not in (dict, list, type(None))
           for key, value in payload.items() if key in _BORROWED):
        return False
    pending = [value for key, value in payload.items() if key not in _BORROWED]
    seen = set()
    while pending:
        value = pending.pop()
        kind = type(value)
        if kind in _ATOMIC:
            continue
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        if kind is dict:
            if any(type(key) is not str for key in value):
                return False
            pending.extend(item for key, item in value.items()
                           if value is not payload or key not in _BORROWED)
        elif kind is list:
            pending.extend(value)
        else:
            return False
    return True


def _copy_payment_input(payload):
    if not _plain_payment_graph(payload):
        return deepcopy(payload)
    result = {}
    memo = {id(payload): result}
    for key, value in payload.items():
        if key not in _BORROWED:
            result[key] = deepcopy(value, memo)
    for key in _BORROWED.intersection(payload):
        value = payload[key]
        # A borrowed root that is also reachable through financial evidence
        # uses that same independent financial copy, never a divergent alias.
        result[key] = memo.get(id(value), value)
    return result


def settled_payment_read_view(payload):
    return _settled_payment_payload(payload, _copy_payment_input)
