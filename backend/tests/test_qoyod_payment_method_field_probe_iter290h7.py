"""Iter-290h.7 — Tests for the read-only payment-method-field probe.

The probe must:
  • Issue ONLY GET requests against قيود (no writes).
  • Return both raw invoice bodies so the operator can inspect them.
  • Surface candidate payment-method-related fields with their values
    in each invoice.
  • Refuse when both invoice ids are identical or missing.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.payment_method_field_probe import (
    probe_payment_method_field,
    _candidate_payment_fields,
    _walk_dict,
)


# ─── 1. Field-walker spots payment-method-style keys ─────────────────
def test_candidate_payment_fields_finds_top_level_payment_keys():
    """Recall test — any reasonable name should land in the
    candidate set."""
    doc = {
        "invoice": {
            "id": 63,
            "payment_method":      "تحويل بنكي",
            "payment_method_name": "Bank Transfer",
            "payment_terms":       "Net 30",
            "payment_mode":        "cash",
            "contact_id":          109,
            "total":               131.92,
            "line_items": [
                # Buried in a list — must NOT be flagged as a header field.
                {"payment_split": "ignored"},
            ],
        },
    }
    out = _candidate_payment_fields(doc)
    assert "payment_method"      in out
    assert "payment_method_name" in out
    assert "payment_terms"       in out
    assert "payment_mode"        in out
    # Header-only: line_items[].payment_split must NOT be present.
    assert all("line_items" not in path for path in out)
    # Boring fields must NOT be flagged.
    assert "contact_id" not in out
    assert "total"      not in out


def test_candidate_payment_fields_unwraps_invoice_envelope():
    """قيود returns `{"invoice": {...}}` — the walker should look
    inside that envelope."""
    doc = {"invoice": {"payment_method": "مدفوعات سلة"}}
    flat_doc = {"payment_method": "مدفوعات سلة"}
    assert _candidate_payment_fields(doc) == {"payment_method": "مدفوعات سلة"}
    assert _candidate_payment_fields(flat_doc) == {"payment_method": "مدفوعات سلة"}


def test_walk_dict_handles_nested_objects():
    doc = {"a": {"b": {"c": 1}}, "d": [10, 20]}
    out = dict(_walk_dict(doc))
    assert out["a.b.c"] == 1
    assert out["d[0]"] == 10
    assert out["d[1]"] == 20


# ─── 2. The probe is strictly read-only ──────────────────────────────
class _StubAPIClient:
    """In-memory API client that records every method called. The
    probe MUST never invoke any of the write-method names listed in
    `WRITE_METHODS`."""
    WRITE_METHODS = (
        "create_contact", "create_product", "create_invoice",
        "create_receipt", "create_invoice_payment",
        "delete_invoice", "delete_receipt", "delete_product",
        "delete_customer", "update_invoice", "patch_invoice",
    )

    def __init__(self, responses: dict):
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    async def get_invoice(self, invoice_id):
        self.calls.append(("get_invoice", str(invoice_id)))
        if invoice_id in self._responses:
            return self._responses[invoice_id]
        raise RuntimeError(f"no stubbed response for {invoice_id}")

    def __getattr__(self, name):
        # Trap any forbidden write attempt.
        if name in self.WRITE_METHODS:
            raise AssertionError(
                f"Probe attempted a forbidden write call: {name!r}")
        raise AttributeError(name)


@pytest.fixture
def patched_probe(monkeypatch):
    """Patch the api-key fetch + replace the QoyodAPIClient with our
    recorder so we can assert on the call log."""
    from integrations.qoyod import payment_method_field_probe as pm

    async def _fake_get_api_key(db, user_id):
        return "test-key"

    monkeypatch.setattr(pm, "get_api_key", _fake_get_api_key)

    holder: dict = {"client": None}

    def _factory(responses):
        client = _StubAPIClient(responses)
        holder["client"] = client

        class _Constructor:
            def __init__(self, _key):
                pass

            def __new__(cls, _key):
                return client

        monkeypatch.setattr(pm, "QoyodAPIClient", _Constructor)
        return holder

    return _factory


@pytest.mark.asyncio
async def test_probe_makes_only_two_get_calls(patched_probe):
    """The probe must call GET twice — once per invoice — and never
    issue any write."""
    holder = patched_probe({
        "63": {"invoice": {"id": 63, "payment_method": None}},
        "42": {"invoice": {"id": 42, "payment_method": "تحويل بنكي"}},
    })
    out = await probe_payment_method_field(
        db=None, user_id="tenant-a",
        empty_payment_method_invoice_id="63",
        reference_invoice_id_with_payment="42",
    )
    assert out["ok"] is True
    assert holder["client"].calls == [
        ("get_invoice", "63"),
        ("get_invoice", "42"),
    ]


# ─── 3. Diff surfaces the divergent fields ───────────────────────────
@pytest.mark.asyncio
async def test_probe_diff_flags_payment_method_set_in_reference_but_empty_for_us(
    patched_probe,
):
    """The reference invoice has `payment_method`; ours has it as
    null. The probe must surface this divergence prominently."""
    patched_probe({
        "ours": {"invoice": {
            "id": 63, "payment_method": None,
            "contact_id": 109, "total": "131.92",
        }},
        "ref":  {"invoice": {
            "id": 42, "payment_method": "تحويل بنكي",
            "payment_method_name": "Bank Transfer",
            "contact_id": 5, "total": "500.00",
        }},
    })
    out = await probe_payment_method_field(
        db=None, user_id="tenant-a",
        empty_payment_method_invoice_id="ours",
        reference_invoice_id_with_payment="ref",
    )
    assert out["ok"] is True
    assert out["candidate_fields_empty_invoice"]["payment_method"] is None
    assert out["candidate_fields_reference_invoice"]["payment_method"] == "تحويل بنكي"
    # Reference has an extra header key — the probe must report it.
    assert "payment_method_name" in out["keys_only_in_reference"]
    # Summary mentions the divergent field path.
    assert "payment_method" in out["summary"]


@pytest.mark.asyncio
async def test_probe_handles_qoyod_fetch_errors_gracefully(monkeypatch):
    """If قيود returns a 404 for one of the invoice ids, the probe
    must record the error and continue."""
    from integrations.qoyod import payment_method_field_probe as pm
    from integrations.qoyod.api_client import QoyodAPIError

    async def _fake_get_api_key(db, user_id):
        return "test-key"

    monkeypatch.setattr(pm, "get_api_key", _fake_get_api_key)

    class _ErrClient:
        def __init__(self, _key):
            pass

        async def get_invoice(self, inv_id):
            if str(inv_id) == "missing":
                raise QoyodAPIError(
                    code="not_found",
                    message="invoice not found",
                    status_code=404,
                    endpoint=f"GET /invoices/{inv_id}",
                )
            return {"invoice": {"id": int(inv_id),
                                "payment_method": "تحويل بنكي"}}

    monkeypatch.setattr(pm, "QoyodAPIClient", _ErrClient)

    out = await probe_payment_method_field(
        db=None, user_id="tenant-a",
        empty_payment_method_invoice_id="missing",
        reference_invoice_id_with_payment="42",
    )
    assert out["ok"] is True
    assert out["empty_invoice"] is None
    assert out["reference_invoice"]["invoice"]["payment_method"] == "تحويل بنكي"
    assert "empty_invoice" in out["fetch_errors"]
    assert out["fetch_errors"]["empty_invoice"]["status_code"] == 404


# ─── 4. Input validation ─────────────────────────────────────────────
@pytest.mark.asyncio
async def test_probe_refuses_when_invoice_ids_are_identical(patched_probe):
    patched_probe({})
    out = await probe_payment_method_field(
        db=None, user_id="tenant-a",
        empty_payment_method_invoice_id="63",
        reference_invoice_id_with_payment="63",
    )
    assert out["ok"] is False
    assert out["code"] == "same_invoice_id"


@pytest.mark.asyncio
async def test_probe_refuses_when_either_invoice_id_is_blank(patched_probe):
    patched_probe({})
    out = await probe_payment_method_field(
        db=None, user_id="tenant-a",
        empty_payment_method_invoice_id="   ",
        reference_invoice_id_with_payment="42",
    )
    assert out["ok"] is False
    assert out["code"] == "missing_invoice_ids"


@pytest.mark.asyncio
async def test_probe_refuses_when_api_key_missing(monkeypatch):
    from integrations.qoyod import payment_method_field_probe as pm

    async def _no_key(db, user_id):
        return None

    monkeypatch.setattr(pm, "get_api_key", _no_key)
    out = await probe_payment_method_field(
        db=None, user_id="tenant-a",
        empty_payment_method_invoice_id="63",
        reference_invoice_id_with_payment="42",
    )
    assert out["ok"] is False
    assert out["code"] == "qoyod_api_key_missing"
