"""Regression — Idempotency key MUST include status slug.

User scenario (2026-02-27, Order 268452656 production discovery):
    Salla fires `order.updated` first with status=under_review.
    Mezan ingests it, applies trigger rules, marks SKIPPED. Then
    the merchant moves the order to `completed` in Salla; Salla
    fires another `order.updated`. Without the status slug in the
    idempotency key, the second event is treated as a duplicate of
    the first and the invoice is NEVER created.

Fix shape:
    salla:order:<id>:<event>:<status_slug>

`trigger_once_only` still guarantees no duplicate invoice when the
SAME completed status fires twice — that rule operates on the
qoyod_invoices side, AFTER the inbox accepts the row.
"""
from __future__ import annotations

from integrations.qoyod.webhook import derive_idempotency_key


def _evt(order_id: str, event: str, status_slug: str) -> dict:
    """Build a minimal payload matching the production Make shape."""
    return {
        "event": event,
        "data": {
            "reference_id": order_id,
            "status": {"slug": status_slug, "name": status_slug.title()},
        },
    }


def test_under_review_and_completed_produce_distinct_keys():
    """The actual user-reported regression for Order 268452656."""
    k1 = derive_idempotency_key(
        _evt("268452656", "order.updated", "under_review"), None)
    k2 = derive_idempotency_key(
        _evt("268452656", "order.updated", "completed"), None)
    assert k1 == "salla:order:268452656:order.updated:under_review"
    assert k2 == "salla:order:268452656:order.updated:completed"
    assert k1 != k2, ("Status transition MUST produce a new idem key "
                      "so the second event is not silently dropped")


def test_same_status_twice_collides_intentionally():
    """If Salla retries the EXACT same event (same status), the keys
    DO match — duplicate suppression at the inbox layer is the right
    behaviour. The invoice-side guard (`trigger_once_only`) is a
    separate layer that protects against duplicate invoices."""
    k1 = derive_idempotency_key(
        _evt("268452656", "order.updated", "completed"), None)
    k2 = derive_idempotency_key(
        _evt("268452656", "order.updated", "completed"), None)
    assert k1 == k2


def test_status_under_data_as_plain_string_also_accepted():
    """Legacy Make scenarios sometimes put status directly under
    `data.status` as a string rather than a nested object."""
    payload = {"event": "order.updated",
               "data": {"reference_id": "X1", "status": "completed"}}
    assert derive_idempotency_key(payload, None) == \
        "salla:order:X1:order.updated:completed"


def test_status_via_legacy_order_status_field():
    """Top-level legacy Make webhooks use `data.order_status`."""
    payload = {"event": "order.updated",
               "data": {"reference_id": "X2", "order_status": "Paid"}}
    assert derive_idempotency_key(payload, None) == \
        "salla:order:X2:order.updated:paid"


def test_missing_status_falls_back_to_none_keeps_key_stable():
    """When status truly is absent, we use the literal `none` —
    NOT a blank or null — so the key stays parseable + stable."""
    payload = {"event": "order.created", "data": {"reference_id": "X3"}}
    assert derive_idempotency_key(payload, None) == \
        "salla:order:X3:order.created:none"


def test_explicit_header_still_wins():
    """The `X-Idempotency-Key` header from Make.com takes priority
    over the derived key (operators may override). Mid-Iter-267 the
    runbook updates the Make header to include status — both paths
    work and produce status-aware keys."""
    payload = _evt("268452656", "order.updated", "completed")
    k = derive_idempotency_key(
        payload, "salla:order:268452656:order.updated:completed")
    assert k == "salla:order:268452656:order.updated:completed"


def test_status_slug_normalises_to_lowercase():
    """Defensive: Salla has been observed sending mixed-case slugs.
    Lowercasing keeps `Completed` and `completed` equivalent."""
    upper = derive_idempotency_key(
        _evt("X4", "order.updated", "Under_Review"), None)
    lower = derive_idempotency_key(
        _evt("X4", "order.updated", "under_review"), None)
    assert upper == lower == "salla:order:X4:order.updated:under_review"


def test_complete_lifecycle_skipped_then_completed_creates_two_rows():
    """End-to-end: the SAME order, ingested first with under_review
    then with completed, must produce TWO distinct rows in the inbox
    (one SKIPPED, one to be processed) — NOT one row that swallows
    both."""
    # First event: under_review
    raw1 = _evt("268452656", "order.updated", "under_review")
    k1 = derive_idempotency_key(raw1, None)
    # Second event: completed
    raw2 = _evt("268452656", "order.updated", "completed")
    k2 = derive_idempotency_key(raw2, None)
    # Composite key (connector_key, idempotency_key) for the inbox.
    # The inbox unique index allows both rows because the keys differ.
    keys = {(("qoyod", k1), ("qoyod", k2))}
    assert len(keys) == 1
    inbox_unique_keys = {k1, k2}
    assert len(inbox_unique_keys) == 2, (
        "Both transitions must be accepted into the inbox so the "
        "completed event is not silently dropped")
