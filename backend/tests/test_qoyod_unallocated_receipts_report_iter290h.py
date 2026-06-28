"""Iter-290h — Unallocated Receipts Report tests.

Covers the matching algorithm in
`integrations/qoyod/unallocated_receipts_report.py` — purely unit
tests so they run fast and don't need a live Qoyod tenant.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.unallocated_receipts_report import (
    _looks_unallocated, _suggest_invoice, _slim_invoice, _slim_receipt,
    build_unallocated_receipts_report,
)


# ─── _looks_unallocated ──────────────────────────────────────────────
def test_looks_unallocated_returns_true_for_bare_receipt():
    assert _looks_unallocated({"id": "R1", "amount": 100}) is True


def test_looks_unallocated_returns_false_when_invoice_id_set():
    assert _looks_unallocated({"id": "R1", "invoice_id": 42}) is False
    assert _looks_unallocated({"id": "R1", "invoice_id": "42"}) is False


def test_looks_unallocated_returns_false_when_allocations_present():
    assert _looks_unallocated({
        "id": "R1",
        "allocations": [{"invoice_id": 7, "amount": 50}],
    }) is False


def test_looks_unallocated_returns_false_for_explicit_flag():
    assert _looks_unallocated({"id": "R1", "allocated": True}) is False
    assert _looks_unallocated({"id": "R1", "is_allocated": True}) is False


# ─── _suggest_invoice — scoring + tie breaks ─────────────────────────
def test_suggest_returns_none_when_no_candidates_match_customer():
    r = {"amount": 100, "contact_id": 5}
    invs = [{"id": 1, "total": 100, "contact_id": 99}]
    assert _suggest_invoice(r, invs) is None


def test_suggest_returns_none_when_amount_off_by_more_than_5_halalat():
    r = {"amount": 100.00, "contact_id": 5}
    invs = [{"id": 1, "total": 100.50, "contact_id": 5,
             "reference": "X"}]
    assert _suggest_invoice(r, invs) is None


def test_reference_match_wins_over_amount_alone():
    """Two candidates, one matches by reference. Reference match has
    the highest weight (100) so it wins regardless of date proximity."""
    r = {"amount": 100, "contact_id": 5, "external_reference": "268784455",
         "date": "2026-02-28"}
    invs = [
        {"id": 1, "total": 100, "contact_id": 5,
         "reference": "OTHER", "issue_date": "2026-02-28"},
        {"id": 2, "total": 100, "contact_id": 5,
         "reference": "268784455", "issue_date": "2026-02-20"},
    ]
    suggestion = _suggest_invoice(r, invs)
    assert suggestion is not None
    assert suggestion["id"] == 2


def test_suggest_prefers_closer_date_when_no_reference_match():
    r = {"amount": 100, "contact_id": 5, "date": "2026-02-28"}
    invs = [
        {"id": 1, "total": 100, "contact_id": 5, "issue_date": "2026-02-27"},
        {"id": 2, "total": 100, "contact_id": 5, "issue_date": "2026-02-10"},
    ]
    suggestion = _suggest_invoice(r, invs)
    assert suggestion["id"] == 1


def test_suggest_tolerates_amount_within_one_halalah():
    r = {"amount": 134.00, "contact_id": 5}
    invs = [{"id": 9, "total": 134.01, "contact_id": 5,
             "reference": "R1"}]
    assert _suggest_invoice(r, invs)["id"] == 9


# ─── build_unallocated_receipts_report — integration with stubbed API ──
class _StubAPIClient:
    """Mimics QoyodAPIClient.list_receipts / list_invoices."""
    def __init__(self, receipts, invoices):
        self._receipts = receipts
        self._invoices = invoices

    async def __aenter__(self): return self
    async def __aexit__(self, *a): pass

    async def list_receipts(self, *, page=1, limit=50):
        # Single page for the test fixtures (well under 50).
        return {"receipts": self._receipts if page == 1 else []}

    async def list_invoices(self, *, page=1, limit=50):
        return {"invoices": self._invoices if page == 1 else []}


class _SettingsCol:
    def __init__(self, doc): self._doc = doc
    async def find_one(self, q, projection=None): return self._doc


class _DB:
    def __init__(self, api_key):
        self.qoyod_settings = _SettingsCol(
            {"credentials": {"api_key_encrypted": api_key},
             "user_id": "main"})


@pytest.mark.asyncio
async def test_report_returns_error_when_api_key_missing(monkeypatch):
    """No API key configured → report bails with structured error."""
    async def _no_key(*a, **kw): return None
    from integrations.qoyod import unallocated_receipts_report as mod
    monkeypatch.setattr(mod, "get_api_key", _no_key)
    out = await build_unallocated_receipts_report(
        db=None, user_id="main")
    assert out["ok"] is False
    assert out["error"]["code"] == "qoyod_api_key_missing"


@pytest.mark.asyncio
async def test_report_isolates_unallocated_and_suggests_match(monkeypatch):
    """The user's PYT1–PYT8 scenario: 8 receipts, all standalone, plus
    the corresponding 8 invoices. Report should pair each receipt with
    its invoice by reference."""
    receipts = [
        {"id": f"R{i}", "amount": 100 + i, "contact_id": 5,
         "date": "2026-02-28",
         "external_reference": f"PYT{i}"}
        for i in range(1, 9)
    ]
    # Mix in one already-allocated receipt that must NOT appear.
    receipts.append({"id": "R-ALLOC", "amount": 999, "invoice_id": 42})

    invoices = [
        {"id": 100 + i, "total": 100 + i, "contact_id": 5,
         "issue_date": "2026-02-28", "reference": f"PYT{i}",
         "status": "Approved"}
        for i in range(1, 9)
    ]

    from integrations.qoyod import unallocated_receipts_report as mod
    async def _fake_key(*a, **kw): return "test-key"
    monkeypatch.setattr(mod, "get_api_key", _fake_key)
    monkeypatch.setattr(
        mod, "QoyodAPIClient",
        lambda api_key: _StubAPIClient(receipts, invoices))

    out = await build_unallocated_receipts_report(
        db=None, user_id="main")
    assert out["ok"] is True
    assert out["summary"]["unallocated_count"]   == 8     # PYT1..PYT8
    assert out["summary"]["with_suggestion"]     == 8
    assert out["summary"]["without_suggestion"]  == 0
    # Every item paired with its matching invoice by reference.
    for it in out["items"]:
        r_ref = it["receipt"]["external_reference"]
        assert it["suggestion"]["reference"] == r_ref
        assert it["confidence"] == "high"


@pytest.mark.asyncio
async def test_report_marks_no_match_as_confidence_none(monkeypatch):
    receipts = [
        {"id": "R-ORPHAN", "amount": 500, "contact_id": 7,
         "date": "2026-02-28", "external_reference": "GHOST"},
    ]
    invoices = [
        {"id": 200, "total": 100, "contact_id": 99,  # different customer
         "issue_date": "2026-02-28", "reference": "OTHER"},
    ]
    from integrations.qoyod import unallocated_receipts_report as mod
    async def _fake_key(*a, **kw): return "test-key"
    monkeypatch.setattr(mod, "get_api_key", _fake_key)
    monkeypatch.setattr(
        mod, "QoyodAPIClient",
        lambda api_key: _StubAPIClient(receipts, invoices))

    out = await build_unallocated_receipts_report(
        db=None, user_id="main")
    assert out["ok"] is True
    assert out["summary"]["unallocated_count"]  == 1
    assert out["summary"]["with_suggestion"]    == 0
    assert out["items"][0]["suggestion"]        is None
    assert out["items"][0]["confidence"]        == "none"


def test_slim_helpers_return_jsonable_subset():
    """Both slim helpers must produce flat dicts the frontend can
    render without extra processing."""
    r = _slim_receipt({"id": 1, "amount": 100, "contact_id": 5,
                       "date": "2026-02-28", "number": "PYT1"})
    assert set(r.keys()) >= {"id", "number", "date", "amount", "contact_id"}
    i = _slim_invoice({"id": 100, "reference": "PYT1", "total": 100,
                       "issue_date": "2026-02-28", "contact_id": 5,
                       "status": "Approved"})
    assert set(i.keys()) >= {"id", "reference", "issue_date",
                             "total", "contact_id", "status"}
