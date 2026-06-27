"""Iter-280 — Idempotency Key Must Be Deterministic for Legacy Make Payloads.

User-reported bug (2026-02-27)
──────────────────────────────
First Sync Monitor showed the same order `268632361` TWICE as two
independent rows with different trace_ids:
  • eac68e664dee48738005a52b15e50a60  (DEAD_LETTER, older)
  • 33c07a10a2994f6796a44fa386a33c00  (DEAD_LETTER, newer)

Both came from Make.com which fires a FLAT JSON body (no `data`
envelope). The webhook handler ALWAYS inserted a new row instead of
hitting the DuplicateKeyError path.

Root cause
──────────
`derive_idempotency_key(body, header)` was inspecting ONLY `body.data.*`.
For legacy flat payloads `body.data` is missing, so `order_id` was
None and the function fell through to:
    return f"salla:unknown:{uuid.uuid4().hex}"
i.e. a fresh UUID on every call → the unique index
`(user_id, connector_key, idempotency_key)` could never trigger.

Fix (Iter-280)
──────────────
`derive_idempotency_key` now also reads ROOT-level legacy fields:
    order_number / order_id / reference_id (root)
    order_status_slug / order_status / status_slug (root)
    event_type (root)
So the same Make payload always produces the same
`salla:order:<id>:<event>:<status_slug>` key, and the unique index
blocks the second insert via DuplicateKeyError.

These tests lock in the contract:
  • Canonical Salla shape still works (no regression).
  • Legacy flat Make body produces deterministic key.
  • Two identical flat bodies produce the SAME key.
  • Different statuses for the same order produce DIFFERENT keys
    (so business-event transitions still create distinct rows —
     under_review → completed must NOT be deduped).
  • Different events for the same order+status produce DIFFERENT keys.
  • X-Idempotency-Key header takes priority.
  • Missing-everything still returns a random key (no crash).
"""
from __future__ import annotations

from integrations.qoyod.webhook import derive_idempotency_key


# ─── Real Make.com legacy body (the smoking-gun payload) ────────────
LEGACY_FLAT_268632361 = {
    "tax": 0,
    "items": [{
        "sku": "AMS11980", "name": "x", "quantity": 1,
        "amounts": {
            "price_without_tax": {"amount": 199, "currency": "SAR"},
            "total_discount":    {"amount": 11.94, "currency": "SAR"},
            "tax": {"percent": "8.00",
                    "amount": {"amount": 14.96, "currency": "SAR"}},
            "total":             {"amount": 202.02, "currency": "SAR"},
        }
    }],
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


# ─── Canonical Salla shape (no regression) ──────────────────────────
def test_canonical_salla_body_produces_deterministic_key():
    body = {
        "event": "order.updated",
        "data": {
            "reference_id": "268632361",
            "status": {"slug": "completed"},
        },
    }
    key = derive_idempotency_key(body, None)
    assert key == "salla:order:268632361:order.updated:completed"


def test_canonical_salla_body_deterministic_on_repeat():
    body = {
        "event": "order.updated",
        "data": {"id": 999, "status": {"slug": "shipped"}},
    }
    k1 = derive_idempotency_key(body, None)
    k2 = derive_idempotency_key(body, None)
    assert k1 == k2 == "salla:order:999:order.updated:shipped"


# ─── Legacy flat Make body — THE BUG ────────────────────────────────
def test_legacy_flat_make_body_produces_deterministic_key():
    """Previously this returned `salla:unknown:<uuid>` (random)."""
    key = derive_idempotency_key(LEGACY_FLAT_268632361, None)
    assert key.startswith("salla:order:"), \
        f"expected `salla:order:*` key, got `{key}` — bug still present!"
    # Composition must include order_number + event_type + status slug.
    assert "268632361"      in key
    assert "order_completed" in key
    assert "completed"       in key


def test_legacy_flat_body_identical_payloads_produce_same_key():
    """Two identical Make calls for the same order must collide on the
    unique index → second insert raises DuplicateKeyError → only ONE
    inbox row exists per logical webhook delivery."""
    body_a = dict(LEGACY_FLAT_268632361)
    body_b = dict(LEGACY_FLAT_268632361)
    assert derive_idempotency_key(body_a, None) == \
           derive_idempotency_key(body_b, None)


def test_legacy_flat_body_different_status_produces_different_key():
    """Salla fires order.updated repeatedly through status transitions.
    Each transition is a different business event; the inbox MUST treat
    them as distinct rows. Status slug is part of the key for exactly
    this reason."""
    completed = dict(LEGACY_FLAT_268632361)
    completed["order_status_slug"] = "completed"
    under_review = dict(LEGACY_FLAT_268632361)
    under_review["order_status_slug"] = "under_review"
    k1 = derive_idempotency_key(completed, None)
    k2 = derive_idempotency_key(under_review, None)
    assert k1 != k2
    assert "completed"     in k1
    assert "under_review"  in k2


def test_legacy_flat_body_different_event_produces_different_key():
    created = dict(LEGACY_FLAT_268632361)
    created["event_type"] = "order_created"
    completed = dict(LEGACY_FLAT_268632361)
    completed["event_type"] = "order_completed"
    assert derive_idempotency_key(created, None) != \
           derive_idempotency_key(completed, None)


def test_legacy_flat_body_falls_back_to_order_id_when_no_order_number():
    """Some legacy Make scenarios send only `order_id` (no order_number)."""
    body = {
        "order_id": "536444300",
        "event_type": "order_completed",
        "order_status_slug": "completed",
    }
    key = derive_idempotency_key(body, None)
    assert key == "salla:order:536444300:order_completed:completed"


def test_legacy_flat_body_missing_status_uses_none_token():
    body = {
        "order_number": "268632361",
        "event_type": "order_completed",
        # NO status anywhere
    }
    key = derive_idempotency_key(body, None)
    assert key == "salla:order:268632361:order_completed:none"


# ─── Header precedence ──────────────────────────────────────────────
def test_explicit_x_idempotency_header_overrides_derivation():
    key = derive_idempotency_key(LEGACY_FLAT_268632361, "explicit-key-123")
    assert key == "explicit-key-123"


def test_blank_header_falls_back_to_derivation():
    key = derive_idempotency_key(LEGACY_FLAT_268632361, "   ")
    assert key.startswith("salla:order:268632361:")


# ─── Defensive: total absence still works ───────────────────────────
def test_empty_body_returns_random_unknown_key():
    key = derive_idempotency_key({}, None)
    assert key.startswith("salla:unknown:")


def test_non_dict_body_returns_random_unknown_key():
    key = derive_idempotency_key("not a dict", None)  # type: ignore
    assert key.startswith("salla:unknown:")
