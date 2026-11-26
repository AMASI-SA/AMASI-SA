"""Closed attempt indexes release private ownership without relying on cyclic GC.

Validation belongs inside the active scope. These regressions do not introduce
post-close validation behavior or clear any caller-owned financial state.
Existing retained-payment tests cover joining cancelled preparation workers.
"""
import asyncio
from copy import deepcopy
import gc
import weakref

import pytest

import operational_customer_payment_basis as basis
import operational_retained_payment_index as routing
from test_operational_customer_payment_basis import AS_OF, CREATED, retained_fixture


MODES = ("validated", "missing_validation", "body_error", "cancelled")


class LifetimeProbe:
    pass


class BodyFailure(RuntimeError):
    pass


@pytest.fixture
def without_cyclic_gc():
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        yield
    finally:
        if was_enabled:
            gc.enable()
        # Cleanup only, after each test's lifetime assertions have finished.
        gc.collect()


def close_attempt(state, mode):
    """Catch locally so a retained exception traceback cannot fake a leak."""
    outcome = None
    try:
        with routing.RetainedPaymentFactScope(state) as scope:
            index = scope.build()
            assert index is not None
            if mode == "validated":
                scope.validate()
            elif mode == "missing_validation":
                pass
            elif mode == "body_error":
                raise BodyFailure("synthetic body failure")
            elif mode == "cancelled":
                raise asyncio.CancelledError("synthetic attempt cancellation")
            else:
                raise AssertionError("unknown lifetime test mode")
    except (ValueError, BodyFailure, asyncio.CancelledError) as error:
        outcome = type(error), str(error)
    expected = {
        "validated": None,
        "missing_validation": (ValueError, "operational_retained_fact_snapshot_changed"),
        "body_error": (BodyFailure, "synthetic body failure"),
        "cancelled": (asyncio.CancelledError, "synthetic attempt cancellation"),
    }
    assert outcome == expected[mode]
    assert scope.closed and not scope.active and index.closed
    return scope, index


def lifetime_references(mode):
    _, _, state = retained_fixture()
    before = deepcopy(state)
    probe = LifetimeProbe()
    # An unrelated root proves that the closed cycle pins the whole state,
    # not merely the small fact records intentionally observed by the index.
    state["synthetic_lifetime_probe"] = probe
    scope, index = close_attempt(state, mode)
    assert {key: value for key, value in state.items()
            if key != "synthetic_lifetime_probe"} == before
    assert state["synthetic_lifetime_probe"] is probe
    return {"scope": weakref.ref(scope), "index": weakref.ref(index),
            "unrelated_state_root": weakref.ref(probe)}


@pytest.mark.parametrize("mode", MODES)
def test_closed_attempt_releases_scope_index_and_state_without_gc(without_cyclic_gc, mode):
    assert not gc.isenabled()
    references = lifetime_references(mode)
    # No gc.collect(), retained scope/index, exception, or state survives the
    # helper boundary. Immediate release must follow ordinary reference counts.
    assert {name: reference() is None for name, reference in references.items()} == {
        "scope": True, "index": True, "unrelated_state_root": True,
    }


@pytest.mark.parametrize("mode", MODES)
def test_externally_retained_closed_index_keeps_legacy_fallback_and_state(mode):
    raw, source, state = retained_fixture()
    before = deepcopy((raw, source, state))
    scope, index = close_attempt(state, mode)
    facts = state["engine"]["facts"]
    for kind in ("capture:", "refund:"):
        assert routing.retained_fact_rows(index, state, facts, raw["id"], kind) is None

    arguments = {
        "owner": "owner", "order_number": raw["reference_id"],
        "provider_order_id": raw["id"], "source": source, "state": state,
    }
    expected = basis.retained_confirmed_paid_basis(
        raw, "SAR", raw["total"], CREATED, AS_OF, **arguments,
    )
    actual = basis.retained_confirmed_paid_basis(
        raw, "SAR", raw["total"], CREATED, AS_OF, fact_index=index, **arguments,
    )
    assert expected is not None and actual == expected
    assert actual["paid_amount"] == "200.00"
    assert actual["payment"]["captures"][0]["captured_at"] == CREATED
    assert (raw, source, state) == before
    with pytest.raises(ValueError, match="^operational_retained_fact_snapshot_changed$"):
        with scope:
            raise AssertionError("a closed attempt was re-entered")
    assert index.closed and scope.closed and not scope.active
    assert (raw, source, state) == before
