"""Iter-290i.1 — Tighter fetch diagnostics for the reference-lists
refresh. After the first production smoke test the operator
reported four lists (categories, unit_types, taxes, branches) came
back empty AND the saved ids were misleadingly labelled "غير موجود
في قيود" — which is wrong because the lists were never actually
fetched. These tests pin the corrected behaviour:

  • Per-list diagnostic dict (`status`, `count`, `used_response_key`,
    `sample_keys`, `error`).
  • Extra candidate wrapper keys (`categories` AND `product_categories`,
    `units` AND `product_units`, …).
  • The `status` field distinguishes `success`, `empty`,
    `parse_failed`, and `fail` so the UI can tell the operator
    exactly what happened, list-by-list.
"""
from __future__ import annotations

import pytest

from integrations.qoyod.reference_lists import (
    refresh_reference_lists, _normalise_with_candidates,
)
from integrations.qoyod.api_client import QoyodAPIError


# ─── 1. Candidate-key probing falls back across wrapper variants ─────
def test_normalise_with_candidates_finds_first_matching_key():
    payload = {"categories": [{"id": 1, "name": "Default"}]}
    rows, used = _normalise_with_candidates(
        payload,
        candidate_keys=["product_categories", "product_category",
                        "categories"],
    )
    assert rows == [{"id": "1", "name": "Default"}]
    assert used == "categories"   # third candidate matched


def test_normalise_with_candidates_handles_singular_wrapper():
    """قيود sometimes wraps with the singular form (`tax` not `taxes`)."""
    payload = {"tax": [{"id": 15, "name": "VAT 15%"}]}
    rows, used = _normalise_with_candidates(
        payload, candidate_keys=["taxes", "tax"])
    assert rows == [{"id": "15", "name": "VAT 15%"}]
    assert used == "tax"


def test_normalise_returns_no_used_key_when_payload_has_no_match():
    """Payload responded OK but with an unexpected shape — the
    caller needs to see `used_key=None` so it can flag a parse fail
    instead of silently calling the list empty."""
    payload = {"some_unexpected_wrapper": [{"id": 1, "name": "X"}]}
    rows, used = _normalise_with_candidates(
        payload, candidate_keys=["product_categories", "categories"])
    assert rows == []
    assert used is None


# ─── 2. Refresh emits per-list diagnostics ──────────────────────────
class _MixedClient:
    """Reproduces a realistic Qoyod tenant where some endpoints work
    and others return shapes we don't recognise — modelled after the
    production smoke test."""
    def __init__(self):
        self.calls = []

    async def list_product_categories(self):
        self.calls.append("categories")
        # Endpoint responded but with a wrapper key we don't expect
        # in the spec — exercise the `parse_failed` path.
        return {"unexpected_wrapper": [{"id": 1, "name": "X"}]}

    async def list_product_units(self):
        self.calls.append("units")
        raise QoyodAPIError(
            code="not_found", message="Not Found",
            status_code=404, endpoint="GET /product_units")

    async def list_inventories(self):
        self.calls.append("inventories")
        return {"inventories": [{"id": 1, "name": "Main"}]}

    async def list_accounts(self):
        self.calls.append("accounts")
        return {"accounts": [
            {"id": 17, "name": "Sales", "type": "income"}]}

    async def list_taxes(self):
        self.calls.append("taxes")
        return {"taxes": []}                # endpoint OK, empty list

    async def list_branches(self):
        self.calls.append("branches")
        raise QoyodAPIError(
            code="forbidden", message="Forbidden",
            status_code=403, endpoint="GET /branches")

    async def list_contacts(self, *, page, limit):
        self.calls.append(f"contacts({page},{limit})")
        return {"customers": [
            {"id": 109, "name": "Mezan",
             "phone": "+966500000000"}]}


class _FakeRefListsCol:
    def __init__(self): self.store = {}
    async def update_one(self, q, u, upsert=False):
        self.store[q["user_id"]] = u["$set"]
    async def find_one(self, q, projection=None):
        return self.store.get(q["user_id"])


class _FakeDB:
    def __init__(self): self.qoyod_reference_lists = _FakeRefListsCol()


@pytest.fixture
def patched_creds(monkeypatch):
    from integrations.qoyod import reference_lists as rl
    async def _k(db, uid): return "test-key"
    monkeypatch.setattr(rl, "get_api_key", _k)
    return rl


@pytest.mark.asyncio
async def test_refresh_emits_per_list_diagnostics(patched_creds):
    out = await refresh_reference_lists(
        _FakeDB(), user_id="tenant-a",
        client_factory=lambda _k: _MixedClient())
    diag = out.get("fetch_diagnostics") or {}

    # Success — non-empty list.
    assert diag["inventories"]["status"]            == "success"
    assert diag["inventories"]["count"]             == 1
    assert diag["inventories"]["used_response_key"] == "inventories"
    assert diag["inventories"]["error"]             is None

    # Endpoint responded OK but with an empty list — NOT a failure,
    # the operator just hasn't configured any rows of that resource.
    assert diag["taxes"]["status"]   == "empty"
    assert diag["taxes"]["count"]    == 0
    assert diag["taxes"]["error"]    is None
    # `taxes` should NOT appear in `fetch_errors` — empty != fail.
    fetch_errors = out.get("fetch_errors") or {}
    assert "taxes" not in fetch_errors

    # Parse failure — endpoint responded but with a shape we don't
    # know how to read. Must be flagged as `parse_failed` AND
    # surfaced in fetch_errors so the UI can distinguish it from a
    # network failure.
    assert diag["categories"]["status"] == "parse_failed"
    assert diag["categories"]["count"]  == 0
    assert "unexpected_wrapper" in diag["categories"]["sample_keys"]
    assert "categories" in fetch_errors

    # Network / authorization failures.
    assert diag["unit_types"]["status"] == "fail"
    assert diag["unit_types"]["error"]["status_code"] == 404
    assert diag["branches"]["status"]   == "fail"
    assert diag["branches"]["error"]["status_code"]   == 403

    # Healthy paginated endpoint.
    assert diag["customers"]["status"] == "success"
    assert diag["customers"]["count"]  == 1


@pytest.mark.asyncio
async def test_refresh_diagnostics_persisted_to_db(patched_creds):
    """Re-fetching via get_reference_lists must surface the same
    diagnostics (so the UI doesn't lose them on page reload)."""
    db = _FakeDB()
    await refresh_reference_lists(
        db, user_id="tenant-a",
        client_factory=lambda _k: _MixedClient())
    stored = db.qoyod_reference_lists.store["tenant-a"]
    assert "fetch_diagnostics" in stored
    assert stored["fetch_diagnostics"]["inventories"]["status"] == "success"


# ─── 3. Older callers using _normalise_list still work ───────────────
def test_normalise_list_backward_compatible():
    """The original `_normalise_list(root_key=...)` signature is
    preserved as a thin wrapper so existing tests don't break."""
    from integrations.qoyod.reference_lists import _normalise_list
    out = _normalise_list(
        {"product_categories": [{"id": 1, "name": "X"}]},
        root_key="product_category",
    )
    assert out == [{"id": "1", "name": "X"}]
