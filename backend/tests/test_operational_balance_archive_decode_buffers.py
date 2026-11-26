"""Packed financial evidence decodes without duplicating its validated bytes."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
import zlib

import bson
from bson import BSON, Binary, Code, Decimal128, Int64, ObjectId, Regex, Timestamp
import pytest

import operational_balance_archive as archive
from operational_balance_store import initial


def stored_packet(kind):
    state = initial("synthetic-decode-owner")
    typed = {
        "amount": Decimal128("9007199254740993.01"), "integer": Int64(7),
        "binary": Binary(b"saved proof", subtype=128), "code": Code("synthetic", {"nested": [1]}),
        "regex": Regex("synthetic", "i"), "timestamp": Timestamp(100, 2),
        "object_id": ObjectId("000000000000000000000001"),
        "at": datetime(2026, 11, 1, tzinfo=timezone.utc), "values": [None, True, 1.25],
    }
    state["engine"].update(facts={"retained": typed}, obligations={
        "shipping:one": {"id": "shipping:one", "confirmed": "20.01", "currency": "SAR"}},
        issues=[{"code": "synthetic_proof", "evidence": deepcopy(typed)}], audit=[])
    state["movements"] = [{"id": "retained", "amount": "97.01"}]
    state["requests"] = {"retry": {"id": "retained"}}
    state["openings"] = {"bank": {"amount": "300.01"}}
    # Match stored BSON semantics, including the default naive UTC decoder.
    expected = BSON(BSON.encode(state)).decode()
    stored = expected
    if kind in {"snapshot", "both"}:
        stored = archive.compact_financial_snapshot(stored, 1024 * 1024)
    if kind in {"issues", "both"}:
        stored = archive.compact_issues(stored, 1024 * 1024)
    return stored, expected


def observe_decoder(monkeypatch):
    decoded = []
    public_decode = bson.decode

    def observed(data, *args, **kwargs):
        decoded.append(data)
        return public_decode(data, *args, **kwargs)

    # Observe both the old BSON.decode delegation and the direct public reader.
    monkeypatch.setattr(bson, "decode", observed)
    monkeypatch.setattr(archive, "decode_bson", observed, raising=False)
    return decoded


@pytest.mark.parametrize("kind", ["snapshot", "issues", "both"])
def test_decoders_consume_the_validated_buffer_once_with_exact_bson_results(monkeypatch, kind):
    stored, expected = stored_packet(kind)
    before = deepcopy(stored)
    decompressed = []
    make_decoder = archive.zlib.decompressobj

    class ObservedDecompressor:
        def __init__(self):
            self.decoder = make_decoder()

        def decompress(self, *args, **kwargs):
            value = self.decoder.decompress(*args, **kwargs)
            decompressed.append(value)
            return value

        def __getattr__(self, name):
            return getattr(self.decoder, name)

    monkeypatch.setattr(archive.zlib, "decompressobj", ObservedDecompressor)
    decoded = observe_decoder(monkeypatch)
    result = archive._unpack_packets(stored)
    assert result == expected and stored == before
    assert len(decoded) == len(decompressed) == (2 if kind == "both" else 1)
    assert all(actual is original for actual, original in zip(decoded, decompressed)), (
        "a complete validated packet was copied before BSON decoding")
    actual_types = {key: type(value) for key, value in result["engine"]["facts"]["retained"].items()}
    assert actual_types == {key: type(value) for key, value in expected["engine"]["facts"]["retained"].items()}


@pytest.mark.parametrize("kind", ["snapshot", "issues"])
@pytest.mark.parametrize("damage", ["hash", "length", "trailing", "compression", "marker", "oversized"])
def test_integrity_failures_never_reach_the_bson_decoder(monkeypatch, kind, damage):
    stored, _ = stored_packet(kind)
    packet = stored[archive.SNAPSHOT if kind == "snapshot" else archive.ISSUES]
    if damage == "hash":
        packet["sha256"] = "0" * 64
    elif damage == "length":
        packet["size"] += 1
    elif damage == "trailing":
        packet["data"] += b"trailing"
    elif damage == "compression":
        packet["data"] = b"not-zlib"
    elif damage == "marker":
        stored["engine"]["facts" if kind == "snapshot" else "issues"] = []
    else:
        packet["size"] = archive.MAX_SNAPSHOT_BSON + 1
    before = deepcopy(stored)
    decoded = observe_decoder(monkeypatch)
    with pytest.raises(archive.ArchiveError):
        archive._unpack_packets(stored)
    assert not decoded and stored == before


@pytest.mark.parametrize("kind", ["snapshot", "issues"])
@pytest.mark.parametrize("damage", ["malformed_bson", "foreign_owner", "wrong_shape"])
def test_valid_length_and_hash_do_not_bypass_bson_scope_or_shape_checks(monkeypatch, kind, damage):
    stored, _ = stored_packet(kind)
    packet = stored[archive.SNAPSHOT if kind == "snapshot" else archive.ISSUES]
    original = BSON(zlib.decompress(packet["data"])).decode()
    if damage == "malformed_bson":
        data = b"\x05\x00\x00\x00\x01"
    else:
        if damage == "foreign_owner":
            original["owner_id"] = "foreign-owner"
        else:
            original["fields" if kind == "snapshot" else "issues"] = None
        data = BSON.encode(original)
    packet.update(size=len(data), sha256=sha256(data).hexdigest(), data=zlib.compress(data, level=1))
    before = deepcopy(stored)
    decoded = observe_decoder(monkeypatch)
    with pytest.raises(archive.ArchiveError):
        archive._unpack_packets(stored)
    assert len(decoded) == 1 and stored == before


@pytest.mark.parametrize("kind", ["snapshot", "issues"])
def test_cancellation_after_decompression_never_decodes_or_changes_the_packet(monkeypatch, kind):
    stored, _ = stored_packet(kind)
    before, checkpoints = deepcopy(stored), []

    def cancel():
        checkpoints.append(True)
        if len(checkpoints) == 2:
            raise asyncio.CancelledError

    # Invoke just the selected decoder so its second checkpoint is the exact
    # post-decompression boundary, before hash acceptance or BSON decoding.
    reader = archive.unpack_financial_snapshot if kind == "snapshot" else archive.unpack_issues
    monkeypatch.setattr(archive, "_checkpoint", cancel)
    decoded = observe_decoder(monkeypatch)
    with pytest.raises(asyncio.CancelledError):
        reader(stored)
    assert len(checkpoints) == 2 and not decoded and stored == before
