"""Private request-lifetime SQLite buffers, never durable application storage.

JSON tags preserve internal value types without executable deserialization.
Disk limits cover the database file, not process RSS or filesystem metadata.
Values returned by a map may be mutated until eviction/flush; callers must not
retain and later mutate references after their LRU entry has been evicted.
"""
from __future__ import annotations

import base64
import json
import os
import sqlite3
import weakref
from collections import OrderedDict, deque
from collections.abc import MutableMapping, MutableSet, Sequence
from datetime import date, datetime
from decimal import Decimal
from bson import ObjectId, Decimal128
from itertools import zip_longest
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo

FETCH_SIZE = 128
CACHE_ENTRIES = 128


class SpillBudgetExceeded(RuntimeError):
    """The request's temporary disk allowance was exhausted; no partial success."""


def _pack(value):
    if value is None or isinstance(value, (bool, int, float, str)):
        return ["scalar", value]
    if isinstance(value, ObjectId):
        return ["objectid", str(value)]
    if isinstance(value, Decimal128):
        return ["decimal128", str(value)]
    if isinstance(value, Decimal):
        return ["decimal", str(value)]
    if isinstance(value, datetime):
        return ["datetime", value.isoformat(), getattr(value.tzinfo, "key", None), value.fold]
    if isinstance(value, date):
        return ["date", value.isoformat()]
    if isinstance(value, bytes):
        return ["bytes", base64.b64encode(value).decode("ascii")]
    if isinstance(value, dict):
        return ["dict", [[_pack(k), _pack(v)] for k, v in value.items()]]
    if isinstance(value, (list, tuple, set, frozenset)):
        tag = type(value).__name__
        members = [_pack(v) for v in value]
        if isinstance(value, (set, frozenset)):
            members.sort(key=lambda item: json.dumps(item, ensure_ascii=False))
        return [tag, members]
    raise TypeError(f"unsupported spill value type: {type(value).__name__}")


def _unpack(value):
    tag, payload, *extra = value
    if tag == "scalar":
        return payload
    if tag == "objectid":
        return ObjectId(payload)
    if tag == "decimal128":
        return Decimal128(payload)
    if tag == "decimal":
        return Decimal(payload)
    if tag == "datetime":
        result = datetime.fromisoformat(payload)
        if extra[0]:
            result = result.astimezone(ZoneInfo(extra[0]))
        return result.replace(fold=extra[1])
    if tag == "date":
        return date.fromisoformat(payload)
    if tag == "bytes":
        return base64.b64decode(payload)
    if tag == "dict":
        return {_unpack(k): _unpack(v) for k, v in payload}
    constructors = {"list": list, "tuple": tuple, "set": set, "frozenset": frozenset}
    if tag in constructors:
        return constructors[tag](_unpack(v) for v in payload)
    raise ValueError("invalid internal spill type tag")


def _encode(value):
    return json.dumps(_pack(value), ensure_ascii=False, separators=(",", ":"))


def _decode(value):
    return _unpack(json.loads(value))


def _key(value):
    hash(value)  # Preserve the mapping's rejection of unhashable keys.
    if isinstance(value, (bool, int)):
        value = int(value)
    elif isinstance(value, float) and value.is_integer():
        value = int(value)
    elif isinstance(value, tuple):
        return json.dumps(["tuple-key", [_key(v) for v in value]], separators=(",", ":"))
    return _encode(value)


class DashboardSpill:
    def __init__(self, *, max_bytes=None):
        self.max_bytes = int(max_bytes if max_bytes is not None else
                             os.environ.get("DASHBOARD_SPILL_MAX_BYTES", 256 * 1024 * 1024))
        if self.max_bytes < 4096:
            raise ValueError("spill max_bytes must be at least one SQLite page (4096)")
        self._temporary = TemporaryDirectory(prefix="dashboard-spill-")
        self.directory = Path(self._temporary.name)
        self._sequences = {}
        self._maps = {}
        self._cursors = weakref.WeakSet()
        self._closed = False
        try:
            self._conn = sqlite3.connect(self.directory / "request.sqlite", isolation_level=None)
            self._conn.execute("PRAGMA page_size=4096")
            self._conn.execute("PRAGMA cache_size=-2048")
            self._conn.execute("PRAGMA temp_store=FILE")
            self._conn.execute("PRAGMA mmap_size=0")
            # This disposable single-connection buffer needs no crash recovery.
            # Disable journals so the database page budget is also the payload
            # disk budget; callers fail the whole request on any SQLite error.
            self._conn.execute("PRAGMA journal_mode=OFF")
            self._conn.execute("PRAGMA synchronous=OFF")
            self._conn.execute(f"PRAGMA max_page_count={self.max_bytes // 4096}")
            self.execute("CREATE TABLE sequences (namespace TEXT, ordinal INTEGER, payload TEXT NOT NULL, PRIMARY KEY(namespace,ordinal)) WITHOUT ROWID")
            self.execute("CREATE TABLE mappings (namespace TEXT, key TEXT, original_key TEXT NOT NULL, payload TEXT NOT NULL, ordinal INTEGER NOT NULL, PRIMARY KEY(namespace,key)) WITHOUT ROWID")
            self.execute("CREATE UNIQUE INDEX mappings_order ON mappings(namespace,ordinal)")
        except BaseException:
            self.close(flush=False)
            raise

    def execute(self, sql, parameters=()):
        """Internal indexed-buffer SQL seam; never accepts user-supplied SQL."""
        if self._closed:
            raise RuntimeError("dashboard spill is closed")
        try:
            cursor = self._conn.execute(sql, parameters)
            self._cursors.add(cursor)
            return cursor
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
                raise SpillBudgetExceeded(f"dashboard spill exceeded {self.max_bytes} byte disk budget") from exc
            raise

    def sequence(self, name):
        if name not in self._sequences:
            self._sequences[name] = SpillSequence(self, name)
        return self._sequences[name]

    def sequence_from(self, name, rows):
        sequence = self.sequence(name)
        sequence.extend(rows)
        return sequence

    def map(self, name):
        if name not in self._maps:
            self._maps[name] = SpillMap(self, name)
        return self._maps[name]

    def set(self, name):
        return SpillSet(self.map(name))

    def discard_sequence(self, name):
        self.execute("DELETE FROM sequences WHERE namespace=?", (name,))
        self._sequences.pop(name, None)

    def discard_map(self, name):
        mapping = self._maps.pop(name, None)
        if mapping is not None:
            mapping._cache.clear()
        self.execute("DELETE FROM mappings WHERE namespace=?", (name,))

    def close(self, *, flush=True):
        if self._closed:
            return
        try:
            if flush:
                for mapping in self._maps.values():
                    mapping.flush()
        finally:
            self._closed = True
            if hasattr(self, "_conn"):
                # SQLite cursors keep Windows file handles alive even after
                # connection.close(); also close paused/abandoned iterators.
                for cursor in list(self._cursors):
                    cursor.close()
                self._conn.close()
            self._maps.clear()
            self._sequences.clear()
            self._temporary.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close(flush=exc_type is None)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.close(flush=exc_type is None)


class SpillSequence(Sequence):
    def __init__(self, store, name):
        self.store, self.name = store, name
        self.observed_fetch_sizes = deque(maxlen=128)

    def __len__(self):
        return self.store.execute("SELECT COUNT(*) FROM sequences WHERE namespace=?", (self.name,)).fetchone()[0]

    def append(self, value):
        ordinal = self.store.execute("SELECT COALESCE(MAX(ordinal)+1,0) FROM sequences WHERE namespace=?", (self.name,)).fetchone()[0]
        self.store.execute("INSERT INTO sequences VALUES(?,?,?)", (self.name, ordinal, _encode(value)))

    def extend(self, values):
        for value in values:
            self.append(value)

    def __iter__(self):
        cursor = self.store.execute("SELECT payload FROM sequences WHERE namespace=? ORDER BY ordinal", (self.name,))
        try:
            while True:
                rows = cursor.fetchmany(FETCH_SIZE)
                self.observed_fetch_sizes.append(len(rows))
                if not rows:
                    break
                for (payload,) in rows:
                    yield _decode(payload)
        finally:
            if not self.store._closed:
                cursor.close()

    def __getitem__(self, ordinal):
        if isinstance(ordinal, slice):
            if ordinal.stop is None:
                raise ValueError("spill sequence requires an explicit bounded slice")
            indexes = range(*ordinal.indices(len(self)))
            if len(indexes) > 1000:
                raise ValueError("spill sequence slice exceeds 1000 rows")
            return [self[index] for index in indexes]
        if not isinstance(ordinal, int):
            raise TypeError("spill sequence supports integer indexes only")
        if ordinal < 0:
            ordinal += len(self)
        row = self.store.execute("SELECT payload FROM sequences WHERE namespace=? AND ordinal=?", (self.name, ordinal)).fetchone()
        if row is None:
            raise IndexError(ordinal)
        return _decode(row[0])

    def __eq__(self, other):
        if not isinstance(other, Sequence):
            return NotImplemented
        if len(self) != len(other):
            return False
        sentinel = object()
        return all(left == right for left, right in zip_longest(self, other, fillvalue=sentinel))

    def filter(self, predicate, name):
        if name == self.name:
            raise ValueError("filtered sequence needs a distinct namespace")
        return self.store.sequence_from(name, (row for row in self if predicate(row)))


class SpillMap(MutableMapping):
    def __init__(self, store, name):
        self.store, self.name = store, name
        self._cache = OrderedDict()

    @property
    def cache_entries(self):
        return len(self._cache)

    def _write(self, encoded, key, value):
        self.store.execute("INSERT INTO mappings VALUES(?,?,?,?,COALESCE((SELECT MAX(ordinal)+1 FROM mappings WHERE namespace=?),0)) ON CONFLICT(namespace,key) DO UPDATE SET payload=excluded.payload",
                           (self.name, encoded, _encode(key), _encode(value), self.name))

    def _remember(self, encoded, key, value):
        if encoded not in self._cache and len(self._cache) >= CACHE_ENTRIES:
            old_encoded, (old_key, old_value) = next(iter(self._cache.items()))
            self._write(old_encoded, old_key, old_value)
            self._cache.pop(old_encoded)
        self._cache[encoded] = (key, value)
        self._cache.move_to_end(encoded)

    def __getitem__(self, key):
        encoded = _key(key)
        if encoded in self._cache:
            self._cache.move_to_end(encoded)
            return self._cache[encoded][1]
        row = self.store.execute("SELECT original_key,payload FROM mappings WHERE namespace=? AND key=?", (self.name, encoded)).fetchone()
        if row is None:
            raise KeyError(key)
        original, value = _decode(row[0]), _decode(row[1])
        self._remember(encoded, original, value)
        return value

    def __setitem__(self, key, value):
        encoded = _key(key)
        self._write(encoded, key, value)
        self._remember(encoded, key, value)

    def __delitem__(self, key):
        encoded = _key(key)
        result = self.store.execute("DELETE FROM mappings WHERE namespace=? AND key=?", (self.name, encoded))
        if result.rowcount == 0:
            raise KeyError(key)
        self._cache.pop(encoded, None)

    def __len__(self):
        return self.store.execute("SELECT COUNT(*) FROM mappings WHERE namespace=?", (self.name,)).fetchone()[0]

    def flush(self):
        for encoded, (key, value) in self._cache.items():
            self._write(encoded, key, value)

    def __iter__(self):
        self.flush()
        cursor = self.store.execute("SELECT original_key FROM mappings WHERE namespace=? ORDER BY ordinal", (self.name,))
        try:
            while True:
                rows = cursor.fetchmany(FETCH_SIZE)
                if not rows:
                    break
                for (key,) in rows:
                    yield _decode(key)
        finally:
            if not self.store._closed:
                cursor.close()


class SpillSet(MutableSet):
    def __init__(self, mapping):
        self.mapping = mapping

    def __contains__(self, value):
        return value in self.mapping

    def __len__(self):
        return len(self.mapping)

    def __iter__(self):
        return iter(self.mapping)

    def add(self, value):
        self.mapping[value] = True

    def discard(self, value):
        try:
            del self.mapping[value]
        except KeyError:
            pass
