"""Iter-001k+ — Admin DRY RCA endpoint (Read-Only) tests.

Guarantees:
    • Refuses if gates are open.
    • Refuses if production_writes_locked=False.
    • No Qoyod API / no writes / no adopt / no send.
    • DRY sentinels surface the right recommendation.
    • bank_transfer → blocked_bank_transfer_iter_294.
    • Existing external / existing mapping → adopt_existing_*.
    • Missing order → found=False with a note.
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "/app/backend")

from integrations.qoyod.dry_rca_report import (   # noqa: E402
    GatesNotFailClosedError,
    build_dry_rca_report,
)


# ── Fake async DB stubs ─────────────────────────────────────────────
class _FakeColl:
    def __init__(self, docs):
        self._docs = docs

    async def find_one(self, q, projection=None):
        for d in self._docs:
            if self._match(d, q):
                return {k: v for k, v in d.items() if k != "_id"}
        return None

    def find(self, q, projection=None):
        return _Cursor([d for d in self._docs if self._match(d, q)])

    @staticmethod
    def _match(doc, q):
        if not isinstance(q, dict):
            return False
        for k, v in q.items():
            if k == "$or":
                if not any(_FakeColl._match(doc, sub) for sub in v):
                    return False
                continue
            if isinstance(v, dict):
                # Support {$ne: X} and {$not: /regex/}.
                if "$ne" in v:
                    if doc.get(k) == v["$ne"]:
                        return False
                    continue
                if "$not" in v:
                    pat = v["$not"]
                    val = doc.get(k)
                    if val is None:
                        continue
                    try:
                        if pat.search(str(val)):
                            return False
                    except AttributeError:
                        return False
                    continue
            if doc.get(k) != v:
                return False
        return True


class _Cursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a, **kw):
        return self

    def limit(self, n):
        self._docs = self._docs[:n]
        return self

    async def to_list(self, length=None):
        return list(self._docs[: (length or len(self._docs))])

    def __aiter__(self):
        self._i = 0
        return self

    async def __anext__(self):
        if self._i >= len(self._docs):
            raise StopAsyncIteration
        d = self._docs[self._i]
        self._i += 1
        return d


class _FakeDB:
    def __init__(self, **collections):
        for k, v in collections.items():
            setattr(self, k, _FakeColl(v))


def _fail_closed_settings():
    return [{"user_id": "main",
             "selective_live_send_enabled":            False,
             "production_writes_locked":               True,
             "qoyod_sync_start_date":                  "2026-07-01",
             "qoyod_tax_period":                       "Q3-2026",
             "bank_transfer_routing_enabled":          False,
             "qoyod_invoice_date_source":              "send_date",
             "qoyod_enabled_invoice_trigger_statuses":
                 ["completed", "تم التنفيذ"]}]


# ── Gate-guard tests ────────────────────────────────────────────────
class TestGatesGuard:

    @pytest.mark.asyncio
    async def test_refuses_when_gate_open(self):
        db = _FakeDB(
            qoyod_settings=[{"user_id": "main",
                             "selective_live_send_enabled": True,
                             "production_writes_locked":    True}],
            integration_inbox=[],
            qoyod_customers_mapping=[],
            qoyod_external_customers=[],
            qoyod_products_mapping=[],
            qoyod_external_products=[])
        with pytest.raises(GatesNotFailClosedError):
            await build_dry_rca_report(
                db, user_id="main", order_numbers=["1"])

    @pytest.mark.asyncio
    async def test_refuses_when_write_lock_open(self):
        db = _FakeDB(
            qoyod_settings=[{"user_id": "main",
                             "selective_live_send_enabled": False,
                             "production_writes_locked":    False}],
            integration_inbox=[],
            qoyod_customers_mapping=[],
            qoyod_external_customers=[],
            qoyod_products_mapping=[],
            qoyod_external_products=[])
        with pytest.raises(GatesNotFailClosedError):
            await build_dry_rca_report(
                db, user_id="main", order_numbers=["1"])


# ── DRY sentinel classification tests ──────────────────────────────
class TestDryClassification:

    def _db_with_order(self, *, order_number, payment_method,
                       dry_customer=True, dry_product=True,
                       ext_customer=False, ext_product=False,
                       real_map_customer=False, real_map_product=False):
        inbox = [{
            "user_id":        "main",
            "salla_order_number": order_number,
            "existing_qoyod_invoice_id":
                f"DRY:invoice:abcdef{order_number}",
            "qoyod_customer_id":
                f"DRY:contact:xyz{order_number}"
                if dry_customer else 555000,
            "canonical_payload": {
                "order_number": order_number,
                "payment_method": payment_method,
                "status": "completed",
                "customer": {"mobile": "+966500000000",
                             "email":  "u@example.com",
                             "name":   "Test User"},
                "items": [{
                    "sku": f"SKU-{order_number}",
                    "name": "Some Product",
                    "quantity": 1,
                    "unit_price": 50.0,
                    "discount_amount": 0.0,
                    "qoyod_product_id":
                        f"DRY:product:pp{order_number}"
                        if dry_product else 777001,
                    "tax_amount": 0.0,
                    "total": 50.0,
                }],
            },
        }]
        return _FakeDB(
            qoyod_settings=_fail_closed_settings(),
            integration_inbox=inbox,
            qoyod_customers_mapping=(
                [{"user_id": "main", "lookup_key": "+966500000000",
                  "qoyod_customer_id": 888001, "dry_run_only": False}]
                if real_map_customer else []),
            qoyod_external_customers=(
                [{"user_id": "main", "mobile": "+966500000000",
                  "qoyod_customer_id": 999001, "name": "Ext Cust"}]
                if ext_customer else []),
            qoyod_products_mapping=(
                [{"user_id": "main", "sku": f"SKU-{order_number}",
                  "qoyod_product_id": 888100, "dry_run_only": False}]
                if real_map_product else []),
            qoyod_external_products=(
                [{"user_id": "main", "sku": f"SKU-{order_number}",
                  "qoyod_product_id": 999100,
                  "name": "Ext Product"}]
                if ext_product else []))

    @pytest.mark.asyncio
    async def test_dry_invoice_gets_ignore_recommendation(self):
        db = self._db_with_order(
            order_number="X1", payment_method="mada")
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["X1"])
        rep = r["reports"][0]
        assert rep["found"] is True
        assert rep["dry_invoice_id"].startswith("DRY:invoice:")
        assert "ignore_dry_invoice_sentinel" in rep["recommended_action"]

    @pytest.mark.asyncio
    async def test_bank_transfer_gets_blocked_iter294(self):
        db = self._db_with_order(
            order_number="X2", payment_method="bank_transfer")
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["X2"])
        rep = r["reports"][0]
        assert "blocked_bank_transfer_iter_294" in rep["recommended_action"]

    @pytest.mark.asyncio
    async def test_customer_in_external_suggests_adopt(self):
        db = self._db_with_order(
            order_number="X3", payment_method="mada",
            ext_customer=True)
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["X3"])
        rep = r["reports"][0]
        assert rep["has_real_customer_in_external"] is True
        assert "adopt_existing_customer" in rep["recommended_action"]

    @pytest.mark.asyncio
    async def test_customer_via_mapping_suggests_adopt(self):
        db = self._db_with_order(
            order_number="X4", payment_method="mada",
            real_map_customer=True)
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["X4"])
        rep = r["reports"][0]
        assert rep["has_real_customer_mapping"] is True
        assert "adopt_existing_customer" in rep["recommended_action"]

    @pytest.mark.asyncio
    async def test_customer_missing_suggests_create_later(self):
        db = self._db_with_order(
            order_number="X5", payment_method="mada")
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["X5"])
        rep = r["reports"][0]
        assert "needs_create_customer_later" \
            in rep["recommended_action"]

    @pytest.mark.asyncio
    async def test_product_in_external_suggests_adopt(self):
        db = self._db_with_order(
            order_number="X6", payment_method="mada",
            ext_product=True)
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["X6"])
        rep = r["reports"][0]
        assert rep["has_real_product_in_external_any"] is True
        assert "adopt_existing_product" in rep["recommended_action"]

    @pytest.mark.asyncio
    async def test_product_missing_suggests_create_later(self):
        db = self._db_with_order(
            order_number="X7", payment_method="mada")
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["X7"])
        rep = r["reports"][0]
        assert "needs_create_product_later" \
            in rep["recommended_action"]

    @pytest.mark.asyncio
    async def test_order_not_found_returns_stub(self):
        db = _FakeDB(
            qoyod_settings=_fail_closed_settings(),
            integration_inbox=[],
            qoyod_customers_mapping=[],
            qoyod_external_customers=[],
            qoyod_products_mapping=[],
            qoyod_external_products=[])
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["999999"])
        rep = r["reports"][0]
        assert rep["found"] is False
        assert rep["note"] == "no integration_inbox row"


# ── Static Read-Only invariants ─────────────────────────────────────
class TestReadOnlyInvariants:

    def test_no_qoyod_api_client_import(self):
        import integrations.qoyod.dry_rca_report as mod
        src = open(mod.__file__, encoding="utf-8").read()
        assert "QoyodAPIClient" not in src
        assert "api_client" not in src
        assert "httpx" not in src
        assert "requests." not in src

    def test_no_db_write_operations(self):
        import integrations.qoyod.dry_rca_report as mod
        src = open(mod.__file__, encoding="utf-8").read()
        for banned in ("insert_one", "update_one", "delete_one",
                       "$set", "$unset", "insert_many",
                       "update_many", ".adopt("):
            assert banned not in src, \
                f"Read-Only invariant violated: `{banned}`"

    def test_returns_readonly_markers(self):
        # Structural check via a minimal fail-closed run.
        import asyncio
        db = _FakeDB(
            qoyod_settings=_fail_closed_settings(),
            integration_inbox=[],
            qoyod_customers_mapping=[],
            qoyod_external_customers=[],
            qoyod_products_mapping=[],
            qoyod_external_products=[])
        out = asyncio.run(
            build_dry_rca_report(db, user_id="main",
                                 order_numbers=["nonexistent"]))
        assert out["read_only"] is True
        assert out["no_qoyod_api_calls"] is True
        assert out["no_db_writes"] is True


# ── Iter-001k+ (2026-02-27) — bug-fix regression tests ─────────────
class TestGatesSnapshotSurfacesAllGates:
    """Bug #1: gates_snapshot returned {} in Production because the
    projection dropped fields other than the two booleans."""

    @pytest.mark.asyncio
    async def test_gates_snapshot_carries_full_settings(self):
        db = _FakeDB(
            qoyod_settings=_fail_closed_settings(),
            integration_inbox=[],
            qoyod_customers_mapping=[],
            qoyod_external_customers=[],
            qoyod_products_mapping=[],
            qoyod_external_products=[])
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["nonexistent"])
        gs = r["gates_snapshot"]
        assert gs["selective_live_send_enabled"] is False
        assert gs["production_writes_locked"]    is True
        assert gs["qoyod_sync_start_date"]       == "2026-07-01"
        assert gs["bank_transfer_routing_enabled"] is False
        assert gs["qoyod_invoice_date_source"]   == "send_date"


class TestPerSkuRecommendation:
    """Bug #2: recommendations were aggregated across all SKUs. Now
    each SKU carries its own recommendation, and top-level rec
    surfaces BOTH `adopt_existing_product` and
    `needs_create_product_later` when the order mixes both."""

    @pytest.mark.asyncio
    async def test_mixed_skus_produce_both_recommendations(self):
        # Two items: SKU-A has a real mapping, SKU-B is missing
        # everywhere → order needs BOTH adopt AND create.
        inbox = [{
            "user_id":              "main",
            "salla_order_number":   "MIXED-1",
            "existing_qoyod_invoice_id": "DRY:invoice:mix1",
            "qoyod_customer_id":    555001,
            "canonical_payload": {
                "order_number": "MIXED-1",
                "payment_method": "mada",
                "status": "completed",
                "customer": {"mobile": "+966555555555",
                             "name":   "Mixed User"},
                "items": [
                    {"sku": "SKU-A", "name": "Product A",
                     "quantity": 1, "unit_price": 10.0,
                     "discount_amount": 0.0, "tax_amount": 0.0,
                     "total": 10.0,
                     "qoyod_product_id": "DRY:product:aaaa"},
                    {"sku": "SKU-B", "name": "Product B",
                     "quantity": 1, "unit_price": 20.0,
                     "discount_amount": 0.0, "tax_amount": 0.0,
                     "total": 20.0,
                     "qoyod_product_id": "DRY:product:bbbb"},
                ],
            },
        }]
        db = _FakeDB(
            qoyod_settings=_fail_closed_settings(),
            integration_inbox=inbox,
            qoyod_customers_mapping=[],
            qoyod_external_customers=[
                {"user_id": "main", "mobile": "+966555555555",
                 "qoyod_customer_id": 12345, "name": "Ext"}],
            # SKU-A → real, SKU-B → missing everywhere.
            qoyod_products_mapping=[
                {"user_id": "main", "sku": "SKU-A",
                 "qoyod_product_id": 45,
                 "dry_run_only": False}],
            qoyod_external_products=[])
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["MIXED-1"])
        rep = r["reports"][0]
        # Per-SKU list carries both recommendations.
        assert len(rep["per_sku_recommendation"]) == 2
        by_sku = {s["sku"]: s for s in rep["per_sku_recommendation"]}
        assert by_sku["SKU-A"]["recommendation"] == \
            "adopt_existing_product"
        assert by_sku["SKU-A"]["real_qoyod_product_id"] == 45
        assert by_sku["SKU-A"]["real_source"] == \
            "qoyod_products_mapping"
        assert by_sku["SKU-B"]["recommendation"] == \
            "needs_create_product_later"
        assert by_sku["SKU-B"]["real_qoyod_product_id"] is None
        # Aggregate includes BOTH markers.
        assert "adopt_existing_product" in rep["recommended_action"]
        assert "needs_create_product_later" in rep["recommended_action"]

    @pytest.mark.asyncio
    async def test_single_sku_adopt_only(self):
        inbox = [{
            "user_id":              "main",
            "salla_order_number":   "ADOPT-1",
            "existing_qoyod_invoice_id": "DRY:invoice:x",
            "qoyod_customer_id":    12345,   # real
            "canonical_payload": {
                "order_number": "ADOPT-1",
                "payment_method": "mada",
                "status": "completed",
                "customer": {"mobile": "+966510000000"},
                "items": [{
                    "sku": "AMS11237", "quantity": 1,
                    "unit_price": 30.0, "discount_amount": 0.0,
                    "tax_amount": 0.0, "total": 30.0,
                    "qoyod_product_id": "DRY:product:tt"}],
            },
        }]
        db = _FakeDB(
            qoyod_settings=_fail_closed_settings(),
            integration_inbox=inbox,
            qoyod_customers_mapping=[],
            qoyod_external_customers=[],
            qoyod_products_mapping=[
                {"user_id": "main", "sku": "AMS11237",
                 "qoyod_product_id": 45, "dry_run_only": False}],
            qoyod_external_products=[])
        r = await build_dry_rca_report(
            db, user_id="main", order_numbers=["ADOPT-1"])
        rep = r["reports"][0]
        assert rep["recommended_action"] == [
            "ignore_dry_invoice_sentinel",
            "adopt_existing_product",
        ]
