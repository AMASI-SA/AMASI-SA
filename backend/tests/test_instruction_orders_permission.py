"""Orders-page grants reach real instruction routes without owner impersonation."""
import ast
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

import order_tracking_notes_routes as routes
import operational_instruction_notifications as notifications
from mobile_app_permissions import MOBILE_APP_ACCESS
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS
from test_operational_instruction_notifications import DB


class Item(BaseModel):
    order_item_id: str
    name: str = "Test product"
    quantity: int = 1


class Order(BaseModel):
    order_number: str = "1"
    status: str = "processing"
    items: list[Item]


def server_dependency(db, authenticated_user):
    # Execute the real request adapter and canonical legacy permission resolver,
    # excluding unrelated server startup/integration side effects.
    names = {"PERMISSIONS_CATALOGUE", "ROLE_DEFAULT_PERMS", "_effective_perms", "current_user"}
    tree = ast.parse(Path(routes.__file__).with_name("server.py").read_text(encoding="utf-8-sig"))
    selected = [node for node in tree.body if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
        or isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id in names
    )]
    namespace = {"Request": Request, "db": db, "get_current_user_from_db": authenticated_user}
    exec(compile(ast.Module(body=selected, type_ignores=[]), "server_instruction_auth", "exec"), namespace)
    return namespace["current_user"]


class OrdersInstructionPermissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = DB()
        self.actor = {"id": "staff", "role": "viewer", "created_by": "owner", "_session_client": "amasi_mobile"}
        self.db.raw.users.insert_many([
            {"id": "owner", "role": "owner"},
            {"id": "staff", "role": "viewer", "created_by": "owner"},
            {"id": "worker", "role": "employee", "created_by": "owner"},
        ])
        self.db.raw[MOBILE_APP_ACCESS].insert_one({"owner_user_id": "owner", "user_id": "staff", "enabled": True, "permissions": ["app.page.orders"]})
        self.db.raw[routes.PIECES].insert_many([
            {"user_id": "owner", "order_number": "1", "piece_id": "p1", "order_item_id": "i1", "responsible_employee_id": "worker", "status": "in_progress"},
            {"user_id": "owner", "order_number": "1", "piece_id": "p2", "order_item_id": "i2", "responsible_employee_id": "worker", "status": "in_progress"},
        ])
        self.db.raw[routes.WORKFLOWS].insert_one({"user_id": "owner", "order_number": "1", "stage": "in_progress"})
        current = server_dependency(self.db, AsyncMock(side_effect=lambda *_: dict(self.actor)))
        app = FastAPI(); app.include_router(routes.make_order_tracking_notes_router(self.db, current), prefix="/api")
        self.client = TestClient(app)
        self.order_patch = patch.object(routes, "get_order", AsyncMock(return_value=Order(items=[Item(order_item_id="i1"), Item(order_item_id="i2")])))
        self.order_mock = self.order_patch.start()
        self.push_patch = patch.object(notifications, "_enqueue", AsyncMock()); self.push_patch.start()

    def tearDown(self):
        self.order_patch.stop(); self.push_patch.stop(); self.client.close()

    def create(self, action="general", **extra):
        return self.client.post("/api/order-tracking-notes/orders/1/instructions", json={
            "scope": "order", "action_type": action, "note": "Synthetic instruction", "target_stages": ["current_stage"], **extra,
        })

    async def test_native_orders_staff_can_read_and_create_with_own_identity(self):
        response = self.client.get("/api/order-tracking-notes/orders/1")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["capabilities"]["can_manage"])
        response = self.create()
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()["instruction"]
        self.assertEqual(row["created_by"], "staff")
        self.assertFalse(row["operational_hold"])
        stored = self.db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one({"id": row["id"]})
        self.assertEqual(stored["user_id"], "owner")
        self.assertEqual(self.order_mock.call_args.kwargs["user_id"], "owner")

    async def test_orders_staff_can_stop_one_product_without_freezing_sibling(self):
        response = self.create("edit_product", scope="item", target_ids=["i1"], edit_flow=True)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["instruction"]["created_by"], "staff")
        self.assertEqual(self.db.raw[routes.PIECES].find_one({"piece_id": "p1"})["status"], "blocked")
        self.assertEqual(self.db.raw[routes.PIECES].find_one({"piece_id": "p2"})["status"], "in_progress")

    async def test_other_app_page_or_disabled_access_cannot_create(self):
        for patch_value in ({"permissions": ["app.page.my_products"]}, {"enabled": False}):
            self.db.raw[MOBILE_APP_ACCESS].update_one({"user_id": "staff"}, {"$set": patch_value})
            self.assertEqual(self.create().status_code, 403)
        self.assertEqual(self.db.raw[ORDER_TRACKING_INSTRUCTIONS].count_documents({}), 0)

    async def test_browser_uses_actual_orders_role_defaults_and_denials(self):
        self.actor.pop("_session_client")
        self.assertEqual(self.create().status_code, 201)  # viewer has orders.view
        self.actor.update(denied_permissions=["orders.view"], _tracking_orders_page_allowed=True)
        self.assertEqual(self.create().status_code, 403)  # stored flag is overwritten
        self.actor.update(role="employee", denied_permissions=[], extra_permissions=["orders.view"])
        self.assertEqual(self.create().status_code, 201)

    async def test_native_grant_does_not_grant_browser_or_delivery_creation(self):
        self.actor.update(role="employee", _session_client=None, mobile_app_permissions=["app.page.orders"])
        self.assertEqual(self.create().status_code, 403)
        self.actor.update(role="store_driver", _session_client="amasi_mobile")
        self.assertEqual(self.create().status_code, 403)

    async def test_orders_access_does_not_override_explicit_instruction_denial(self):
        self.actor["denied_permissions"] = [routes.TRACKING_MANAGE_PERMISSION]
        self.assertEqual(self.create().status_code, 403)

    async def test_orders_staff_cannot_approve_someone_elses_stop(self):
        self.db.raw[ORDER_TRACKING_INSTRUCTIONS].insert_one({"id": "other", "user_id": "owner", "created_by": "other-staff", "action_type": "edit_order", "status": "waiting_customer_service_approval"})
        response = self.client.post("/api/order-tracking-notes/instructions/other/approve", json={"note": "Approve"})
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"]["code"], "instruction_creator_resume_required")

    async def test_missing_store_link_fails_and_owner_keeps_access(self):
        self.actor = {"id": "stranger", "role": "viewer"}
        self.assertEqual(self.create().status_code, 409)
        self.actor = {"id": "owner", "role": "owner"}
        self.assertEqual(self.create().status_code, 201)
