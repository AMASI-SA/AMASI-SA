"""Request-local undo records and order indexes for the pure reconciliation loop.

Only replaced dictionary entries are transactional, matching the former shallow
snapshots. Nothing is retained across requests or returned in the saved state.
"""

_MISSING = object()


class ReconciliationMap(dict):
    """A dict with proportional savepoints and stable order after rollback.

Ordinary replacements and additions only retain their before-images. Restoring
a deleted entry also restores its original insertion position; that uncommon
rollback rebuilds the map once, rather than copying it for every order.
"""

    def __init__(self, values, checkpoint, *, index_facts=False):
        super().__init__(values)
        self._checkpoint = checkpoint
        self._undo = []
        self._positions = {}
        self._next_position = len(self)
        self._index_facts = index_facts
        self._groups = {"capture:": {}, "refund:": {}}
        self._unindexed = {"capture:": set(), "refund:": set()}
        for position, (key, value) in enumerate(dict.items(self)):
            if position % 256 == 0:
                checkpoint()
            self._positions[key] = position
            self._index_add(key, value)

    @staticmethod
    def _family(key):
        if isinstance(key, str):
            for prefix in ("capture:", "refund:"):
                if key.startswith(prefix):
                    return prefix
        return None

    def _index_add(self, key, value):
        family = self._family(key) if self._index_facts else None
        if family is None:
            return
        # Unusual legacy rows retain the original lazy full-scan behavior,
        # including where missing fields are detected and which error wins.
        if type(value) is not dict or type(value.get("order_id")) is not str:
            self._unindexed[family].add(key)
        else:
            self._groups[family].setdefault(value["order_id"], set()).add(key)

    def _index_remove(self, key, value):
        family = self._family(key) if self._index_facts else None
        if family is None:
            return
        if key in self._unindexed[family]:
            self._unindexed[family].remove(key)
        else:
            group = self._groups[family][value["order_id"]]
            group.remove(key)
            if not group:
                del self._groups[family][value["order_id"]]

    def _record(self, key):
        previous = dict.get(self, key, _MISSING)
        self._undo.append((key, previous, self._positions.get(key)))
        return previous

    def __setitem__(self, key, value):
        previous = self._record(key)
        if previous is _MISSING:
            self._positions[key] = self._next_position
            self._next_position += 1
        else:
            self._index_remove(key, previous)
        dict.__setitem__(self, key, value)
        self._index_add(key, value)

    def __delitem__(self, key):
        previous = dict.__getitem__(self, key)
        self._record(key)
        self._index_remove(key, previous)
        dict.__delitem__(self, key)
        del self._positions[key]

    def pop(self, key, default=_MISSING):
        if key not in self:
            if default is _MISSING:
                raise KeyError(key)
            return default
        value = dict.__getitem__(self, key)
        del self[key]
        return value

    def popitem(self):
        if not self:
            raise KeyError("popitem(): dictionary is empty")
        key = next(reversed(self))
        return key, self.pop(key)

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return self[key]

    def update(self, values=(), **kwargs):
        entries = ((key, values[key]) for key in values.keys()) if hasattr(values, "keys") else values
        for key, value in entries:
            self[key] = value
        for key, value in kwargs.items():
            self[key] = value

    def clear(self):
        for key in list(self):
            del self[key]

    def __ior__(self, values):
        self.update(values)
        return self

    def checkpoint(self):
        return len(self._undo)

    def rollback(self, checkpoint):
        reorder = False
        while len(self._undo) > checkpoint:
            key, previous, position = self._undo.pop()
            current = dict.get(self, key, _MISSING)
            if current is not _MISSING:
                self._index_remove(key, current)
            if previous is _MISSING:
                if current is not _MISSING:
                    dict.__delitem__(self, key)
                    del self._positions[key]
            else:
                reorder = reorder or self._positions.get(key) != position
                dict.__setitem__(self, key, previous)
                self._positions[key] = position
                self._index_add(key, previous)
        if reorder:
            self._checkpoint()
            entries = list(self.ordered_items(self))
            dict.clear(self)
            dict.update(self, entries)
            self._checkpoint()

    def forget(self):
        self._undo.clear()

    def ordered_items(self, keys):
        """Read the selected existing entries in their full-dictionary order."""
        for key in sorted((key for key in keys if key in self), key=self._positions.__getitem__):
            yield key, self[key]

    def facts_for(self, family, order_id):
        families = (family,) if isinstance(family, str) else family
        if any(self._unindexed[prefix] for prefix in families):
            return self.items()
        keys = set().union(*(self._groups[prefix].get(order_id, ()) for prefix in families))
        return self.ordered_items(keys)
