"""Saved report identity hashing is bounded without changing financial output."""
from collections import Counter
from copy import deepcopy
import json

import pytest

import operational_balance_service as service
from operational_balance_store import digest
from operational_financial_position import build_financial_position
from test_operational_balance_cod_projection_performance import cod_fixture


def instrument(monkeypatch):
    calls = Counter()
    original = service.party_key

    def observed(kind, identity, currency):
        calls[(kind, type(identity), identity, currency)] += 1
        return original(kind, identity, currency)

    monkeypatch.setattr(service, "party_key", observed)
    return calls


def test_repeated_saved_movements_hash_each_party_once_per_report(monkeypatch):
    state = cod_fixture(movements=400, claims=100)
    before = digest(state)
    calls = instrument(monkeypatch)
    first = service.report(state)
    assert sum(calls.values()) == 2
    assert service.report(state) == first
    assert sum(calls.values()) == 4  # The memo cannot survive the report call.
    assert digest(state) == before


def test_party_memo_stops_growing_without_discarding_saved_parties(monkeypatch):
    monkeypatch.setattr(service, "_REPORT_PARTY_KEY_CACHE_LIMIT", 2)
    state = cod_fixture(movements=0, claims=0)
    prototype = cod_fixture(movements=1, claims=1)["movements"][0]
    state["movements"] = [dict(prototype, id=str(i), party_id=f"driver-{i % 3}", allocations=[])
                          for i in range(30)]
    calls = instrument(monkeypatch)
    result = service.report(state)
    assert calls[("bank", str, "bank", "SAR")] == 1
    assert calls[("store_driver", str, "driver-0", "SAR")] == 1
    assert calls[("store_driver", str, "driver-1", "SAR")] == 10
    assert calls[("store_driver", str, "driver-2", "SAR")] == 10
    assert len(result["parties"]) == 4
    assert next(row for row in result["parties"] if row["party_type"] == "bank")["actual"] == "30.00"


@pytest.mark.parametrize("view", ["summary", "advertising", "drivers", "couriers", "providers", "employees",
                                  "accounts", "suppliers", "other", "expected", "forecast", "all"])
def test_every_view_is_byte_identical_to_uncached_party_hashing(monkeypatch, view):
    from test_operational_financial_position_views import rich_state

    state = rich_state()
    before = deepcopy(state)
    stamp = state["engine"]["as_of"]
    with monkeypatch.context() as disabled:
        disabled.setattr(service, "_REPORT_PARTY_KEY_CACHE_LIMIT", 0)
        expected = build_financial_position(state, view=view, generated_at=stamp)
    actual = build_financial_position(state, view=view, generated_at=stamp)
    encode = lambda value: json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    assert encode(actual) == encode(expected)
    assert state == before


def test_nonstring_saved_identities_keep_the_original_digest_semantics(monkeypatch):
    state = cod_fixture(movements=0, claims=0)
    prototype = cod_fixture(movements=1, claims=1)["movements"][0]
    state["movements"] = [dict(prototype, id=str(i), party_id=identity, allocations=[])
                          for i, identity in enumerate((True, 1, "1", None))]
    before = deepcopy(state)
    with monkeypatch.context() as disabled:
        disabled.setattr(service, "_REPORT_PARTY_KEY_CACHE_LIMIT", 0)
        expected = service.report(state)
    assert service.report(state) == expected
    assert len(expected["parties"]) == 5
    assert state == before
