"""Supplier receipt delivery uses current custody, not component approval."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import assembly_completion_delivery as delivery
import assembly_status_policy as statuses
import fulfillment_carrier_label as carrier_labels
from order_engine.mapper import map_salla_order
from supplier_receipt_ready_policy import POLICY


_ABSENT = object()


def _get(row, path):
    for key in path.split("."):
        if not isinstance(row, dict) or key not in row:
            return _ABSENT
        row = row[key]
    return row


def _matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(row, part) for part in expected):
                return False
            continue
        actual = _get(row, key)
        if isinstance(expected, dict):
            if "$ne" in expected and actual == expected["$ne"]:
                return False
            if "$exists" in expected and (actual is not _ABSENT) != expected["$exists"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
        elif expected is None and actual is _ABSENT:
            continue
        elif actual != expected:
            return False
    return True


def _set(row, path, value):
    keys = path.split(".")
    for key in keys[:-1]:
        row = row.setdefault(key, {})
    row[keys[-1]] = deepcopy(value)


class _Collection:
    def __init__(self, rows):
        self.rows = rows
        self.updates = []

    async def find_one(self, query, projection=None):
        row = next((deepcopy(row) for row in self.rows if _matches(row, query)), None)
        if row is not None and projection and any(projection.values()):
            selected = {}
            for path, include in projection.items():
                value = _get(row, path)
                if include and value is not _ABSENT:
                    _set(selected, path, value)
            return selected
        return row

    def find(self, query, projection=None):
        rows = [deepcopy(row) for row in self.rows if _matches(row, query)]

        async def to_list(limit):
            return rows if limit is None else rows[:limit]

        return SimpleNamespace(to_list=to_list)

    async def update_one(self, query, patch):
        self.updates.append((deepcopy(query), deepcopy(patch)))
        row = next((row for row in self.rows if _matches(row, query)), None)
        if row is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        for path, value in patch.get("$set", {}).items():
            _set(row, path, value)
        for path, increment in patch.get("$inc", {}).items():
            prior = _get(row, path)
            _set(row, path, (0 if prior is _ABSENT else prior) + increment)
        return SimpleNamespace(matched_count=1, modified_count=1)


class _Database:
    def __init__(self, source, workflow, pieces):
        self.collections = {
            "unified_orders": _Collection([source]),
            "order_review_workflows": _Collection([workflow]),
            "mezan_preparation_pieces_v1": _Collection(pieces),
        }
        self.reads = []

    def __getitem__(self, name):
        self.reads.append(name)
        if name not in self.collections:
            raise AssertionError("Receipt delivery must not access " + name)
        return self.collections[name]

    def __getattr__(self, name):
        return self[name]


class SupplierReceiptDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.instant = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.source = {
            "user_id": "owner", "order_number": "410001", "order_status": "in_progress",
            "raw_by_source": {"salla_direct": {
                "id": 101, "reference_id": "410001", "status": {"slug": "in_progress"},
                "created_at": "2030-01-01T00:00:00Z",
                "currency": "SAR", "payment_method": "mada", "payment_status": "paid",
                "items": [{"id": 201, "quantity": 1,
                           "product": {"id": 301, "name": "Synthetic sweater"}}],
                "shipping_address": {"city": "Riyadh"},
            }},
            "g47_salla_snapshot": {"component_pending": True, "requires_authoritative_refresh": True},
        }
        identity = map_salla_order(self.source["raw_by_source"]["salla_direct"]).items[0].order_item_id
        self.piece = {
            "user_id": "owner", "order_number": "410001", "piece_id": "supplier-piece",
            "order_item_id": identity, "unit_index": 1, "status": "ready_for_assembly",
            "supplier_dispatch_status": "received", "received_at": self.instant,
            "preparation_receipt_status": "received", "preparation_received_at": self.instant,
            "preparation_received_by": "receiver", "services": [], "assembly_status": "ready",
            "assembly_ready_basis": POLICY,
        }
        self.workflow = {
            "user_id": "owner", "order_number": "410001", "revision": 4,
            "stage": "completed", "assembly_status": "completed", "assembly_completion_basis": POLICY,
            "completion_mode": "mezan_local_v1", "assembly_ready_piece_count": 1,
            "assembly_piece_count": 1, "items": [], "operational_items": [],
            "shipping_print_batch_id": "synthetic-batch",
        }
        self.workflow[delivery.FIELD] = {
            "version": 2, "state": "pending", "attempts": 1,
            "claim": "synthetic-claim", "lease_until": (self.instant + timedelta(minutes=5)).isoformat(),
            "ready_basis": POLICY, "workflow_revision": 4, "status_attempted": False,
            "awb_attempted": False, "order_confirmed": False,
            "source_fingerprint": delivery.source_fingerprint(self.source, supplier_receipt=True),
            "workflow_fingerprint": delivery.workflow_fingerprint(self.workflow),
        }
        self.operation = deepcopy(self.workflow)
        self.db = _Database(self.source, self.workflow, [self.piece])

        async def owner_scope(db, owner, action):
            self.assertEqual(owner, "owner")
            return await action(db)

        self.patches = [
            patch.object(delivery, "now", lambda: self.instant),
            patch.object(delivery, "operational_owner", owner_scope),
            patch.object(delivery.shipping, "_label_baseline", AsyncMock(return_value={"shipment": "one"})),
        ]
        for patcher in self.patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def provider_order(self, status):
        return {**deepcopy(self.source["raw_by_source"]["salla_direct"]), "status": {"slug": status}}

    async def assert_guard_blocked(self, code=None, *, effect=None):
        with self.assertRaises(delivery.shipping.ShippingLabelError) as raised:
            await delivery.guard_effect(self.db, self.operation, effect=effect)
        if code is not None:
            self.assertEqual(raised.exception.code, code)
        self.assertFalse(self.db.collections["order_review_workflows"].updates)

    async def test_current_received_completion_bypasses_component_reads(self):
        before = deepcopy(self.source)
        await delivery.guard_effect(self.db, self.operation, effect="status")
        self.assertTrue(self.workflow[delivery.FIELD]["status_attempted"])
        self.assertEqual(self.workflow[delivery.FIELD]["status_dispatch_count"], 1)
        self.assertEqual(self.source, before)
        self.assertTrue(set(self.db.reads).issubset(self.db.collections))

    async def test_status_fallback_and_invalidation_revalidate_real_receipt(self):
        self.assertEqual(await statuses.source_status(self.db, "owner", "410001"), "in_progress")
        await statuses.invalidate_shipping_if_blocked(self.db, "owner", "410001")
        self.assertFalse(self.db.collections["order_review_workflows"].updates)
        self.piece["services"] = [{"status": "pending", "required_quantity": 1, "completed_quantity": 0}]
        self.assertIsNone(await statuses.source_status(self.db, "owner", "410001"))
        await self.assert_guard_blocked("assembly_completion_evidence_changed")

    async def test_completed_provider_status_is_valid_for_receipt_readback(self):
        self.source["order_status"] = "completed"
        self.source["raw_by_source"]["salla_direct"]["status"] = {"slug": "completed"}
        self.assertEqual(await statuses.source_status(self.db, "owner", "410001"), "completed")
        await delivery.guard_effect(self.db, self.operation)
        await self.assert_guard_blocked("completion_readback_pending", effect="status")

    async def test_completed_policy_remains_printable_after_handoff_without_new_effects(self):
        self.source["order_status"] = "completed"
        self.source["raw_by_source"]["salla_direct"]["status"] = {"slug": "completed"}

        async def owner_scope(db, owner, action):
            return await action(db)

        for stage in ("delivering", "delivered"):
            with self.subTest(stage=stage):
                self.workflow["stage"] = stage
                self.assertEqual(await statuses.source_status(self.db, "owner", "410001"), "completed")
                await statuses.invalidate_shipping_if_blocked(self.db, "owner", "410001")
                self.assertFalse(self.db.collections["order_review_workflows"].updates)
                with patch.object(delivery.shipping, "operational_owner", owner_scope):
                    fence = await delivery.shipping._assert_current_print_status(self.db, "owner", "410001")
                self.assertEqual(fence["shipping"], {"shipment": "one"})
                await self.assert_guard_blocked("assembly_completion_evidence_changed", effect="status")

    async def test_unmarked_ordinary_completion_keeps_component_barrier(self):
        self.workflow.pop("assembly_completion_basis")
        self.workflow[delivery.FIELD].pop("ready_basis")
        self.operation = deepcopy(self.workflow)
        self.assertIsNone(await statuses.source_status(self.db, "owner", "410001"))
        self.source["g47_salla_snapshot"]["requires_authoritative_refresh"] = False
        await self.assert_guard_blocked("completion_source_refresh_pending")

    async def test_marker_alone_cannot_replace_actual_custody_or_current_units(self):
        mutations = (
            lambda: self.piece.pop("received_at"),
            lambda: self.piece.update(assembly_status="pending"),
            lambda: self.piece.update(active_hold_id="stop"),
            lambda: self.piece.update(user_id="other-owner"),
            lambda: self.piece.update(assembly_ready_basis=None),
            lambda: self.source["raw_by_source"]["salla_direct"]["items"][0].update(quantity=2),
        )
        saved_piece, saved_source = deepcopy(self.piece), deepcopy(self.source)
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.piece.clear()
                self.piece.update(deepcopy(saved_piece))
                self.source.clear()
                self.source.update(deepcopy(saved_source))
                mutate()
                await self.assert_guard_blocked("assembly_completion_evidence_changed")

    async def test_every_workflow_and_outbox_marker_must_agree(self):
        for target, key in ((self.workflow, "assembly_completion_basis"),
                            (self.workflow[delivery.FIELD], "ready_basis"),
                            (self.operation, "assembly_completion_basis"),
                            (self.operation[delivery.FIELD], "ready_basis")):
            with self.subTest(key=key):
                target[key] = "other-policy"
                await self.assert_guard_blocked("assembly_completion_evidence_changed")
                target[key] = POLICY

    async def test_claim_lease_and_shipping_assignment_fences_are_preserved(self):
        self.operation[delivery.FIELD]["claim"] = "old-claim"
        await self.assert_guard_blocked("completion_lease_lost")
        self.operation[delivery.FIELD]["claim"] = "synthetic-claim"
        self.workflow[delivery.FIELD]["lease_until"] = self.instant.isoformat()
        await self.assert_guard_blocked("completion_lease_lost")
        self.workflow[delivery.FIELD]["lease_until"] = (self.instant + timedelta(minutes=5)).isoformat()
        self.operation["_shipping_fence"] = await delivery.guard_effect(self.db, self.operation)
        self.workflow["store_courier_assignee_id"] = "different-driver"
        await self.assert_guard_blocked("shipping_snapshot_changed")

    async def test_actual_material_change_remains_fenced(self):
        self.source["raw_by_source"]["salla_direct"]["payment_method"] = "cash_on_delivery"
        await self.assert_guard_blocked("assembly_completion_evidence_changed")

    async def test_cancelled_provider_status_never_authorizes(self):
        self.source["order_status"] = "cancelled"
        self.source["raw_by_source"]["salla_direct"]["status"] = {"slug": "cancelled"}
        await self.assert_guard_blocked()
        await statuses.invalidate_shipping_if_blocked(self.db, "owner", "410001")
        self.assertEqual(self.workflow[delivery.FIELD]["state"], "requires_attention")
        self.assertEqual(self.workflow[delivery.FIELD]["error_code"], "assembly_canonical_status_blocked")

    async def test_conflicting_provider_and_root_status_never_authorizes(self):
        self.source["raw_by_source"]["salla_direct"]["status"] = {"slug": "completed"}
        self.assertIsNone(await statuses.source_status(self.db, "owner", "410001"))
        await self.assert_guard_blocked("assembly_completion_evidence_changed")

    async def test_outbox_version_or_readback_mode_mismatch_cannot_claim_policy(self):
        self.workflow[delivery.FIELD]["version"] = 1
        await self.assert_guard_blocked("assembly_completion_evidence_changed")
        self.workflow[delivery.FIELD]["version"] = 2
        self.workflow[delivery.FIELD]["legacy_readback_only"] = True
        await self.assert_guard_blocked("assembly_completion_evidence_changed")

    def mark_legacy(self, version=2):
        for workflow in (self.workflow, self.operation):
            workflow[delivery.FIELD].update(
                version=version, legacy_readback_only=True,
                status_attempted=True, awb_attempted=True,
            )

    async def test_legacy_policy_reads_completed_label_without_new_effects(self):
        for version in (1, 2):
            with self.subTest(version=version):
                # Restore a live claim after the previous successful publish.
                self.workflow[delivery.FIELD]["claim"] = "synthetic-claim"
                self.workflow[delivery.FIELD]["lease_until"] = (self.instant + timedelta(minutes=5)).isoformat()
                self.mark_legacy(version)
                document = {"ready": True, "label_url": "https://carrier.invalid/legacy.pdf",
                            "order_status_completed": True, "label_type": "carrier"}

                async def read_label(db, owner, number, *, publish):
                    await publish(db, document)
                    return document

                external = AsyncMock()
                with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("101", self.provider_order("completed")))), \
                        patch.object(delivery.shipping, "call_salla", external), \
                        patch.object(delivery.labels, "read_current_carrier_label", read_label):
                    self.assertTrue((await delivery._deliver(self.db, self.operation))["ready"])
                external.assert_not_awaited()
                outbox = self.workflow[delivery.FIELD]
                self.assertTrue(outbox["status_attempted"])
                self.assertTrue(outbox["awb_attempted"])
                self.assertTrue(outbox["legacy_readback_only"])

    async def test_legacy_policy_in_progress_read_never_dispatches_new_status(self):
        self.mark_legacy()
        await delivery.guard_effect(self.db, self.operation)
        await self.assert_guard_blocked("completion_effect_unconfirmed", effect="status")
        external = AsyncMock()
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("101", self.provider_order("in_progress")))), \
                patch.object(delivery.shipping, "call_salla", external):
            with self.assertRaises(delivery.shipping.ShippingLabelError) as raised:
                await delivery._deliver(self.db, self.operation)
        self.assertEqual(raised.exception.code, "completion_status_unconfirmed")
        external.assert_not_awaited()

    async def test_ready_retry_reseal_cannot_publish_old_claim_label(self):
        document = {"ready": True, "label_url": "https://carrier.invalid/old-address.pdf",
                    "order_status_completed": True, "label_type": "carrier"}

        async def read_label(db, owner, number, *, publish):
            # Simulate an independently committed Ready retry while provider IO
            # is suspended. The old operation keeps its claim and old proof.
            self.source["raw_by_source"]["salla_direct"]["shipping_address"]["city"] = "Jeddah"
            self.workflow[delivery.FIELD]["source_fingerprint"] = delivery.source_fingerprint(
                self.source, supplier_receipt=True)
            await publish(db, document)
            return document

        external = AsyncMock()
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("101", self.provider_order("completed")))), \
                patch.object(delivery.shipping, "call_salla", external), \
                patch.object(delivery.labels, "read_current_carrier_label", read_label):
            with self.assertRaises(delivery.shipping.ShippingLabelError) as raised:
                await delivery._deliver(self.db, self.operation)
        self.assertEqual(raised.exception.code, "assembly_completion_evidence_changed")
        self.assertNotIn("carrier_label_ready", self.workflow)
        self.assertNotIn("carrier_label_url", self.workflow)
        external.assert_not_awaited()

    async def test_courier_handover_skips_snapshot_recovery_only_with_actual_receipt_proof(self):
        recovery = AsyncMock(side_effect=AssertionError("Receipt policy must not recover component snapshots"))
        fence = {"epoch": 0, "shipping": {"shipment": "one"}, "assignment": {
            "store_courier_assignee_id": None, "store_courier_assignee_name": None,
            "store_delivery_assignment_id": None,
        }}
        checked = AsyncMock(return_value=fence)
        document = {"ready": True, "label_type": "store_courier", "order_status_completed": True,
                    "print_data": {"synthetic": True}}

        async def destination(db, owner, action):
            return await action(db, [])

        with patch("shipping_pending_snapshot_recovery.recover_pending_print_snapshot", recovery), \
                patch.object(carrier_labels.compat, "mezan_courier_name", AsyncMock(return_value="Store courier")), \
                patch.object(carrier_labels.compat, "with_courier_destination", destination), \
                patch.object(carrier_labels.compat, "local_mezan_courier_label", AsyncMock(return_value=document)), \
                patch.object(delivery.shipping, "_assert_current_print_status", checked):
            result = await carrier_labels.recover_store_courier_label_for_handover(
                self.db, user_id="owner", order_number="410001")
        self.assertTrue(result)
        recovery.assert_not_awaited()
        checked.assert_awaited_once()
        self.assertTrue(self.workflow["carrier_label_ready"])

    async def test_courier_handover_retains_recovery_when_receipt_proof_fails(self):
        self.piece.pop("received_at")
        recovery = AsyncMock(side_effect=delivery.shipping.ShippingLabelError(
            "component_execution_blocked", "Synthetic blocked component snapshot"))
        checked = AsyncMock()
        with patch("shipping_pending_snapshot_recovery.recover_pending_print_snapshot", recovery), \
                patch.object(carrier_labels.compat, "mezan_courier_name", AsyncMock(return_value="Store courier")), \
                patch.object(delivery.shipping, "_assert_current_print_status", checked):
            with self.assertRaises(delivery.shipping.ShippingLabelError):
                await carrier_labels.recover_store_courier_label_for_handover(
                    self.db, user_id="owner", order_number="410001")
        recovery.assert_awaited_once_with(self.db, "owner", "410001", allow_print_catalog_rename=False)
        checked.assert_not_awaited()

    def courier_source(self, status):
        self.source["order_status"] = status
        raw = self.source["raw_by_source"]["salla_direct"]
        raw["status"] = {"slug": status}
        raw["store"] = {"name": "Synthetic store", "phone": "0500000000"}
        raw["customer"] = {"full_name": "Synthetic recipient", "mobile": "0500000001"}
        raw["shipping_address"] = {"city": "Riyadh", "street": "Current delivery street"}

    async def test_real_courier_document_revalidates_supplier_completion_after_handoff(self):
        self.courier_source("completed")
        before = deepcopy(self.source)
        with patch.object(carrier_labels.compat, "mezan_courier_name", AsyncMock(return_value="Store courier")):
            for stage in ("completed", "delivering", "delivered"):
                with self.subTest(stage=stage):
                    self.workflow["stage"] = stage
                    document = await carrier_labels.compat.local_mezan_courier_label(
                        self.db, "owner", "410001", self.operation)
                    self.assertTrue(document["ready"])
                    self.assertEqual(document["label_type"], "store_courier")
                    self.assertEqual(document["print_data"]["recipient_name"], "Synthetic recipient")
                    self.assertEqual(document["print_data"]["address"]["street"], "Current delivery street")
                    self.assertEqual(document["print_data"]["barcode_value"], "410001")
                    self.assertTrue(document["print_data"]["qr_code"].startswith("data:image/svg+xml;base64,"))
        self.assertEqual(self.source, before)
        self.assertFalse(self.db.collections["order_review_workflows"].updates)
        self.assertTrue(set(self.db.reads).issubset(self.db.collections))

    async def test_real_courier_in_progress_requires_fresh_saved_provider_confirmation(self):
        self.courier_source("in_progress")
        self.workflow[delivery.FIELD]["order_confirmed"] = True
        self.workflow["salla_order_status"] = "completed"
        self.workflow["salla_order_status_verified_at"] = self.instant.isoformat()
        with patch.object(carrier_labels.compat, "mezan_courier_name", AsyncMock(return_value="Store courier")):
            document = await carrier_labels.compat.local_mezan_courier_label(
                self.db, "owner", "410001", self.operation)
            self.assertTrue(document["ready"])
            stale_confirmed = deepcopy(self.workflow)
            self.workflow[delivery.FIELD]["order_confirmed"] = False
            with self.assertRaises(delivery.shipping.ShippingLabelError) as raised:
                await carrier_labels.compat.local_mezan_courier_label(
                    self.db, "owner", "410001", stale_confirmed)
        self.assertEqual(raised.exception.code, "carrier_order_in_progress")

    async def test_real_courier_retains_receipt_identity_and_actual_status_barriers(self):
        self.courier_source("completed")
        original_source = deepcopy(self.source)
        original_piece = deepcopy(self.piece)
        original_workflow = deepcopy(self.workflow)
        changes = (
            (self.piece, "received_at", None),
            (self.piece, "assembly_ready_basis", None),
            (self.piece, "active_hold_id", "current-hold"),
            (self.workflow, "assembly_completion_basis", None),
            (self.source, "raw_by_source.salla_direct.reference_id", "different-order"),
            (self.source, "raw_by_source.salla_direct.status.slug", "cancelled"),
            (self.source, "raw_by_source.salla_direct.items", []),
        )
        with patch.object(carrier_labels.compat, "mezan_courier_name", AsyncMock(return_value="Store courier")):
            for row, path, value in changes:
                with self.subTest(path=path):
                    self.source.clear()
                    self.source.update(deepcopy(original_source))
                    self.piece.clear()
                    self.piece.update(deepcopy(original_piece))
                    self.workflow.clear()
                    self.workflow.update(deepcopy(original_workflow))
                    _set(row, path, value)
                    with self.assertRaises(delivery.shipping.ShippingLabelError) as raised:
                        await carrier_labels.compat.local_mezan_courier_label(
                            self.db, "owner", "410001", self.operation)
                    self.assertEqual(raised.exception.code, "order_status_not_completed")

    async def test_provider_completed_publishes_label_without_another_status_post(self):
        post = AsyncMock(side_effect=AssertionError("Completed provider must not get a status POST"))
        document = {"ready": True, "label_url": "https://carrier.invalid/label.pdf",
                    "order_status_completed": True, "label_type": "carrier"}

        async def read_label(db, owner, number, *, publish):
            await publish(db, document)
            return document

        async def read_order(*args):
            # Reconciliation flags changing during readback are not material.
            self.source["g47_salla_snapshot"].update(
                component_pending=False, requires_authoritative_refresh=False)
            return "101", self.provider_order("completed")

        with patch.object(delivery.shipping, "_resolve_order", read_order), \
                patch.object(delivery.shipping, "call_salla", post), \
                patch.object(delivery.labels, "read_current_carrier_label", read_label):
            result = await delivery._deliver(self.db, self.operation)
        self.assertTrue(result["ready"])
        self.assertEqual(self.workflow[delivery.FIELD]["state"], "confirmed")
        self.assertTrue(self.workflow[delivery.FIELD]["order_confirmed"])
        self.assertTrue(self.workflow["carrier_label_ready"])
        post.assert_not_awaited()

    async def test_fresh_in_progress_provider_is_journaled_before_one_status_post(self):
        async def post(db, owner, method, path, **kwargs):
            self.assertTrue(self.workflow[delivery.FIELD]["status_attempted"])
            self.assertEqual(self.workflow[delivery.FIELD]["status_dispatch_count"], 1)
            self.assertEqual((method, path, kwargs["json"]),
                             ("POST", "/orders/101/status", {"slug": "completed"}))
            return {"success": True}

        external = AsyncMock(side_effect=post)
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("101", self.provider_order("in_progress")))), \
                patch.object(delivery.shipping, "call_salla", external):
            with self.assertRaises(delivery.shipping.ShippingLabelError) as raised:
                await delivery._deliver(self.db, self.operation)
        self.assertEqual(raised.exception.code, "completion_readback_pending")
        external.assert_awaited_once()
        self.assertFalse(self.workflow[delivery.FIELD]["order_confirmed"])

    async def test_negative_provider_read_never_dispatches_status(self):
        external = AsyncMock()
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("101", self.provider_order("cancelled")))), \
                patch.object(delivery.shipping, "call_salla", external):
            with self.assertRaises(delivery.shipping.ShippingLabelError) as raised:
                await delivery._deliver(self.db, self.operation)
        self.assertEqual(raised.exception.code, "assembly_order_not_ready")
        external.assert_not_awaited()

    async def test_different_provider_order_identity_never_dispatches_status(self):
        external = AsyncMock()
        provider = self.provider_order("in_progress")
        provider["reference_id"] = "different-order"
        with patch.object(delivery.shipping, "_resolve_order", AsyncMock(return_value=("101", provider))), \
                patch.object(delivery.shipping, "call_salla", external):
            with self.assertRaises(delivery.shipping.ShippingLabelError) as raised:
                await delivery._deliver(self.db, self.operation)
        self.assertEqual(raised.exception.code, "salla_order_reference_mismatch")
        external.assert_not_awaited()

    async def test_invalid_receipt_proof_never_uses_old_approval_recovery(self):
        self.piece.pop("received_at")
        recovered = AsyncMock()
        with patch("assembly_evidence_recovery.recover_evidence", recovered):
            with self.assertRaises(delivery.shipping.ShippingLabelError):
                await delivery._deliver_with_recovery(self.db, self.operation)
        recovered.assert_not_awaited()


class SupplierReceiptFingerprintTests(unittest.TestCase):
    def source(self):
        return {
            "raw_by_source": {"salla_direct": {
                "id": 101, "reference_id": "supplier-order",
                "items": [{"id": 201, "quantity": 1}],
                "shipping_address": {"city": "Riyadh"},
                "payment_method": "mada", "payment_status": "paid",
            }},
            "g47_salla_snapshot": {
                "component_pending": True, "requires_authoritative_refresh": True,
            },
        }

    def test_supplier_fingerprint_ignores_only_local_component_flags(self):
        source = self.source()
        changed = deepcopy(source)
        changed["g47_salla_snapshot"].update(
            component_pending=False, requires_authoritative_refresh=False)
        self.assertNotEqual(delivery.source_fingerprint(source), delivery.source_fingerprint(changed))
        self.assertEqual(
            delivery.source_fingerprint(source, supplier_receipt=True),
            delivery.source_fingerprint(changed, supplier_receipt=True),
        )
        changed["raw_by_source"]["salla_direct"]["items"][0]["quantity"] = 2
        self.assertNotEqual(
            delivery.source_fingerprint(source, supplier_receipt=True),
            delivery.source_fingerprint(changed, supplier_receipt=True),
        )

    def test_old_workflow_fingerprint_remains_byte_compatible(self):
        workflow = {"user_id": "owner", "order_number": "supplier-order",
                    "stage": "completed", "assembly_status": "completed"}
        self.assertEqual(delivery.workflow_fingerprint(workflow),
                         "6ecddc48c1e706f32943d91a9aa38341923ab7861c44c09075733fc44a763ac5")

    def test_workflow_fingerprint_binds_receipt_policy(self):
        workflow = {"user_id": "owner", "order_number": "supplier-order",
                    "stage": "completed", "assembly_status": "completed"}
        marked = {**workflow, "assembly_completion_basis": "supplier_receipt_v1"}
        self.assertNotEqual(delivery.workflow_fingerprint(workflow), delivery.workflow_fingerprint(marked))


if __name__ == "__main__":
    unittest.main()
