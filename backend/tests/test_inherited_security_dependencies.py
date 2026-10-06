"""Offline dependency security boundaries and legitimate compatibility controls."""
import gc
import sys

import pytest
from bson import BSON, ObjectId
from bson.decimal128 import Decimal128
from fsspec.implementations.reference import ReferenceFileSystem
from jinja2.exceptions import SecurityError
from motor.motor_asyncio import AsyncIOMotorClient
from multidict import CIMultiDict
from pymongo.uri_parser import parse_uri
from pymongo.errors import ConfigurationError


@pytest.mark.parametrize("operation", [lambda d, o: o | d.items(), lambda d, o: d.items() - o])
def test_multidict_items_operations_do_not_leak_value_references(operation):
    value = object()
    mapping = CIMultiDict(seed="value")
    operand = [(f"header-{i}", value) for i in range(100)]
    gc.collect()
    before = sys.getrefcount(value)
    result = operation(mapping, operand)
    assert isinstance(result, set)
    del result
    gc.collect()
    assert sys.getrefcount(value) == before


def test_multidict_preserves_case_insensitive_duplicate_headers():
    headers = CIMultiDict([("X-Trace", "one"), ("x-trace", "two")])
    assert headers.getall("X-TRACE") == ["one", "two"]


def test_fsspec_generated_reference_blocks_template_object_traversal():
    # No command execution or network; accessing Python internals must be rejected.
    with pytest.raises(SecurityError):
        ReferenceFileSystem({"version": 1, "refs": {}, "gen": [{
            "key": "{{ joiner.__init__.__globals__ }}", "url": "memory://safe/{{ i }}",
            "dimensions": {"i": [0]},
        }]}, simple_templates=False)


def test_fsspec_legitimate_generated_references_work():
    fs = ReferenceFileSystem({"version": 1, "refs": {}, "gen": [{
        "key": "chunk-{{ i }}", "url": "memory://safe/{{ i }}",
        "offset": "0", "length": "4", "dimensions": {"i": [0, 1]},
    }]}, simple_templates=False)
    assert fs.references["chunk-1"] == ["memory://safe/1", 0, 4]


@pytest.mark.parametrize("host", ["trusted.invalid%2Cattacker.invalid", "trusted.invalid%3A27018"])
def test_pymongo_encoded_host_delimiters_are_not_accepted(host):
    with pytest.raises((ValueError, ConfigurationError)):
        parse_uri(f"mongodb://{host}/test")


def test_motor_driver_and_bson_compatibility_without_connection():
    client = AsyncIOMotorClient("mongodb://127.0.0.1:1", connect=False)
    try:
        assert client.test.items.full_name == "test.items"
        document = {"_id": ObjectId(), "amount": Decimal128("12.50"), "items": [1, 2]}
        assert BSON(BSON.encode(document)).decode() == document
        assert parse_uri("mongodb://127.0.0.1:27017/test")['nodelist'] == [('127.0.0.1', 27017)]
    finally:
        client.close()
