from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from test_operational_instruction_notifications import DB
import mobile_employee_monitoring_workspace_routes as routes
import operational_instruction_notifications as alerts
from operational_instruction_progress import refresh_instruction_progress
from order_tracking_notes import ORDER_TRACKING_INSTRUCTIONS


@pytest.fixture
def fixture():
    db = DB()
    for uid in ("owner", "manager", "worker", "other"):
        db.raw.users.insert_one({"id": uid, "name": uid, "role": "owner" if uid == "owner" else "employee", "created_by": "owner"})
    db.raw.mezan_employees_v2.insert_one({"id": "employee", "account_user_id": "worker", "user_id": "owner"})
    db.raw.mezan_preparation_pieces_v1.insert_one({"user_id": "owner", "piece_id": "p", "order_item_id": "i", "order_number": "1",
        "responsible_employee_id": "worker", "status": "in_progress", "supplier_dispatch_status": "sent", "assigned_at": datetime.now(timezone.utc), "image_url": "product.png"})
    async def current():
        return db.raw.users.find_one({"id": "manager"})
    async def access(*args):
        return {"permissions": [routes.MONITORING_PERMISSION]}
    app = FastAPI()
    app.include_router(routes.make_mobile_employee_monitoring_workspace_router(db, current))
    with patch.object(routes, "mobile_app_access_for_user", access), patch.object(alerts, "_enqueue", AsyncMock()):
        with TestClient(app) as client:
            yield db, client


def test_urgency_is_piece_scoped_nonblocking_and_deduplicated(fixture):
    db, client = fixture
    path = "/mobile/operations-monitoring/preparation/employee/pieces/p/urgency"
    original = db.raw.mezan_preparation_pieces_v1.find_one()
    first, second = client.post(path), client.post(path)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    assert db.raw[ORDER_TRACKING_INSTRUCTIONS].count_documents({}) == 1
    row = db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one()
    assert row["created_by"] == "manager"
    assert row["target_ids"] == ["p"]
    assert row["enforcement"] == "notice" and not row["approval_required"]
    assert db.raw[alerts.NOTIFICATIONS].count_documents({"recipient_id": "worker", "event_type": "created"}) == 1
    assert db.raw.mezan_preparation_pieces_v1.find_one() == original
    db.raw.mezan_preparation_pieces_v1.update_one({"piece_id": "p"}, {"$set": {"responsible_employee_id": "other"}})
    assert client.post(path).status_code == 404


@pytest.mark.asyncio
async def test_partial_receipt_remains_active_and_full_receipt_notifies_sender_once(fixture):
    db, client = fixture
    assert client.post("/mobile/operations-monitoring/preparation/employee/pieces/p/urgency").status_code == 200
    row = db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one()
    db.raw.mezan_preparation_pieces_v1.update_one({"piece_id": "p"}, {"$set": {"supplier_dispatch_status": "partial_received"}})
    await refresh_instruction_progress(db, row)
    assert "worker" in await alerts.recipients(db, "owner", row)
    assert db.raw[alerts.NOTIFICATIONS].count_documents({"event_type": "supplier_received"}) == 0
    # "ready" at the supplier is not a physical receipt and cannot close the note.
    db.raw.mezan_preparation_pieces_v1.update_one({"piece_id": "p"}, {"$set": {"status": "ready_for_employee_receipt", "supplier_dispatch_status": "ready"}})
    await refresh_instruction_progress(db, row)
    assert "worker" in await alerts.recipients(db, "owner", row)
    assert db.raw[alerts.NOTIFICATIONS].count_documents({"event_type": "supplier_received"}) == 0
    db.raw.mezan_preparation_pieces_v1.update_one({"piece_id": "p"}, {"$set": {"status": "received", "supplier_dispatch_status": "received", "remaining_service_count": 0}})
    await refresh_instruction_progress(db, row)
    await refresh_instruction_progress(db, row)
    assert "worker" not in await alerts.recipients(db, "owner", row)
    completed = list(db.raw[alerts.NOTIFICATIONS].find({"event_type": "supplier_received"}))
    assert len(completed) == 1
    assert completed[0]["recipient_id"] == "manager"
    assert completed[0]["metadata"]["image_url"] == "product.png"
    assert completed[0]["read_at"] is None
    assert db.raw[ORDER_TRACKING_INSTRUCTIONS].find_one()["status"] == "active"


def test_completed_piece_and_permission_denial_never_create_urgency(fixture):
    db, client = fixture
    path = "/mobile/operations-monitoring/preparation/employee/pieces/p/urgency"
    db.raw.mezan_preparation_pieces_v1.update_one({"piece_id": "p"}, {"$set": {"status": "ready_for_employee_receipt"}})
    assert client.post(path).status_code == 409
    async def denied(*args):
        return {"permissions": []}
    with patch.object(routes, "mobile_app_access_for_user", denied):
        assert client.post(path).status_code == 403
    assert db.raw[ORDER_TRACKING_INSTRUCTIONS].count_documents({}) == 0
