"""Forensic — verify `contact_name` is always present in the payload
that the resolver sends to Qoyod's POST /customers.

User scenario (2026-02-27, Order 268625412 production failure):
    The order has `customer_name="Abdulaziz Barnawi"` in the raw
    Salla webhook. Qoyod responded `contact_name: Can't be blank`.
    Operator wanted certainty: did the payload ACTUALLY include
    `contact_name`?

This test traces the full path:
    raw_legacy_payload
      → legacy_adapter (customer_name → customer.full_name)
        → normalizer  (full_name → CustomerDTO.name)
          → _build_contact_payload (name + contact_name = safe_name)

And asserts BOTH keys are present and equal the original customer
name. If this test ever fails, the contact_name regression has
returned and Go-Live must be blocked.
"""
from __future__ import annotations

from integrations.qoyod.legacy_adapter import adapt
from integrations.qoyod.normalizer import normalize
from integrations.qoyod.customer_resolver import _build_contact_payload


def _raw_268625412() -> dict:
    """Minimal reproduction of the Salla legacy webhook shape that
    triggered the production failure."""
    return {
        "order_id":       "268625412",
        "order_number":   "268625412",
        "status":         "completed",
        "customer_name":  "Abdulaziz Barnawi",
        "customer_mobile": "966500000000",
        "customer_email":  "abdulaziz@example.com",
        "total_amount":   "150.00",
        "currency":       "SAR",
        "created_at":     "2026-02-27T10:00:00Z",
        "completed_at":   "2026-02-27T11:00:00Z",
        "payment_method": "cash",
        "items": [
            {"sku": "TEST-1", "name": "Test product",
             "quantity": 1, "unit_price": "150.00"},
        ],
    }


def test_legacy_payload_propagates_customer_name_to_dto():
    """The chain raw → adapter → normalizer must NOT lose the name."""
    raw = _raw_268625412()
    adapted, _meta = adapt(raw)
    assert adapted["data"]["customer"]["full_name"] == "Abdulaziz Barnawi"
    dto = normalize(adapted)
    assert dto.customer.name == "Abdulaziz Barnawi"


def test_customer_payload_contains_both_name_and_contact_name():
    """The exact JSON body sent to POST /customers must carry BOTH
    `name` and `contact_name` set to the customer's actual name —
    not blank, not None, not missing."""
    raw = _raw_268625412()
    adapted, _meta = adapt(raw)
    dto = normalize(adapted)
    body = _build_contact_payload(dto.customer)
    inner = body["contact"]
    assert inner["name"] == "Abdulaziz Barnawi"
    assert inner["contact_name"] == "Abdulaziz Barnawi"
    assert inner["name"] == inner["contact_name"]
    assert inner.get("phone_number") == "+966500000000"
    assert inner.get("email") == "abdulaziz@example.com"


def test_customer_payload_never_omits_contact_name_even_for_guest():
    """Edge case: even a fully nameless guest must produce
    `contact_name` (set to the safe-guest label, e.g. "ضيف #NNN"),
    never a blank string."""
    from integrations.qoyod.dto import CustomerDTO
    guest = CustomerDTO(name="ضيف #999", is_guest=True)
    body = _build_contact_payload(guest)
    inner = body["contact"]
    assert "contact_name" in inner
    assert inner["contact_name"] == "ضيف #999"
    assert inner["contact_name"].strip(), \
        "contact_name must NEVER be a blank string"
