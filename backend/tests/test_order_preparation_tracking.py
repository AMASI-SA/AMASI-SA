"""Synthetic read-only DB + actual FastAPI route. No Mezan/Mongo connections."""
import copy
import importlib.util
import unittest
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

HERE = Path(__file__).resolve().parent
TARGET = HERE.parent / "order_engine" / "preparation_tracking.py"
spec = importlib.util.spec_from_file_location("preparation_tracking", TARGET)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

NOW = "2026-09-26T12:00:00+00:00"


def order():
    return {
        "order_number": "900001",
        "status": "in_progress",
        "items": [
            {"order_item_id": "line-1", "product_id": "product-same", "quantity": 2},
            {"order_item_id": "line-2", "product_id": "product-same", "quantity": 1},
        ],
    }


def piece(identifier="p1", **extra):
    return {
        "piece_id": identifier,
        "user_id": "merchant-A",
        "order_number": "900001",
        "order_item_id": "line-1",
        "unit_index": 1,
        "status": "assigned",
        "responsible_employee_id": "employee-A",
        "responsible_employee_name": "موظف اختبار أ",
        "supplier_id": None,
        "supplier_name": None,
        **extra,
    }


def view(rows, o=None, w=None, **kwargs):
    return m.build_tracking(
        o or order(),
        w or {},
        rows,
        observed_at=NOW,
        truncated=kwargs.get("truncated", False),
    )


class Mapping(unittest.TestCase):
    def test_two_distinct_assignments_same_order_line_not_overwritten(self):
        value = view(
            [
                piece(),
                piece(
                    "p2",
                    unit_index=2,
                    responsible_employee_id="employee-B",
                    supplier_id="s2",
                    supplier_name="مورد اختبار",
                    supplier_dispatch_status="sent",
                ),
            ]
        )
        self.assertEqual(len(value["items"][0]["pieces"]), 2)
        self.assertEqual(value["items"][0]["pieces"][1]["stage"], "at_supplier")
        self.assertEqual(value["items"][0]["coverage"], "complete")

    def test_same_product_different_order_line_remains_untracked(self):
        value = view([piece()])
        self.assertEqual(value["items"][1]["coverage"], "no_records")

    def test_removed_or_unknown_order_line_not_matched_by_product(self):
        value = view([piece(order_item_id="removed", product_id="product-same")])
        self.assertEqual(value["unmapped_piece_count"], 1)
        self.assertTrue(all(not row["pieces"] for row in value["items"]))

    def test_archived_assignment_not_shown_as_current(self):
        value = view(
            [
                piece(experiment_archived_at=NOW),
                piece("new", responsible_employee_id="new"),
            ]
        )
        self.assertEqual(value["items"][0]["pieces"][0]["employee"]["id"], "new")

    def test_employee_id_without_name_stays_assigned(self):
        value = view([piece(responsible_employee_name=None)])
        self.assertEqual(
            value["items"][0]["pieces"][0]["employee"],
            {"id": "employee-A", "name": None},
        )

    def test_unassigned_state_does_not_reuse_stale_employee(self):
        value = view([piece(assignment_status="unassigned_after_rejection")])
        self.assertIsNone(value["items"][0]["pieces"][0]["employee"])
        self.assertEqual(value["items"][0]["pieces"][0]["stage"], "unassigned")

    def test_duplicate_piece_identity_rejected(self):
        with self.assertRaises(m.TrackingShapeError):
            view([piece(), piece(unit_index=2)])

    def test_duplicate_active_physical_unit_rejected(self):
        with self.assertRaises(m.TrackingShapeError):
            view([piece(), piece("different")])

    def test_unknown_status_is_not_invented_as_preparing(self):
        self.assertEqual(
            view([piece(status="new_unknown")])["items"][0]["pieces"][0]["stage"],
            "unknown",
        )

    def test_partial_read_does_not_claim_no_assignment(self):
        value = view([], truncated=True)
        self.assertTrue(all(row["coverage"] == "partial" for row in value["items"]))

    def test_order_without_registry_not_falsely_ready_from_order_stage(self):
        value = view([], w={"stage": "completed"})
        self.assertTrue(all(row["coverage"] == "no_records" for row in value["items"]))

    def test_direct_assembly_uses_workflow_and_no_fake_employee(self):
        workflow = {
            "items": [
                {
                    "order_item_id": "line-1",
                    "quantity": 2,
                    "preparation_route": "direct_assembly",
                    "direct_assembly_piece_ids": ["d1", "d2"],
                    "assembly_ready_piece_ids": ["d2"],
                }
            ]
        }
        value = view([], w=workflow)["items"][0]
        self.assertEqual(
            [row["stage"] for row in value["pieces"]],
            ["assembly", "ready"],
        )
        self.assertTrue(
            all(
                row["employee"] is None and row["source"] == "direct_assembly"
                for row in value["pieces"]
            )
        )

    def test_direct_assembly_stale_quantity_is_not_attached(self):
        with self.assertRaises(m.TrackingShapeError):
            view(
                [],
                w={
                    "items": [
                        {
                            "order_item_id": "line-1",
                            "quantity": 3,
                            "preparation_route": "direct_assembly",
                        }
                    ]
                },
            )

    def test_physical_and_direct_routes_conflict_not_double_counted(self):
        with self.assertRaises(m.TrackingShapeError):
            view(
                [piece()],
                w={
                    "items": [
                        {
                            "order_item_id": "line-1",
                            "quantity": 2,
                            "preparation_route": "direct_assembly",
                        }
                    ]
                },
            )

    def test_operational_accessory_does_not_count_as_order_product(self):
        self.assertEqual(
            view(
                [],
                w={"operational_items": [{"source_order_item_id": "line-1", "quantity": 1}]},
            )["items"][0]["pieces"],
            [],
        )

    def test_allowlist_excludes_notes_prices_customer_secrets_and_last_actor(self):
        public = view(
            [
                piece(
                    customer_name="PRIVATE",
                    note="PRIVATE",
                    token="PRIVATE",
                    cost=99,
                    received_by_name="WRONG ACTOR",
                )
            ]
        )["items"][0]["pieces"][0]
        self.assertNotIn("PRIVATE", str(public))
        self.assertNotIn("WRONG ACTOR", str(public))
        self.assertEqual(
            set(public),
            {"piece_id", "unit_index", "stage", "stage_label", "employee", "supplier", "source"},
        )


for name, piece_values, workflow, order_values, expected in [
    ("cancelled_before_delivered", {"status": "cancelled"}, {"stage": "delivered"}, {}, "cancelled"),
    ("order_cancelled_before_old_workflow", {}, {"stage": "delivered"}, {"status_native": "ملغي"}, "cancelled"),
    ("held_before_completed", {"active_hold_id": "h"}, {"stage": "completed"}, {}, "blocked"),
    ("supplier_in_receiving", {"supplier_receiving_session_id": "session"}, {}, {}, "supplier_receiving"),
    ("supplier_ready", {"supplier_dispatch_status": "ready"}, {}, {}, "supplier_ready"),
    ("partial_services", {"status": "in_progress", "supplier_dispatch_status": "partial_received"}, {}, {}, "remaining_services"),
    ("supplier_received_is_not_assembly", {"status": "received", "supplier_dispatch_status": "received"}, {}, {}, "supplier_received"),
    ("employee_receipt_ready", {"status": "ready_for_employee_receipt"}, {}, {}, "employee_receiving"),
    ("assembly_over_stale_supplier", {"status": "ready_for_assembly", "supplier_dispatch_status": "sent"}, {}, {}, "assembly"),
    ("ready_piece", {"assembly_status": "ready"}, {}, {}, "ready"),
    ("order_delivering", {}, {"stage": "delivering"}, {}, "delivering"),
    ("returned_order", {}, {"stage": "completed"}, {"status_native": "مسترجع"}, "returned"),
]:
    def test(self, piece_values=piece_values, workflow=workflow, order_values=order_values, expected=expected):
        current = order()
        current.update(order_values)
        self.assertEqual(
            view([piece(**piece_values)], current, workflow)["items"][0]["pieces"][0]["stage"],
            expected,
        )

    setattr(Mapping, "test_stage_" + name, test)


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, keys):
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, n):
        return self.rows[:n]


class Collection:
    def __init__(self, name, db):
        self.name = name
        self.db = db

    async def find_one(self, query, projection):
        self.db.calls.append((self.name, query, projection))
        return next(
            (
                row
                for row in self.db.rows[self.name]
                if all(row.get(key) == value for key, value in query.items())
            ),
            None,
        )

    def find(self, query, projection):
        self.db.calls.append((self.name, query, projection))
        rows = [
            row
            for row in self.db.rows[self.name]
            if all(row.get(key) == value for key, value in query.items())
        ]
        return Cursor(rows)


class DB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def __getitem__(self, name):
        return Collection(name, self)


class MissingOrder(Exception):
    pass


class ReadRoute(unittest.TestCase):
    def setUp(self):
        self.actor = {"id": "merchant-A", "role": "owner"}
        self.o = order()
        self.owner_calls = []
        self.db = DB(
            {
                m.PIECES: [
                    piece(),
                    piece("other-tenant", user_id="merchant-B"),
                    piece("other-order", order_number="900002"),
                ],
                m.WORKFLOWS: [],
            }
        )

        async def user():
            return self.actor

        async def get_order(_, **kwargs):
            self.owner_calls.append(kwargs)
            if kwargs["order_number"] != "900001":
                raise MissingOrder()
            return self.o

        def require_owner(value):
            if value.get("role") != "owner":
                raise HTTPException(status_code=403, detail={"code": "owner_only"})
            return value

        app = FastAPI()
        router = APIRouter(prefix="/orders-v2")
        m.install_preparation_tracking_read_route(
            router,
            db=self.db,
            current_user=user,
            require_owner=require_owner,
            get_order=get_order,
            repository=lambda: None,
            not_found_error=MissingOrder,
        )
        app.include_router(router)
        self.client = TestClient(app)

    def test_real_route_tenant_scoped_read_no_writes_or_secrets(self):
        response = self.client.get("/orders-v2/900001/preparation-tracking")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json()["items"][0]["pieces"][0]["piece_id"], "p1")
        self.assertEqual(len(response.json()["items"][0]["pieces"]), 1)
        self.assertEqual(len(self.db.calls), 2)
        for _, query, projection in self.db.calls:
            self.assertEqual(query["user_id"], "merchant-A")
            self.assertEqual(query["order_number"], "900001")
            self.assertEqual(projection["_id"], 0)
            self.assertNotIn("services", projection)

    def test_permission_denied_before_database_access(self):
        self.actor = {"id": "employee", "role": "employee"}
        self.assertEqual(
            self.client.get("/orders-v2/900001/preparation-tracking").status_code,
            403,
        )
        self.assertEqual(self.db.calls, [])
        self.assertEqual(self.owner_calls, [])

    def test_authorized_mobile_principal_uses_merchant_not_actor_as_tenant(self):
        self.actor.update(_mobile_actor_id="employee")
        self.assertEqual(
            self.client.get("/orders-v2/900001/preparation-tracking").status_code,
            200,
        )
        self.assertEqual(self.owner_calls[0]["user_id"], "merchant-A")

    def test_unknown_order_is_404_not_no_assignment(self):
        self.assertEqual(
            self.client.get("/orders-v2/unknown/preparation-tracking").status_code,
            404,
        )
        self.assertEqual(self.db.calls, [])

    def test_conflicting_registry_is_409_not_empty_success(self):
        self.db.rows[m.PIECES].append(piece("conflict"))
        self.assertEqual(
            self.client.get("/orders-v2/900001/preparation-tracking").status_code,
            409,
        )

    def test_registry_query_is_bounded_and_truncation_visible(self):
        previous = m.MAX_PIECES
        m.MAX_PIECES = 1
        try:
            self.db.rows[m.PIECES] = [piece(), piece("p2", unit_index=2)]
            result = self.client.get("/orders-v2/900001/preparation-tracking")
            self.assertEqual(result.status_code, 200)
            self.assertTrue(result.json()["truncated"])
            self.assertTrue(
                all(row["coverage"] == "partial" for row in result.json()["items"])
            )
        finally:
            m.MAX_PIECES = previous

    def test_post_is_not_allowed(self):
        self.assertEqual(
            self.client.post("/orders-v2/900001/preparation-tracking", json={}).status_code,
            405,
        )
        self.assertEqual(self.db.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
