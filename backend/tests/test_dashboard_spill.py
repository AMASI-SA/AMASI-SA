import asyncio
import hashlib
from datetime import date, datetime, timezone

import pytest

from dashboard_spill import DashboardSpill, SpillBudgetExceeded


def test_sequence_replay_filter_types_and_private_cleanup():
    original = {"float": 1.25, "text": "مرحبا", "date": date(2026, 10, 4),
                "dt": datetime(2026, 10, 4, 1, tzinfo=timezone.utc),
                "set": {1, "two"}, "tuple": (1, None), "integer": 2 ** 80,
                "dict": {1: "numeric key", "1": "string key"}}
    with DashboardSpill() as spill:
        path = spill.directory
        sequence = spill.sequence("orders")
        sequence.extend([original, {"second": True}])
        assert len(sequence) == 2
        assert list(sequence) == [original, {"second": True}]
        assert list(sequence) == [original, {"second": True}]
        assert sequence[-1] == {"second": True}
        assert sequence == [original, {"second": True}]
        assert sequence != [original]
        assert sequence[:1] == [original]
        with pytest.raises(ValueError, match="bounded"):
            sequence[:]
        with pytest.raises(IndexError):
            sequence[2]
        selected = sequence.filter(lambda row: "float" in row, "selected")
        assert list(selected) == [original]
    assert not path.exists()


def test_map_large_mutable_eviction_and_streaming():
    with DashboardSpill() as spill:
        mapping = spill.map("products")
        for i in range(3000):
            mapping.setdefault(str(i), {"seen": set(), "items": []})["seen"].add(i)
            mapping[str(i)]["items"].append(i)
            assert mapping.cache_entries <= 128
        assert len(mapping) == 3000
        assert list(mapping)[:3] == ["0", "1", "2"]
        mapping.flush()
        assert sum(value["items"][0] for value in mapping.values()) == sum(range(3000))
        assert mapping["0"] == {"seen": {0}, "items": [0]}
        for key, value in mapping.items():
            value["items"].append("mutated")
        mapping.flush()
        assert mapping["0"]["items"] == [0, "mutated"]
        assert mapping.get("missing") is None
        del mapping["0"]
        assert "0" not in mapping
        assert len(mapping) == 2999


def test_namespace_and_large_sequence_bounded_fetches():
    with DashboardSpill() as spill:
        one = spill.sequence("one")
        one.extend({"n": n} for n in range(5000))
        two = spill.sequence_from("two", (row for row in one if row["n"] % 2 == 0))
        assert len(two) == 2500
        assert sum(r["n"] for r in one) == sum(range(5000))
        assert max(one.observed_fetch_sizes) <= 128
        with pytest.raises(ValueError, match="1000"):
            one[:2000]
        assert spill.map("one").get("n") is None


def test_map_key_identity_set_and_last_yield_writeback():
    with DashboardSpill() as spill:
        mapping = spill.map("identities")
        mapping[("owner", 1)] = {"count": 1}
        assert mapping[("owner", 1.0)] == {"count": 1}
        mapping["z"] = {"count": 2}
        mapping["a"] = {"count": 3}
        for value in mapping.values():
            value["count"] += 1
        mapping.flush()
        assert mapping["a"] == {"count": 4}
        assert list(mapping) == [("owner", 1), "z", "a"]
        seen = spill.set("seen")
        seen.add(("owner", 1))
        seen.add(("owner", 1))
        assert len(seen) == 1
        assert ("owner", 1) in seen
        seen.discard(("owner", 1))
        seen.discard("missing")
        assert len(seen) == 0


def test_mutable_writeback_budget_failure_is_visible_and_cleanup_still_runs():
    with pytest.raises(SpillBudgetExceeded):
        with DashboardSpill(max_bytes=65536) as spill:
            path = spill.directory
            mapping = spill.map("growing")
            mapping["one"] = {"items": []}
            mapping["one"]["items"].append(hashlib.shake_256(b"budget-fixture").hexdigest(100000))
    assert not path.exists()


def test_sqlite_settings_private_requests_and_abandoned_iterator_cleanup():
    with DashboardSpill() as first, DashboardSpill() as second:
        assert first.directory != second.directory
        assert first.execute("PRAGMA cache_size").fetchone()[0] == -2048
        assert first.execute("PRAGMA temp_store").fetchone()[0] == 1
        assert first.execute("PRAGMA mmap_size").fetchone()[0] == 0
        one = first.sequence_from("orders", ({"n": n} for n in range(300)))
        iterator = iter(one)
        assert next(iterator) == {"n": 0}
        path = first.directory
    assert not path.exists()
    # Closing an abandoned iterator after request teardown must be safe too.
    iterator.close()


def test_exception_cleanup_and_visible_disk_budget():
    with pytest.raises(RuntimeError, match="caller"):
        with DashboardSpill() as spill:
            path = spill.directory
            spill.sequence("orders").append({"id": 1})
            raise RuntimeError("caller")
    assert not path.exists()
    with pytest.raises(SpillBudgetExceeded):
        with DashboardSpill(max_bytes=65536) as spill:
            limited_path = spill.directory
            spill.sequence("orders").extend({"payload": hashlib.shake_256(str(i).encode()).hexdigest(8192)} for i in range(100))
    assert not limited_path.exists()


@pytest.mark.asyncio
async def test_cancelled_request_cleans_private_spill():
    entered = asyncio.Event()
    paths = []
    async def request():
        async with DashboardSpill() as spill:
            paths.append(spill.directory)
            spill.map("rows")["key"] = {"value": 1}
            entered.set()
            await asyncio.Event().wait()
    task = asyncio.create_task(request())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not paths[0].exists()


def test_bson_values_and_discarded_intermediate_buffers():
    from bson import ObjectId, Decimal128
    from decimal import Decimal
    value = {"id": ObjectId(), "money": Decimal128("12.30"), "decimal": Decimal("0.0100")}
    with DashboardSpill() as spill:
        rows = spill.sequence_from("intermediate", [value])
        assert rows[0] == value
        proofs = spill.map("proofs")
        proofs["key"] = value
        assert proofs["key"] == value
        spill.discard_sequence("intermediate")
        spill.discard_map("proofs")
        assert len(spill.sequence("intermediate")) == 0
        assert len(spill.map("proofs")) == 0
        assert not proofs._cache


def test_selections_reuse_source_payload_and_preserve_nested_order():
    with DashboardSpill() as spill:
        source = spill.sequence_from("source", ({"n": n, "payload": "x" * 1000} for n in range(301)))
        even = source.filter(lambda row: row["n"] % 2 == 0, "even")
        nested = even.filter(lambda row: row["n"] > 10, "nested")
        assert len(even) == 151
        assert nested[0]["n"] == 12
        assert nested[-1]["n"] == 300
        assert [row["n"] for row in nested[:3]] == [12, 14, 16]
        assert sum(row["n"] for row in nested) == sum(range(12, 301, 2))
        assert spill.execute("SELECT COUNT(*) FROM sequences").fetchone()[0] == 301
        with pytest.raises(TypeError, match="read-only"):
            nested.append({})


def test_read_only_lookup_eviction_does_not_write_back():
    with DashboardSpill() as spill:
        mapping = spill.map("lookup", mutable=False)
        for i in range(300):
            mapping[i] = {"value": i}
        written = spill._conn.total_changes
        for i in range(300):
            assert mapping[i] == {"value": i}
        mapping.flush()
        assert spill._conn.total_changes == written


def test_compact_codec_preserves_reserved_keys_nested_types_and_string_key_identity():
    from decimal import Decimal
    from zoneinfo import ZoneInfo
    from dashboard_spill import _store_value, _decode
    value = {'\0dashboard-type': ['set', ['ordinary user value']],
             'nested': [{'services': {'one', 'two'}, 'tuple': (1, '2')}],
             'money': Decimal('1.0050'), 'huge': 2 ** 90,
             'time': datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo('America/New_York'), fold=1),
             'keys': {(1, 2): frozenset({3}), '1': 'text', 1: 'number'}}
    assert _decode(_store_value(value)) == value
    assert _decode(_store_value(value))['time'].fold == 1
    with DashboardSpill() as store:
        values = store.map('typed', mutable=False)
        for key in ('', 'Kkey', '["scalar",1]', 'مرحبا', 1, ('1',)):
            values[key] = value
        assert len(values) == 6
        assert list(values) == ['', 'Kkey', '["scalar",1]', 'مرحبا', 1, ('1',)]
        for key in values:
            assert values[key] == value


def test_batch_decode_preserves_mixed_formats_order_and_native_reserved_tags():
    from dashboard_spill import _store_value, _decode_many, FETCH_SIZE
    values = [{"\0dashboard-type": ["set", ["user data"]]},
              {"services": {"one", "two"}, "padding": "x" * 1000},
              None, 12.3, [1, 2], (3, 4)]
    assert _decode_many([_store_value(value) for value in values]) == values
    assert _decode_many(['Khello', 'T["scalar",1]']) == ['hello', 1]
    with pytest.raises(ValueError, match="exceeds"):
        _decode_many(['J0'] * (FETCH_SIZE + 1))
    with DashboardSpill() as store:
        rows = store.sequence_from('mixed', values * 50)
        assert list(rows) == values * 50
        assert list(rows.filter(lambda value: value is not None, 'non-null')) == [value for value in values * 50 if value is not None]
