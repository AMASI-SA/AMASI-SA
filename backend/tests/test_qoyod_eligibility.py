"""Contract v1.0 — invoice eligibility tests.

Locks in the SKU + positive-total guards introduced on 2026-06-26:

  Rule 2 — every line item MUST have a non-empty SKU
           → code: `items_missing_sku`
  Rule 4 — order grand total MUST be strictly positive
           → code: `total_must_be_positive`

Both gates produce FAILED_VALIDATION → DEAD_LETTER. Neither is auto-
retryable. Neither produces an invoice in Qoyod.
"""
from __future__ import annotations

import os
import secrets
import uuid

import pytest
import pytest_asyncio
import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))

from integrations.qoyod.eligibility import check_invoice_eligibility


API_BASE = os.environ.get(
    "REACT_APP_BACKEND_URL", "http://localhost:8001")
WEBHOOK_TOKEN_ENV = os.environ.get("QOYOD_WEBHOOK_TOKEN", "")


# ─── Unit tests — pure function ─────────────────────────────────────
class TestCheckEligibilityUnit:
    def _ok_payload(self):
        return {
            "data": {
                "items": [
                    {"sku": "SKU-A", "name": "A", "quantity": 1,
                     "amounts": {"price_without_tax": {"amount": 50}}},
                ],
                "amounts": {"total": {"amount": 50.0, "currency": "SAR"}},
            }
        }

    def test_happy_path_returns_none(self):
        assert check_invoice_eligibility(self._ok_payload()) is None

    def test_non_dict_payload_rejected(self):
        err = check_invoice_eligibility(None)
        assert err["code"] == "invalid_payload_shape"
        err = check_invoice_eligibility([])
        assert err["code"] == "invalid_payload_shape"

    # ─── SKU rule ──────────────────────────────────────────────────
    def test_empty_sku_on_single_item_rejected(self):
        p = self._ok_payload()
        p["data"]["items"][0]["sku"] = ""
        err = check_invoice_eligibility(p)
        assert err["code"] == "items_missing_sku"
        assert err["total_offenders"] == 1
        assert err["offenders"][0]["reason"] == "empty_sku"
        assert err["offenders"][0]["index"] == 0

    def test_missing_sku_key_rejected(self):
        p = self._ok_payload()
        del p["data"]["items"][0]["sku"]
        assert check_invoice_eligibility(p)["code"] == "items_missing_sku"

    def test_whitespace_sku_rejected(self):
        p = self._ok_payload()
        p["data"]["items"][0]["sku"] = "   "
        assert check_invoice_eligibility(p)["code"] == "items_missing_sku"

    def test_mixed_items_only_one_missing_sku_still_rejects(self):
        p = self._ok_payload()
        p["data"]["items"] = [
            {"sku": "GOOD", "name": "Good"},
            {"sku": "", "name": "Bad"},
        ]
        err = check_invoice_eligibility(p)
        assert err["code"] == "items_missing_sku"
        assert err["total_offenders"] == 1

    def test_non_object_item_is_skipped_let_normalize_catch_it(self):
        """Non-dict items are STRUCTURAL bugs caught by normalize() — not
        an eligibility concern. Eligibility passes when the only problem
        is shape, so the row routes to FAILED_NORMALIZATION (not
        FAILED_VALIDATION). This preserves the Day-3 contract."""
        p = self._ok_payload()
        p["data"]["items"] = ["not-an-object"]
        # No dict items remain to check SKU on. Eligibility is silent.
        # (validate() will still pass because items[] is non-empty;
        # normalize() will then raise.)
        assert check_invoice_eligibility(p) is None

    def test_offenders_list_capped_at_five(self):
        p = self._ok_payload()
        p["data"]["items"] = [
            {"sku": "", "name": f"item-{i}"} for i in range(12)]
        err = check_invoice_eligibility(p)
        assert err["total_offenders"] == 12
        assert len(err["offenders"]) == 5     # capped

    # ─── Total rule ────────────────────────────────────────────────
    def test_total_zero_rejected(self):
        p = self._ok_payload()
        p["data"]["amounts"]["total"]["amount"] = 0
        err = check_invoice_eligibility(p)
        assert err["code"] == "total_must_be_positive"

    def test_total_negative_rejected(self):
        p = self._ok_payload()
        p["data"]["amounts"]["total"]["amount"] = -10
        assert check_invoice_eligibility(p)["code"] == "total_must_be_positive"

    def test_total_missing_rejected(self):
        p = self._ok_payload()
        p["data"]["amounts"] = {}
        assert check_invoice_eligibility(p)["code"] == "total_missing"

    def test_total_invalid_string_rejected(self):
        p = self._ok_payload()
        p["data"]["amounts"]["total"]["amount"] = "abc"
        err = check_invoice_eligibility(p)
        assert err["code"] == "total_invalid"

    def test_total_as_flat_string_accepted(self):
        """Make.com often sends '139.51' as a string — must parse."""
        p = self._ok_payload()
        p["data"]["amounts"]["total"] = "139.51"
        assert check_invoice_eligibility(p) is None

    def test_root_level_total_amount_fallback(self):
        """Legacy-adapted payloads may put total at data.total_amount."""
        p = {"data": {
            "items": [{"sku": "A", "name": "A"}],
            "amounts": {},
            "total_amount": 99.99,
        }}
        assert check_invoice_eligibility(p) is None

    # ─── Order of checks ───────────────────────────────────────────
    def test_sku_check_runs_before_total_check(self):
        """When BOTH rules fail, we report the SKU one first — it's the
        higher-signal problem (data missing vs. arithmetic edge)."""
        p = {"data": {
            "items":   [{"sku": "", "name": "X"}],
            "amounts": {"total": {"amount": 0}},
        }}
        err = check_invoice_eligibility(p)
        assert err["code"] == "items_missing_sku"

    def test_no_data_envelope_uses_root(self):
        """The function is robust against missing `data` envelope —
        falls back to treating the root as the data block."""
        p = {
            "items": [{"sku": "A", "name": "A"}],
            "amounts": {"total": {"amount": 50}},
        }
        assert check_invoice_eligibility(p) is None


# ─── HTTP integration — full webhook → eligibility → DEAD_LETTER ────
@pytest_asyncio.fixture
async def db():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]
    client.close()


@pytest_asyncio.fixture(autouse=True)
async def _cleanup():
    yield
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    try:
        await client[os.environ["DB_NAME"]].integration_inbox.delete_many(
            {"salla_order_number": {"$regex": "^ELIG-TEST-"}})
    finally:
        client.close()


def _full_legacy_payload(*, sku: str = "SKU-A", total: float = 139.51) -> dict:
    return {
        "event_type":      "order_completed",
        "order_number":    f"ELIG-TEST-{uuid.uuid4().hex[:8]}",
        "order_id":        f"id-{uuid.uuid4().hex[:8]}",
        "created_at":      "2026-06-26 07:00:16.000000",
        "completed_at":    "2026-06-26 14:30:00",
        "total_amount":    total,
        "subtotal":        105,
        "shipping_cost":   22.61,
        "tax":             11.90,
        "payment_method":  "mada",
        "currency":        "SAR",
        "customer_name":   "عميل تجريبي",
        "customer_mobile": "+966500000000",
        "order_status":      "تم التنفيذ",
        "order_status_slug": "completed",
        "items": [
            {"sku": sku, "name": "منتج 1", "quantity": 2,
             "price": {"amount": 50, "currency": "SAR"}},
        ],
    }


async def _post(body: dict, token: str, idem: str) -> httpx.Response:
    headers = {"X-Webhook-Token": token, "X-Idempotency-Key": idem,
               "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=30) as c:
        return await c.post(
            f"{API_BASE}/api/integrations/qoyod/webhook",
            json=body, headers=headers)


@pytest.mark.asyncio
async def test_http_empty_sku_dead_letters_with_items_missing_sku(db):
    if not WEBHOOK_TOKEN_ENV:
        pytest.skip("QOYOD_WEBHOOK_TOKEN not configured")
    body = _full_legacy_payload(sku="")     # ← empty SKU
    idem = f"empty-sku-{secrets.token_hex(6)}"
    r = await _post(body, WEBHOOK_TOKEN_ENV, idem)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["pipeline_stage"] == "DEAD_LETTER"
    assert j["error"]["code"] == "items_missing_sku"
    row = await db.integration_inbox.find_one({"idempotency_key": idem})
    stages = [h["to_stage"] for h in row["stage_history"]]
    assert "FAILED_VALIDATION" in stages
    assert "DEAD_LETTER" in stages
    # Critical: NO invoice was attempted
    assert row["pipeline_stage"] == "DEAD_LETTER"


@pytest.mark.asyncio
async def test_http_zero_total_dead_letters(db):
    if not WEBHOOK_TOKEN_ENV:
        pytest.skip("QOYOD_WEBHOOK_TOKEN not configured")
    body = _full_legacy_payload(total=0)
    idem = f"zero-total-{secrets.token_hex(6)}"
    r = await _post(body, WEBHOOK_TOKEN_ENV, idem)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["pipeline_stage"] == "DEAD_LETTER"
    assert j["error"]["code"] == "total_must_be_positive"


@pytest.mark.asyncio
async def test_http_negative_total_dead_letters(db):
    if not WEBHOOK_TOKEN_ENV:
        pytest.skip("QOYOD_WEBHOOK_TOKEN not configured")
    body = _full_legacy_payload(total=-50)
    idem = f"neg-total-{secrets.token_hex(6)}"
    r = await _post(body, WEBHOOK_TOKEN_ENV, idem)
    j = r.json()
    assert j["error"]["code"] == "total_must_be_positive"


@pytest.mark.asyncio
async def test_http_valid_payload_passes_eligibility(db):
    if not WEBHOOK_TOKEN_ENV:
        pytest.skip("QOYOD_WEBHOOK_TOKEN not configured")
    body = _full_legacy_payload()
    idem = f"valid-{secrets.token_hex(6)}"
    r = await _post(body, WEBHOOK_TOKEN_ENV, idem)
    j = r.json()
    # MUST NOT hit any eligibility failure code
    err_code = (j.get("error") or {}).get("code")
    assert err_code not in (
        "items_missing_sku", "total_must_be_positive",
        "total_missing", "total_invalid",
    )
    # Pipeline may proceed past FAILED_VALIDATION (subsequent stages may
    # still fail for other reasons in this Preview env — that's not
    # what this test guards).
    row = await db.integration_inbox.find_one({"idempotency_key": idem})
    stages = [h["to_stage"] for h in row["stage_history"]]
    # At minimum VALIDATED reached
    assert "VALIDATED" in stages or "NORMALIZED" in stages or \
           "RULES_APPLIED" in stages
