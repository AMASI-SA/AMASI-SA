"""Iter-50 — Fix Meta Ads purchase count being inflated 5-10×.

Root cause: Meta's Graph API `/insights` endpoint returns the SAME
conversion event under multiple `action_type` values when the merchant
has Pixel + Conversions API + Instagram/Facebook Shop all wired up:

  [
    {"action_type": "purchase",                                   "value": 5},
    {"action_type": "omni_purchase",                              "value": 5},
    {"action_type": "offsite_conversion.fb_pixel_purchase",       "value": 5},
    {"action_type": "onsite_web_purchase",                        "value": 5},
    {"action_type": "onsite_conversion.purchase",                 "value": 5},
    ...
  ]

The previous code summed every action_type containing the substring
"purchase" → 5 events × 5 types = 25 "purchases" reported when the
real number was 5. Saudi merchants commonly report this as exactly
"10x" or "5x" depending on their tracking stack.

Fix: pick a SINGLE canonical action_type, following Meta's priority:
  1. omni_purchase                          (Meta-official cross-channel dedup)
  2. purchase                               (base Pixel event)
  3. offsite_conversion.fb_pixel_purchase   (Pixel-only attribution)
  4. onsite_web_purchase                    (Shop purchases)
  5. onsite_conversion.purchase             (Shop purchases v2)

This test locks the behaviour down so we never regress.
"""
from __future__ import annotations

import pytest

from meta_routes import _extract_purchases, _extract_purchase_value


# ── 1. Original bug case — 5 duplicated action_types should give 5, not 25 ─
def test_purchases_not_inflated_by_duplicate_action_types():
    actions = [
        {"action_type": "purchase",                            "value": 5},
        {"action_type": "omni_purchase",                       "value": 5},
        {"action_type": "offsite_conversion.fb_pixel_purchase", "value": 5},
        {"action_type": "onsite_web_purchase",                 "value": 5},
        {"action_type": "onsite_conversion.purchase",          "value": 5},
    ]
    # OLD behaviour returned 25 (5+5+5+5+5).
    # NEW behaviour: picks omni_purchase (priority 1) → 5.
    assert _extract_purchases(actions) == 5


def test_purchase_value_not_inflated_by_duplicate_action_types():
    action_values = [
        {"action_type": "purchase",                            "value": 1234.50},
        {"action_type": "omni_purchase",                       "value": 1234.50},
        {"action_type": "offsite_conversion.fb_pixel_purchase", "value": 1234.50},
    ]
    # OLD: 3,703.50. NEW: 1234.50.
    assert _extract_purchase_value(action_values) == pytest.approx(1234.50, abs=0.01)


# ── 2. Priority order is respected ─────────────────────────────────────
def test_omni_purchase_preferred_over_plain_purchase():
    """When both exist, omni_purchase wins (it's Meta's officially
    deduplicated cross-channel count)."""
    actions = [
        {"action_type": "purchase",      "value": 10},
        {"action_type": "omni_purchase", "value": 8},
    ]
    assert _extract_purchases(actions) == 8


def test_purchase_fallback_when_no_omni():
    """omni_purchase is not always present — fall back to `purchase`."""
    actions = [
        {"action_type": "purchase",                             "value": 12},
        {"action_type": "offsite_conversion.fb_pixel_purchase", "value": 12},
    ]
    assert _extract_purchases(actions) == 12


def test_fb_pixel_purchase_used_when_only_subset_available():
    """Some legacy pixels only report fb_pixel_purchase."""
    actions = [
        {"action_type": "offsite_conversion.fb_pixel_purchase", "value": 7},
        {"action_type": "view_content",                         "value": 500},
    ]
    assert _extract_purchases(actions) == 7


def test_onsite_purchases_used_when_only_shop_available():
    """Facebook Shop / Instagram Shop stores report onsite_*."""
    actions = [
        {"action_type": "onsite_web_purchase",        "value": 3},
        {"action_type": "onsite_conversion.purchase", "value": 3},
    ]
    # Priority: onsite_web_purchase (4) before onsite_conversion.purchase (5).
    assert _extract_purchases(actions) == 3


# ── 3. Non-purchase actions are ignored ────────────────────────────────
def test_non_purchase_actions_ignored():
    actions = [
        {"action_type": "view_content",      "value": 1000},
        {"action_type": "add_to_cart",       "value": 200},
        {"action_type": "initiate_checkout", "value": 50},
    ]
    assert _extract_purchases(actions) == 0
    assert _extract_purchase_value(actions) == 0.0


# ── 4. Empty / malformed input is safe ─────────────────────────────────
def test_empty_actions_safe():
    assert _extract_purchases(None) == 0
    assert _extract_purchases([]) == 0
    assert _extract_purchase_value(None) == 0.0
    assert _extract_purchase_value([]) == 0.0


def test_malformed_value_ignored():
    actions = [
        {"action_type": "purchase", "value": "not-a-number"},
        {"action_type": "purchase", "value": None},
    ]
    assert _extract_purchases(actions) == 0


# ── 5. When same type is repeated twice (different attribution windows),
# the LARGER value is kept (defensive choice — Meta sometimes splits 7d
# and 1d click windows into separate rows of the same action_type). ────
def test_same_type_repeated_keeps_max():
    actions = [
        {"action_type": "omni_purchase", "value": 4},
        {"action_type": "omni_purchase", "value": 6},
    ]
    assert _extract_purchases(actions) == 6


# ── 6. Real-world payload from a Saudi merchant (sanitised) ────────────
def test_realistic_saudi_merchant_payload():
    """Reproduces the exact 10× inflation the merchant reported on
    Production. With the OLD logic this returned 50; now returns 5."""
    actions = [
        {"action_type": "purchase",                            "value": 5},
        {"action_type": "omni_purchase",                       "value": 5},
        {"action_type": "offsite_conversion.fb_pixel_purchase", "value": 5},
        {"action_type": "onsite_web_purchase",                 "value": 5},
        {"action_type": "onsite_conversion.purchase",          "value": 5},
        # And the "noise" action types that don't have *purchase* in the name
        # but appear in real Meta payloads.
        {"action_type": "link_click",                          "value": 142},
        {"action_type": "video_view",                          "value": 980},
        {"action_type": "post_engagement",                     "value": 256},
    ]
    assert _extract_purchases(actions) == 5

    action_values = [
        {"action_type": "purchase",                            "value": 2375.00},
        {"action_type": "omni_purchase",                       "value": 2375.00},
        {"action_type": "offsite_conversion.fb_pixel_purchase", "value": 2375.00},
        {"action_type": "onsite_web_purchase",                 "value": 2375.00},
        {"action_type": "onsite_conversion.purchase",          "value": 2375.00},
    ]
    assert _extract_purchase_value(action_values) == pytest.approx(2375.0, abs=0.01)
