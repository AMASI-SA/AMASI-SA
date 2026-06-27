"""Iter-279 — Regression: `normalize-row-self-test` must not crash with
`NormalizationError(missing_order_status)` for legacy Make payloads.

Smoking gun (trace `eac68e664dee48738005a52b15e50a60`)
─────────────────────────────────────────────────────
The admin endpoint `/admin/normalize-row-self-test` previously called
`normalize(raw)` directly on the raw Make payload. Legacy Make scenarios
carry the order status at the ROOT (`raw.order_status_slug`,
`raw.order_status`) — they do NOT ship `raw.data.status`. The normalizer
only inspects `data.status`, so it raised:

    NormalizationError(missing_order_status, "could not extract status string")

even after the operator had verified that Make was sending the correct
nested `amounts` payload.

Fix
───
1. `legacy_adapter.adapt()` now ALSO mirrors `order_status` / `order_status_slug`
   onto the adapted root (purely for downstream visibility — the normalizer
   itself still reads only `data.status`, which the adapter has been
   writing all along).
2. `routes.admin_normalize_row_self_test` now chains
   `adapt(raw) → normalize(adapted)` instead of `normalize(raw)`. So the
   self-test mirrors the production webhook chain exactly.

These tests assert:
  • Adapter writes `order_status`/`order_status_slug` to the adapted root.
  • Adapter writes `data.status` (already covered; double-checked here).
  • `normalize(adapted_payload)` does NOT raise `NormalizationError`.
  • DTO line item carries the correct numeric fields:
        unit_price=199, tax_amount=14.96, discount_amount=11.94, total=202.02
  • Order status canonicalises to "completed".
"""
from __future__ import annotations

import pytest

from integrations.qoyod.legacy_adapter import adapt
from integrations.qoyod.normalizer import normalize, NormalizationError


# ─── Real production payload — trace eac68e664dee48738005a52b15e50a60 ─
# Mirrors the legacy Make body that exposed the bug in production.
RAW_MAKE_BODY_TRACE_EAC68 = {
    "tax": 0,
    "items": [
        {
            "sku": "AMS11980",
            "name": "عباية ستيتش بناتي - تصميم أنيق مع طرحة",
            "quantity": 1,
            "amounts": {
                "original_price":    {"amount": 199,   "currency": "SAR"},
                "price_without_tax": {"amount": 199,   "currency": "SAR"},
                "total_discount":    {"amount": 11.94, "currency": "SAR"},
                "tax": {
                    "percent": "8.00",
                    "amount":  {"amount": 14.96, "currency": "SAR"},
                },
                "total": {"amount": 202.02, "currency": "SAR"},
            },
        }
    ],
    "currency": "SAR",
    "order_id": "536444300",
    "subtotal": 199,
    "created_at": "2026-06-27 01:09:26.000000",
    "event_type": "order_completed",
    "completed_at": "2026-06-27 20:10:45",
    "order_number": "268632361",
    "order_status": "تم التنفيذ",
    "total_amount": 228.02,
    "customer_name": "محمد العتيبي",
    "received_from": "make",
    "shipping_cost": 24.07,
    "payment_method": "tamara_installment",
    "customer_mobile": "505589357",
    "order_status_slug": "completed",
}


# ─── Adapter mirror checks ──────────────────────────────────────────
def test_adapter_writes_order_status_slug_to_adapted_root():
    adapted, meta = adapt(RAW_MAKE_BODY_TRACE_EAC68)
    assert meta["adapter_applied"] is True
    assert adapted.get("order_status_slug") == "completed"


def test_adapter_writes_order_status_name_to_adapted_root():
    adapted, _ = adapt(RAW_MAKE_BODY_TRACE_EAC68)
    assert adapted.get("order_status") == "تم التنفيذ"


def test_adapter_writes_data_status_node():
    adapted, _ = adapt(RAW_MAKE_BODY_TRACE_EAC68)
    status_node = (adapted.get("data") or {}).get("status")
    assert isinstance(status_node, dict)
    assert status_node.get("slug") == "completed"
    assert status_node.get("name") == "تم التنفيذ"


def test_adapter_also_mirrors_top_level_status_key():
    """`adapted.status` mirrors the slug for any consumer that
    reads the flat key."""
    adapted, _ = adapt(RAW_MAKE_BODY_TRACE_EAC68)
    assert adapted.get("status") == "completed"


# ─── Normalizer does NOT raise NormalizationError ───────────────────
def test_normalize_adapted_payload_does_not_raise_status_error():
    """The user's exact failure mode: previously
    `normalize(raw_make_body)` raised
    `NormalizationError(missing_order_status, ...)`. With the fix in
    place, `normalize(adapt(raw_make_body))` succeeds cleanly."""
    adapted, _ = adapt(RAW_MAKE_BODY_TRACE_EAC68)
    try:
        dto = normalize(adapted)
    except NormalizationError as exc:  # pragma: no cover
        pytest.fail(
            f"unexpected NormalizationError after adapter chain: "
            f"{exc.code} — {exc.message}")
    assert dto is not None


def test_normalize_raw_legacy_payload_still_fails_without_adapter():
    """Defensive: confirm that bypassing the adapter (the OLD self-test
    behaviour) still triggers `NormalizationError(missing_order_status)`.
    This documents WHY the route now chains adapt() → normalize()."""
    with pytest.raises(NormalizationError) as excinfo:
        normalize(RAW_MAKE_BODY_TRACE_EAC68)
    assert excinfo.value.code == "missing_order_status"


# ─── End-to-end DTO field checks ────────────────────────────────────
def test_dto_line_item_fields_are_correct_after_chain():
    """The full adapter → normalizer chain for the real production
    payload must produce the canonical line-item values the operator
    expects:
        unit_price       = 199
        tax_amount       = 14.96
        discount_amount  = 11.94
        total            = 202.02
    Line math: 199 − 11.94 + 14.96 = 202.02 ✓
    """
    adapted, _ = adapt(RAW_MAKE_BODY_TRACE_EAC68)
    dto = normalize(adapted)
    assert len(dto.items) == 1
    first = dto.items[0]
    assert first.sku             == "AMS11980"
    assert first.unit_price      == 199.0,  f"got {first.unit_price}"
    assert first.tax_amount      == 14.96,  f"got {first.tax_amount}"
    assert first.discount_amount == 11.94,  f"got {first.discount_amount}"
    assert first.total           == 202.02, f"got {first.total}"


def test_dto_order_status_is_canonical_completed():
    adapted, _ = adapt(RAW_MAKE_BODY_TRACE_EAC68)
    dto = normalize(adapted)
    assert dto.order_status        == "completed"
    assert dto.order_status_native == "تم التنفيذ"


def test_dto_order_number_and_currency():
    adapted, _ = adapt(RAW_MAKE_BODY_TRACE_EAC68)
    dto = normalize(adapted)
    assert dto.order_number == "268632361"
    assert dto.currency     == "SAR"


# ─── Status slug-only (no Arabic name) path ─────────────────────────
def test_adapter_handles_slug_only_legacy_payload():
    """Older Make scenarios that send ONLY the slug (no Arabic name)
    must still survive the chain."""
    body = dict(RAW_MAKE_BODY_TRACE_EAC68)
    body.pop("order_status")  # remove Arabic name
    adapted, _ = adapt(body)
    assert adapted.get("order_status_slug") == "completed"
    dto = normalize(adapted)
    assert dto.order_status == "completed"
