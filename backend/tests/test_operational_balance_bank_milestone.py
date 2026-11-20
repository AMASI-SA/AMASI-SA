"""Order-scoped Salla preparation is the gate for operational bank receipts."""
from copy import deepcopy
from datetime import datetime
from unittest import TestCase

from operational_bank_order_milestone import (
    HISTORY_MISSING, HISTORY_UNPROVEN, PROOF_FIELD, SOURCE_INVALID, history_proof, reached_in_progress,
)


SCOPE = {"owner": "owner", "order_number": "9001", "provider_order_id": "81501",
         "as_of": "2026-11-20T19:00:00+03:00"}
CREATED = "2026-11-13T13:00:00+03:00"
UPDATED = "2026-11-16T21:53:00+03:00"


class _Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls.fromisoformat(SCOPE["as_of"]).astimezone(tz)


def snapshot(status="delivered"):
    raw = {"id": 81501, "reference_id": 9001, "date": CREATED, "updated_at": UPDATED,
           "status": {"slug": status}, "payment_method": "bank", "payment_methods": [{"payment_method": "bank"}]}
    return {"user_id": "owner", "order_number": "9001", "salla_order_id": "81501",
            "raw_by_source": {"salla_direct": raw}, "g47_salla_snapshot": {"source_updated_at": UPDATED}}


def native_event():
    return {"id": "1123991", "type": "activity", "note": "", "status": "قيد التنفيذ",
            "customized": {"name": "قيد التنفيذ", "slug": "in_progress"},
            "created_at": {"date": "2026-11-13 19:27:52.000000", "timezone": "Asia/Riyadh"}}


class BankMilestoneTests(TestCase):
    def test_current_and_historical_milestones_are_distinct(self):
        for status in ("in_progress", "قيد التنفيذ"):
            s = snapshot(status)
            self.assertTrue(reached_in_progress(s["raw_by_source"]["salla_direct"], s, **SCOPE))
        for status in ("completed", "delivering", "delivered"):
            s = snapshot(status)
            raw = s["raw_by_source"]["salla_direct"]
            with self.assertRaisesRegex(ValueError, HISTORY_MISSING):
                reached_in_progress(raw, s, **SCOPE)
            s[PROOF_FIELD] = history_proof(raw, s, [native_event()], **SCOPE)
            self.assertTrue(reached_in_progress(raw, s, **SCOPE))
            self.assertEqual(s[PROOF_FIELD]["at"], "2026-11-13T16:27:52+00:00")

    def test_waiting_review_payment_and_reversed_status_do_not_start_a_new_receipt(self):
        source = snapshot()
        raw = source["raw_by_source"]["salla_direct"]
        source[PROOF_FIELD] = history_proof(raw, source, [native_event()], **SCOPE)
        for status in ("under_review", "reviewed", "تمت المراجعة", "payment_pending", "cancelled", "restored", "unknown"):
            raw["status"] = {"slug": status}
            self.assertFalse(reached_in_progress(raw, source, **SCOPE))

    def test_stale_conflicting_and_foreign_snapshots_never_qualify(self):
        for field, value in (("salla_order_id", "other"), ("order_number", "other"), ("user_id", "other"),
                             ("order_status_slug", "reviewed"), ("g47_salla_snapshot", {"requires_authoritative_refresh": True}),
                             ("last_salla_direct_status_source_updated_at", "2026-11-21T00:00:00Z")):
            s = {**snapshot("in_progress"), field: value}
            with self.assertRaisesRegex(ValueError, SOURCE_INVALID):
                reached_in_progress(s["raw_by_source"]["salla_direct"], s, **SCOPE)

    def test_activities_cannot_promote_notes_or_conflicting_metadata(self):
        for patch in ({"type": "comment"}, {"note": "قيد التنفيذ"}, {"customized": {"slug": "reviewed"}},
                      {"status": "تمت المراجعة"}, {"new_status": "reviewed", "to_status": "in_progress"}):
            s = snapshot()
            raw = s["raw_by_source"]["salla_direct"]
            s[PROOF_FIELD] = history_proof(raw, s, [{**native_event(), **patch}], **SCOPE)
            with self.assertRaisesRegex(ValueError, HISTORY_UNPROVEN):
                reached_in_progress(raw, s, **SCOPE)
        s = snapshot()
        raw = s["raw_by_source"]["salla_direct"]
        for patch in ({"order_id": "other"}, {"reference_id": "other"}, {"created_at": "2026-11-17T00:00:00Z"},
                      {"created_at": "2026-10-17T00:00:00Z"}, {"id": ""}):
            with self.assertRaisesRegex(ValueError, HISTORY_UNPROVEN):
                history_proof(raw, s, [{**native_event(), **patch}], **SCOPE)

    def test_proof_scope_and_time_are_checked_each_time_it_is_read(self):
        s = snapshot()
        raw = s["raw_by_source"]["salla_direct"]
        proof = history_proof(raw, s, [native_event()], **SCOPE)
        for key, value in (("source", "workflow"), ("owner", "other"), ("order_id", "other"),
                           ("order_number", "other"), ("order_created_at", "2026-11-01T12:00:00Z"),
                           ("source_updated_at", "2026-11-21T00:00:00Z"), ("event_id", "")):
            s[PROOF_FIELD] = {**proof, key: value}
            with self.assertRaisesRegex(ValueError, HISTORY_UNPROVEN):
                reached_in_progress(raw, s, **SCOPE)
        s[PROOF_FIELD] = history_proof(raw, s, [], **SCOPE)
        raw["updated_at"] = "2026-11-17T00:00:00Z"
        s["g47_salla_snapshot"]["source_updated_at"] = raw["updated_at"]
        with self.assertRaisesRegex(ValueError, HISTORY_MISSING):
            reached_in_progress(raw, s, **SCOPE)


def test_visible_adapter_acquires_only_missing_current_order_proof(monkeypatch):
    from salla_integration import bank_payment_sync, courier_delivery_sync
    from test_operational_balance_integration import run
    monkeypatch.setattr(bank_payment_sync, "datetime", _Clock)

    async def scenario(db):
        original = snapshot()
        await db.unified_orders.insert_one(deepcopy(original))
        calls = []

        async def complete(scoped, owner, path, params, *, max_pages):
            assert owner == "owner" and max_pages <= 3 and not params
            calls.append(path)
            return [native_event()]
        monkeypatch.setattr(courier_delivery_sync, "_complete_pages", complete)
        pending = [{"code": HISTORY_MISSING, "source_id": "81501"}]
        await bank_payment_sync.acquire_missing(db, "other", pending)
        await bank_payment_sync.acquire_missing(db, "owner", [])
        assert not calls
        await bank_payment_sync.acquire_missing(db, "owner", pending)
        current = await db.unified_orders.find_one({"user_id": "owner"}, {"_id": 0})
        assert reached_in_progress(current["raw_by_source"]["salla_direct"], current, **SCOPE)
        current.pop(PROOF_FIELD)
        current.pop(bank_payment_sync._RETRY)
        assert original == current
        await bank_payment_sync.acquire_missing(db, "owner", pending)
        assert calls == ["/orders/81501/histories"]
    run(scenario)


def test_history_read_cannot_rebind_concurrently_changed_source(monkeypatch):
    from salla_integration import bank_payment_sync, courier_delivery_sync
    from test_operational_balance_integration import run
    monkeypatch.setattr(bank_payment_sync, "datetime", _Clock)

    async def scenario(db):
        await db.unified_orders.insert_one(snapshot())

        async def complete(scoped, owner, path, params, *, max_pages):
            await db.unified_orders.update_one({"order_number": "9001"},
                {"$set": {"raw_by_source.salla_direct.status": {"slug": "payment_pending"}}})
            return [native_event()]
        monkeypatch.setattr(courier_delivery_sync, "_complete_pages", complete)
        await bank_payment_sync.acquire_missing(db, "owner", [{"code": HISTORY_MISSING, "source_id": "81501"}])
        current = await db.unified_orders.find_one({"user_id": "owner"})
        assert PROOF_FIELD not in current
    run(scenario)

