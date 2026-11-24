"""Copy one report value without repeated scalar deepcopy dispatch.

All protective copies remain. Only exact plain containers and atomic built-ins
use this path; BSON types, subclasses and custom copy hooks retain deepcopy.
"""
from copy import _keep_alive, deepcopy

from operational_financial_activity_runtime import preparation_checkpoint


_ATOMIC = frozenset((str, int, float, bool, bytes, type(None)))
_MISSING = object()


def copy_report_value(value):
    """Preserve deepcopy's aliases, cycles and fallback memo within this call."""
    memo = {}
    visits = 0
    used_fallback = False

    def clone(source):
        nonlocal visits, used_fallback
        kind = type(source)
        if kind in _ATOMIC:
            # A custom deepcopy hook may explicitly replace even an atomic
            # identity in the shared memo. Plain-container traversal never
            # adds such entries, so only fallback requires this lookup.
            return memo.get(id(source), source) if used_fallback else source
        visits += 1
        if visits % 256 == 0:
            preparation_checkpoint()
        key = id(source)
        saved = memo.get(key, _MISSING)
        if saved is not _MISSING:
            return saved
        if kind is dict:
            result = {}
            memo[key] = result
            for name, item in source.items():
                result[name if not used_fallback and type(name) in _ATOMIC else clone(name)] = clone(item)
        elif kind is list:
            result = []
            memo[key] = result
            result.extend(clone(item) for item in source)
        else:
            used_fallback = True
            return deepcopy(source, memo)
        # Use the stdlib protocol also used by fallback hooks, retaining
        # original containers until this copy's memo can be discarded.
        memo[key] = result
        _keep_alive(source, memo)
        return result

    preparation_checkpoint()
    result = clone(value)
    preparation_checkpoint()
    return result
