"""Iter-293 — Posting mode (COD = credit_invoice_only) tests.

Covers the four contracts from the spec:

  1. `is_cod_family` recognises every COD variant (en + ar + aliases).
  2. `resolve_posting_mode` ALWAYS returns credit_invoice_only for COD,
     IGNORING the operator's setting (defense in depth).
  3. `coerce_cod_rows` enforces the same rule at the API write boundary.
  4. `needs_qoyod_account` is True ONLY for paid_receipt.

Plus integration tests against the live diagnostic endpoints.
"""
from __future__ import annotations

import os
import sys
import uuid

import pytest
import requests

sys.path.insert(0, "/app/backend")
from integrations.qoyod.payment_methods import (  # noqa: E402
    POSTING_MODE_CREDIT_INVOICE_ONLY,
    POSTING_MODE_DISABLED,
    POSTING_MODE_PAID_RECEIPT,
    VALID_POSTING_MODES,
    coerce_cod_rows,
    is_cod_family,
    needs_qoyod_account,
    resolve_posting_mode,
)
from integrations.qoyod.bank_transfer_discovery import (  # noqa: E402
    discover_candidate_paths,
)


def _read_backend_url() -> str:
    explicit = os.environ.get("REACT_APP_BACKEND_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    return line.split("=", 1)[1].strip().strip('"').rstrip("/")
    except FileNotFoundError:
        pass
    return ""


BASE_URL = _read_backend_url()
API = f"{BASE_URL}/api" if BASE_URL else ""


# ── Unit tests ────────────────────────────────────────────────────────
class TestIsCodFamily:
    @pytest.mark.parametrize("v", [
        "cod", "COD", "Cod",
        "cash_on_delivery", "Cash on Delivery", "CASH ON DELIVERY",
        "cash",
        "الدفع عند الاستلام",
        "النوع_عند_الاستلام",
        "نقد عند الاستلام",
        "نقدًا عند الاستلام",
    ])
    def test_recognises_cod_variant(self, v):
        assert is_cod_family(v) is True, f"failed for {v!r}"

    @pytest.mark.parametrize("v", [
        "mada", "apple_pay", "stc_pay", "visa", "credit_card",
        "bank_transfer", "tamara", "tabby",
        "", None, "  ",
    ])
    def test_rejects_non_cod(self, v):
        assert is_cod_family(v) is False, f"false positive for {v!r}"


class TestResolvePostingMode:
    def test_cod_always_credit_invoice_only_even_when_settings_say_otherwise(self):
        """The DEFENSE-IN-DEPTH rule: even if the operator (or a bug)
        saved posting_mode=paid_receipt for a COD row, the resolver
        IGNORES it and returns credit_invoice_only."""
        sneaky_settings = {
            "payment_method_mapping": [{
                "salla_method":      "cod",
                "posting_mode":      "paid_receipt",  # ← attempted override
                "qoyod_account_id":  "17",
            }]
        }
        assert resolve_posting_mode(sneaky_settings, "cod") \
            == POSTING_MODE_CREDIT_INVOICE_ONLY
        assert resolve_posting_mode(sneaky_settings, "cash_on_delivery") \
            == POSTING_MODE_CREDIT_INVOICE_ONLY
        assert resolve_posting_mode(sneaky_settings, "الدفع عند الاستلام") \
            == POSTING_MODE_CREDIT_INVOICE_ONLY

    def test_non_cod_defaults_to_paid_receipt(self):
        assert resolve_posting_mode({}, "mada") == POSTING_MODE_PAID_RECEIPT
        assert resolve_posting_mode({}, "apple_pay") == POSTING_MODE_PAID_RECEIPT
        assert resolve_posting_mode({}, "bank_transfer") == POSTING_MODE_PAID_RECEIPT

    def test_disabled_mode_respected_for_non_cod(self):
        settings = {"payment_method_mapping": [
            {"salla_method": "tamara", "posting_mode": "disabled"}]}
        assert resolve_posting_mode(settings, "tamara") \
            == POSTING_MODE_DISABLED

    def test_alias_lookup_uses_family_mode(self):
        settings = {"payment_method_mapping": [
            {"salla_method": "tamara", "posting_mode": "paid_receipt",
             "qoyod_account_id": "17"}]}
        # tamara_installment → alias → tamara
        assert resolve_posting_mode(settings, "tamara_installment") \
            == POSTING_MODE_PAID_RECEIPT

    def test_empty_input_returns_default(self):
        assert resolve_posting_mode({}, None) == POSTING_MODE_PAID_RECEIPT
        assert resolve_posting_mode({}, "") == POSTING_MODE_PAID_RECEIPT


class TestCoerceCodRows:
    def test_forces_cod_to_credit_invoice_only(self):
        out = coerce_cod_rows([
            {"salla_method": "cod", "posting_mode": "paid_receipt",
             "qoyod_account_id": "17"}
        ])
        assert len(out) == 1
        assert out[0]["posting_mode"] == "credit_invoice_only"
        assert out[0]["qoyod_account_id"] is None

    def test_forces_arabic_cod(self):
        out = coerce_cod_rows([
            {"salla_method": "الدفع عند الاستلام",
             "posting_mode": "paid_receipt", "qoyod_account_id": "5"}
        ])
        assert out[0]["posting_mode"] == "credit_invoice_only"
        assert out[0]["qoyod_account_id"] is None

    def test_non_cod_passes_through_with_default(self):
        out = coerce_cod_rows([
            {"salla_method": "mada", "qoyod_account_id": "17"}
        ])
        # paid_receipt is filled in as the default.
        assert out[0]["posting_mode"] == "paid_receipt"
        assert out[0]["qoyod_account_id"] == "17"

    def test_non_cod_with_explicit_disabled_kept(self):
        out = coerce_cod_rows([
            {"salla_method": "tamara", "posting_mode": "disabled"}
        ])
        assert out[0]["posting_mode"] == "disabled"

    def test_invalid_posting_mode_normalised_to_paid_receipt(self):
        out = coerce_cod_rows([
            {"salla_method": "mada", "posting_mode": "wat", "qoyod_account_id": "17"}
        ])
        assert out[0]["posting_mode"] == "paid_receipt"

    def test_returns_new_list_does_not_mutate_input(self):
        original = [{"salla_method": "cod", "posting_mode": "paid_receipt",
                     "qoyod_account_id": "17"}]
        snapshot = list(original[0].items())
        coerce_cod_rows(original)
        assert list(original[0].items()) == snapshot


class TestNeedsQoyodAccount:
    def test_only_paid_receipt_needs_account(self):
        assert needs_qoyod_account(POSTING_MODE_PAID_RECEIPT) is True
        assert needs_qoyod_account(POSTING_MODE_CREDIT_INVOICE_ONLY) is False
        assert needs_qoyod_account(POSTING_MODE_DISABLED) is False


class TestValidPostingModes:
    def test_constants_match_set(self):
        assert VALID_POSTING_MODES == {
            POSTING_MODE_PAID_RECEIPT,
            POSTING_MODE_CREDIT_INVOICE_ONLY,
            POSTING_MODE_DISABLED,
        }


class TestBankTransferDiscovery:
    def test_finds_bank_paths(self):
        payload = {
            "order": {
                "id": 12345,
                "payment_method": "bank_transfer",
                "customer": {"name": "X", "email": "y@z.com"},  # redacted
                "transactions": [
                    {"id": 1, "bank_name": "الراجحي", "amount": 100},
                    {"id": 2, "iban": "SA00000000", "amount": 50},
                ],
            }
        }
        paths = discover_candidate_paths(payload)
        keys = {h["key"] for h in paths}
        assert "bank_name" in keys
        assert "iban" in keys
        # Customer email path is NOT bank-related → not included.
        assert all("email" not in p["path"] for p in paths)
        # The bank_name hit should be inside the transactions array →
        # path must include `transactions`.
        bank_name_paths = [p["path"] for p in paths if p["key"] == "bank_name"]
        assert any("transactions" in p for p in bank_name_paths)


# ── E2E (live HTTP) ──────────────────────────────────────────────────
class TestLiveEndpoints:
    """Hits the live backend through REACT_APP_BACKEND_URL."""

    @pytest.fixture(autouse=True)
    def _check(self):
        if not API:
            pytest.skip("REACT_APP_BACKEND_URL not configured")

    def _token(self):
        r = requests.post(
            f"{API}/auth/login",
            json={"email": "admin@hesab.app", "password": "admin123"},
            timeout=10,
        )
        if r.status_code != 200:
            pytest.skip(f"admin login failed: {r.status_code}")
        return (r.json().get("access_token") or r.json().get("token"))

    def test_cod_report_endpoint_returns_shape(self):
        h = {"Authorization": f"Bearer {self._token()}"}
        r = requests.get(
            f"{API}/integrations/qoyod/admin/cod-receipts-report?limit=5",
            headers=h, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        for k in ("total_cod", "with_receipt", "without_receipt",
                  "rows", "filters"):
            assert k in data, f"missing field {k}"

    def test_bank_transfer_discovery_endpoint_returns_shape(self):
        h = {"Authorization": f"Bearer {self._token()}"}
        r = requests.get(
            f"{API}/integrations/qoyod/admin/bank-transfer-discovery?limit=3",
            headers=h, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True
        for k in ("scanned_total", "sample_count", "samples", "notes"):
            assert k in data

    def test_settings_put_coerces_cod_row(self):
        """End-to-end: send a CRAFTED payload with COD wrongly set as
        paid_receipt + a Qoyod account, then GET /settings and assert
        the persisted row has been coerced."""
        token = self._token()
        h = {"Authorization": f"Bearer {token}",
             "Content-Type": "application/json"}
        # Read current mapping first so we can restore.
        cur = requests.get(f"{API}/integrations/qoyod/settings",
                           headers=h, timeout=10).json()
        original = cur.get("payment_method_mapping") or []
        # Patch in a sneaky COD row.
        sneaky = original + [{
            "salla_method": "cod",
            "qoyod_account_id": "999999",
            "posting_mode": "paid_receipt",  # ← attempted override
            "label_ar": "الدفع عند الاستلام",
        }]
        r = requests.put(
            f"{API}/integrations/qoyod/settings",
            headers=h, json={"payment_method_mapping": sneaky},
            timeout=15)
        assert r.status_code == 200, r.text
        # Read back.
        after = requests.get(f"{API}/integrations/qoyod/settings",
                             headers=h, timeout=10).json()
        cod_rows = [r_ for r_ in (after.get("payment_method_mapping") or [])
                    if (r_.get("salla_method") or "").lower() == "cod"]
        assert cod_rows, "COD row should have been persisted"
        for r_ in cod_rows:
            assert r_["posting_mode"] == "credit_invoice_only", (
                f"backend failed to coerce COD: {r_}")
            assert (r_.get("qoyod_account_id") in (None, "", "null")), (
                f"backend failed to null account for COD: {r_}")
        # Restore.
        requests.put(f"{API}/integrations/qoyod/settings",
                     headers=h, json={"payment_method_mapping": original},
                     timeout=10)


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
