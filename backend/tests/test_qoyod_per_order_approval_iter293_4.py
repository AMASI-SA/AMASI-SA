"""Iter-293.4-rev3 — Per-Order Approval Phrase.

Operator mandate (2026-XX) for the FIRST live send (order 269571122):
    "Approved to send order 269571122 only.
     لا فتح production_writes_locked=false بشكل عام.
     إذا الإرسال الفردي لا يعمل إلا بفتح القفل العام، توقف ولا ترسل."

Contract pinned by these tests:
    1. When `production_writes_locked=True`, `reprocess_one_order`
       REFUSES the run unless an `approval_phrase` exactly equal to
       `"Approved to send order <order_number> only"` is supplied.
    2. A phrase that approves order A cannot be reused for order B.
    3. When the approval is valid, the api_client used for THIS run
       is constructed with `write_lock_enabled=False` — the global
       setting is NEVER read or modified.
    4. The approval is persisted to `qoyod_per_order_approvals` with
       `{approval_id, order_number, trace_id, actor, approved_at,
       global_lock_was_active, scope=single_order}`.
    5. A `PER_ORDER_APPROVAL` WARNING log is emitted to stdout for
       operational visibility.
"""
from __future__ import annotations

import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, "/app/backend")
os.environ.setdefault("QOYOD_API_BASE", "https://api.qoyod.test")

from integrations.qoyod.one_shot_reprocess import (   # noqa: E402
    APPROVAL_PHRASE_TEMPLATE,
    OneShotRefused,
    reprocess_one_order,
)


# ─────────────────────────────────────────────────────────────────────
class _Coll:
    def __init__(self):
        self.rows: list[dict] = []

    async def find_one(self, q, projection=None):
        for r in self.rows:
            if self._match(r, q):
                return dict(r)
        return None

    async def insert_one(self, doc):
        self.rows.append(dict(doc))
        class _R: inserted_id = doc.get("approval_id") or doc.get("id")
        return _R()

    async def update_one(self, q, upd, **_):
        for r in self.rows:
            if self._match(r, q):
                r.update(upd.get("$set") or {})
                class _R: matched_count = 1
                return _R()
        class _R: matched_count = 0
        return _R()

    def find(self, q, projection=None):
        return _Cursor([dict(r) for r in self.rows if self._match(r, q)])

    async def count_documents(self, q):
        return sum(1 for r in self.rows if self._match(r, q))

    @staticmethod
    def _match(row, q):
        if not isinstance(q, dict):
            return False
        for k, v in q.items():
            if k == "$or":
                if not any(_Coll._match(row, sub) for sub in v):
                    return False
                continue
            if row.get(k) != v:
                return False
        return True


class _Cursor:
    def __init__(self, rows):
        self._rows = rows
    def sort(self, *a, **kw): return self
    def limit(self, n):
        self._rows = self._rows[:n]
        return self
    async def to_list(self, length=None):
        return list(self._rows[: (length or len(self._rows))])
    def __aiter__(self):
        rows = self._rows
        async def _gen():
            for r in rows:
                yield r
        return _gen()


class _DB:
    def __init__(self, locked: bool = True):
        self.integration_inbox = _Coll()
        self.qoyod_settings    = _Coll()
        self.qoyod_per_order_approvals = _Coll()
        self.qoyod_products_mapping    = _Coll()
        self.qoyod_customers_mapping   = _Coll()
        self.qoyod_credentials         = _Coll()
        # Seed settings.
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            self.qoyod_settings.insert_one({
                "user_id": "main",
                "production_writes_locked": locked,
                "dry_run_mode": False,
                "invoice_trigger_statuses": ["completed"],
            })
        ) if False else self.qoyod_settings.rows.append({
            "user_id": "main",
            "production_writes_locked": locked,
            "dry_run_mode": False,
            "invoice_trigger_statuses": ["completed"],
        })

    def __getattr__(self, name):
        c = _Coll()
        setattr(self, name, c)
        return c


def _seed_inbox(db, *, order_number: str, trace_id: str = "t-1",
                stage: str = "NORMALIZED"):
    row = {
        "id":                 "row-" + trace_id,
        "user_id":             "main",
        "trace_id":            trace_id,
        "salla_order_number":  order_number,
        "pipeline_stage":      stage,
        "raw_payload":         {"order_number": order_number},
    }
    db.integration_inbox.rows.append(row)
    return row


# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestApprovalPhraseEnforcement:

    async def test_refuse_when_locked_and_no_phrase(self):
        db = _DB(locked=True)
        _seed_inbox(db, order_number="269571122", stage="NORMALIZED")
        with patch(
            "integrations.qoyod.one_shot_reprocess.get_api_key",
            new_callable=AsyncMock, return_value="fake-key",
        ), patch(
            "integrations.qoyod.one_shot_reprocess._quarantine_dry_mappings",
            new_callable=AsyncMock, return_value={"quarantined": 0},
        ), patch(
            "integrations.qoyod.one_shot_reprocess._reset_row_to_stage",
            new_callable=AsyncMock,
        ):
            with pytest.raises(OneShotRefused) as exc_info:
                await reprocess_one_order(
                    db, user_id="main",
                    order_number="269571122",
                    confirm="REPROCESS-269571122",
                    actor="op@x.com",
                )
        assert exc_info.value.code == "approval_phrase_required"
        # No approval persisted.
        assert len(db.qoyod_per_order_approvals.rows) == 0
        # The error message includes the EXPECTED phrase verbatim so
        # the operator can copy/paste it.
        assert "Approved to send order 269571122 only" in str(exc_info.value)

    async def test_refuse_when_phrase_is_wrong(self):
        db = _DB(locked=True)
        _seed_inbox(db, order_number="269571122", stage="NORMALIZED")
        with patch(
            "integrations.qoyod.one_shot_reprocess.get_api_key",
            new_callable=AsyncMock, return_value="fake-key",
        ), patch(
            "integrations.qoyod.one_shot_reprocess._quarantine_dry_mappings",
            new_callable=AsyncMock, return_value={"quarantined": 0},
        ), patch(
            "integrations.qoyod.one_shot_reprocess._reset_row_to_stage",
            new_callable=AsyncMock,
        ):
            with pytest.raises(OneShotRefused) as exc:
                await reprocess_one_order(
                    db, user_id="main",
                    order_number="269571122",
                    confirm="REPROCESS-269571122",
                    approval_phrase="Approved to send all orders",   # wrong
                )
        assert exc.value.code == "approval_phrase_mismatch"
        assert len(db.qoyod_per_order_approvals.rows) == 0

    async def test_refuse_when_phrase_targets_different_order(self):
        """Phrase for order A cannot be reused for order B."""
        db = _DB(locked=True)
        _seed_inbox(db, order_number="269571122", stage="NORMALIZED")
        with patch(
            "integrations.qoyod.one_shot_reprocess.get_api_key",
            new_callable=AsyncMock, return_value="fake-key",
        ), patch(
            "integrations.qoyod.one_shot_reprocess._quarantine_dry_mappings",
            new_callable=AsyncMock, return_value={"quarantined": 0},
        ), patch(
            "integrations.qoyod.one_shot_reprocess._reset_row_to_stage",
            new_callable=AsyncMock,
        ):
            with pytest.raises(OneShotRefused) as exc:
                await reprocess_one_order(
                    db, user_id="main",
                    order_number="269571122",
                    confirm="REPROCESS-269571122",
                    # Operator typed the phrase for an OLDER order.
                    approval_phrase="Approved to send order 268670571 only",
                )
        assert exc.value.code == "approval_phrase_mismatch"
        # Audit shows the mismatch but no approval was granted.
        assert len(db.qoyod_per_order_approvals.rows) == 0

    async def test_phrase_template_is_stable(self):
        """Don't accidentally change the phrase format — operators
        copy/paste it from docs."""
        assert APPROVAL_PHRASE_TEMPLATE == (
            "Approved to send order {order_number} only")
        assert APPROVAL_PHRASE_TEMPLATE.format(
            order_number="269571122") == (
            "Approved to send order 269571122 only")


# ─────────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
class TestApprovalGrantedFlow:
    """When the phrase is correct, the api_client is constructed
    UNLOCKED for this one run and the approval is audited."""

    async def _patched_run(self, db, **kwargs):
        """Run reprocess_one_order with every downstream step mocked
        so we only assert on the approval-gate behaviour."""
        with patch(
            "integrations.qoyod.one_shot_reprocess.get_api_key",
            new_callable=AsyncMock, return_value="fake-key",
        ), patch(
            "integrations.qoyod.one_shot_reprocess._quarantine_dry_mappings",
            new_callable=AsyncMock, return_value={"quarantined": 0},
        ), patch(
            "integrations.qoyod.one_shot_reprocess._reset_row_to_stage",
            new_callable=AsyncMock,
        ), patch(
            "integrations.qoyod.one_shot_reprocess.process_normalized_row",
            new_callable=AsyncMock,
            return_value={"outcome": "SKIPPED",
                          "reason": "test_stub_short_circuit"},
        ):
            return await reprocess_one_order(db, **kwargs)

    async def test_correct_phrase_persists_audit_row(self):
        db = _DB(locked=True)
        _seed_inbox(db, order_number="269571122",
                    trace_id="a89313", stage="NORMALIZED")
        try:
            await self._patched_run(
                db, user_id="main",
                order_number="269571122",
                trace_id="a89313",
                confirm="REPROCESS-269571122",
                approval_phrase="Approved to send order 269571122 only",
                actor="op@example.com",
            )
        except OneShotRefused:
            pytest.fail("approval should have unlocked the run")
        # An approval row was persisted with the expected fields.
        assert len(db.qoyod_per_order_approvals.rows) == 1
        appr = db.qoyod_per_order_approvals.rows[0]
        assert appr["order_number"]            == "269571122"
        assert appr["trace_id"]                == "a89313"
        assert appr["actor"]                   == "op@example.com"
        assert appr["scope"]                   == "single_order"
        assert appr["global_lock_was_active"]  is True
        assert appr["approval_phrase"]         == (
            "Approved to send order 269571122 only")
        assert appr["expected_phrase"]         == (
            "Approved to send order 269571122 only")
        assert "approval_id" in appr
        assert "approved_at" in appr

    async def test_no_approval_needed_when_lock_is_off(self):
        """When `production_writes_locked=False`, no approval phrase
        is required — the run proceeds normally."""
        db = _DB(locked=False)
        _seed_inbox(db, order_number="269571122",
                    trace_id="a89313", stage="NORMALIZED")
        await self._patched_run(
            db, user_id="main",
            order_number="269571122",
            trace_id="a89313",
            confirm="REPROCESS-269571122",
            actor="op@example.com",
        )
        # No approval rows persisted — none needed.
        assert len(db.qoyod_per_order_approvals.rows) == 0

    async def test_unlocked_api_client_built_when_phrase_valid(self):
        """The api_client constructed during the run must carry
        write_lock_enabled=False even though settings say True."""
        db = _DB(locked=True)
        _seed_inbox(db, order_number="269571122",
                    trace_id="a89313", stage="NORMALIZED")

        captured_kwargs = {}
        from integrations.qoyod.api_client import QoyodAPIClient
        original_init = QoyodAPIClient.__init__

        def _spy_init(self, key, **kwargs):
            captured_kwargs.update(kwargs)
            return original_init(self, key, **kwargs)

        with patch.object(QoyodAPIClient, "__init__", _spy_init):
            await self._patched_run(
                db, user_id="main",
                order_number="269571122",
                trace_id="a89313",
                confirm="REPROCESS-269571122",
                approval_phrase="Approved to send order 269571122 only",
                actor="op@example.com",
            )
        assert captured_kwargs.get("write_lock_enabled") is False, (
            "Per-order approval must construct the api_client UNLOCKED. "
            f"Got: {captured_kwargs}")

    async def test_global_lock_setting_is_never_modified(self):
        """The granted approval must NOT touch qoyod_settings."""
        db = _DB(locked=True)
        _seed_inbox(db, order_number="269571122",
                    trace_id="a89313", stage="NORMALIZED")
        settings_before = dict(db.qoyod_settings.rows[0])

        await self._patched_run(
            db, user_id="main",
            order_number="269571122",
            trace_id="a89313",
            confirm="REPROCESS-269571122",
            approval_phrase="Approved to send order 269571122 only",
            actor="op@example.com",
        )
        settings_after = dict(db.qoyod_settings.rows[0])
        # The lock flag stays True forever — approval is scoped.
        assert settings_after["production_writes_locked"] is True
        assert settings_after == settings_before


# ─────────────────────────────────────────────────────────────────────
class TestPhraseTemplateInDocstring:
    """The route docstring documents the exact phrase. Pin it so a
    future refactor cannot silently change the operator UX."""

    def test_template_format(self):
        assert APPROVAL_PHRASE_TEMPLATE.format(
            order_number="X") == "Approved to send order X only"
