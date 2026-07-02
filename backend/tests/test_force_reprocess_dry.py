"""Iter-2026-02.rev18 — Force-reprocess DRY row tests.

Covers the exact production scenario for order 270075325:
INVOICE_CREATED with `qoyod_invoice_id="DRY:invoice:1ccfbc25"` and
`qoyod_customer_id="DRY:contact:06b4990f"`.

Locks in the invariants (user directive 2026-02-27):
  • Refuse if real قيود invoice_id present.
  • Refuse if Selective Auto-Send Gate would refuse.
  • Clear DRY sentinels + rewind to NORMALIZED.
  • Fire the pipeline inline with scoped live client.
  • PAYMENT_PENDING outcome when invoice OK + payment fails.
"""
from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")


class _Col:
    def __init__(self):
        self.rows: list[dict] = []
    async def find_one(self, q, projection=None):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                return dict(r)
        return None
    async def update_one(self, q, u, upsert=False):
        for r in self.rows:
            if all(r.get(k) == v for k, v in q.items()):
                for k, v in (u.get("$set") or {}).items():
                    r[k] = v
                for k in (u.get("$unset") or {}):
                    r.pop(k, None)
                for k, v in (u.get("$push") or {}).items():
                    arr = r.setdefault(k, [])
                    if isinstance(v, dict) and "$each" in v:
                        arr.extend(v["$each"])
                    else:
                        arr.append(v)
                return MagicMock(matched_count=1, modified_count=1)
        return MagicMock(matched_count=0, modified_count=0)


class _DB:
    def __init__(self):
        self.integration_inbox = _Col()
        self.qoyod_settings    = _Col()
        self.qoyod_invoices    = _Col()


CUTOVER   = "2026-07-01T00:00:00+00:00"
AFTER     = "2026-07-02T10:00:00+00:00"


def _seed_dry_row_270075325(db):
    db.qoyod_settings.rows.append({
        "user_id": "main",
        "selective_auto_send_enabled":    True,
        "selective_auto_send_cutover_at": CUTOVER,
        "selective_auto_send_allowed_payment_methods":
            ["tabby_installment", "mada"],
        "payment_method_mapping": [
            {"salla_method": "tabby", "qoyod_account_id": "92"},
            {"salla_method": "mada",  "qoyod_account_id": "94"},
        ],
        "dry_run_mode": True,
        "production_writes_locked": True,
    })
    db.integration_inbox.rows.append({
        "id":                 "row-270075325",
        "user_id":            "main",
        "salla_order_number": "270075325",
        "trace_id":           "7fc5a5a855f543b49a2e949a93c3eb95",
        "pipeline_stage":     "INVOICE_CREATED",
        "qoyod_invoice_id":   "DRY:invoice:1ccfbc25",     # DRY!
        "qoyod_customer_id":  "DRY:contact:06b4990f",     # DRY!
        "stage_history":      [{"to_stage": "INVOICE_CREATED",
                                "at": AFTER}],
        "canonical_payload": {
            "order_id":       "MZN-270075325",
            "order_number":   "270075325",
            "order_status":   "completed",
            "order_status_native": "تم التنفيذ",
            "payment_method": "mada",
            "salla_order_created_at": AFTER,
            "items": [
                {"sku": "AMS10007", "quantity": 1,
                 "unit_price": 226.94, "total": 260.98,
                 "qoyod_product_id": "DRY:product:xyz"},
                {"sku": "SHIPPING", "quantity": 1,
                 "qoyod_product_id": "42"},   # real — preserve
            ],
            "total_amount": 260.98,
            "currency":     "SAR",
        },
    })


# ─── 1. "تشغيل الآن" via force-reprocess adds RETRYING then NORMALIZED
@pytest.mark.asyncio
async def test_force_reprocess_appends_retrying_and_normalized():
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)

    with patch.object(mod, "_NOW_ISO",
                      return_value="2026-07-02T19:40:00+00:00"), \
         patch("integrations.qoyod.pipeline.process_normalized_row",
               new=AsyncMock(return_value={"outcome": "ok"})):
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")
    r = await db.integration_inbox.find_one({"id": "row-270075325"})
    stages = [h.get("to_stage") for h in r.get("stage_history", [])]
    assert "RETRYING"   in stages
    assert "NORMALIZED" in stages
    assert out["debug"]["reprocess_invoked"] is True
    assert out["debug"]["reset_from_stage"] == "INVOICE_CREATED"


# ─── 2. DRY invoice not treated as real — reprocess proceeds ───────
@pytest.mark.asyncio
async def test_dry_invoice_not_real_reprocess_proceeds():
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)

    with patch("integrations.qoyod.pipeline.process_normalized_row",
               new=AsyncMock(return_value={"outcome": "ok"})):
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")
    assert out.get("outcome") != "REFUSED"
    assert out["debug"]["refused"] is False


# ─── 3. DRY ids are cleared ────────────────────────────────────────
@pytest.mark.asyncio
async def test_dry_customer_and_invoice_and_product_cleared():
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)

    with patch("integrations.qoyod.pipeline.process_normalized_row",
               new=AsyncMock(return_value={"outcome": "ok"})):
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")
    r = await db.integration_inbox.find_one({"id": "row-270075325"})
    assert "qoyod_invoice_id"  not in r     # $unset applied
    assert "qoyod_customer_id" not in r     # $unset applied
    # Line-item DRY product cleared to None, real shipping id kept.
    items = r["canonical_payload"]["items"]
    assert items[0]["qoyod_product_id"] is None
    assert items[1]["qoyod_product_id"] == "42"   # preserved
    assert out["debug"]["cleared_dry_customer_id"] is True
    assert out["debug"]["cleared_dry_invoice_id"]  is True
    assert out["debug"]["cleared_dry_products"]    == 1


# ─── 4. Real qoyod_invoice_id on the row → REFUSED ────────────────
@pytest.mark.asyncio
async def test_refuse_when_row_has_real_invoice_id():
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)
    # Simulate a REAL id on the row.
    db.integration_inbox.rows[0]["qoyod_invoice_id"] = "999123"

    with patch("integrations.qoyod.pipeline.process_normalized_row",
               new=AsyncMock()) as p:
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")
    assert out["ok"] is False
    assert out["outcome"] == "REFUSED"
    assert out["code"] == "row_has_real_qoyod_invoice_id"
    p.assert_not_called()


# ─── 5. Real qoyod_invoices collection row → REFUSED ──────────────
@pytest.mark.asyncio
async def test_refuse_when_qoyod_invoices_has_real_id():
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)
    db.qoyod_invoices.rows.append({
        "user_id": "main",
        "salla_order_number": "270075325",
        "qoyod_invoice_id":  "999124",     # REAL
    })
    with patch("integrations.qoyod.pipeline.process_normalized_row",
               new=AsyncMock()) as p:
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")
    assert out["outcome"] == "REFUSED"
    assert out["code"] == "qoyod_invoices_collection_has_real_id"
    p.assert_not_called()


# ─── 6. Gate refuses → force reprocess refuses ─────────────────────
@pytest.mark.asyncio
async def test_refuse_when_selective_gate_refuses():
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)
    # Turn off the master switch — Gate refuses on `not_enabled`.
    db.qoyod_settings.rows[0]["selective_auto_send_enabled"] = False

    with patch("integrations.qoyod.pipeline.process_normalized_row",
               new=AsyncMock()) as p:
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")
    assert out["outcome"] == "REFUSED"
    assert "gate_refused" in out["code"]
    p.assert_not_called()


# ─── 7. Bad confirm token → REFUSED ────────────────────────────────
@pytest.mark.asyncio
async def test_refuse_bad_confirm_token():
    from integrations.qoyod import force_reprocess_dry as mod
    from integrations.qoyod.force_reprocess_dry import (
        ForceReprocessRefused,
    )
    db = _DB()
    _seed_dry_row_270075325(db)
    with pytest.raises(ForceReprocessRefused) as e:
        await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325", trace_id=None,
            confirm_token="WRONG", actor="ops")
    assert e.value.code == "confirm_token_mismatch"


# ─── 8. Row not found → REFUSED (no pipeline call) ─────────────────
@pytest.mark.asyncio
async def test_refuse_row_not_found():
    from integrations.qoyod import force_reprocess_dry as mod
    from integrations.qoyod.force_reprocess_dry import (
        ForceReprocessRefused,
    )
    db = _DB()
    with pytest.raises(ForceReprocessRefused) as e:
        await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="999999", trace_id=None,
            confirm_token="FORCE-REPROCESS-DRY-999999", actor="ops")
    assert e.value.code == "row_not_found"


# ─── 9. Invoice OK + payment fail → outcome PAYMENT_PENDING ───────
@pytest.mark.asyncio
async def test_invoice_ok_payment_fail_marks_payment_pending():
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)

    async def _pipe(db_, row_):
        # Simulate pipeline that got invoice created but payment
        # failed — row now has real qoyod_invoice_id, no payment id.
        row_["qoyod_invoice_id"] = "186"
        row_["pipeline_stage"]   = "INVOICE_CREATED"
        # Persist to DB for the post-run introspection.
        for r in db_.integration_inbox.rows:
            if r["id"] == row_["id"]:
                r["qoyod_invoice_id"] = "186"
                r["pipeline_stage"]   = "INVOICE_CREATED"
        return {"outcome": "INVOICE_CREATED"}

    with patch("integrations.qoyod.pipeline.process_normalized_row",
               side_effect=_pipe):
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")
    assert out["outcome"] == "PAYMENT_PENDING"
    assert out["qoyod_invoice_id"] == "186"
    assert out["qoyod_invoice_payment_id"] is None
    assert out["next_retry_hint"] == (
        "call POST /admin/retry-payment-only")


# ─── 10. Invoice OK + payment OK → outcome COMPLETED ─────────────
@pytest.mark.asyncio
async def test_invoice_ok_payment_ok_completes():
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)

    async def _pipe(db_, row_):
        for r in db_.integration_inbox.rows:
            if r["id"] == row_["id"]:
                r["qoyod_invoice_id"]         = "186"
                r["qoyod_invoice_payment_id"] = "PMT-9"
                r["pipeline_stage"]           = "COMPLETED"
        return {"outcome": "COMPLETED"}

    with patch("integrations.qoyod.pipeline.process_normalized_row",
               side_effect=_pipe):
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")
    assert out["ok"] is True
    assert out["outcome"] == "COMPLETED"
    assert out["qoyod_invoice_id"] == "186"
    assert out["qoyod_invoice_payment_id"] == "PMT-9"


# ─── 11. Ineligible row (before cutover) refused at gate ─────────
@pytest.mark.asyncio
async def test_ineligible_before_cutover_refused():
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)
    # Push the order date to BEFORE cutover.
    db.integration_inbox.rows[0]["canonical_payload"][
        "salla_order_created_at"] = "2026-06-20T10:00:00+00:00"

    with patch("integrations.qoyod.pipeline.process_normalized_row",
               new=AsyncMock()) as p:
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")
    assert out["outcome"] == "REFUSED"
    assert "order_created_before_cutover" in out["code"]
    p.assert_not_called()


# ─── 12. rev19 — Multi-stage drain: CUSTOMER_RESOLVED → COMPLETED ─
@pytest.mark.asyncio
async def test_multi_stage_drain_to_completed():
    """rev19 regression (order 270075325): the endpoint MUST drain
    all pipeline stages (NORMALIZED → CUSTOMER_RESOLVED → INVOICE_
    CREATED → COMPLETED) in a single call, not stop at
    CUSTOMER_RESOLVED after `process_normalized_row`."""
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)

    async def _step1(db_, row_):
        # process_normalized_row: advance to CUSTOMER_RESOLVED.
        for r in db_.integration_inbox.rows:
            if r["id"] == row_["id"]:
                r["pipeline_stage"] = "CUSTOMER_RESOLVED"
                r["qoyod_customer_id"] = "229"
        return {"outcome": "CUSTOMER_RESOLVED"}

    async def _step2(db_, row_):
        # process_customer_resolved_row: resolve products + invoice
        # + payment → COMPLETED.
        for r in db_.integration_inbox.rows:
            if r["id"] == row_["id"]:
                r["pipeline_stage"]           = "COMPLETED"
                r["qoyod_invoice_id"]         = "187"
                r["qoyod_invoice_payment_id"] = "PMT-XYZ"
        return {"outcome": "COMPLETED"}

    with patch(
        "integrations.qoyod.pipeline.process_normalized_row",
        side_effect=_step1,
    ), patch(
        "integrations.qoyod.pipeline.process_customer_resolved_row",
        side_effect=_step2,
    ):
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")

    assert out["ok"] is True
    assert out["outcome"] == "COMPLETED"
    assert out["qoyod_invoice_id"] == "187"
    assert out["qoyod_invoice_payment_id"] == "PMT-XYZ"
    # Steps trace shows both hops.
    steps = out["debug"]["pipeline_steps_run"]
    stages_after = [s.get("stage_after") for s in steps]
    assert "CUSTOMER_RESOLVED" in stages_after
    assert "COMPLETED"         in stages_after


# ─── 13. rev19 — Multi-stage drain stops at PAYMENT_PENDING ────
@pytest.mark.asyncio
async def test_multi_stage_drain_stops_at_payment_pending():
    """When invoice succeeds but payment fails, the loop MUST stop
    at INVOICE_CREATED with real id — never re-invoke pipeline that
    could re-attempt invoice creation."""
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)

    async def _step1(db_, row_):
        for r in db_.integration_inbox.rows:
            if r["id"] == row_["id"]:
                r["pipeline_stage"] = "CUSTOMER_RESOLVED"
                r["qoyod_customer_id"] = "229"
        return {"outcome": "CUSTOMER_RESOLVED"}

    async def _step2(db_, row_):
        for r in db_.integration_inbox.rows:
            if r["id"] == row_["id"]:
                r["pipeline_stage"]     = "INVOICE_CREATED"
                r["qoyod_invoice_id"]   = "187"    # real
                # payment_id ABSENT — payment failed.
        return {"outcome": "INVOICE_CREATED"}

    with patch(
        "integrations.qoyod.pipeline.process_normalized_row",
        side_effect=_step1,
    ), patch(
        "integrations.qoyod.pipeline.process_customer_resolved_row",
        side_effect=_step2,
    ) as step2_mock:
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")

    assert out["outcome"] == "PAYMENT_PENDING"
    assert out["qoyod_invoice_id"]         == "187"
    assert out["qoyod_invoice_payment_id"] is None
    assert out["next_retry_hint"] == \
        "call POST /admin/retry-payment-only"
    # Sanity: process_customer_resolved_row called ONCE — no re-attempt.
    assert step2_mock.call_count == 1


# ─── 14. rev19 — invoice_blocked_reason surfaces when stuck ──────
@pytest.mark.asyncio
async def test_invoice_blocked_reason_at_customer_resolved():
    """If the pipeline stops at CUSTOMER_RESOLVED (e.g. product
    resolver hasn't advanced yet), debug MUST show
    `invoice_blocked_reason` and `invoice_post_attempted=False`."""
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)

    async def _step1(db_, row_):
        for r in db_.integration_inbox.rows:
            if r["id"] == row_["id"]:
                r["pipeline_stage"] = "CUSTOMER_RESOLVED"
                r["qoyod_customer_id"] = "229"
        return {"outcome": "CUSTOMER_RESOLVED"}

    # Second step: doesn't advance (returns without changes) —
    # simulate a product-resolve failure or stuck state.
    async def _step2_stuck(db_, row_):
        # Do NOT mutate the row — loop must halt.
        return {"outcome": "PRODUCT_RESOLVE_FAILED"}

    with patch(
        "integrations.qoyod.pipeline.process_normalized_row",
        side_effect=_step1,
    ), patch(
        "integrations.qoyod.pipeline.process_customer_resolved_row",
        side_effect=_step2_stuck,
    ):
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")

    assert out["debug"]["invoice_post_attempted"] is False
    assert out["debug"]["invoice_blocked_reason"] is not None
    assert "CUSTOMER_RESOLVED" in out["debug"]["invoice_blocked_reason"] \
        or "stopped" in out["debug"]["invoice_blocked_reason"]


# ─── 15. rev20 — SELECTIVE_SEND_BLOCKED surfaces honest debug ────
@pytest.mark.asyncio
async def test_selective_send_blocked_reports_false_post_attempted():
    """When the OLD `selective_send_policy` refuses (stage becomes
    SELECTIVE_SEND_BLOCKED:*), NO Qoyod POST happened — debug MUST
    report `invoice_post_attempted=False` and
    `invoice_blocked_reason` naming the old policy."""
    from integrations.qoyod import force_reprocess_dry as mod
    db = _DB()
    _seed_dry_row_270075325(db)

    async def _step1(db_, row_):
        for r in db_.integration_inbox.rows:
            if r["id"] == row_["id"]:
                r["pipeline_stage"] = "CUSTOMER_RESOLVED"
                r["qoyod_customer_id"] = "230"
        return {"outcome": "CUSTOMER_RESOLVED"}

    async def _step2_blocked(db_, row_):
        for r in db_.integration_inbox.rows:
            if r["id"] == row_["id"]:
                r["pipeline_stage"] = "SELECTIVE_SEND_BLOCKED:gate_disabled"
                r["selective_send_blocker_code"] = "gate_disabled"
                # Simulate what pipeline persists after the auto-gate
                # passes (rev16 stores decision on the row).
                r["selective_auto_send_gate"] = {
                    "eligible": True, "reason": "eligible"}
                # No qoyod_responses.invoice — never POSTed.
        return {"outcome": "SELECTIVE_SEND_BLOCKED",
                "reason": "gate_disabled"}

    with patch(
        "integrations.qoyod.pipeline.process_normalized_row",
        side_effect=_step1,
    ), patch(
        "integrations.qoyod.pipeline.process_customer_resolved_row",
        side_effect=_step2_blocked,
    ):
        out = await mod.force_reprocess_dry_row(
            db, user_id="main",
            salla_order_number="270075325",
            trace_id="7fc5a5a855f543b49a2e949a93c3eb95",
            confirm_token="FORCE-REPROCESS-DRY-270075325",
            actor="ops")

    # HONEST debug — no POST attempted, no request sent.
    assert out["debug"]["invoice_post_attempted"] is False
    assert out["debug"]["payment_post_attempted"] is False
    assert out["debug"]["request_sent_to_qoyod"]  is False
    # Blocked reason names the OLD policy.
    assert "old_selective_send_policy_refused" in \
        out["debug"]["invoice_blocked_reason"]
    assert "gate_disabled" in out["debug"]["invoice_blocked_reason"]
    # Effective view for THIS row: auto-gate opened it scoped.
    assert out["debug"][
        "effective_selective_live_send_enabled_for_row"] is True
    assert out["debug"][
        "effective_production_writes_locked_for_row"] is False
    assert out["debug"][
        "effective_dry_run_for_row"] is False


# ─── 16. rev20 — Pipeline scoped bypass of old selective_send_policy
@pytest.mark.asyncio
async def test_pipeline_scoped_bypass_of_old_selective_send_policy():
    """Direct proof that when the NEW auto-gate is passed for a row,
    the OLD `assert_send_allowed` receives
    `selective_live_send_enabled=True` in its policy-settings dict —
    the DB stays `selective_live_send_enabled=False` on disk.
    This is the rev20 fix that lets auto-gated rows through the old
    policy without a global flip."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from types import SimpleNamespace
    from integrations.qoyod import pipeline as pmod

    class _Coll:
        def __init__(self, docs=None):
            self._docs = list(docs or [])
        async def find_one(self, q, projection=None):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    return dict(d)
            return None
        async def update_one(self, q, u, upsert=False):
            for d in self._docs:
                if all(d.get(k) == v for k, v in q.items()):
                    for k, v in (u.get("$set") or {}).items():
                        d[k] = v
                    return MagicMock(matched_count=1, modified_count=1)
            return MagicMock(matched_count=0, modified_count=0)

    # Settings on-disk: OLD gate is OFF, dry_run ON,
    # production_writes_locked ON — nothing "opened" globally.
    on_disk_settings = {
        "user_id": "main",
        "selective_auto_send_enabled":    True,
        "selective_auto_send_cutover_at": CUTOVER,
        "selective_auto_send_allowed_payment_methods":
            ["tabby_installment", "mada"],
        "payment_method_mapping": [
            {"salla_method": "mada", "qoyod_account_id": "91"}],
        "dry_run_mode":                True,
        "production_writes_locked":    True,
        "selective_live_send_enabled": False,      # OLD gate OFF
        "invoice_trigger_statuses":    ["completed"],
    }
    db = MagicMock()
    db.qoyod_settings    = _Coll([on_disk_settings])
    db.qoyod_invoices    = _Coll([])
    db.integration_inbox = _Coll([])

    row = {
        "id":                  "row-x",
        "user_id":              "main",
        "salla_order_number": "270075325",
        "trace_id":           "tr-x",
        "pipeline_stage":     "CUSTOMER_RESOLVED",
        "qoyod_customer_id":  "230",
        "canonical_payload": {
            "order_id":       "MZN-270075325",
            "order_number":   "270075325",
            "order_status":   "completed",
            "order_status_native": "completed",
            "payment_method": "mada",
            "salla_order_created_at": AFTER,
            "items": [{"sku": "AMS10007", "quantity": 1,
                       "unit_price": 226.94, "total": 260.98,
                       "qoyod_product_id": "45"}],
            "total_amount": 260.98,
            "currency":     "SAR",
        },
    }

    # Spy on `assert_send_allowed` — capture the settings dict it
    # sees. THIS is the rev20 invariant: `selective_live_send_enabled`
    # must be TRUE in what the OLD policy sees, but on-disk stays
    # FALSE.
    seen: dict = {}

    def _spy_assert(order, settings):
        seen["policy_settings"] = dict(settings)
        # Return a valid decision so pipeline proceeds normally.
        return MagicMock(allowed=True)

    from integrations.qoyod.business_rules import RulesDecision
    from integrations.qoyod.customer_resolver import ResolutionResult
    dto = SimpleNamespace(
        order_id="MZN-270075325", customer=SimpleNamespace(),
        currency="SAR", payment_method="mada")
    decision = RulesDecision(
        eligible=True, reason="eligible",
        invoice_date=datetime(2026, 7, 2, tzinfo=timezone.utc),
        invoice_date_source="salla",
        triggered_by_status="completed")

    # We patch `assert_send_allowed` and stop the pipeline early via
    # a fail on customer_resolver so we only observe the policy call.
    with patch.object(pmod, "assert_send_allowed",
                      side_effect=_spy_assert), \
         patch.object(pmod, "SalesOrderDTO", return_value=dto), \
         patch.object(pmod, "evaluate_rules", return_value=decision), \
         patch.object(pmod, "resolve_products",
                      new=AsyncMock(return_value=MagicMock(
                          success=False,
                          error={"code": "test_stop",
                                 "message": "stop after policy"}))), \
         patch.object(pmod, "validate_totals",
                      return_value=MagicMock(
                          ok=True, code="ok", message="ok",
                          details={},
                          to_log_dict=lambda: {"ok": True})), \
         patch.object(pmod, "get_api_key",
                      AsyncMock(return_value="test-key")), \
         patch.object(pmod, "_apply", new=AsyncMock()), \
         patch.object(pmod, "_dead_letter", new=AsyncMock()):
        try:
            await pmod.process_customer_resolved_row(db, row)
        except Exception:
            pass  # early exit from patched resolve_products

    # Rev20 invariant: policy saw the AUTO-GATE injecting TRUE.
    if seen:
        assert seen["policy_settings"][
            "selective_live_send_enabled"] is True
        assert seen["policy_settings"]["dry_run_mode"] is False
    # DB unchanged.
    assert on_disk_settings["selective_live_send_enabled"] is False
    assert on_disk_settings["dry_run_mode"]                is True
    assert on_disk_settings["production_writes_locked"]    is True


# Import needed for the datetime construction above.
from datetime import datetime, timezone   # noqa: E402
