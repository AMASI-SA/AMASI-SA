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


def test_custom_order_equality_under_other_family_forces_lazy_full_scan():
    class StrangeOrder:
        def __eq__(self, other):
            raise ValueError("custom_order_identity")

    strange = StrangeOrder()
    facts = _indexed({
        "other:custom": {"order_id": strange},
        "capture:target": {"order_id": "target", "amount": "100"},
    })
    assert list(facts.facts_for(("capture:", "refund:", "provider_order_balance:"), "target")) == list(facts.items())


def test_provider_fact_index_scales_with_matching_order_not_total_orders():
    values = {
        "capture:%04d" % i: {"order_id": "order-%04d" % i, "amount": "1"}
        for i in range(2000)
    }
    values["provider_order_balance:target"] = {"order_id": "target", "paid": "0"}
    facts = _indexed(values)
    selected = list(facts.facts_for(("capture:", "refund:", "provider_order_balance:"), "target"))
    assert selected == [("provider_order_balance:target", {"order_id": "target", "paid": "0"})]
    assert len(values) == 2001


def test_full_reducer_late_provider_recovery_matches_unindexed_scan(monkeypatch):
    """The real reducer must preserve every output with and without narrowing."""
    from copy import deepcopy
    import operational_balance_engine as engine
    import operational_provider_history as history
    from operational_provider_transaction_evidence import CANONICAL_WITH_UNBOUND_ACTIVITY
    from test_operational_balance_engine import initial, order, policy, NOW

    existing = engine.reconcile(initial(), {"orders": [order()]}, NOW)
    # In production the aggregate store owns the saved calculation timestamp;
    # the pure reducer intentionally does not assign it. Retain that metadata
    # so a later-arriving capture reaches the actual historical overlap gate.
    existing["engine"]["as_of"] = NOW
    old_state = deepcopy(existing)
    payment = {
        "provider": "tabby", "currency": "SAR", "gross": "100.00",
        "capture_source": CANONICAL_WITH_UNBOUND_ACTIVITY,
        "captures": [{"id": "late-capture-1", "amount": "100.00",
                      "captured_at": "2026-10-07T08:00:00+00:00"}],
    }
    sources = {"orders": [order(payment=payment, updated_at="2026-10-07T10:00:00+00:00")],
               "fee_policies": [policy()]}
    seen = []
    original_context = history._context

    def recording_context(*args, **kwargs):
        seen.append(args[1]["order_id"])
        return original_context(*args, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(history, "_context", recording_context)
        indexed = engine.reconcile(deepcopy(existing), deepcopy(sources), NOW)
    assert seen, "fixture must exercise the historical provider check"
    seen.clear()

    # The exact class identity gate now disables the indexed path in _context.
    # Engine reconciliation itself still uses its regular request-local indexes.
    with monkeypatch.context() as m:
        m.setattr(history, "_context", recording_context)
        m.setattr(history, "ReconciliationMap", object)
        unindexed = engine.reconcile(deepcopy(existing), deepcopy(sources), NOW)
    assert seen, "baseline must exercise the same history check"
    assert indexed == unindexed
    assert existing == old_state
    assert engine.reconcile(deepcopy(indexed), deepcopy(sources), NOW) == indexed
