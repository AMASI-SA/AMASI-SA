"""Exercise the installed production capture wrapper in a fresh interpreter.

Importing easy_mode_webhook directly bypasses the package installer's ordering.
These tests mount the canonical factory, retain its real dispatcher/capture,
and use only synthetic in-memory storage with network access forbidden.
"""
import asyncio
import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest


BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("scenario", ["completed", "strict_failure", "legacy_failure"])
def test_production_installed_capture_contract(scenario):
    # Do not inherit database URLs, provider credentials or cached app modules.
    environment = {
        "PATH": os.defpath,
        "PYTHONPATH": str(BACKEND),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "SALLA_ORDERS_V3_SHADOW_ENABLED": "false",
        "MEZAN_SNAPCHAT_CAPI_ENABLED": "false",
        "JWT_SECRET": "synthetic-installed-capture-test-secret",
    }
    result = subprocess.run(
        [sys.executable, "-B", str(Path(__file__).resolve()), scenario],
        cwd=BACKEND, env=environment, capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


async def _exercise_installed_capture(scenario):
    from cryptography.fernet import Fernet
    from fastapi import APIRouter
    from mongomock_motor import AsyncMongoMockClient, AsyncMongoMockCollection

    os.environ["MEZAN_CUSTOMER_PII_ENC_KEY"] = Fernet.generate_key().decode()
    db = AsyncMongoMockClient(tz_aware=True).installed_capture_test
    await db.salla_integrations.insert_one({
        "store_id": "synthetic-installed-store", "user_id": "synthetic-owner",
    })
    fake_server = ModuleType("server")

    async def current_user():
        raise AssertionError("The queue processor must not require HTTP authentication")

    fake_server.current_user = current_user
    sys.modules["server"] = fake_server
    from salla_integration import attach_salla_routes

    assert "salla_integration.easy_mode_webhook" not in sys.modules
    attach_salla_routes(APIRouter(), db)
    from salla_integration import easy_mode_webhook as easy
    from salla_integration import webhook_event_capture as capture
    from salla_integration import webhook_inbox as inbox

    assert easy.capture_unknown_event is capture.capture_unknown_event
    assert getattr(easy.capture_unknown_event, "_mezan_cart_reconciliation_installed", False)

    # mongomock_motor otherwise returns a synchronous collection here. This
    # storage adapter does not replace the inbox processor or business capture.
    def with_options(collection, **options):
        assert options["write_concern"].document == {"w": "majority", "j": True, "wtimeout": 5000}
        return collection

    body = {
        "event": "product.updated", "merchant": "synthetic-installed-store",
        "data": {"id": "synthetic-product", "name": "Synthetic product"},
    }
    with patch.object(AsyncMongoMockCollection, "with_options", with_options, create=True):
        if scenario == "completed":
            receipt = await inbox.enqueue_verified_event(db, body)
            counts = await inbox.run_once(db)
            row = await db[inbox.COLLECTION].find_one({"_id": receipt["job_id"]})
            assert counts["done"] == 1, (counts, row.get("last_error_code"))
            assert row["state"] == "done" and row["attempts"] == 1
            assert "payload_ciphertext" not in row
            audit = await db.salla_webhook_event_captures.find_one({})
            assert audit["verified_before_capture"] is True
            assert audit["processing_retry_required"] is False
            assert audit["delivery_count"] == 1
            duplicate = await inbox.enqueue_verified_event(db, body)
            assert duplicate["duplicate"] is True
            assert (await inbox.run_once(db))["claimed"] == 0
            assert await db[inbox.COLLECTION].count_documents({}) == 1
            assert await db.salla_webhook_event_captures.count_documents({}) == 1
            assert (await db.salla_webhook_event_captures.find_one({}))["delivery_count"] == 1
        else:
            body["event"] = "order.created"
            failed_order = {"attempted": True, "synced": False,
                            "reason": "order_webhook_persist_failed"}
            with patch.object(capture, "sync_order_from_verified_webhook",
                              AsyncMock(return_value=failed_order)):
                if scenario == "strict_failure":
                    with pytest.raises(capture.WebhookProcessingIncomplete,
                                       match="^salla_webhook_processing_incomplete$"):
                        await easy.dispatch_event(db, body, raise_on_processing_error=True,
                                                  expected_user_id="synthetic-owner")
                else:
                    result = await easy.dispatch_event(db, body)
                    assert result["ok"] is True
                    assert result["capture"]["processing_retry_required"] is True
            audit = await db.salla_webhook_event_captures.find_one({})
            assert audit["processing_retry_required"] is True
            assert audit["order_sync"]["reason"] == "order_webhook_persist_failed"
        assert await db.unified_orders.count_documents({}) == 0


if __name__ == "__main__":
    with patch("socket.socket.connect", side_effect=AssertionError("network forbidden")) as connect, \
         patch("socket.socket.connect_ex", side_effect=AssertionError("network forbidden")) as connect_ex, \
         patch("socket.getaddrinfo", side_effect=AssertionError("network forbidden")) as dns:
        asyncio.run(_exercise_installed_capture(sys.argv[1]))
        connect.assert_not_called()
        connect_ex.assert_not_called()
        dns.assert_not_called()
