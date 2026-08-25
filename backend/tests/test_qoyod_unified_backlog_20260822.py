"""Acceptance coverage for the 2026-08-22 unified Qoyod backlog fix.

These tests are deliberately database-local and read-only with respect to
Qoyod.  ``unified_orders`` introduces candidates, ``integration_inbox`` only
adds evidence, and only an exact local ``qoyod_invoices.reference`` match
proves that a candidate was sent.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timedelta, timezone
from typing import Any

import mongomock_motor
import pytest

from integrations.qoyod import candidate_orders
from integrations.qoyod.candidate_orders import (
    PAYMENT_NEEDS_LIVE_VERIFICATION,
    build_candidate_audit,
    payment_is_eligible,
    resolve_candidate_date_range,
)
from integrations.qoyod.unsent_orders import (
    FAILED,
    SENT,
    UNSENT,
    list_unsent_orders,
)
from integrations.qoyod_manual import auto_send
from integrations.qoyod_manual.pending import list_pending_orders


QOYOD_TENANT = "main"
ORDERS_OWNER = "orders-owner-20260822"
FROM_DATE = "2026-08-15"
TO_DATE = "2026-08-22"
NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    client = mongomock_motor.AsyncMongoMockClient()
    return client["test_qoyod_unified_backlog_20260822"]


def _native_status(slug: str) -> str:
    return {
        "completed": "تم التنفيذ",
        "delivering": "جاري التوصيل",
        "in_delivery": "جاري التوصيل",
        "delivered": "تم التوصيل",
        "payment_pending": "بانتظار الدفع",
        "under_review": "قيد المراجعة",
        "shipped": "تم الشحن",
        "canceled": "ملغي",
        "restored": "مسترجع",
        "restoring": "قيد الاسترجاع",
    }.get(slug, slug)


def test_explicit_from_to_before_july_is_not_clamped_to_sync_metadata():
    date_range = resolve_candidate_date_range(
        from_date="2026-06-10",
        to_date="2026-06-30",
        now=NOW,
    )

    assert date_range.from_date.isoformat() == "2026-06-10"
    assert date_range.to_date.isoformat() == "2026-06-30"
    assert date_range.requested_from_date.isoformat() == "2026-06-10"


def _unified_order(
    order_number: str,
    *,
    owner: str = ORDERS_OWNER,
    stored_date: str = "2026-08-20",
    raw_salla_date: str | None = None,
    status: str = "completed",
    status_native: str | None = None,
    payment_method: str = "mada",
    payment_status: str = "paid",
    total: float = 100.0,
) -> dict[str, Any]:
    paid = payment_status == "paid"
    row: dict[str, Any] = {
        "user_id": owner,
        "order_id": f"salla-id-{order_number}",
        "order_number": order_number,
        "order_date": stored_date,
        "order_status": status,
        "order_status_slug": status,
        "order_status_native": status_native or _native_status(status),
        "payment_method": payment_method,
        "payment_status": payment_status,
        "payment_collection_status": payment_status,
        "paid_amount": total if paid else 0.0,
        "remaining_amount": 0.0 if paid else total,
        "has_remaining_amount": not paid,
        "total_amount": total,
        "currency": "SAR",
        "customer_name": f"Customer {order_number}",
    }
    if raw_salla_date is not None:
        row["raw_by_source"] = {
            "salla_direct": {"date": raw_salla_date},
        }
    return row


async def _audit(db, **overrides):
    kwargs = {
        "orders_user_id": ORDERS_OWNER,
        "markers_user_id": QOYOD_TENANT,
        "marker_user_ids": (QOYOD_TENANT, ORDERS_OWNER),
        "from_date": FROM_DATE,
        "to_date": TO_DATE,
        "now": NOW,
    }
    kwargs.update(overrides)
    return await build_candidate_audit(db, **kwargs)


async def _pending(db, status: str):
    return await list_pending_orders(
        db,
        user_id=QOYOD_TENANT,
        orders_user_id=ORDERS_OWNER,
        status=status,
        from_date=FROM_DATE,
        to_date=TO_DATE,
        now=NOW,
        limit=1000,
    )


async def _unsent(db):
    return await list_unsent_orders(
        db,
        user_id=QOYOD_TENANT,
        orders_user_id=ORDERS_OWNER,
        from_date=FROM_DATE,
        to_date=TO_DATE,
        now=NOW,
        limit=5000,
    )


@pytest.mark.asyncio
async def test_candidate_snapshot_scans_unified_orders_once(db):
    await db.unified_orders.insert_many([
        _unified_order("single-scan-completed", status="completed"),
        _unified_order("single-scan-delivering", status="delivering"),
        _unified_order("single-scan-delivered", status="delivered"),
    ])

    class CountingCollection:
        def __init__(self, collection):
            self.collection = collection
            self.find_calls = 0

        def find(self, *args, **kwargs):
            self.find_calls += 1
            return self.collection.find(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(self.collection, name)

    class CountingDb:
        def __init__(self, database):
            self.database = database
            self.unified_orders = CountingCollection(
                database.unified_orders
            )

        def __getattr__(self, name):
            return getattr(self.database, name)

    counting_db = CountingDb(db)
    audit = await _audit(counting_db)

    assert counting_db.unified_orders.find_calls == 1
    assert audit["worker_candidate_status_counts"] == {
        "completed": 1,
        "delivering": 1,
        "delivered": 1,
    }


@pytest.mark.asyncio
async def test_snapshot_deduplicates_across_statuses_and_owner_scope(db):
    await db.unified_orders.insert_many([
        _unified_order("only-completed", status="completed"),
        _unified_order("only-delivering", status="delivering"),
        _unified_order("only-delivered", status="delivered"),
        _unified_order(
            "same-reference-two-statuses",
            status="delivering",
            stored_date="2026-08-21",
        ),
        _unified_order(
            "same-reference-two-statuses",
            status="completed",
            stored_date="2026-08-20",
        ),
        _unified_order("same-reference-two-owners", status="completed"),
        _unified_order(
            "same-reference-two-owners",
            owner=QOYOD_TENANT,
            status="delivered",
        ),
    ])

    audit = await _audit(db)
    references = [row["order_number"] for row in audit["orders"]]

    assert len(references) == len(set(references)) == 5
    assert audit["status_counts"] == {
        "completed": 2,
        "delivering": 2,
        "delivered": 1,
    }
    assert sum(audit["status_counts"].values()) == len(references)
    assert audit["unified_exclusions"][
        "duplicate_unified_reference"
    ] == 1
    assert audit["by_reference"]["same-reference-two-owners"][
        "current_status_key"
    ] == "completed"
    assert all(
        row["unified_orders_owner_id"] == ORDERS_OWNER
        for row in audit["orders"]
    )


@pytest.mark.asyncio
async def test_next_snapshot_reflects_completed_to_delivering_transition(db):
    order_number = "status-transition"
    await db.unified_orders.insert_one(
        _unified_order(order_number, status="completed")
    )

    completed_snapshot = await _audit(db, now=NOW)
    await db.unified_orders.update_one(
        {"user_id": ORDERS_OWNER, "order_number": order_number},
        {"$set": {
            "order_status": "delivering",
            "order_status_slug": "delivering",
            "order_status_native": "جاري التوصيل",
        }},
    )
    delivering_snapshot = await _audit(
        db, now=NOW + timedelta(minutes=1)
    )

    assert completed_snapshot["worker_candidate_status_counts"] == {
        "completed": 1,
        "delivering": 0,
        "delivered": 0,
    }
    assert delivering_snapshot["worker_candidate_status_counts"] == {
        "completed": 0,
        "delivering": 1,
        "delivered": 0,
    }
    assert completed_snapshot["eligible_references"] == {
        order_number
    } == delivering_snapshot["eligible_references"]
    assert completed_snapshot["snapshot_fingerprint"] != (
        delivering_snapshot["snapshot_fingerprint"]
    )


@pytest.mark.asyncio
async def test_snapshot_has_capture_time_and_deterministic_fingerprint(db):
    await db.unified_orders.insert_one(
        _unified_order("stable-snapshot", status="delivered")
    )

    first = await _audit(db, now=NOW)
    second = await _audit(db, now=NOW + timedelta(minutes=5))

    first_capture = datetime.fromisoformat(first["captured_at"])
    second_capture = datetime.fromisoformat(second["captured_at"])
    assert first_capture.tzinfo is not None
    assert second_capture.tzinfo is not None
    assert first_capture < second_capture
    assert first["snapshot_fingerprint"] == second["snapshot_fingerprint"]
    assert len(first["snapshot_fingerprint"]) == 64


def test_runtime_policy_does_not_embed_incident_totals():
    runtime_modules = (candidate_orders, auto_send)
    forbidden_numbers = {60, 158, 11898.31, 28704.24}

    for module in runtime_modules:
        tree = ast.parse(inspect.getsource(module))
        runtime_numbers = {
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, (int, float))
            and not isinstance(node.value, bool)
        }
        assert runtime_numbers.isdisjoint(forbidden_numbers), module.__name__


@pytest.mark.asyncio
async def test_historical_completed_sixty_benchmark_remains_one_exact_set(db):
    """Replay the historical completed-only benchmark, not a runtime total.

    The 60 rows and SAR 11,898.31 amount describe the captured completed-only
    sample.  Candidate policy must continue to derive all three eligible
    states dynamically from ``unified_orders``.
    """
    documents = [
        {
            "user_id": ORDERS_OWNER,
            "order_number": f"BENCHMARK-COMPLETED-60-{index:02d}",
            "order_date": "2026-08-20",
            "order_status": "completed",
            "order_status_slug": "completed",
            "order_status_native": "تم التنفيذ",
            "total_amount": 100.0 if index < 59 else 5998.31,
            "currency": "SAR",
            # No durable payment proof: the row remains visible, while the
            # unchanged sender must still verify Salla live before writing.
        }
        for index in range(60)
    ]
    documents[0]["order_date"] = "2026-08-14"
    documents[0]["raw_by_source"] = {
        "salla_direct": {"date": "2026-08-15T02:14:10+03:00"},
    }
    await db.unified_orders.insert_many(documents)

    audit = await _audit(db)

    assert len(audit["eligible_references"]) == 60
    assert len(audit["unsent_references"]) == 60
    assert audit["sent_references"] == set()
    assert audit["eligible_total_amount"] == 11898.31
    assert all(
        row["payment_eligibility"] == PAYMENT_NEEDS_LIVE_VERIFICATION
        for row in audit["orders"]
    )
    assert sum(
        row["legacy_worker_visibility_reason"]
        == "missing_from_integration_inbox"
        for row in audit["orders"]
    ) == 60


@pytest.mark.asyncio
async def test_legacy_hidden_split_replays_as_exact_post_fix_worker_set(db):
    rows = [
        {
            "order_number": f"INCIDENT-462-{index:03d}",
            "in_integration_inbox": index >= 379,
        }
        for index in range(462)
    ]
    await db.unified_orders.insert_many([{
        "user_id": ORDERS_OWNER,
        "order_number": row["order_number"],
        "order_date": "2026-08-10",
        "order_status": "completed",
        "order_status_slug": "completed",
        "total_amount": 1.0,
        "currency": "SAR",
    } for row in rows])
    inbox_rows = [{
        "id": f'incident-{row["order_number"]}',
        "trace_id": f'incident-{row["order_number"]}',
        "user_id": QOYOD_TENANT,
        "salla_order_number": row["order_number"],
        "received_at": NOW,
        "canonical_payload": {
            "order_status": "under_review",
            "order_status_native": "قيد المراجعة",
        },
    } for row in rows if row["in_integration_inbox"]]
    await db.integration_inbox.insert_many(inbox_rows)

    audit = await build_candidate_audit(
        db,
        orders_user_id=ORDERS_OWNER,
        markers_user_id=QOYOD_TENANT,
        marker_user_ids=(QOYOD_TENANT, ORDERS_OWNER),
        from_date="2026-07-01",
        to_date=TO_DATE,
        now=NOW,
    )

    assert len(audit["eligible_references"]) == 462
    assert len(audit["unsent_references"]) == 462
    assert audit["sent_references"] == set()
    assert audit["eligible_total_amount"] == 462.0
    reasons: dict[str, int] = {}
    for row in audit["orders"]:
        reason = row["legacy_worker_visibility_reason"]
        reasons[reason] = reasons.get(reason, 0) + 1
    assert reasons == {
        "missing_from_integration_inbox": 379,
        "newest_inbox_status_not_eligible": 83,
    }


@pytest.mark.asyncio
async def test_unified_only_order_is_visible_in_pending_and_unsent(db):
    """Missing inbox evidence must not make an eligible order disappear."""
    order_number = "unified-only-20260822"
    await db.unified_orders.insert_one(_unified_order(order_number))

    pending = await _pending(db, "completed")
    unsent = await _unsent(db)

    assert pending["source_authority"] == "unified_orders"
    assert [row["order_number"] for row in pending["orders"]] == [
        order_number,
    ]
    assert pending["orders"][0]["in_unified_orders"] is True
    assert pending["orders"][0]["in_integration_inbox"] is False
    assert pending["counts"]["missing_from_integration_inbox"] == 1

    assert unsent["source_authority"] == "unified_orders"
    assert unsent["worker_candidate_count"] == 1
    assert unsent["counts"][UNSENT] == 1
    assert unsent["counts"][SENT] == 0
    assert unsent["orders"][0]["order_number"] == order_number
    assert unsent["orders"][0]["in_integration_inbox"] is False
    assert unsent["orders"][0]["worker_candidate"] is True


@pytest.mark.asyncio
async def test_exact_invoice_with_failed_payment_stays_actionable_without_dup(db):
    order_number = "invoice-exists-payment-failed"
    await db.unified_orders.insert_one(_unified_order(order_number))
    await db.integration_inbox.insert_one({
        "id": "payment-failed-trace",
        "trace_id": "payment-failed-trace",
        "user_id": QOYOD_TENANT,
        "salla_order_number": order_number,
        "received_at": NOW,
        "manual_qoyod_invoice_id": "q-partial-1",
        "manual_qoyod_payment_id": "",
        "canonical_payload": {
            "order_status": "completed",
            "payment_method": "mada",
        },
    })
    await db.qoyod_invoices.insert_one({
        "user_id": QOYOD_TENANT,
        "qoyod_invoice_id": "q-partial-1",
        "reference": order_number,
        "qoyod_official_reference": order_number,
        "reference_provenance": "qoyod.reference",
        "source": "plan_b_send",
        "status": "partial",
        "total": 100.0,
        "paid_amount": 0.0,
        "remaining": 100.0,
    })
    await db.qoyod_manual_auto_quarantines.insert_one({
        "_id": f"{QOYOD_TENANT}:{order_number}",
        "user_id": QOYOD_TENANT,
        "order_number": order_number,
        "status": "open",
        "code": "invoice_created_payment_failed",
        "message": "فشل تسجيل السداد بعد نجاح الفاتورة",
    })

    audit = await _audit(db)
    unsent = await _unsent(db)

    assert audit["sent_references"] == {order_number}
    assert audit["unsent_references"] == set()
    assert audit["counts"]["worker_candidates"] == 0
    assert unsent["counts"][FAILED] == 1
    assert unsent["counts"][SENT] == 0
    assert unsent["orders"][0]["retry_allowed"] is True
    assert unsent["orders"][0]["failure_code"] == (
        "invoice_created_payment_failed"
    )
    assert unsent["orders"][0]["has_qoyod_reference_match"] is True
    assert unsent["orders"][0]["worker_candidate"] is False


@pytest.mark.asyncio
async def test_closed_status_and_payment_policy_excludes_unsafe_orders(db):
    eligible = {
        "eligible-completed",
        "eligible-delivering",
        "eligible-delivered",
        # COD is invoice-only, so an unpaid collection state is expected.
        "eligible-cod-invoice-only",
    }
    rows = [
        _unified_order("eligible-completed", status="completed"),
        _unified_order("eligible-delivering", status="delivering"),
        _unified_order("eligible-delivered", status="delivered"),
        _unified_order(
            "eligible-cod-invoice-only",
            status="completed",
            payment_method="الدفع عند الاستلام",
            payment_status="unpaid",
        ),
    ]
    for unsafe_status in (
        "payment_pending",
        "under_review",
        "shipped",
        "canceled",
        "restored",
        "restoring",
    ):
        rows.append(_unified_order(
            f"unsafe-state-{unsafe_status}",
            status=unsafe_status,
            payment_method="cod" if unsafe_status == "payment_pending" else "mada",
        ))
    rows.extend([
        _unified_order(
            "unsafe-unpaid-mada",
            status="completed",
            payment_status="unpaid",
        ),
        _unified_order(
            "unsafe-payment-pending-mada",
            status="completed",
            payment_status="payment_pending",
        ),
        _unified_order(
            "unsafe-refunded-cod",
            status="completed",
            payment_method="cod",
            payment_status="refunded",
        ),
        {
            **_unified_order(
                "unsafe-one-riyal-of-one-hundred",
                status="completed",
            ),
            "payment_status": "",
            "payment_collection_status": "",
            "paid_amount": 1.0,
            "remaining_amount": None,
            "has_remaining_amount": False,
            "total_amount": 100.0,
        },
        {
            **_unified_order(
                "unsafe-paid-label-conflicts-with-partial-amount",
                status="completed",
            ),
            "payment_status": "paid",
            "payment_collection_status": "paid",
            "paid_amount": 1.0,
            "remaining_amount": None,
            "has_remaining_amount": False,
            "total_amount": 100.0,
        },
    ])
    await db.unified_orders.insert_many(rows)

    audit = await _audit(db)

    assert audit["eligible_references"] == eligible
    assert audit["unsent_references"] == eligible
    assert audit["unified_exclusions"]["status_not_eligible"] == 6
    assert audit["unified_exclusions"]["payment_not_eligible"] == 5


@pytest.mark.asyncio
async def test_exact_reference_matching_and_duplicate_detection_use_sets(db):
    references = {
        "exact-sent",
        "duplicate-reference",
        "prefix-only",
        "dry-marker-only",
    }
    await db.unified_orders.insert_many([
        _unified_order(reference) for reference in references
    ])
    await db.qoyod_invoices.insert_many([
        {
            "user_id": QOYOD_TENANT,
            "qoyod_invoice_id": "q-100",
            "reference": "exact-sent",
            "qoyod_official_reference": "exact-sent",
            "reference_provenance": "qoyod.reference",
        },
        {
            "user_id": QOYOD_TENANT,
            "qoyod_invoice_id": "q-200",
            "reference": "duplicate-reference",
            "qoyod_official_reference": "duplicate-reference",
            "reference_provenance": "qoyod.reference",
        },
        {
            "user_id": QOYOD_TENANT,
            "qoyod_invoice_id": "q-201",
            "reference": "duplicate-reference",
            "qoyod_official_reference": "duplicate-reference",
            "reference_provenance": "qoyod.reference",
        },
        {
            "user_id": QOYOD_TENANT,
            "qoyod_invoice_id": "q-300",
            "reference": "prefix-only-extra",
            "qoyod_official_reference": "prefix-only-extra",
            "reference_provenance": "qoyod.reference",
        },
        {
            "user_id": QOYOD_TENANT,
            "qoyod_invoice_id": "DRY:q-400",
            "reference": "dry-marker-only",
        },
        {
            "user_id": QOYOD_TENANT,
            "qoyod_invoice_id": "q-500",
            "reference": "qoyod-only-reference",
            "qoyod_official_reference": "qoyod-only-reference",
            "reference_provenance": "qoyod.reference",
        },
    ])

    audit = await _audit(db)

    assert audit["eligible_references"] == references
    assert audit["sent_references"] == {
        "exact-sent",
        "duplicate-reference",
    }
    assert audit["unsent_references"] == {
        "prefix-only",
        "dry-marker-only",
    }
    assert audit["qoyod_only_references"] == {
        "prefix-only-extra",
        "qoyod-only-reference",
    }
    assert set(audit["duplicate_qoyod_references"]) == {
        "duplicate-reference",
    }
    assert audit["counts"]["exact_qoyod_reference_matches"] == 2
    assert audit["counts"]["worker_candidates"] == 2


@pytest.mark.asyncio
async def test_synced_alias_cannot_masquerade_as_official_qoyod_reference(db):
    order_number = "official-reference-only"
    await db.unified_orders.insert_one(_unified_order(order_number))
    await db.qoyod_invoices.insert_one({
        "user_id": QOYOD_TENANT,
        "qoyod_invoice_id": "alias-only-invoice",
        "source": "synced_from_qoyod",
        # Historical buggy sync copied this alias into `reference`.
        "reference": order_number,
        "external_reference": order_number,
        "reference_provenance": "external_reference",
        "raw_response": {"external_reference": order_number},
    })

    alias_audit = await _audit(db)

    assert alias_audit["sent_references"] == set()
    assert alias_audit["unsent_references"] == {order_number}
    assert alias_audit["orders"][0]["has_qoyod_reference_match"] is False

    await db.qoyod_invoices.update_one(
        {"qoyod_invoice_id": "alias-only-invoice"},
        {"$set": {
            "qoyod_official_reference": order_number,
            "reference_provenance": "qoyod.reference",
            "raw_response.reference": order_number,
        }},
    )
    official_audit = await _audit(db)

    assert official_audit["sent_references"] == {order_number}
    assert official_audit["unsent_references"] == set()


@pytest.mark.asyncio
async def test_generic_local_reference_without_qoyod_provenance_stays_unsent(db):
    order_number = "local-reference-without-proof"
    await db.unified_orders.insert_one(_unified_order(order_number))
    await db.qoyod_invoices.insert_one({
        "user_id": QOYOD_TENANT,
        "qoyod_invoice_id": "legacy-local-invoice-id",
        "reference": order_number,
        "qoyod_official_reference": order_number,
        "source": "legacy_local",
    })

    audit = await _audit(db)

    assert audit["sent_references"] == set()
    assert audit["unsent_references"] == {order_number}
    assert audit["orders"][0]["has_qoyod_reference_match"] is False


@pytest.mark.asyncio
async def test_unchanged_plan_b_ledger_reference_remains_exact_sent_evidence(db):
    order_number = "plan-b-ledger-reference"
    await db.unified_orders.insert_one(_unified_order(order_number))
    await db.qoyod_invoices.insert_one({
        "user_id": QOYOD_TENANT,
        "qoyod_invoice_id": "real-plan-b-invoice-id",
        "reference": order_number,
        "source": "plan_b_send",
    })

    audit = await _audit(db)

    assert audit["sent_references"] == {order_number}
    assert audit["unsent_references"] == set()


@pytest.mark.asyncio
async def test_explicit_range_uses_riyadh_business_date(db):
    """A UTC Aug 14 timestamp can belong to Aug 15 in Riyadh."""
    await db.unified_orders.insert_many([
        _unified_order(
            "RIYADH-DATE-BOUNDARY-001",
            stored_date="2026-08-14",
            raw_salla_date="2026-08-14T21:30:00+00:00",
        ),
        _unified_order(
            "before-from-boundary",
            stored_date="2026-08-14",
            raw_salla_date="2026-08-14T20:59:59+00:00",
        ),
        _unified_order(
            "inside-to-boundary",
            stored_date="2026-08-22",
            raw_salla_date="2026-08-22T20:59:59+00:00",
        ),
        _unified_order(
            "after-to-boundary",
            stored_date="2026-08-22",
            raw_salla_date="2026-08-22T21:00:00+00:00",
        ),
    ])

    audit = await _audit(db)
    pending = await _pending(db, "completed")

    expected = {"RIYADH-DATE-BOUNDARY-001", "inside-to-boundary"}
    assert audit["from_date"] == FROM_DATE
    assert audit["to_date"] == TO_DATE
    assert audit["eligible_references"] == expected
    assert {
        row["order_number"] for row in pending["orders"]
    } == expected
    boundary = audit["by_reference"]["RIYADH-DATE-BOUNDARY-001"]
    assert boundary["stored_order_date"] == "2026-08-14"
    assert boundary["order_date"] == "2026-08-15"
    assert boundary["order_date_mismatch"] is True
    assert boundary["order_date_source"] == (
        "raw_by_source.salla_direct.date"
    )
    assert audit["unified_exclusions"]["outside_requested_date_range"] == 2


@pytest.mark.asyncio
async def test_orders_owner_and_qoyod_tenant_are_separate_authorities(db):
    await db.unified_orders.insert_many([
        _unified_order("main-qoyod-reference"),
        _unified_order("wrong-owner-qoyod-reference"),
        _unified_order("orders-owner-inbox"),
        # A row owned by main is not allowed to expand this store's universe.
        _unified_order("decoy-main-unified", owner=QOYOD_TENANT),
    ])
    await db.integration_inbox.insert_many([
        {
            "id": "trace-main",
            "trace_id": "trace-main",
            "user_id": QOYOD_TENANT,
            "salla_order_number": "main-qoyod-reference",
            "received_at": NOW,
            "canonical_payload": {
                "order_status": "completed",
                "order_status_native": "تم التنفيذ",
            },
        },
        {
            "id": "trace-orders-owner",
            "trace_id": "trace-orders-owner",
            "user_id": ORDERS_OWNER,
            "salla_order_number": "orders-owner-inbox",
            "received_at": NOW,
            "canonical_payload": {
                "order_status": "completed",
                "order_status_native": "تم التنفيذ",
            },
        },
    ])
    await db.qoyod_invoices.insert_many([
        {
            "user_id": QOYOD_TENANT,
            "qoyod_invoice_id": "q-main",
            "reference": "main-qoyod-reference",
            "qoyod_official_reference": "main-qoyod-reference",
            "reference_provenance": "qoyod.reference",
        },
        {
            "user_id": ORDERS_OWNER,
            "qoyod_invoice_id": "q-wrong-owner",
            "reference": "wrong-owner-qoyod-reference",
            "qoyod_official_reference": "wrong-owner-qoyod-reference",
            "reference_provenance": "qoyod.reference",
        },
    ])

    audit = await _audit(db)
    pending = await _pending(db, "completed")

    assert audit["eligible_references"] == {
        "main-qoyod-reference",
        "wrong-owner-qoyod-reference",
        "orders-owner-inbox",
    }
    assert audit["sent_references"] == {"main-qoyod-reference"}
    assert audit["unsent_references"] == {
        "wrong-owner-qoyod-reference",
        "orders-owner-inbox",
    }
    assert audit["by_reference"]["main-qoyod-reference"][
        "integration_inbox_owner_ids"
    ] == [QOYOD_TENANT]
    assert audit["by_reference"]["orders-owner-inbox"][
        "integration_inbox_owner_ids"
    ] == [ORDERS_OWNER]
    assert {
        row["order_number"] for row in pending["orders"]
    } == audit["unsent_references"]


@pytest.mark.asyncio
async def test_ui_worker_candidates_equal_union_of_pending_status_tabs(db):
    await db.unified_orders.insert_many([
        _unified_order("candidate-completed", status="completed"),
        _unified_order("candidate-delivering", status="delivering"),
        _unified_order("candidate-delivered", status="delivered"),
        _unified_order("local-marker-is-not-qoyod-proof", status="completed"),
        _unified_order("already-sent", status="completed"),
        _unified_order("excluded-shipped", status="shipped"),
        _unified_order(
            "excluded-unpaid",
            status="completed",
            payment_status="unpaid",
        ),
    ])
    await db.integration_inbox.insert_one({
        "id": "trace-local-marker",
        "trace_id": "trace-local-marker",
        "user_id": QOYOD_TENANT,
        "salla_order_number": "local-marker-is-not-qoyod-proof",
        "received_at": NOW,
        "manual_qoyod_invoice_id": "local-777",
        "canonical_payload": {
            "order_status": "completed",
            "order_status_native": "تم التنفيذ",
        },
    })
    await db.qoyod_invoices.insert_one({
        "user_id": QOYOD_TENANT,
        "qoyod_invoice_id": "q-sent",
        "reference": "already-sent",
        "qoyod_official_reference": "already-sent",
        "reference_provenance": "qoyod.reference",
    })

    ui = await _unsent(db)
    tabs = {
        status: await _pending(db, status)
        for status in ("completed", "in_delivery", "delivered")
    }
    pending_candidates = {
        row["order_number"]
        for response in tabs.values()
        for row in response["orders"]
    }
    ui_candidates = {
        row["order_number"]
        for row in ui["orders"]
        if row["worker_candidate"]
    }

    assert pending_candidates == ui_candidates == {
        "candidate-completed",
        "candidate-delivering",
        "candidate-delivered",
        "local-marker-is-not-qoyod-proof",
    }
    assert ui["worker_candidate_count"] == len(pending_candidates)
    assert ui["counts"][UNSENT] == len(pending_candidates)
    assert ui["counts"][SENT] == 1
    for response in tabs.values():
        assert response["counts"][
            "authoritative_worker_candidates"
        ] == len(pending_candidates)


@pytest.mark.asyncio
async def test_worker_auto_approval_flag_blocks_unified_backlog_writes(
    db, monkeypatch,
):
    order_number = "rollout-blocked-unified-order"
    await db.unified_orders.insert_one(_unified_order(order_number))
    await db.qoyod_settings.insert_one({
        "user_id": QOYOD_TENANT,
        "enabled": True,
        "auto_send": True,
        "auto_receipt": True,
        "dry_run_mode": False,
        "legacy_pipeline_frozen": True,
        "production_writes_locked": False,
        "invoice_trigger_statuses": [
            "completed", "delivering", "delivered",
        ],
        "trigger_once_only": True,
        "plan_b_auto_send_armed_at": "2026-08-22T08:00:00+00:00",
        "plan_b_auto_send_orders_user_id": ORDERS_OWNER,
        # Worker-specific unified backlog approval intentionally absent.
    })

    calls: list[str] = []

    async def acquire_lease(inner_db):
        raise AssertionError("disabled rollout must not write a lease")

    async def release_lease(inner_db, owner):
        assert owner == "acceptance-lease"

    async def forbidden_manual_send(*args, **kwargs):
        calls.append(str(kwargs.get("order_number")))
        raise AssertionError("manual_send_one must remain behind worker flag")

    async def forbidden_live_refresh(*args, **kwargs):
        raise AssertionError("live write preparation must not start")

    async def forbidden_recovery(*args, **kwargs):
        raise AssertionError("legacy recovery must remain behind rollout flag")

    monkeypatch.setattr(auto_send, "_acquire_lease", acquire_lease)
    monkeypatch.setattr(auto_send, "_release_lease", release_lease)
    monkeypatch.setattr(auto_send, "manual_send_one", forbidden_manual_send)
    monkeypatch.setattr(
        auto_send,
        "_refresh_and_verify_salla_status",
        forbidden_live_refresh,
    )
    monkeypatch.setattr(
        auto_send,
        "_recover_legacy_circuit_breaker",
        forbidden_recovery,
    )

    result = await auto_send.run_once(db, batch_limit=5)

    assert result["ok"] is True
    assert result["status"] == "unified_auto_rollout_disabled"
    assert result["candidate_count"] == 0
    assert result["sent_count"] == 0
    assert calls == []
    assert await db.qoyod_manual_auto_runs.count_documents({}) == 0
    assert await db.qoyod_invoices.count_documents({}) == 0


@pytest.mark.asyncio
async def test_worker_candidate_loader_reports_full_set_not_batch_limit(
    db, monkeypatch,
):
    await db.unified_orders.insert_many([
        _unified_order(f"backlog-{index:03d}") for index in range(90)
    ])
    actual_audit = build_candidate_audit

    async def snapshot_at_fixed_time(inner_db, **kwargs):
        kwargs.update({
            "from_date": FROM_DATE,
            "to_date": TO_DATE,
            "now": NOW,
        })
        return await actual_audit(inner_db, **kwargs)

    monkeypatch.setattr(
        auto_send,
        "build_candidate_audit",
        snapshot_at_fixed_time,
    )

    candidates, counts = await auto_send._load_candidate_rows(
        db,
        settings={"invoice_trigger_statuses": ["completed"]},
        orders_user_id=ORDERS_OWNER,
        batch_limit=5,
    )
    ui = await _unsent(db)

    assert counts["authoritative_backlog_count"] == (
        ui["worker_candidate_count"]
    ) == 90
    assert counts["runnable_candidate_count"] == 90
    assert counts["open_quarantined_candidate_count"] == 0
    assert counts["batch_candidate_count"] == len(candidates) == 5
    assert await db.qoyod_manual_auto_runs.count_documents({}) == 0
    assert await db.integration_inbox.count_documents({}) == 0


@pytest.mark.asyncio
async def test_live_worker_uses_resynced_owner_row_without_compatibility_write(
    db, monkeypatch,
):
    order_number = "unified-only-owner-row"
    await db.unified_orders.insert_one(_unified_order(order_number))
    await db.qoyod_settings.insert_one({
        "user_id": QOYOD_TENANT,
        "enabled": True,
        "auto_send": True,
        "auto_receipt": True,
        "dry_run_mode": False,
        "legacy_pipeline_frozen": True,
        "invoice_trigger_statuses": ["completed"],
        "trigger_once_only": True,
        "plan_b_auto_send_armed_at": "2026-08-22T08:00:00+00:00",
        "plan_b_auto_send_orders_user_id": ORDERS_OWNER,
        auto_send.UNIFIED_CANDIDATE_AUTO_FLAG: True,
    })
    sender_calls: list[str] = []

    async def acquire_lease(inner_db):
        return "owner-row-lease"

    async def release_lease(inner_db, owner):
        assert owner == "owner-row-lease"

    async def resync_owner_row(inner_db, owner_id, current_order_number):
        assert owner_id == ORDERS_OWNER
        assert current_order_number == order_number
        await inner_db.integration_inbox.insert_one({
            "id": "resynced-owner-trace",
            "trace_id": "resynced-owner-trace",
            "user_id": ORDERS_OWNER,
            "salla_order_number": order_number,
            "received_at": NOW,
            "canonical_payload": {
                "order_number": order_number,
                "order_status": "completed",
                "order_status_native": "تم التنفيذ",
                "payment_status": "paid",
                "paid_amount": 100.0,
                "remaining_amount": 0.0,
                "total_amount": 100.0,
            },
        })
        return {
            "ok": True,
            "found": True,
            "plan_b_status_snapshot": {
                "status_slug": "completed",
                "status_native": "تم التنفيذ",
            },
        }

    async def sender_sees_owner_row(
        inner_db, *, user_id, orders_user_id, order_number, actor,
        **kwargs,
    ):
        assert kwargs["allow_historical_positive_total"] is True
        assert user_id == QOYOD_TENANT
        assert orders_user_id == ORDERS_OWNER
        owner_row = await inner_db.integration_inbox.find_one({
            "user_id": ORDERS_OWNER,
            "salla_order_number": order_number,
        })
        main_row = await inner_db.integration_inbox.find_one({
            "user_id": QOYOD_TENANT,
            "salla_order_number": order_number,
        })
        assert owner_row is not None
        assert main_row is None
        sender_calls.append(order_number)
        return {"invoice_id": "test-only", "payment_id": "test-only"}

    monkeypatch.setattr(auto_send, "_acquire_lease", acquire_lease)
    monkeypatch.setattr(auto_send, "_release_lease", release_lease)
    monkeypatch.setattr(auto_send, "resync_single_order", resync_owner_row)
    monkeypatch.setattr(auto_send, "manual_send_one", sender_sees_owner_row)

    result = await auto_send.run_once(db, batch_limit=1)

    assert not hasattr(auto_send, "_ensure_unified_sender_compatibility_row")
    assert sender_calls == [order_number]
    assert result["sent_count"] == 1
    assert await db.integration_inbox.count_documents({
        "user_id": ORDERS_OWNER,
        "salla_order_number": order_number,
    }) == 1
    assert await db.integration_inbox.count_documents({
        "user_id": QOYOD_TENANT,
        "salla_order_number": order_number,
    }) == 0
    assert await db.qoyod_invoices.count_documents({}) == 0
