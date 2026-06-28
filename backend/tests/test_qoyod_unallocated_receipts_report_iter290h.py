"""Iter-290h — Unallocated Receipts Report tests.

Covers the matching algorithm in
`integrations/qoyod/unallocated_receipts_report.py` — purely unit
tests so they run fast and don't need a live Qoyod tenant.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.unallocated_receipts_report import (
    _looks_unallocated, _suggest_invoice, _slim_invoice, _slim_receipt,
    _qoyod_deep_links,
    build_unallocated_receipts_report, dismiss_receipt, undismiss_receipt,
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
    invoice, reasons, score = _suggest_invoice(r, invs)
    assert invoice is None
    assert reasons == []
    assert score == 0


def test_suggest_returns_none_when_amount_off_by_more_than_5_halalat():
    r = {"amount": 100.00, "contact_id": 5}
    invs = [{"id": 1, "total": 100.50, "contact_id": 5,
             "reference": "X"}]
    invoice, _, _ = _suggest_invoice(r, invs)
    assert invoice is None


def test_reference_match_wins_over_amount_alone():
    r = {"amount": 100, "contact_id": 5, "external_reference": "268784455",
         "date": "2026-02-28"}
    invs = [
        {"id": 1, "total": 100, "contact_id": 5,
         "reference": "OTHER", "issue_date": "2026-02-28"},
        {"id": 2, "total": 100, "contact_id": 5,
         "reference": "268784455", "issue_date": "2026-02-20"},
    ]
    invoice, reasons, _ = _suggest_invoice(r, invs)
    assert invoice is not None
    assert invoice["id"] == 2
    # All four signal categories must surface in reasons.
    assert "reference" in reasons
    assert "amount" in reasons
    assert "customer" in reasons


def test_suggest_prefers_closer_date_when_no_reference_match():
    r = {"amount": 100, "contact_id": 5, "date": "2026-02-28"}
    invs = [
        {"id": 1, "total": 100, "contact_id": 5, "issue_date": "2026-02-27"},
        {"id": 2, "total": 100, "contact_id": 5, "issue_date": "2026-02-10"},
    ]
    invoice, reasons, _ = _suggest_invoice(r, invs)
    assert invoice["id"] == 1
    assert "date" in reasons
    assert "reference" not in reasons


def test_suggest_tolerates_amount_within_one_halalah():
    r = {"amount": 134.00, "contact_id": 5}
    invs = [{"id": 9, "total": 134.01, "contact_id": 5,
             "reference": "R1"}]
    invoice, reasons, _ = _suggest_invoice(r, invs)
    assert invoice["id"] == 9
    assert "amount" in reasons


# ─── build_unallocated_receipts_report — integration with stubbed API ──
class _StubAPIClient:
    """Mimics QoyodAPIClient.list_receipts / list_invoices."""
    def __init__(self, receipts, invoices):
        self._receipts = receipts
        self._invoices = invoices

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


# ─── _qoyod_deep_links ───────────────────────────────────────────────
def test_deep_links_use_default_base_when_not_configured():
    out = _qoyod_deep_links({}, receipt_id=42, invoice_id=100)
    assert out["receipt_url"] == "https://www.qoyod.com/tenant/receipts/42"
    assert out["invoice_url"] == "https://www.qoyod.com/tenant/invoices/100"


def test_deep_links_use_configured_base_url():
    settings = {"qoyod_ui_base_url": "https://acme.qoyod.com/admin/"}
    out = _qoyod_deep_links(settings, receipt_id=42, invoice_id=100)
    assert out["receipt_url"] == "https://acme.qoyod.com/admin/receipts/42"
    assert out["invoice_url"] == "https://acme.qoyod.com/admin/invoices/100"


def test_deep_links_emit_empty_string_for_missing_id():
    out = _qoyod_deep_links({}, receipt_id=42, invoice_id=None)
    assert out["receipt_url"] != ""
    assert out["invoice_url"] == ""


# ─── Report includes match_reasons + deep links ──────────────────────
@pytest.mark.asyncio
async def test_report_items_carry_match_reasons_and_deep_links(monkeypatch):
    """Operator-facing fields: every item carries `match_reasons`
    (subset of reference/amount/customer/date), `qoyod_receipt_url`,
    and `qoyod_invoice_url`."""
    receipts = [
        {"id": "R1", "amount": 100, "contact_id": 5,
         "date": "2026-02-28", "external_reference": "PYT1"},
    ]
    invoices = [
        {"id": 100, "total": 100, "contact_id": 5,
         "issue_date": "2026-02-28", "reference": "PYT1"},
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
    item = out["items"][0]
    assert item["match_reasons"] == ["reference", "date", "amount", "customer"]
    assert item["qoyod_receipt_url"].endswith("/receipts/R1")
    assert item["qoyod_invoice_url"].endswith("/invoices/100")
    assert out["summary"]["by_confidence"]["high"] == 1


# ─── Dismiss / undismiss flow ────────────────────────────────────────
class _DismissalsCol:
    """In-memory stand-in for `qoyod_unallocated_dismissals`."""
    def __init__(self):
        self.rows: list[dict] = []

    async def update_one(self, q, u, upsert=False):
        # Find by (user_id, qoyod_receipt_id).
        for r in self.rows:
            if (r["user_id"] == q["user_id"]
                    and r["qoyod_receipt_id"] == q["qoyod_receipt_id"]):
                r.update(u.get("$set", {}))
                return
        if upsert:
            new = {**q, **u.get("$set", {}), **u.get("$setOnInsert", {})}
            self.rows.append(new)

    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                return dict(r)
        return None

    def find(self, q, projection=None):
        # Return an async iterator over matching rows.
        matches = [dict(r) for r in self.rows
                   if all(r.get(k) == v for k, v in q.items())]
        return _AsyncListCursor(matches)


class _AsyncListCursor:
    def __init__(self, items): self._items = items
    def __aiter__(self): return self
    async def __anext__(self):
        if not self._items: raise StopAsyncIteration
        return self._items.pop(0)


class _DBWithDismissals:
    def __init__(self):
        self.qoyod_unallocated_dismissals = _DismissalsCol()
        self.qoyod_settings = type("_S", (), {
            "find_one": staticmethod(
                lambda q, projection=None: _none_coro())
        })()


async def _none_coro(): return None


@pytest.mark.asyncio
async def test_dismiss_then_report_excludes_receipt(monkeypatch):
    """Operator-marked receipts vanish from subsequent reports."""
    db = _DBWithDismissals()
    await dismiss_receipt(db, user_id="main",
                          qoyod_receipt_id="R1",
                          actor="ops@mezan", note="linked manually in قيود")
    receipts = [
        {"id": "R1", "amount": 100, "contact_id": 5,
         "external_reference": "PYT1"},
        {"id": "R2", "amount": 200, "contact_id": 5,
         "external_reference": "PYT2"},
    ]
    invoices = [
        {"id": 1, "total": 100, "contact_id": 5, "reference": "PYT1"},
        {"id": 2, "total": 200, "contact_id": 5, "reference": "PYT2"},
    ]
    from integrations.qoyod import unallocated_receipts_report as mod
    async def _fake_key(*a, **kw): return "test-key"
    monkeypatch.setattr(mod, "get_api_key", _fake_key)
    monkeypatch.setattr(
        mod, "QoyodAPIClient",
        lambda api_key: _StubAPIClient(receipts, invoices))

    out = await build_unallocated_receipts_report(db, user_id="main")
    receipt_ids = [it["receipt"]["id"] for it in out["items"]]
    assert "R1" not in receipt_ids
    assert "R2" in receipt_ids
    assert out["dismissed_count"] == 1


@pytest.mark.asyncio
async def test_dismiss_is_idempotent():
    """Second dismiss call must NOT create a duplicate row — it just
    refreshes `dismissed_at`."""
    db = _DBWithDismissals()
    await dismiss_receipt(db, user_id="main",
                          qoyod_receipt_id="R1", actor="ops@mezan")
    await dismiss_receipt(db, user_id="main",
                          qoyod_receipt_id="R1", actor="ops@mezan",
                          note="second time")
    assert len(db.qoyod_unallocated_dismissals.rows) == 1
    row = db.qoyod_unallocated_dismissals.rows[0]
    assert row["note"] == "second time"
    assert row["active"] is True


@pytest.mark.asyncio
async def test_undismiss_soft_toggles_active_false():
    db = _DBWithDismissals()
    await dismiss_receipt(db, user_id="main",
                          qoyod_receipt_id="R1", actor="ops@mezan")
    out = await undismiss_receipt(db, user_id="main",
                                  qoyod_receipt_id="R1")
    assert out["active"] is False
    # Audit trail preserved — row still exists.
    assert len(db.qoyod_unallocated_dismissals.rows) == 1
    assert db.qoyod_unallocated_dismissals.rows[0]["active"] is False
    assert "undismissed_at" in db.qoyod_unallocated_dismissals.rows[0]
