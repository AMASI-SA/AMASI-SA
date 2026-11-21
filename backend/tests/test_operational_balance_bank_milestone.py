"""Current order status and historical cutover evidence for operational bank receipts."""
from copy import deepcopy
from datetime import datetime
from unittest import TestCase

from operational_bank_order_milestone import (
    HISTORY_MISSING, HISTORY_UNPROVEN, PROOF_FIELD, SOURCE_INVALID, bank_status_evidence, history_proof,
    reached_in_progress,
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
    def test_four_current_statuses_and_their_aliases_independently_qualify(self):
        for state, aliases in (
            ("in_progress", ("in_progress", "processing", "in progress", "قيد التنفيذ", "جاري التنفيذ")),
            ("completed", ("completed", "تم التنفيذ")),
            ("delivering", ("delivering", "shipping", "out_for_delivery", "in_delivery", "جاري التوصيل",
                            "جارٍ التوصيل", "قيد التوصيل")),
            ("delivered", ("delivered", "تم التوصيل")),
        ):
            for alias in aliases:
                with self.subTest(alias=alias):
                    s = snapshot(alias)
                    raw = s["raw_by_source"]["salla_direct"]
                    self.assertEqual(bank_status_evidence(raw, s, **SCOPE),
                        {"status": state, "observed_at": "2026-11-16T18:53:00+00:00", "source": "salla_order_status"})
                    with self.assertRaisesRegex(ValueError, HISTORY_MISSING):
                        bank_status_evidence(raw, s, require_history=True, **SCOPE)

    def test_history_dates_first_qualifying_status_even_when_preparation_is_later(self):
        s = snapshot()
        raw = s["raw_by_source"]["salla_direct"]
        earlier = {"id": "earliest", "type": "status_changed", "new_status": "تم التنفيذ",
                   "created_at": "2026-11-13T16:00:00+03:00"}
        for rows in ([native_event(), earlier], [earlier, native_event()]):
            s[PROOF_FIELD] = history_proof(raw, s, rows, **SCOPE)
            self.assertEqual(bank_status_evidence(raw, s, require_history=True, **SCOPE),
                {"source": "salla_order_history", "event_id": "earliest", "status": "completed",
                 "observed_at": "2026-11-13T13:00:00+00:00"})

    def test_customized_status_determines_admission_and_parent_cannot_override_review(self):
        s = snapshot()
        raw = s["raw_by_source"]["salla_direct"]
        s.update(order_status="تم التنفيذ", order_status_slug="completed")
        raw["status_slug"] = "completed"
        raw["status"] = {"slug": "completed", "name": "تم التنفيذ",
                         "customized": {"slug": "reviewed", "name": "تمت المراجعة"}}
        self.assertIsNone(bank_status_evidence(raw, s, **SCOPE))
        raw["status"]["customized"] = {"slug": "in_delivery", "name": "جاري التوصيل"}
        self.assertEqual(bank_status_evidence(raw, s, **SCOPE)["status"], "delivering")

    def test_legacy_history_does_not_establish_first_of_four_statuses_for_cutover(self):
        s = snapshot()
        raw = s["raw_by_source"]["salla_direct"]
        for rows in ([], [native_event()]):
            legacy = history_proof(raw, s, rows, **SCOPE)
            legacy.pop("policy_version")
            legacy.pop("status", None)
            s[PROOF_FIELD] = legacy
            self.assertEqual(bank_status_evidence(raw, s, **SCOPE)["status"], "delivered")
            with self.assertRaisesRegex(ValueError, HISTORY_MISSING):
                bank_status_evidence(raw, s, require_history=True, **SCOPE)

    def test_current_status_does_not_invent_a_first_transition_time(self):
        s = snapshot("in_progress")
        raw = s["raw_by_source"]["salla_direct"]
        with self.assertRaisesRegex(ValueError, HISTORY_MISSING):
            reached_in_progress(raw, s, require_history=True, **SCOPE)
        s[PROOF_FIELD] = history_proof(raw, s, [native_event()], **SCOPE)
        self.assertTrue(reached_in_progress(raw, s, require_history=True, **SCOPE))
        raw["cancelled"] = True
        with self.assertRaisesRegex(ValueError, SOURCE_INVALID):
            reached_in_progress(raw, s, require_history=True, **SCOPE)

    def test_waiting_review_payment_and_reversed_status_do_not_start_a_new_receipt(self):
        source = snapshot()
        raw = source["raw_by_source"]["salla_direct"]
        source[PROOF_FIELD] = history_proof(raw, source, [native_event()], **SCOPE)
        for status in ("under_review", "reviewed", "تمت المراجعة", "payment_pending", "cancelled", "restored", "shipped", "تم الشحن", "unknown"):
            raw["status"] = {"slug": status}
            self.assertFalse(reached_in_progress(raw, source, **SCOPE))

    def test_confirmed_cancellation_explains_unapproved_status_even_with_prior_history(self):
        s = snapshot()
        raw = s["raw_by_source"]["salla_direct"]
        s[PROOF_FIELD] = history_proof(raw, s, [native_event()], **SCOPE)
        raw.update(status={"name": "ملغي", "slug": "canceled"}, is_cancelled=True)
        s.update(order_status_slug="cancelled", order_status="ملغى")
        s["g47_salla_snapshot"].update(cancelled=True, component_pending=True)
        self.assertIsNone(bank_status_evidence(raw, s, **SCOPE))
        self.assertIsNone(bank_status_evidence(raw, s, require_history=True, **SCOPE))
        s["salla_order_id"] = "other"
        with self.assertRaisesRegex(ValueError, SOURCE_INVALID):
            bank_status_evidence(raw, s, **SCOPE)

    def test_stale_conflicting_and_foreign_snapshots_never_qualify(self):
        for field, value in (("salla_order_id", "other"), ("order_number", "other"), ("user_id", "other"),
                             ("order_status_slug", "reviewed"), ("g47_salla_snapshot", {"requires_authoritative_refresh": True}),
                             ("last_salla_direct_status_source_updated_at", "2026-11-21T00:00:00Z")):
            s = {**snapshot("in_progress"), field: value}
            with self.assertRaisesRegex(ValueError, SOURCE_INVALID):
                reached_in_progress(s["raw_by_source"]["salla_direct"], s, **SCOPE)

    def test_activities_cannot_promote_notes_or_conflicting_metadata(self):
        for patch in ({"type": "comment"}, {"type": "comment", "new_status": "in_progress"},
                      {"note": "قيد التنفيذ", "new_status": "in_progress"},
                      {"note": "قيد التنفيذ"}, {"type": None, "new_status": "in_progress"}):
            s = snapshot()
            raw = s["raw_by_source"]["salla_direct"]
            s[PROOF_FIELD] = history_proof(raw, s, [{**native_event(), **patch}], **SCOPE)
            with self.assertRaisesRegex(ValueError, HISTORY_UNPROVEN):
                reached_in_progress(raw, s, require_history=True, **SCOPE)
        s = snapshot()
        raw = s["raw_by_source"]["salla_direct"]
        for patch in ({"customized": {"slug": "reviewed"}}, {"status": "تمت المراجعة"},
                      {"new_status": "reviewed", "to_status": "in_progress"}, {"order_id": "other"},
                      {"reference_id": "other"}, {"created_at": "2026-11-17T00:00:00Z"},
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
                reached_in_progress(raw, s, require_history=True, **SCOPE)
        s[PROOF_FIELD] = history_proof(raw, s, [], **SCOPE)
        raw["updated_at"] = "2026-11-17T00:00:00Z"
        s["g47_salla_snapshot"]["source_updated_at"] = raw["updated_at"]
        with self.assertRaisesRegex(ValueError, HISTORY_MISSING):
            reached_in_progress(raw, s, require_history=True, **SCOPE)


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
        assert bank_status_evidence(current["raw_by_source"]["salla_direct"], current,
                                    require_history=True, **SCOPE)["status"] == "in_progress"
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


def test_missing_bank_identity_recovered_for_newly_supported_native_tenders(monkeypatch):
    from salla_integration import bank_payment_sync
    from test_operational_balance_bank_tender_proof import order
    from test_operational_balance_integration import run

    async def scenario(db):
        calls, details = [], {}
        monkeypatch.setattr(bank_payment_sync, "datetime", _Clock)
        for index, mixed in enumerate((True, False)):
            raw = order(mixed)
            number, identity = str(1720 + index), str(51720 + index)
            raw.update(id=identity, reference_id=number)
            document = {"user_id": "owner", "order_number": number, "salla_order_id": identity,
                        "g47_salla_snapshot": {"source_updated_at": raw["updated_at"]},
                        "raw_by_source": {"salla_direct": deepcopy(raw)}}
            await db.unified_orders.insert_one(document)
            details[identity] = {**raw, "bank": {"id": "123", "bank_name": "مصرف الراجحي"}}

        async def read(scoped, owner, method, path):
            assert owner == "owner" and method == "GET"
            identity = path.removeprefix("/orders/")
            calls.append(identity)
            return {"data": deepcopy(details[identity])}
        monkeypatch.setattr(bank_payment_sync, "call_salla", read)
        await bank_payment_sync.acquire_missing(db, "owner", [
            {"code": "operational_order_bank_binding_incomplete", "source_id": identity} for identity in details])
        assert set(calls) == set(details) and len(calls) == 2
        for identity, raw in details.items():
            current = await db.unified_orders.find_one({"user_id": "owner", "salla_order_id": identity})
            assert current["raw_by_source"]["salla_direct"] == raw
            assert PROOF_FIELD not in current
    run(scenario)

