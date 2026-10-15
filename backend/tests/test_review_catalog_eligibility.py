"""Real replica-set selection before the catalogue limit, with bounded hydration."""
import json
import unittest
from collections import Counter
from copy import deepcopy
from unittest.mock import patch

from pymongo.monitoring import CommandListener
from bson.codec_options import CodecOptions
from bson.decimal128 import Decimal128
import test_review_local_assembly as assembly
import test_g47_component_lifecycle_integration as fixture
import reviewed_products_catalog as catalog
import order_review_completion as completion
import fulfillment_v2_routes as fulfillment
from stock_component_consumption_service import PLANS


class Reads(CommandListener):
    def __init__(self):
        self.enabled = False
        self.commands = Counter()
        self.returned = Counter()
        self.finds = []

    def started(self, event):
        if self.enabled:
            self.commands[event.command_name] += 1
            if event.command_name == "find":
                self.finds.append({k: deepcopy(v) for k, v in event.command.items()
                                  if k in {"find", "filter", "projection", "sort", "limit", "skip", "hint", "collation"}})

    def succeeded(self, event):
        if self.enabled:
            cursor = event.reply.get("cursor", {})
            self.returned[cursor.get("ns", event.command_name).split(".")[-1]] += len(
                cursor.get("firstBatch", cursor.get("nextBatch", [])))

    def failed(self, event):
        pass


def rename(value, number):
    if isinstance(value, str):
        return value.replace("local-assembly", number)
    if isinstance(value, list):
        return [rename(v, number) for v in value]
    if isinstance(value, dict):
        return {k: rename(v, number) for k, v in value.items()}
    return deepcopy(value)


class CatalogEligibilityTests(unittest.IsolatedAsyncioTestCase):
    # Reuse fixture actions, not inherited test methods.
    for _name in ("asyncTearDown", "source_payload", "webhook", "mark_piece", "on_hand",
                  "complete_review", "workflow", "mount_assignment", "catalog_cards", "assign_cards", "mark"):
        locals()[_name] = getattr(assembly.LocalAssemblyTests, _name)

    async def asyncSetUp(self):
        self.reads = Reads()
        original = fixture.AsyncIOMotorClient
        with patch.object(fixture, "AsyncIOMotorClient", side_effect=lambda *a, **kw: original(
            *a, **kw, event_listeners=[self.reads],
        )):
            await assembly.LocalAssemblyTests.asyncSetUp(self)
        self.mount_assignment()
        # Same canonical index installed by server.py startup. Catalogue GET
        # must not create indexes or mutate any collection.
        await self.db.unified_orders.create_index([("user_id", 1), ("order_number", 1)], unique=True)

    async def seed_orders(self, exhausted, eligible):
        await self.complete_review(direct=False)
        reply = await self.assign_cards(await self.catalog_cards(), "allocated-template")
        self.assertEqual(reply.status_code, 200, reply.text)
        self.assertEqual((await self.workflow())["stage"], "in_progress")
        names = (completion.WORKFLOWS, completion.OPERATIONS, "unified_orders",
                 fulfillment.COMPONENT_LIFECYCLES, PLANS, catalog.PREPARATION_UNIT_ALLOCATIONS)
        templates = {name: await self.db[name].find({}).to_list(10) for name in names}
        for name in names:
            rows = []
            for index in range(exhausted + eligible):
                number = f"catalog-{index:04}"
                if name == catalog.PREPARATION_UNIT_ALLOCATIONS and index >= exhausted:
                    continue
                for i, template in enumerate(templates[name]):
                    row = rename(template, number)
                    row["_id"] = f"{number}:{name}:{i}"
                    if name == completion.WORKFLOWS:
                        row["review_completion_operation_id"] = f"{number}:{completion.OPERATIONS}:0"
                        row["reviewed_at"] = f"2026-10-01T00:00:{index:05}"
                        if index >= exhausted:
                            row.update(stage="reviewed", preparation_assignments=[], preparation_batch_ids=[])
                    rows.append(row)
            if rows:
                await self.db[name].insert_many(rows)
        await self.db[completion.WORKFLOWS].delete_one({"order_number": "local-assembly"})

    async def test_2000_exhausted_orders_do_not_consume_limit_or_hydration(self):
        await self.seed_orders(2000, 1)
        stock = await self.on_hand()
        loaded = []
        original = catalog.get_orders

        async def observe(*args, **kwargs):
            loaded.extend(kwargs["order_numbers"])
            return await original(*args, **kwargs)

        with patch.object(catalog, "get_orders", side_effect=observe):
            self.reads.enabled = True
            page = await self.client.get("/reviewed-products-v1/catalog")
            self.reads.enabled = False
        self.assertEqual(page.status_code, 200, page.text)
        self.assertEqual(len(page.json()["products"]), 2)
        self.assertFalse(page.json()["truncated"])
        self.assertEqual(loaded, ["catalog-2000"])
        self.assertEqual(self.reads.commands["aggregate"], 1)
        self.assertEqual(self.reads.commands["find"], 7)
        self.assertFalse(set(self.reads.commands) - {"find", "aggregate", "getMore", "killCursors"})
        self.assertEqual(self.reads.returned[completion.WORKFLOWS], 1)
        self.assertEqual(await self.on_hand(), stock)
        self.assertEqual(await self.catalog_cards(), page.json()["products"])
        # Mongo 8's Windows explain can emit invalid UTF-8 in its textual plan
        # dump. Decode only this diagnostic lossily; numeric execution metrics
        # and all application/source reads retain their strict BSON decoder.
        explained = await self.db.command("explain", {
            "aggregate": completion.WORKFLOWS,
            "pipeline": catalog._available_workflow_pipeline({"user_id": "owner", **catalog.assignment_workflow_query()}),
            "cursor": {},
        }, verbosity="executionStats", codec_options=CodecOptions(unicode_decode_error_handler="replace"))
        examined = []
        for stage in explained.get("stages", []):
            if "$cursor" in stage:
                examined.append({"stage": "workflow", **{k: stage["$cursor"]["executionStats"].get(k)
                                                        for k in ("totalDocsExamined", "nReturned")}})
            if "$lookup" in stage:
                examined.append({"stage": stage["$lookup"]["from"], **{k: stage.get(k)
                                 for k in ("totalDocsExamined", "nReturned", "indexesUsed", "collectionScans")}})
        self.assertEqual(len(examined), 3)
        self.assertEqual([row["totalDocsExamined"] for row in examined], [2001, 2001, 4000])
        self.assertTrue(all(row["collectionScans"] == 0 and row["indexesUsed"] for row in examined[1:]))
        find_examined = []
        for command in self.reads.finds:
            result = await self.db.command("explain", command, verbosity="executionStats",
                codec_options=CodecOptions(unicode_decode_error_handler="replace"))
            find_examined.append({"collection": command["find"], "documents_examined": result["executionStats"]["totalDocsExamined"]})
        print("CATALOG_2000_EVIDENCE=" + json.dumps({"commands": self.reads.commands,
              "documents_returned": self.reads.returned, "full_orders_loaded": loaded,
              "server_examined": examined, "find_examined": find_examined,
              "total_documents_examined": sum(row["totalDocsExamined"] for row in examined) +
                                          sum(row["documents_examined"] for row in find_examined),
              "cards": 2, "truncated": False}))
        # Both consumers use the same selection; last assignment still reconciles.
        desktop = await self.assign_cards(page.json()["products"][:1], "after-2000-desktop")
        self.assertEqual(desktop.status_code, 200, desktop.text)
        mobile = await self.assign_cards(await self.catalog_cards(), "after-2000-mobile", mobile=True)
        self.assertEqual(mobile.status_code, 200, mobile.text)
        self.assertEqual(await self.catalog_cards(), [])
        self.external.assert_not_awaited()

    async def test_real_eligible_limit_is_retained_and_hydration_bounded(self):
        await self.seed_orders(12, 4)
        loaded = []
        original = catalog.get_orders
        async def observe(*args, **kwargs):
            loaded.extend(kwargs["order_numbers"])
            return await original(*args, **kwargs)
        with patch.object(catalog, "get_orders", side_effect=observe):
            page = await self.client.get("/reviewed-products-v1/catalog?limit=2")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertTrue(page.json()["truncated"])
        self.assertEqual(len(page.json()["products"]), 4)
        self.assertEqual(loaded, ["catalog-0012", "catalog-0013", "catalog-0014"])
        self.assertEqual(await self.catalog_cards(), (await self.client.get("/reviewed-products-v1/catalog")).json()["products"])

    async def test_false_positive_candidates_refill_before_limit(self):
        await self.seed_orders(0, 4)
        await self.db[completion.OPERATIONS].update_many({"order_number": {"$in": ["catalog-0000", "catalog-0001"]}},
                                                       {"$set": {"superseded_by": "later"}})
        page = await self.client.get("/reviewed-products-v1/catalog?limit=2")
        self.assertEqual(page.status_code, 200, page.text)
        self.assertFalse(page.json()["truncated"])
        self.assertEqual(len(page.json()["products"]), 4)

    async def test_live_quantity_growth_is_not_hidden_by_frozen_snapshot(self):
        await self.seed_orders(1, 0)
        await self.db.unified_orders.update_one({"order_number": "catalog-0000"},
            {"$set": {"raw_by_source.salla_direct.items.0.quantity": 3}})
        cards = await self.catalog_cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["source_lines"][0]["unit_index"], 3)

    async def test_released_and_out_of_range_allocations_do_not_consume_capacity(self):
        await self.seed_orders(1, 0)
        await self.db[catalog.PREPARATION_UNIT_ALLOCATIONS].update_one(
            {"order_number": "catalog-0000", "unit_index": 1}, {"$set": {"status": "released"}})
        await self.db[catalog.PREPARATION_UNIT_ALLOCATIONS].update_one(
            {"order_number": "catalog-0000", "unit_index": 2}, {"$set": {"unit_index": 99}})
        self.assertEqual(len(await self.catalog_cards()), 2)

    async def test_legacy_reviewed_exhausted_is_excluded_without_changing_history(self):
        await self.seed_orders(2, 1)
        await self.db[completion.WORKFLOWS].update_many({}, {
            "$set": {"stage": "reviewed", "reviewed_at": "2026-10-01T00:00:00+00:00"},
            "$unset": {"completion_mode": ""},
        })
        page = await self.client.get("/reviewed-products-v1/catalog?limit=1")
        self.assertFalse(page.json()["truncated"])
        self.assertEqual(len(page.json()["products"]), 2)
        history = await self.client.get("/reviewed-products-v1/catalog?reviewed_date=2026-10-01")
        self.assertEqual(history.json()["summary"]["total_quantity"], 6)

    async def test_missing_snapshot_uses_canonical_source(self):
        await self.complete_review(direct=False)
        await self.db[completion.WORKFLOWS].update_one({"order_number": "local-assembly"}, {"$set": {"items": []}})
        self.assertEqual(len(await self.catalog_cards()), 2)

    async def test_direct_and_ledger_overlap_consumes_position_once(self):
        await self.complete_review(mixed=True)
        await self.mark(self.ids[0])
        await self.db[catalog.PREPARATION_UNIT_ALLOCATIONS].insert_one({
            "user_id": "owner", "order_number": "local-assembly", "order_item_id": self.direct_line_id,
            "unit_index": 1, "status": "committed",
        })
        self.assertEqual(len(await self.catalog_cards()), 3)
        self.assertEqual(await self.on_hand(), 18)

    async def test_new_live_line_is_not_hidden_by_exhausted_snapshot(self):
        await self.seed_orders(1, 0)
        row = await self.db.unified_orders.find_one({"order_number": "catalog-0000"})
        item = {**row["raw_by_source"]["salla_direct"]["items"][0], "id": "new-line"}
        await self.db.unified_orders.update_one({"_id": row["_id"]},
            {"$push": {"raw_by_source.salla_direct.items": item}})
        self.assertEqual(len(await self.catalog_cards()), 2)

    async def test_unusual_identities_are_not_exhaustion_evidence(self):
        await self.seed_orders(1, 0)
        row = await self.db.unified_orders.find_one({"order_number": "catalog-0000"})
        workflow = await self.db[completion.WORKFLOWS].find_one({"order_number": "catalog-0000"})
        for mutate in ("provider-number", "padded-id", "colliding-snapshot", "trailing-newline"):
            with self.subTest(mutation=mutate):
                raw, state = deepcopy(row), deepcopy(workflow)
                if mutate == "provider-number":
                    raw["raw_by_source"]["salla_direct"]["reference_id"] = "other-reference"
                elif mutate == "padded-id":
                    raw["raw_by_source"]["salla_direct"]["items"][0]["id"] = " padded "
                    state["items"][0]["order_item_id"] = "salla:catalog-0000: padded "
                elif mutate == "colliding-snapshot":
                    state["items"].append({**state["items"][0],
                        "order_item_id": " " + state["items"][0]["order_item_id"] + " "})
                else:
                    state["items"].append({**state["items"][0],
                        "order_item_id": state["items"][0]["order_item_id"] + "\n"})
                await self.db.unified_orders.replace_one({"_id": row["_id"]}, raw)
                await self.db[completion.WORKFLOWS].replace_one({"_id": workflow["_id"]}, state)
                candidates = await self.db[completion.WORKFLOWS].aggregate(
                    catalog._available_workflow_pipeline({"order_number": "catalog-0000"}),
                ).to_list(2)
                self.assertEqual(len(candidates), 1, mutate)

    async def test_bson_decimal_allocation_is_not_false_exhaustion_evidence(self):
        await self.seed_orders(1, 0)
        await self.db[catalog.PREPARATION_UNIT_ALLOCATIONS].update_one(
            {"order_number": "catalog-0000", "unit_index": 1}, {"$set": {"unit_index": Decimal128("1")}})
        self.assertEqual(len(await self.catalog_cards()), 1)
