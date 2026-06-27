"""Tests for customer-name fallback chain + Arabic COD aliases.

User spec (2026-02-26 final):
    A) Customer name must NEVER be sent blank to Qoyod. Fallback order:
       full_name → first_name+last_name → name → mobile → "ضيف #order_no" → "ضيف".
    B) Arabic COD variants ("الدفع عند الاستلام", "النوع عند الاستلام", …)
       must canonicalize to `cod` so a single mapping covers them all.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.normalizer import (
    _normalize_customer, _canonical_payment_method,
)
from integrations.qoyod.customer_resolver import (
    _build_contact_payload, _safe_guest_name,
)
from integrations.qoyod.dto import CustomerDTO
from integrations.qoyod.payment_methods import (
    provider_family, resolve_payment_account,
)


# ─── (A) Customer-name fallback chain ───────────────────────────────
def test_uses_first_plus_last_when_both_set():
    dto = _normalize_customer({"customer": {
        "first_name": "Sara", "last_name": "Ali"}}, order_number="1001")
    assert dto.name == "Sara Ali"


def test_uses_full_name_when_first_last_missing():
    dto = _normalize_customer({"customer": {
        "full_name": "Mohammed Al-Otaibi"}}, order_number="1001")
    assert dto.name == "Mohammed Al-Otaibi"


def test_uses_name_field_when_only_name_present():
    dto = _normalize_customer({"customer": {
        "name": "Just A Name"}}, order_number="1001")
    assert dto.name == "Just A Name"


def test_first_last_takes_priority_over_full_name():
    dto = _normalize_customer({"customer": {
        "first_name": "Sara", "last_name": "Ali",
        "full_name":  "IGNORED Full Name"}}, order_number="1001")
    assert dto.name == "Sara Ali"


def test_falls_back_to_phone_label_when_only_phone():
    dto = _normalize_customer({"customer": {
        "mobile": "+966501234567"}}, order_number="1001")
    assert dto.name.startswith("عميل ")
    assert "966501234567" in dto.name


def test_falls_back_to_guest_with_order_number():
    dto = _normalize_customer({"customer": {}}, order_number="1001")
    assert dto.name == "ضيف #1001"


def test_falls_back_to_bare_guest_without_order_number():
    dto = _normalize_customer({"customer": {}}, order_number=None)
    assert dto.name == "ضيف"


def test_string_customer_payload_still_falls_back():
    dto = _normalize_customer({"customer": ""}, order_number="999")
    assert dto.name == "ضيف #999"


def test_string_customer_with_value_kept_as_is():
    dto = _normalize_customer({"customer": "Ahmed"}, order_number="999")
    assert dto.name == "Ahmed"


# ─── Payload builder belt-and-suspenders ────────────────────────────
def test_build_contact_payload_never_sends_blank():
    """If the DTO somehow has a blank name (legacy rows pre-fix), the
    payload builder must still produce a non-blank name."""
    dto = CustomerDTO(name="", phone="+966501234567", email=None)
    body = _build_contact_payload(dto)
    assert body["contact"]["name"]            # non-empty
    assert "966501234567" in body["contact"]["name"]


def test_build_contact_payload_uses_email_when_no_phone():
    dto = CustomerDTO(name="   ", phone=None, email="a@b.com")
    body = _build_contact_payload(dto)
    assert body["contact"]["name"] == "عميل a@b.com"


def test_build_contact_payload_last_resort_literal_guest():
    dto = CustomerDTO(name="", phone=None, email=None)
    body = _build_contact_payload(dto)
    assert body["contact"]["name"] == "ضيف"


def test_safe_guest_name_helper_phone_priority():
    dto = CustomerDTO(name="ignored", phone="+9665", email="a@b.com")
    assert _safe_guest_name(dto) == "عميل +9665"


# ─── (B) Arabic COD aliases ─────────────────────────────────────────
@pytest.mark.parametrize("native,expected", [
    # English
    ("cash on delivery", "cod"),
    ("Cash on Delivery", "cod"),
    ("COD",              "cod"),
    ("cash",             "cod"),
    # Arabic — the user's exact reported variant + neighbours
    ("الدفع عند الاستلام",       "cod"),
    ("النوع عند الاستلام",       "cod"),
    ("الدفع نقدا عند الاستلام",  "cod"),
    ("نقد عند الاستلام",         "cod"),
])
def test_canonical_payment_method_resolves_cod_variants(native, expected):
    assert _canonical_payment_method(native) == expected


@pytest.mark.parametrize("native,expected", [
    ("تمارا",             "tamara"),
    ("تابي",              "tabby"),
    ("إمكان",             "emkan"),
    ("تحويل بنكي",        "bank_transfer"),
])
def test_canonical_payment_method_resolves_other_arabic_variants(native, expected):
    assert _canonical_payment_method(native) == expected


def test_alias_table_covers_already_normalised_arabic_keys():
    """If a legacy inbox row already has the Arabic key with underscores
    (because the normalizer at write-time didn't recognise it), the
    alias table must still resolve it to `cod`."""
    assert provider_family("الدفع_عند_الاستلام") == "cod"
    assert provider_family("النوع_عند_الاستلام") == "cod"


def test_cod_mapping_covers_arabic_native_strings_end_to_end():
    """End-to-end: Salla sends Arabic native → normalizer canonicalises
    → resolver matches `cod` mapping in settings."""
    settings = {"payment_method_mapping": [
        {"salla_method": "cod", "qoyod_account_id": "A-COD"},
    ]}
    # Salla → normalizer → "cod"
    canonical = _canonical_payment_method("النوع عند الاستلام")
    assert canonical == "cod"
    # Resolver → matched
    assert resolve_payment_account(settings, canonical) == "A-COD"
