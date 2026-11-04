"""The sender may yield a partial scan to the event loop, never to the writer."""
import asyncio
from datetime import datetime, timezone

import mongomock_motor
import pytest

from integrations.qoyod import candidate_orders
from qoyod_auto_unified import queue_select
from .test_qoyod_unified_backlog_20260822 import ORDERS_OWNER, _unified_order


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [0, 7])
async def test_cooperative_scan_preserves_exact_audit_and_oldest_batch(monkeypatch, pending):
    from integrations.qoyod_manual import auto_send

    db = mongomock_motor.AsyncMongoMockClient().fairness
    refs = [str(410000000 + i) for i in range(100)]
    await db.unified_orders.insert_many([_unified_order(ref) for ref in refs] + [
        _unified_order("canceled", status="canceled"), _unified_order("foreign", owner="other"),
    ])
    cutoff = len(refs) - pending
    await db.qoyod_invoices.insert_many([{
        "user_id": "main", "reference": ref, "qoyod_official_reference": ref,
        "reference_provenance": "qoyod.reference", "qoyod_invoice_id": "fixture-" + ref,
    } for ref in refs[:cutoff]])
    settings = {"invoice_trigger_statuses": ["completed"]}
    source = candidate_orders.build_candidate_audit
    audits = []

    async def checked_audit(inner_db, **kwargs):
        checkpoint = kwargs.pop("checkpoint")
        assert kwargs.pop("sender_snapshot_only") is True
        fixed = datetime(2026, 8, 22, tzinfo=timezone.utc)
        reference = await source(inner_db, **kwargs, now=fixed)
        candidate = await source(inner_db, **kwargs, now=fixed, checkpoint=checkpoint, sender_snapshot_only=True)
        assert reference["unified_exclusions"]["status_not_eligible"] == 1
        assert candidate["orders"] == [r for r in reference["orders"] if r["worker_candidate"]]
        for key in candidate.keys() - {"orders"}:
            assert candidate[key] == reference[key], key
        audits.append(candidate)
        return candidate

    monkeypatch.setattr(queue_select, "build_candidate_audit", checked_audit)
    selected, counts = await queue_select._load_candidate_rows_oldest_first(
        auto_send, db, settings=settings, orders_user_id=ORDERS_OWNER, batch_limit=5,
    )
    assert [row["order_number"] for row in selected] == refs[cutoff:cutoff + 5]
    assert counts["runnable_candidate_count"] == pending
    assert counts["candidate_snapshot"]["snapshot_fingerprint"] == audits[0]["snapshot_fingerprint"]


@pytest.mark.asyncio
async def test_cancellation_while_scanning_ineligible_rows_returns_no_candidates():
    db = mongomock_motor.AsyncMongoMockClient().fairness
    await db.unified_orders.insert_many([
        _unified_order(str(410000000 + i), status="canceled") for i in range(128)
    ])
    entered = asyncio.Event()

    async def checkpoint():
        entered.set()
        await asyncio.Future()

    scan = asyncio.create_task(candidate_orders.build_candidate_audit(
        db, orders_user_id=ORDERS_OWNER, markers_user_id="main", from_date="2026-08-01",
        to_date="2026-08-22", include_inbox_evidence=False, checkpoint=checkpoint,
    ))
    try:
        await asyncio.wait_for(entered.wait(), 3)
    finally:
        scan.cancel()
    with pytest.raises(asyncio.CancelledError):
        await scan
    assert await db.qoyod_invoices.count_documents({}) == 0
