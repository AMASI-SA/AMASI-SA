"""Tests for payment-method alias resolution (2026-02-26).

User spec:
    1. No hardcoded provider names anywhere in the runtime — accept any
       string Salla sends.
    2. Variant names (`tamara_installment`, `tabby_installment`, …)
       resolve to their base provider mapping by default.
    3. User can still override by mapping the variant explicitly to a
       different Qoyod account.
    4. New unknown methods don't block runtime — they surface as a
       Settings-page row, not as a generic crash.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.payment_methods import (
    PAYMENT_METHOD_ALIASES,
    provider_family,
    resolve_payment_account,
    explain_resolution,
)
from integrations.qoyod.preflight import run as preflight_run


# ─── Provider family ─────────────────────────────────────────────────
def test_provider_family_collapses_known_aliases():
    assert provider_family("tamara_installment")  == "tamara"
    assert provider_family("TAMARA_INSTALLMENT")  == "tamara"
    assert provider_family("tabby_installment")   == "tabby"
    assert provider_family("tabby_payment")       == "tabby"
    assert provider_family("emkan_installment")   == "emkan"
    assert provider_family("bank")                == "bank_transfer"
    assert provider_family("cash_on_delivery")    == "cod"
    assert provider_family("applepay")            == "apple_pay"
    assert provider_family("stcpay")              == "stc_pay"


def test_provider_family_passthrough_for_canonical_keys():
    assert provider_family("mada")      == "mada"
    assert provider_family("tamara")    == "tamara"
    assert provider_family("apple_pay") == "apple_pay"


def test_provider_family_passthrough_for_unknown_keys():
    """A brand-new method we've never seen returns itself — no crash,
    no None — so the Settings page can still surface it."""
    assert provider_family("brand_new_method_2030") == "brand_new_method_2030"
    assert provider_family("some_BNPL_provider") == "some_bnpl_provider"


def test_provider_family_handles_empty_and_none():
    assert provider_family(None) is None
    assert provider_family("")   is None
    assert provider_family("   ") is None


# ─── Resolver: direct + alias paths ──────────────────────────────────
def test_resolver_direct_match_takes_priority():
    settings = {"payment_method_mapping": [
        {"salla_method": "tamara",              "qoyod_account_id": "A-1"},
        {"salla_method": "tamara_installment",  "qoyod_account_id": "A-2"},
    ]}
    # Direct match wins over alias even when both exist.
    assert resolve_payment_account(settings, "tamara_installment") == "A-2"
    assert resolve_payment_account(settings, "tamara")             == "A-1"


def test_resolver_falls_back_to_alias_family():
    settings = {"payment_method_mapping": [
        {"salla_method": "tamara", "qoyod_account_id": "A-1"},
    ]}
    # No direct entry for variant, but alias collapses to "tamara".
    assert resolve_payment_account(settings, "tamara_installment")  == "A-1"
    assert resolve_payment_account(settings, "tamara_installments") == "A-1"
    assert resolve_payment_account(settings, "tamara_pay")          == "A-1"


def test_resolver_returns_none_when_neither_direct_nor_alias():
    settings = {"payment_method_mapping": [
        {"salla_method": "mada", "qoyod_account_id": "A-99"},
    ]}
    assert resolve_payment_account(settings, "tamara_installment") is None
    assert resolve_payment_account(settings, "brand_new_method")   is None


def test_resolver_ignores_blank_account_ids():
    settings = {"payment_method_mapping": [
        {"salla_method": "tamara", "qoyod_account_id": ""},
        {"salla_method": "tabby",  "qoyod_account_id": "   "},
    ]}
    assert resolve_payment_account(settings, "tamara_installment") is None
    assert resolve_payment_account(settings, "tabby")              is None


def test_resolver_is_case_insensitive_and_whitespace_tolerant():
    settings = {"payment_method_mapping": [
        {"salla_method": "Tamara", "qoyod_account_id": "A-1"},
    ]}
    assert resolve_payment_account(settings, "TAMARA_INSTALLMENT") == "A-1"
    assert resolve_payment_account(settings, " tamara ") == "A-1"


# ─── explain_resolution diagnostic ───────────────────────────────────
def test_explain_returns_full_breakdown():
    settings = {"payment_method_mapping": [
        {"salla_method": "tamara", "qoyod_account_id": "A-1"},
    ]}
    info = explain_resolution(settings, "tamara_installment")
    assert info == {
        "input": "tamara_installment", "family": "tamara",
        "matched_via": "alias", "matched_key": "tamara",
        "qoyod_account_id": "A-1",
    }
    direct = explain_resolution(settings, "tamara")
    assert direct["matched_via"] == "direct"
    miss = explain_resolution(settings, "brand_new")
    assert miss["matched_via"] is None and miss["qoyod_account_id"] is None


# ─── Preflight integration ───────────────────────────────────────────
def _ok_dto(payment_method: str = "tamara_installment") -> dict:
    """Build a DTO that would pass preflight EXCEPT for the payment-
    method mapping under test."""
    return {
        "order_id":          "1001",
        "order_number":      "1001",
        "order_status":      "completed",
        "payment_method":    payment_method,
        "items": [{"sku": "P1", "name": "X", "quantity": 1,
                   "unit_price": 100, "tax_amount": 15}],
        "customer": {"name": "x", "phone": "+966500000000"},
    }


def test_preflight_passes_when_variant_resolves_via_alias():
    settings = {
        "default_tax_id": "1",
        "tax_mode": "mezan_fixed_15",
        "invoice_trigger_statuses": ["completed"],
        "payment_method_mapping": [
            {"salla_method": "tamara", "qoyod_account_id": "A-1"},
        ],
    }
    res = preflight_run(
        dto_dict=_ok_dto("tamara_installment"),
        settings=settings,
        qoyod_customer_id="C-1",
        product_resolutions=[{"sku": "P1", "qoyod_product_id": "Q1"}],
    )
    assert res.passed is True, f"failures: {res.failures}"


def test_preflight_fails_for_unmapped_variant_with_no_alias_fallback():
    settings = {
        "default_tax_id": "1",
        "invoice_trigger_statuses": ["completed"],
        "payment_method_mapping": [
            {"salla_method": "mada", "qoyod_account_id": "A-99"},
        ],
    }
    res = preflight_run(
        dto_dict=_ok_dto("tamara_installment"),
        settings=settings,
        qoyod_customer_id="C-1",
        product_resolutions=[{"sku": "P1", "qoyod_product_id": "Q1"}],
    )
    assert res.passed is False
    pm_fail = next(f for f in res.failures
                   if f["code"] == "payment_method_mapping_missing")
    # The error message must surface the suggested family so the
    # operator sees what to map next.
    assert "tamara" in pm_fail["message"]
    assert pm_fail["extra"]["provider_family"] == "tamara"


# ─── Settings validation alias awareness ─────────────────────────────
@pytest.mark.asyncio
async def test_setup_validation_treats_variant_as_mapped_via_alias():
    """The exact bug repro: an order arrived with `tamara_installment`
    but Settings only has `tamara` mapped. The Settings page must NOT
    flag this as a blocker — the alias covers it."""
    from integrations.qoyod.setup_validation import validate_settings_for_setup

    class _Cursor:
        def __init__(self, rows): self.rows = rows
        def __aiter__(self): self._it = iter(self.rows); return self
        async def __anext__(self):
            try: return next(self._it)
            except StopIteration: raise StopAsyncIteration

    class _Coll:
        def __init__(self, rows=None): self.rows = rows or []
        def find(self, q=None, projection=None): return _Cursor(self.rows)
        async def find_one(self, q, projection=None):
            for r in self.rows:
                if all(r.get(k) == v for k, v in q.items()): return r
            return None

    class _DB:
        def __init__(self):
            self.qoyod_settings = _Coll([{
                "user_id": "u1",
                "default_tax_id": "1",
                "default_product_type": "service",
                "payment_method_mapping": [
                    {"salla_method": "tamara", "qoyod_account_id": "A-1"},
                ],
            }])
            # One inbox row with the variant.
            self.integration_inbox = _Coll([{
                "user_id": "u1",
                "canonical_payload": {
                    "payment_method": "tamara_installment",
                    "order_status": "completed"}}])
            self.unified_orders = _Coll([])

    res = await validate_settings_for_setup(_DB(), user_id="u1")
    # The variant must NOT be in `missing_payment_methods` because the
    # `tamara` mapping covers it via alias.
    assert "tamara_installment" not in res["context"]["missing_payment_methods"]
    blockers = [i for i in res["issues"] if i["severity"] == "blocker"]
    pm_blockers = [b for b in blockers
                   if b["code"] == "unmapped_payment_methods"]
    assert not pm_blockers, f"unexpected blocker(s): {pm_blockers}"
