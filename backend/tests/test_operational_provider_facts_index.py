"""Parity checks for the request-local provider fact index.

No financial balances or production data are created by these tests.
"""
from operational_reconciliation_state import ReconciliationMap
from operational_provider_history import _context


def _indexed(values):
    return ReconciliationMap(values, lambda: None, index_facts=True)


def test_provider_fact_candidates_keep_global_insertion_order():
    values = {
        "unrelated:a": {"order_id": "a"},
        "provider_order_balance:a": {"order_id": "a", "paid": "0"},
        "capture:b": {"order_id": "b"},
        "refund:a": {"order_id": "a"},
        "capture:a": {"order_id": "a"},
    }
    facts = _indexed(values)
    assert list(facts.facts_for(("capture:", "refund:", "provider_order_balance:"), "a")) == [
        ("provider_order_balance:a", values["provider_order_balance:a"]),
        ("refund:a", values["refund:a"]),
        ("capture:a", values["capture:a"]),
    ]


def test_provider_fact_index_follows_replace_delete_and_rollback():
    facts = _indexed({"capture:a": {"order_id": "a"}, "refund:b": {"order_id": "b"}})
    checkpoint = facts.checkpoint()
    facts["capture:a"] = {"order_id": "b"}
    facts["provider_order_balance:a"] = {"order_id": "a", "paid": "2"}
    del facts["refund:b"]
    assert [key for key, _ in facts.facts_for(("capture:", "refund:", "provider_order_balance:"), "a")] == [
        "provider_order_balance:a"
    ]
    facts.rollback(checkpoint)
    assert [key for key, _ in facts.facts_for(("capture:", "refund:", "provider_order_balance:"), "a")] == ["capture:a"]
    assert list(facts) == ["capture:a", "refund:b"]


def test_unusual_provider_fact_reverts_to_full_scan():
    facts = _indexed({
        "capture:a": {"order_id": "a"},
        "refund:unknown": {"order_id": None},
    })
    assert list(facts.facts_for(("capture:", "refund:", "provider_order_balance:"), "a")) == list(facts.items())
    del facts["refund:unknown"]
    assert list(facts.facts_for(("capture:", "refund:", "provider_order_balance:"), "a")) == [
        ("capture:a", {"order_id": "a"})
    ]


def test_nonstandard_fact_key_falls_back_to_original_iteration():
    facts = _indexed({7: {"order_id": "a"}, "capture:a": {"order_id": "a"}})
    assert list(facts.facts_for(("capture:", "refund:", "provider_order_balance:"), "a")) == list(facts.items())


def test_provider_history_context_matches_original_full_scan():
    values = {
        "other:a": {"order_id": "a"},
        "provider_order_balance:b": {"order_id": "b", "paid": "3"},
        "provider_order_balance:a": {"order_id": "a", "paid": "0"},
        "refund:b": {"order_id": "b"},
        "capture:a": {"order_id": "a"},
    }
    row = {
        "owner_id": "owner", "order_id": "a", "order_number": "A-1",
        "provider": "tabby", "currency": "SAR", "gross": "100",
        "paid": "100", "remaining": "0",
        "created_at": "2026-11-25T12:00:00+00:00",
    }
    def state(facts):
        return {
            "owner_id": "owner", "started_at": "2026-10-03T00:00:00+00:00",
            "engine": {
                "as_of": "2026-11-26T12:00:00+00:00",
                "obligations": {}, "facts": facts,
            },
            "movements": [], "openings": {},
            "customer_returns": [], "customer_exchanges": [],
        }
    assert _context(state(_indexed(values)), row) == _context(state(dict(values)), row)


def test_provider_history_calls_index_only_for_exact_safe_map(monkeypatch):
    calls = []
    original = ReconciliationMap.facts_for

    def tracking(self, family, order_id):
        calls.append((family, order_id))
        return original(self, family, order_id)

    monkeypatch.setattr(ReconciliationMap, "facts_for", tracking)
    from operational_provider_history import _context

    values = {"capture:other": {"order_id": "other", "amount": "1"}}
    row = {
        "owner_id": "owner", "order_id": "target", "order_number": "T-1",
        "provider": "tabby", "currency": "SAR", "gross": "100",
        "paid": "100", "remaining": "0",
        "created_at": "2026-11-25T12:00:00+00:00",
    }
    def state(facts):
        return {
            "owner_id": "owner", "started_at": "2026-10-03T00:00:00+00:00",
            "engine": {
                "as_of": "2026-11-26T12:00:00+00:00",
                "obligations": {}, "facts": facts,
            },
            "movements": [], "openings": {},
            "customer_returns": [], "customer_exchanges": [],
        }

    assert _context(state(_indexed(values)), row) == _context(state(dict(values)), row)
    assert calls == [(("capture:", "refund:", "provider_order_balance:"), "target")]


def test_provider_balance_and_capture_conflicts_match_unindexed_context():
    from operational_provider_history import _context

    row = {
        "owner_id": "owner", "order_id": "target", "order_number": "T-1",
        "provider": "tabby", "currency": "SAR", "gross": "100",
        "paid": "100", "remaining": "0",
        "created_at": "2026-11-25T12:00:00+00:00",
    }
    for facts in (
        {"provider_order_balance:target": {"order_id": "target", "paid": "1"}},
        {"capture:target": {"order_id": "target", "amount": "100"}},
        {"refund:target": {"order_id": "target", "amount": "20"}},
        {"provider_order_balance:target": {"order_id": "target"}},
    ):
        def state(items):
            return {
                "owner_id": "owner", "started_at": "2026-10-03T00:00:00+00:00",
                "engine": {
                    "as_of": "2026-11-26T12:00:00+00:00",
                    "obligations": {}, "facts": items,
                },
                "movements": [], "openings": {},
                "customer_returns": [], "customer_exchanges": [],
            }
        assert _context(state(_indexed(facts)), row) == _context(state(dict(facts)), row)
