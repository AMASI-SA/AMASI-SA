"""End-to-end test for the user-reported customer name issue (order 268316484).

User report (2026-02-26): A real production order came through with
`customer_name = "هيفاء الحيدر الشمري"` in the raw payload. The order
reached `RULES_APPLIED → CUSTOMER_RESOLVED` then failed at
`FAILED_CUSTOMER` because Qoyod responded:

    contact_name: ["Can't be blank"]

Root cause found:
    Qoyod's `POST /customers` endpoint requires BOTH `name` AND
    `contact_name` fields. Our payload only sent `name`.

This test reproduces the exact flow:
    Make/Salla payload → legacy_adapter → normalizer → customer_resolver
    → asserts the final payload sent to the Qoyod client has BOTH
    `name` AND `contact_name` populated with the actual customer name.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.legacy_adapter import adapt as apply_legacy_adapter
from integrations.qoyod.normalizer import normalize
from integrations.qoyod.customer_resolver import (
    _build_contact_payload, resolve_customer, ResolutionResult,
)
from integrations.qoyod.dto import CustomerDTO


# ─── (1) Direct payload-builder coverage ─────────────────────────────
def test_payload_includes_both_name_and_contact_name():
    """The exact fix: both fields must be populated, identical for B2C."""
    dto = CustomerDTO(name="هيفاء الحيدر الشمري",
                      phone="+966500000000", email=None)
    body = _build_contact_payload(dto)
    body_customer = body["customer"]
    assert body_customer["name"]         == "هيفاء الحيدر الشمري"
    assert body_customer["contact_name"] == "هيفاء الحيدر الشمري"


def test_payload_contact_name_uses_safe_fallback_when_name_blank():
    """If the DTO has a blank name but a phone, BOTH fields fall back
    to the safe guest label — never blank."""
    dto = CustomerDTO(name="", phone="+966500000000", email=None)
    body = _build_contact_payload(dto)
    bc = body["customer"]
    assert bc["name"].strip(), "name must never be blank"
    assert bc["contact_name"].strip(), "contact_name must never be blank"
    assert bc["name"] == bc["contact_name"]
    assert "966500000000" in bc["name"]


def test_payload_contact_name_when_only_email():
    dto = CustomerDTO(name="", phone=None, email="x@y.com")
    body = _build_contact_payload(dto)
    bc = body["customer"]
    assert bc["contact_name"] == "عميل x@y.com"


def test_payload_contact_name_literal_guest_when_nothing():
    dto = CustomerDTO(name="", phone=None, email=None)
    bc = _build_contact_payload(dto)["customer"]
    assert bc["contact_name"] == "ضيف"


# ─── (2) Full flow: Make payload → adapter → normalizer → builder ───
def _make_payload_like_user_report() -> dict:
    """The exact Make-flattened shape that triggered the user's bug.
    `customer_name` at top level + minimal fields."""
    return {
        "event_type":      "order_completed",
        "order_id":        "268316484",
        "order_number":    "268316484",
        "status":          "completed",
        "customer_name":   "هيفاء الحيدر الشمري",
        "customer_mobile": "+966512345678",
        "currency":        "SAR",
        "total_amount":    150.50,
        "subtotal":        130.00,
        "tax":             20.50,
        "payment_method":  "mada",
        "items": [{
            "name":    "Test Product",
            "sku":     "SKU-1",
            "quantity": 1,
            "price":   130.00,
        }],
    }


def test_end_to_end_make_payload_produces_qoyod_payload_with_contact_name():
    """The user's exact repro. Every stage must preserve the customer name."""
    raw, meta = apply_legacy_adapter(_make_payload_like_user_report())
    assert meta["adapter_applied"] is True
    # Adapter put the name in customer.first_name + customer.last_name
    # AND defensively also as full_name.
    cust = raw["data"]["customer"]
    assert cust.get("first_name") == "هيفاء"
    assert cust.get("last_name")  == "الحيدر الشمري"
    assert cust.get("full_name")  == "هيفاء الحيدر الشمري"

    # Normalize → DTO has the joined name.
    dto = normalize(raw)
    assert dto.customer.name == "هيفاء الحيدر الشمري"

    # Build the Qoyod /customers payload → BOTH name + contact_name set.
    body = _build_contact_payload(dto.customer)["customer"]
    assert body["name"]         == "هيفاء الحيدر الشمري"
    assert body["contact_name"] == "هيفاء الحيدر الشمري"
    assert body["phone_number"] == "+966512345678"


def test_end_to_end_with_only_full_name_set_in_make_payload():
    """If Make sends `customer_name` but `_split_name` somehow yielded
    empty parts (single-character names, RTL marks), the `full_name`
    fallback in the adapter still carries the value through."""
    p = _make_payload_like_user_report()
    p["customer_name"] = "م"   # single character — _split_name → ("م","")
    raw, _ = apply_legacy_adapter(p)
    dto = normalize(raw)
    body = _build_contact_payload(dto.customer)["customer"]
    assert body["name"]         == "م"
    assert body["contact_name"] == "م"


# ─── (3) resolve_customer always returns the payload snapshot ───────
class _FakeAPIClient:
    """Captures whatever payload was sent. Stub-like behaviour."""
    def __init__(self):
        self.captured = None
    async def create_contact(self, payload, *, idem):
        self.captured = payload
        return {"customer": {"id": "Q-123"}}


class _FakeColl:
    def __init__(self): self.docs = {}
    async def find_one(self, q, projection=None):
        for d in self.docs.values():
            if all(d.get(k) == v for k, v in q.items()):
                return d
        return None
    async def update_one(self, q, update, upsert=False):
        key = q.get("lookup_key", "_")
        cur = self.docs.get(key, {})
        if "$set" in update: cur.update(update["$set"])
        if "$setOnInsert" in update and key not in self.docs:
            cur.update(update["$setOnInsert"])
        cur.update(q)
        self.docs[key] = cur
        class _R: matched_count = 1; modified_count = 1; upserted_id = None
        return _R()


class _FakeDB:
    def __init__(self):
        self.qoyod_customers_mapping = _FakeColl()


@pytest.mark.asyncio
async def test_resolve_customer_returns_payload_snapshot_on_success():
    """Forensic requirement: the inbox row must always carry the EXACT
    payload sent to Qoyod, so we can diagnose any `Can't be blank` style
    error without rerunning the order."""
    db = _FakeDB()
    api = _FakeAPIClient()
    dto = CustomerDTO(name="هيفاء الحيدر الشمري",
                      phone="+966500000000", email=None)
    res: ResolutionResult = await resolve_customer(
        db, "u1", dto, trace_id="t1", api_client=api)
    assert res.success is True
    assert res.qoyod_customer_id == "Q-123"
    # Snapshot is populated AND captures both name + contact_name.
    snap = res.qoyod_request_payload
    assert snap is not None
    assert snap["customer"]["name"]         == "هيفاء الحيدر الشمري"
    assert snap["customer"]["contact_name"] == "هيفاء الحيدر الشمري"
    # It's the same dict we actually passed to the client.
    assert api.captured == snap


class _FailingAPIClient:
    async def create_contact(self, payload, *, idem):
        from integrations.qoyod.api_client import QoyodAPIError
        raise QoyodAPIError(
            status_code=422, code="qoyod_validation_error",
            message='contact_name: ["Can\'t be blank"]',
            response_excerpt='{"errors":{"contact_name":["Can\'t be blank"]}}',
            endpoint="POST /customers")


@pytest.mark.asyncio
async def test_resolve_customer_returns_payload_snapshot_even_on_failure():
    """Without this, an operator seeing FAILED_CUSTOMER on the inbox
    row has no way to verify what we actually sent. The snapshot must
    be persisted even when Qoyod rejects."""
    db = _FakeDB()
    dto = CustomerDTO(name="هيفاء الحيدر الشمري",
                      phone="+966500000000", email=None)
    res = await resolve_customer(
        db, "u1", dto, trace_id="t1",
        api_client=_FailingAPIClient())
    assert res.success is False
    # Snapshot still attached on failure for diagnostics.
    snap = res.qoyod_request_payload
    assert snap is not None
    assert snap["customer"]["contact_name"] == "هيفاء الحيدر الشمري"


# ─── (4) to_log_dict carries the snapshot to the inbox row ──────────
def test_to_log_dict_includes_qoyod_request_payload():
    """The pipeline stores `res.to_log_dict()` on the inbox row under
    `customer_resolution`. The snapshot must propagate there too."""
    dto = CustomerDTO(name="هيفاء الحيدر الشمري", phone=None, email=None)
    payload = _build_contact_payload(dto)
    res = ResolutionResult(
        success=False, lookup_key="guest", lookup_kind="guest_order",
        error={"code": "x", "message": "y"},
        qoyod_request_payload=payload)
    d = res.to_log_dict()
    assert d["qoyod_request_payload"] == payload
    assert d["qoyod_request_payload"]["customer"]["contact_name"] \
           == "هيفاء الحيدر الشمري"
