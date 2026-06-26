"""Tests for the Qoyod Fresh-Start Audit analysers.

The orchestrator path is exercised via the HTTP route. These tests
focus on the deterministic, pure-function analysers so we can lock
their behaviour against future Qoyod payload-shape drift.
"""
from __future__ import annotations

from integrations.qoyod.fresh_start_audit import (
    _analyse_invoices, _analyse_receipts,
    _analyse_products, _analyse_customers,
    _build_flags, _looks_like_salla_ref,
    _month_bucket, _extract_list,
)


# ─── _looks_like_salla_ref ─────────────────────────────────────────
def test_salla_ref_pure_long_digits_pass():
    assert _looks_like_salla_ref("123456789") is True
    assert _looks_like_salla_ref("987654321012345") is True


def test_salla_ref_short_digits_fail():
    # 5-digit references are too short to confidently be Salla.
    assert _looks_like_salla_ref("12345") is False


def test_salla_ref_word_in_string_pass():
    assert _looks_like_salla_ref("salla-987654") is True
    assert _looks_like_salla_ref("manual-2024-001") is False


def test_salla_ref_empty_or_none_fail():
    assert _looks_like_salla_ref(None) is False
    assert _looks_like_salla_ref("") is False
    assert _looks_like_salla_ref("    ") is False


# ─── _month_bucket ─────────────────────────────────────────────────
def test_month_bucket_iso_date():
    assert _month_bucket("2024-10-15") == "2024-10"
    assert _month_bucket("2024-10-15T12:30:00Z") == "2024-10"
    assert _month_bucket("2024/10/15") == "2024-10"


def test_month_bucket_garbage_returns_unknown():
    assert _month_bucket(None) == "unknown"
    assert _month_bucket("") == "unknown"
    assert _month_bucket("not-a-date") == "unknown"


# ─── _extract_list ─────────────────────────────────────────────────
def test_extract_list_handles_raw_list_and_keyed_dict():
    assert _extract_list([1, 2, 3], ("invoices",)) == [1, 2, 3]
    assert _extract_list({"invoices": [1, 2]}, ("invoices",)) == [1, 2]
    assert _extract_list({"data": [1]}, ("invoices",)) == [1]
    assert _extract_list({"data": {"invoices": [1, 2]}},
                         ("invoices",)) == [1, 2]
    assert _extract_list({}, ("invoices",)) == []
    assert _extract_list(None, ("invoices",)) == []


# ─── _analyse_invoices ─────────────────────────────────────────────
def test_analyse_invoices_buckets_and_classifies():
    items = [
        {"id": "1", "issue_date": "2024-10-15",
         "total": 100, "status": "paid",
         "external_reference": "123456789", "contact_id": "C1"},
        {"id": "2", "issue_date": "2024-11-01",
         "total": 50, "status": "draft",
         "external_reference": None, "contact_id": "C2"},
        {"id": "3", "issue_date": "2024-11-15",
         "total": 200, "status": "paid",
         "external_reference": "salla-987", "contact_id": "C1"},
    ]
    # Receipts referencing invoice ids 1 and 3.
    receipt_inv_ids = {"1", "3"}
    out = _analyse_invoices(items, receipt_inv_ids)
    assert out["total"] == 3
    assert out["total_amount"] == 350.0
    assert out["with_external_ref"] == 2
    assert out["without_external_ref"] == 1
    assert out["matches_salla_pattern"] == 2
    assert out["with_receipt"] == 2
    assert out["without_receipt"] == 1
    assert out["by_month"] == {"2024-10": 1, "2024-11": 2}
    assert out["by_status"] == {"paid": 2, "draft": 1}
    assert out["contact_ids_referenced"] == 2
    # samples populated
    assert len(out["samples"]["no_receipt"]) == 1
    assert out["samples"]["no_receipt"][0]["id"] == "2"


def test_analyse_invoices_empty_returns_zeroes():
    out = _analyse_invoices([], set())
    assert out["total"] == 0
    assert out["total_amount"] == 0.0
    assert out["with_receipt"] == 0


# ─── _analyse_receipts ─────────────────────────────────────────────
def test_analyse_receipts_buckets_and_detects_orphan():
    items = [
        {"id": "r1", "date": "2024-10-01", "amount": 100,
         "invoice_id": "i1", "account_id": "A1"},
        {"id": "r2", "date": "2024-10-15", "amount": 50,
         "invoice_id": None, "account_id": "A2"},  # orphan
        {"id": "r3", "created_at": "2024-11-01T10:00:00Z",
         "amount": 200, "invoice_id": "i2", "account_id": "A1"},
    ]
    out = _analyse_receipts(items)
    assert out["total"] == 3
    assert out["total_amount"] == 350.0
    assert out["invoice_ids"] == 2
    assert out["orphan"] == 1
    assert out["by_month"] == {"2024-10": 2, "2024-11": 1}
    assert out["by_account_id"] == {"A1": 2, "A2": 1}
    assert out["_invoice_ids"] == {"i1", "i2"}


# ─── _analyse_products ─────────────────────────────────────────────
def test_analyse_products_separates_sku_presence():
    items = [
        {"id": "p1", "sku": "ABC-1", "name": "Ring 18K",
         "created_at": "2024-09-01"},
        {"id": "p2", "code": "X-9", "name": "Necklace",
         "created_at": "2024-09-15"},  # uses "code" not "sku"
        {"id": "p3", "name": "Random handmade",
         "created_at": "2024-10-01"},  # no sku
        {"id": "p4", "sku": "  ", "name": "Whitespace SKU",
         "created_at": "2024-10-15"},  # blank counts as no-sku
    ]
    out = _analyse_products(items)
    assert out["total"] == 4
    assert out["with_sku"] == 2
    assert out["without_sku"] == 2
    assert out["by_month"] == {"2024-09": 2, "2024-10": 2}
    assert any(s["name"] == "Random handmade"
               for s in out["samples"]["without_sku"])


# ─── _analyse_customers ────────────────────────────────────────────
def test_analyse_customers_flags_guests_and_orphans():
    items = [
        {"id": "C1", "name": "أحمد محمد", "phone": "+966500000001",
         "email": "ahmed@example.com", "created_at": "2024-08-01"},
        {"id": "C2", "name": "ضيف", "created_at": "2024-08-02"},  # guest
        {"id": "C3", "name": "Guest User", "created_at": "2024-08-03"},
        {"id": "C4", "name": "Walid", "email": "w@x.com",
         "created_at": "2024-09-01"},
    ]
    # Only C1 and C4 appear in invoices.
    out = _analyse_customers(items, {"C1", "C4"})
    assert out["total"] == 4
    assert out["with_phone"] == 1
    assert out["with_email"] == 2
    assert out["guests"] == 2          # C2 + C3 (name OR no contact info)
    assert out["has_invoices"] == 2
    assert out["no_invoices"] == 2
    assert out["by_month"]["2024-08"] == 3


# ─── _build_flags — cross-entity warnings ──────────────────────────
def test_build_flags_warns_on_invoices_without_receipt():
    inv = {"total": 10, "with_receipt": 7, "without_receipt": 3,
           "with_external_ref": 10, "without_external_ref": 0}
    rec = {"total": 7, "invoice_ids": 7, "orphan": 0}
    prods = {"total": 5, "with_sku": 5, "without_sku": 0}
    cust  = {"total": 4, "no_invoices": 0}
    flags = _build_flags(inv, rec, prods, cust)
    codes = {f["code"] for f in flags}
    assert "invoices_without_receipts" in codes


def test_build_flags_warns_on_orphan_receipts():
    inv = {"total": 5, "with_receipt": 5, "without_receipt": 0,
           "with_external_ref": 5, "without_external_ref": 0}
    rec = {"total": 7, "invoice_ids": 5, "orphan": 2}
    prods = {"total": 0, "with_sku": 0, "without_sku": 0}
    cust  = {"total": 0, "no_invoices": 0}
    flags = _build_flags(inv, rec, prods, cust)
    codes = {f["code"] for f in flags}
    assert "orphan_receipts" in codes


def test_build_flags_clean_payload_yields_no_flags():
    inv = {"total": 0, "with_receipt": 0, "without_receipt": 0,
           "with_external_ref": 0, "without_external_ref": 0}
    rec = {"total": 0, "invoice_ids": 0, "orphan": 0}
    prods = {"total": 0, "with_sku": 0, "without_sku": 0}
    cust  = {"total": 0, "no_invoices": 0}
    flags = _build_flags(inv, rec, prods, cust)
    assert flags == []


def test_build_flags_info_on_products_without_sku():
    inv = {"total": 1, "with_receipt": 1, "without_receipt": 0,
           "with_external_ref": 1, "without_external_ref": 0}
    rec = {"total": 1, "invoice_ids": 1, "orphan": 0}
    prods = {"total": 3, "with_sku": 1, "without_sku": 2}
    cust  = {"total": 0, "no_invoices": 0}
    flags = _build_flags(inv, rec, prods, cust)
    codes = {f["code"] for f in flags}
    assert "products_without_sku" in codes
