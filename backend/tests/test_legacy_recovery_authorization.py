"""Revalidate the native actor, not the request's substituted owner."""
import pytest
from fastapi import HTTPException
from fulfillment_carrier_label import fresh_recovery_principal
from test_mobile_app_request_context import DB, staff


@pytest.mark.asyncio
async def test_native_actor_revoked_page_grant_cannot_reuse_owner_principal():
    db = DB()
    db.users.rows.append(staff())
    principal = {"id": "owner-1", "role": "owner", "_mobile_actor_id": "staff-1",
                 "_session_client": "amasi_mobile"}
    path = "/api/preparation-work-v1/assembly/orders/synthetic/completion/resume"
    grant = db.cols["mezan_mobile_app_access_v1"].rows[0]
    grant["permissions"] = ["app.page.assembly_shipping"]
    current = await fresh_recovery_principal(db, principal, path=path)
    assert current["_mobile_actor_id"] == "staff-1"
    grant["permissions"] = []
    with pytest.raises(HTTPException) as caught:
        await fresh_recovery_principal(db, principal, path=path)
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_deleted_actor_cannot_reuse_owner_principal():
    with pytest.raises(HTTPException) as caught:
        await fresh_recovery_principal(DB(), {"id": "owner-1", "_mobile_actor_id": "missing"},
                                       path="/api/orders-v2/synthetic/shipping-label")
    assert caught.value.status_code == 403


@pytest.mark.asyncio
async def test_current_browser_owner_retains_existing_authority():
    current = await fresh_recovery_principal(DB(), {"id": "owner-1", "role": "owner"},
                                           path="/api/orders-v2/synthetic/shipping-label")
    assert current["id"] == "owner-1"
