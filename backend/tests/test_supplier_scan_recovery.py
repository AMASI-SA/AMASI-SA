"""Build20 supplier scan lost-response/idempotency contract.

Uses a disposable loopback MongoDB. No production data, providers, Salla, Qoyod,
invoice repair, merge or deployment.
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

import supplier_receiving_routes as r

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def env(monkeypatch):
    url = os.environ.get("BUILD20_SCAN_TEST_MONGO_URL", "")
    assert urlparse(url).hostname in {"127.0.0.1", "localhost"}
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=5000)
    await client.admin.command("ping")
    db = client["build20_scan_" + uuid.uuid4().hex]
    identity = {"id": "receiver"}

    async def base_context(_db, user):
        return {
            "merchant_id": "merchant",
            "actor_id": user["id"],
            "is_owner": False,
            "permissions": [r.RECEIVE_PERMISSION],
        }

    async def current_user():
        return {"id": identity["id"], "name": "Synthetic receiver"}

    async def no_stage_block(*_args, **_kwargs):
        return None

    async def product_reference(*_args, **_kwargs):
        return {
            "reference_product_unit_price_halalas": 2100,
            "reference_product_price_complete": True,
            "reference_product_price_source": "mezan_v2_base",
            "product_price_authority": "mezan_v2",
            "salla_price_fallback_allowed": False,
        }

    monkeypatch.setattr(r, "_base_actor_context", base_context)
    monkeypatch.setattr(r, "enforce_stage_instructions", no_stage_block)
    monkeypatch.setattr(r, "_supplier_product_reference_price", product_reference)

    app = FastAPI()
    app.include_router(r.make_supplier_receiving_router(db, current_user))
    await r.ensure_supplier_receiving_indexes(db)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://synthetic.test",
    ) as http:
        yield db, http, identity
    await client.drop_database(db.name)
    client.close()


async def seed(env, count=1, *, session_id="session-1", status=r.PIECE_STATUS_IN_PROGRESS):
    db, _http, _identity = env
    now = datetime.now(timezone.utc)
    session = {
        "id": session_id,
        "user_id": "merchant",
        "client_request_id": "session-request-" + uuid.uuid4().hex,
        "reference": "SR-SYNTHETIC",
        "status": "open",
        "supplier_id": "supplier-1",
        "supplier_snapshot": {
            "id": "supplier-1",
            "company_name": "Synthetic supplier",
            "service_links": [],
        },
        "opened_by": "receiver",
        "opened_by_name": "Synthetic receiver",
        "opened_at": now,
        "scan_count": 0,
        "order_numbers": [],
        "file_numbers": [],
    }
    await db[r.SESSIONS].insert_one(dict(session))
    pieces = []
    for index in range(1, count + 1):
        piece_id = f"{index:032x}"
        piece = {
            "user_id": "merchant",
            "piece_id": piece_id,
            "group_key": "group-1",
            "batch_id": "batch-1",
            "file_number": "file-1",
            "order_number": "123456789",
            "order_item_id": "item-1",
            "unit_index": index,
            "product_id": "product-1",
            "product_name": "Synthetic item",
            "sku": "SKU-1",
            "selected_image_url": "",
            "status": status,
            "execution_status": "supplier_sent",
            "supplier_id": "supplier-1",
            "supplier_name": "Synthetic supplier",
            "supplier_dispatch_status": "sent",
            "responsible_employee_id": "preparer",
            "responsible_employee_name": "Synthetic preparer",
            "services": [],
        }
        await db[r.PIECES].insert_one(dict(piece))
        pieces.append(piece)
    return session, pieces


def barcode(piece):
    return "MEZAN-PIECE:" + piece["piece_id"]


async def post_scan(http, session_id, piece, request_id=None, quantity=None):
    payload = {"barcode": barcode(piece)}
    if request_id is not None:
        payload["client_request_id"] = request_id
    if quantity is not None:
        payload["quantity"] = quantity
    return await http.post(
        f"/supplier-receiving-v1/sessions/{session_id}/scan",
        json=payload,
    )


async def test_successful_scan_commits_exactly_once(env):
    db, http, _ = env
    session, pieces = await seed(env)
    response = await post_scan(http, session["id"], pieces[0], "scan-request-0001")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["idempotent"] is False
    assert [row["piece_id"] for row in body["scans"]] == [pieces[0]["piece_id"]]
    saved = await db[r.SESSIONS].find_one({"id": session["id"]})
    assert saved["scan_count"] == 1
    assert await db[r.RECEIVING_EVENTS].count_documents(
        {"event_type": "supplier_piece_scanned"}
    ) == 1


async def test_same_request_same_payload_is_idempotent(env):
    db, http, _ = env
    session, pieces = await seed(env)
    request_id = "scan-request-0002"
    first = await post_scan(http, session["id"], pieces[0], request_id)
    second = await post_scan(http, session["id"], pieces[0], request_id)
    assert first.status_code == second.status_code == 200
    assert second.json()["idempotent"] is True
    assert [row["piece_id"] for row in second.json()["scans"]] == [
        row["piece_id"] for row in first.json()["scans"]
    ]
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == 1
    assert await db[r.RECEIVING_EVENTS].count_documents(
        {"client_request_id": request_id}
    ) == 1


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload, other: payload.update(barcode=barcode(other)),
        lambda payload, other: payload.update(quantity=2),
    ],
)
async def test_same_request_different_payload_conflicts_without_writes(env, mutator):
    db, http, _ = env
    session, pieces = await seed(env, 2)
    request_id = "scan-request-0003"
    first = await post_scan(http, session["id"], pieces[0], request_id, 1)
    assert first.status_code == 200
    before_count = (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"]
    before_events = await db[r.RECEIVING_EVENTS].count_documents({})
    payload = {
        "client_request_id": request_id,
        "barcode": barcode(pieces[0]),
        "quantity": 1,
    }
    mutator(payload, pieces[1])
    response = await http.post(
        f"/supplier-receiving-v1/sessions/{session['id']}/scan",
        json=payload,
    )
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "supplier_receiving_scan_request_conflict"
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == before_count
    assert await db[r.RECEIVING_EVENTS].count_documents({}) == before_events


async def test_lost_response_recovery_reads_committed_request_without_post(env):
    db, http, _ = env
    session, pieces = await seed(env)
    request_id = "scan-request-0004"
    committed = await post_scan(http, session["id"], pieces[0], request_id)
    assert committed.status_code == 200
    recovered = await http.get(
        f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/{request_id}"
    )
    assert recovered.status_code == 200
    body = recovered.json()
    assert body["found"] is True and body["committed"] is True
    assert body["idempotent"] is True and body["recovered"] is True
    assert [row["piece_id"] for row in body["scans"]] == [pieces[0]["piece_id"]]
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == 1


async def test_recovery_before_commit_is_found_false(env):
    _db, http, _ = env
    session, _pieces = await seed(env)
    response = await http.get(
        f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/scan-request-0005"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["found"] is False and body["committed"] is False
    assert body["scans"] == []


async def test_same_piece_same_session_recovers_existing_scan(env):
    db, http, _ = env
    session, pieces = await seed(env)
    first = await post_scan(http, session["id"], pieces[0], "scan-request-0006")
    assert first.status_code == 200
    # A human re-scan uses a new attempt ID. Exact physical identity plus the
    # same receiving session resolves the already-persisted event read-only.
    second = await post_scan(http, session["id"], pieces[0], "scan-request-0007")
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["same_session_recovery"] is True and body["idempotent"] is True
    assert body["scan"]["piece_id"] == pieces[0]["piece_id"]
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == 1
    assert await db[r.RECEIVING_EVENTS].count_documents(
        {"event_type": "supplier_piece_scanned"}
    ) == 1


async def test_piece_in_different_session_remains_real_conflict(env):
    db, http, _ = env
    session, pieces = await seed(env)
    await db[r.PIECES].update_one(
        {"piece_id": pieces[0]["piece_id"]},
        {"$set": {
            "supplier_receiving_session_id": "other-session",
            "receipt_event_id": "other-event",
        }},
    )
    response = await post_scan(http, session["id"], pieces[0], "scan-request-0008")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "supplier_piece_already_in_receiving_session"
    assert detail["same_session"] is False
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == 0


async def test_truly_received_piece_remains_already_received(env):
    db, http, _ = env
    session, pieces = await seed(env)
    await db[r.PIECES].update_one(
        {"piece_id": pieces[0]["piece_id"]},
        {"$set": {
            "status": r.PIECE_STATUS_RECEIVED,
            "received_at": datetime.now(timezone.utc),
        }},
    )
    response = await post_scan(http, session["id"], pieces[0], "scan-request-0009")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "supplier_piece_already_received"
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == 0


async def test_quantity_twenty_recovery_returns_exact_same_piece_ids_once(env):
    db, http, _ = env
    session, pieces = await seed(env, 20)
    request_id = "scan-request-0020"
    first = await post_scan(http, session["id"], pieces[0], request_id, 20)
    assert first.status_code == 200, first.text
    expected = [row["piece_id"] for row in first.json()["scans"]]
    assert len(expected) == 20 and len(set(expected)) == 20
    recovered = await http.get(
        f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/{request_id}"
    )
    assert recovered.status_code == 200
    body = recovered.json()
    assert body["found"] is True and body["committed"] is True
    assert [row["piece_id"] for row in body["scans"]] == expected
    replay = await post_scan(http, session["id"], pieces[0], request_id, 20)
    assert replay.status_code == 200
    assert [row["piece_id"] for row in replay.json()["scans"]] == expected
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == 20
    assert await db[r.RECEIVING_EVENTS].count_documents(
        {"client_request_id": request_id}
    ) == 20


async def test_build19_request_without_client_request_id_still_works(env):
    db, http, _ = env
    session, pieces = await seed(env)
    response = await post_scan(http, session["id"], pieces[0], None)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True and body["client_request_id"] is None
    event = await db[r.RECEIVING_EVENTS].find_one(
        {"event_type": "supplier_piece_scanned"}
    )
    assert "client_request_id" not in event
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == 1


async def test_recovery_route_uses_same_actor_session_authorization(env):
    _db, http, identity = env
    session, _pieces = await seed(env)
    identity["id"] = "other-employee"
    response = await http.get(
        f"/supplier-receiving-v1/sessions/{session['id']}/scan-requests/scan-request-auth"
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "supplier_receiving_session_owner_required"


async def test_remove_exact_piece_from_hundred_piece_draft_and_rescan(env):
    db, http, _ = env
    session, pieces = await seed(env, 100)
    scanned = await post_scan(http, session["id"], pieces[0], "scan-request-hundred", 100)
    assert scanned.status_code == 200, scanned.text
    assert len(scanned.json()["scans"]) == 100
    target = pieces[47]
    url = f"/supplier-receiving-v1/sessions/{session['id']}/scans"
    before = await db[r.RECEIVING_EVENTS].count_documents({})

    preview = await http.get(f"{url}/lookup", params={"barcode": barcode(target)})
    assert preview.status_code == 200, preview.text
    assert preview.json()["piece_id"] == target["piece_id"]
    assert preview.json()["receipt_provenance"]["received_by_name"] == "Synthetic receiver"
    event_id = preview.json()["event_id"]
    assert await db[r.RECEIVING_EVENTS].count_documents({}) == before
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == 100

    removed = await http.post(f"{url}/remove", json={
        "barcode": barcode(target), "expected_event_id": event_id,
    })
    assert removed.status_code == 200, removed.text
    assert removed.json()["session"]["scan_count"] == 99
    assert (await db[r.PIECES].find_one({"piece_id": target["piece_id"]})).get(
        "supplier_receiving_session_id"
    ) is None
    assert await db[r.RECEIVING_EVENTS].count_documents(
        {"event_type": "supplier_piece_scanned"}
    ) == 99
    assert await db[r.RECEIVING_EVENTS].count_documents(
        {"event_type": "supplier_piece_scan_cancelled"}
    ) == 1
    assert await db[r.PIECE_EVENTS].count_documents(
        {"id": event_id, "event_type": "supplier_piece_scan_cancelled"}
    ) == 1

    retry = await http.post(f"{url}/remove", json={
        "barcode": barcode(target), "expected_event_id": event_id,
    })
    assert retry.status_code == 200, retry.text
    assert retry.json()["idempotent"] is True
    assert await db[r.RECEIVING_EVENTS].count_documents({}) == before

    fresh = await post_scan(http, session["id"], target, "scan-request-after-cancel")
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["scan"]["id"] != event_id
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == 100
    assert await db[r.RECEIVING_EVENTS].count_documents(
        {"event_type": "supplier_piece_scanned"}
    ) == 100
    stale = await http.post(f"{url}/remove", json={
        "barcode": barcode(target), "expected_event_id": event_id,
    })
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "supplier_receiving_piece_changed_since_preview"


async def test_remove_requires_current_actor_and_exact_preview_without_financial_writes(env):
    db, http, identity = env
    session, pieces = await seed(env, 2)
    scanned = await post_scan(http, session["id"], pieces[0], "scan-request-guarded", 1)
    assert scanned.status_code == 200
    preview = await http.get(
        f"/supplier-receiving-v1/sessions/{session['id']}/scans/lookup",
        params={"barcode": barcode(pieces[0])},
    )
    assert preview.status_code == 200, preview.text
    event_id = preview.json()["event_id"]
    path = f"/supplier-receiving-v1/sessions/{session['id']}/scans/remove"
    wrong = await http.post(path, json={
        "barcode": barcode(pieces[1]), "expected_event_id": event_id,
    })
    assert wrong.status_code == 409
    assert wrong.json()["detail"]["code"] == "supplier_receiving_piece_not_in_current_draft"
    forged = await http.post(path, json={
        "barcode": barcode(pieces[0]), "expected_event_id": "stale-event",
    })
    assert forged.status_code == 409
    assert forged.json()["detail"]["code"] == "supplier_receiving_piece_changed_since_preview"
    legacy = await http.post(path, json={
        "barcode": "SKU-1", "expected_event_id": event_id,
    })
    assert legacy.status_code == 422
    identity["id"] = "different-employee"
    denied = await http.post(path, json={
        "barcode": barcode(pieces[0]), "expected_event_id": event_id,
    })
    assert denied.status_code == 403
    assert (await db[r.SESSIONS].find_one({"id": session["id"]}))["scan_count"] == 1
    assert await db[r.SUPPLIER_INVOICES].count_documents({}) == 0
    assert await db[r.RECEIVING_EVENTS].count_documents({"event_type": "supplier_piece_scanned"}) == 1


async def test_duplicate_receipt_explains_prior_invoice_and_employee(env):
    db, http, _ = env
    session, pieces = await seed(env)
    received_at = datetime(2026, 9, 20, 11, 30, tzinfo=timezone.utc)
    await db[r.PIECES].update_one(
        {"piece_id": pieces[0]["piece_id"]},
        {"$set": {"status": r.PIECE_STATUS_RECEIVED, "received_at": received_at,
                  "supplier_receiving_history": [{
                      "invoice_id": "invoice-previous", "session_reference": "SR-OLD",
                      "supplier_name": "Synthetic old supplier", "received_by_name": "Earlier receiver",
                      "received_at": received_at,
                  }]}},
    )
    response = await post_scan(http, session["id"], pieces[0], "scan-request-prior-invoice")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail["code"] == "supplier_piece_already_received"
    assert detail["receipt_scope"] == "previous_invoice"
    assert detail["invoice_id"] == "invoice-previous"
    assert detail["supplier_name"] == "Synthetic old supplier"
    assert detail["received_by_name"] == "Earlier receiver"
    assert detail["received_at"].startswith("2026-09-20T11:30:00")
