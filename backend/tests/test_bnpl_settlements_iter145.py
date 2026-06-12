"""Iter-145 — Regression tests for BNPL settlements UI fix.

The frontend `BnplSettlements.jsx` was hiding the "المحوَّل" (transferred)
column whenever a weekly invoice fell into the `over` / `under` near-miss
buckets, even though the matching service correctly returns the near-miss
`matched_transfer.amount`.  This test pins the contract that drives the UI:

  • compute_matches_for_provider() returns a `matched_transfer` payload
    (with `amount` and `delta`) for `over` and `under` rows too — not only
    for `matched`.

If this changes, the UI fix made in Iter-145 must be revisited.
"""

import asyncio
import inspect

import pytest

from backend.bnpl.matching_service import (
    _classify,
    _tolerance,
    AMOUNT_FLAT_TOL,
    AMOUNT_PCT_TOL,
)


def test_tolerance_floor():
    """Tolerance never drops below the flat SAR floor."""
    assert _tolerance(0) == AMOUNT_FLAT_TOL
    assert _tolerance(50) == AMOUNT_FLAT_TOL  # 2% of 50 = 1 < 3


def test_tolerance_scales_with_net():
    """Tolerance scales with the invoice net at 2 %."""
    assert _tolerance(10_000) == pytest.approx(10_000 * AMOUNT_PCT_TOL)


def test_classify_over_under_matched():
    # net = 15,290.84 → tol ≈ 305.82 SAR
    net = 15_290.84
    tol = _tolerance(net)

    # Exactly equal → matched.
    assert _classify(net, net) == "matched"
    # Just inside tolerance → matched.
    assert _classify(net, net + tol - 0.01) == "matched"
    assert _classify(net, net - tol + 0.01) == "matched"
    # Just outside tolerance → over / under.
    assert _classify(net, net + tol + 1) == "over"
    assert _classify(net, net - tol - 1) == "under"


def test_compute_matches_signature():
    """Sanity: function exists and is async."""
    from backend.bnpl.matching_service import compute_matches_for_provider
    assert inspect.iscoroutinefunction(compute_matches_for_provider)


@pytest.mark.asyncio
async def test_near_miss_payload_contract():
    """The near-miss branch in compute_matches_for_provider must include
    `amount` and `delta` keys so the frontend can render the transferred
    column for over / under rows.

    We assert against the SOURCE of compute_matches_for_provider rather
    than running it against Mongo — the function builds `near_payload`
    inline, and we want to fail fast if a future refactor drops those
    keys.
    """
    src = inspect.getsource(
        __import__("backend.bnpl.matching_service", fromlist=["compute_matches_for_provider"]).compute_matches_for_provider,
    )
    # Both keys must remain in the near_payload dict literal.
    assert '"amount":' in src
    assert '"delta":' in src
    # The status assignment must use _classify (i.e., over/under can occur).
    assert "_classify(net_payable, t_amt)" in src
