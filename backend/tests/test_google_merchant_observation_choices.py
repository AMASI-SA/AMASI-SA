"""Local choices through the actual owner principal, snapshots and HTTP route."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone

from fastapi import APIRouter, FastAPI
import httpx
import pytest

from integrations_control_center import google_merchant_observation_choices as choices
from tests.test_google_merchant_observation_routes import DB, OWNER, ACCOUNT, MERCHANT, USER, CREDS, ACCOUNTS, INTEGRATIONS


def local_db():
    db = DB()
    del db.rows[CREDS]
    db.rows[ACCOUNTS][0]["display_name"] = "إعلانات المتجر"
    db.rows[ACCOUNTS][1]["display_name"] = "كتالوج المتجر"
    return db


async def call(db, *, user=None, params=None):
    async def current_user(): return deepcopy(USER if user is None else user)
    app, router = FastAPI(), APIRouter(prefix="/integrations-v2")
    choices.attach_google_merchant_observation_choices(router, db, current_user)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://synthetic.invalid") as client:
        return await client.get("/integrations-v2/google_ads/merchant-products/choices", params=params)


@pytest.mark.asyncio
async def test_complete_local_choices_preserve_ids_names_and_no_provider_authority():
    db = local_db()
    before = deepcopy(db.rows)
    response = await call(db)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["owner_id"] == OWNER and data["source"] == "mezan_google_local_membership"
    assert data["ads_accounts"] == [{"account_id": ACCOUNT, "display_name": "إعلانات المتجر"}]
    assert data["merchant_accounts"] == [{"merchant_id": MERCHANT, "display_name": "كتالوج المتجر"}]
    assert data["local_set_complete"] is True and data["local_account_count"] == 2
    assert all(data[name] is False for name in ("remote_account_set_complete", "native_link_verified",
        "merchant_access_verified", "continuous_authorization_proved", "store_product_mapping_verified",
        "ads_eligibility_verified", "execution_authorized"))
    assert data["provider_calls"] == data["provider_writes"] == 0
    assert db.rows == before and db.transactions == db.sessions_closed == 2
    assert {read[0] for read in db.reads} == {"users", ACCOUNTS, INTEGRATIONS}
    assert all(read[3] is True for read in db.reads)
    assert "password_updated_at" not in response.text and "ciphertext" not in response.text
    assert "selected" not in response.text and "default" not in response.text


@pytest.mark.asyncio
async def test_empty_local_set_is_not_remote_absence_or_default():
    db = local_db(); db.rows[ACCOUNTS] = []
    data = (await call(db)).json()
    assert data["local_account_count"] == 0 and data["local_set_complete"] is True
    assert data["ads_accounts"] == data["merchant_accounts"] == []
    assert data["remote_account_set_complete"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("count,status", [(400, 200), (401, 409)])
async def test_full_set_boundary_without_silent_first50_or_per_provider_truncation(count, status):
    db = local_db()
    row = deepcopy(db.rows[ACCOUNTS][1])
    db.rows[ACCOUNTS] = [{**row, "_id": str(i), "external_account_id": str(i + 1)} for i in range(count)]
    response = await call(db)
    assert response.status_code == status
    if status == 200:
        assert len(response.json()["merchant_accounts"]) == count
        assert response.json()["local_account_count"] == count
    else:
        assert "merchant_accounts" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("external_account_id", None), ("external_account_id", 123),
    ("external_account_id", "0123"), ("external_account_id", "1-23"), ("external_account_id", "9223372036854775808"),
    ("external_account_id", "0"), ("ad_account_id", "999"), ("connection_status", None),
    ("connection_status", "error"), ("connection_provenance", None), ("connection_provenance", "data_feed"),
    ("display_name", 123), ("display_name", ""), ("display_name", "x" * 257), ("display_name", "name\nPRIVATE"),
    ("login_customer_id", "invalid")])
async def test_any_invalid_row_refuses_entire_set_before_selection(field, value):
    db = local_db(); db.rows[ACCOUNTS][1][field] = value
    response = await call(db)
    assert response.status_code == 409
    assert "PRIVATE" not in response.text and "accounts" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["provider_missing", "provider_duplicate", "provider_error", "provider_provenance_missing",
    "account_duplicate", "account_identity_missing", "account_status_missing", "account_provenance_missing",
    "owner_duplicate", "owner_missing", "owner_disabled", "owner_inactive", "owner_deleted"])
async def test_incomplete_ambiguous_and_revoked_local_evidence_is_not_a_candidate_list(change):
    db = local_db()
    if change == "provider_missing": db.rows[INTEGRATIONS].pop()
    elif change == "provider_duplicate": db.rows[INTEGRATIONS].append(deepcopy(db.rows[INTEGRATIONS][0]))
    elif change == "provider_error": db.rows[INTEGRATIONS][0]["connection_status"] = "error"
    elif change == "provider_provenance_missing": del db.rows[INTEGRATIONS][0]["connection_provenance"]
    elif change == "account_duplicate": db.rows[ACCOUNTS].append({**db.rows[ACCOUNTS][0], "_id": "replacement"})
    elif change == "account_identity_missing": del db.rows[ACCOUNTS][0]["_id"]
    elif change == "account_status_missing": del db.rows[ACCOUNTS][0]["connection_status"]
    elif change == "account_provenance_missing": del db.rows[ACCOUNTS][0]["connection_provenance"]
    elif change == "owner_duplicate": db.rows["users"].append(deepcopy(db.rows["users"][0]))
    elif change == "owner_missing": db.rows["users"] = []
    elif change == "owner_disabled": db.rows["users"][0]["disabled"] = True
    elif change == "owner_inactive": db.rows["users"][0]["is_active"] = False
    elif change == "owner_deleted": db.rows["users"][0]["deleted_at"] = "yesterday"
    response = await call(db)
    assert response.status_code in {403, 409}
    assert "ads_accounts" not in response.json()


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [("disabled", None), ("is_active", None),
    ("password_updated_at", datetime(2026, 10, 29, tzinfo=timezone.utc))])
async def test_auth_dependency_snapshot_binding_preserves_missing_null_and_password_change(field, value):
    db = local_db(); db.rows["users"][0][field] = value
    response = await call(db)
    assert response.status_code == 403
    assert db.transactions == 1
    user = {**USER, field: value}
    assert (await call(db, user=user)).status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["owner_password", "owner_update", "account_add", "account_delete", "account_name",
    "provider_revision", "absent_null", "bson_type"])
async def test_second_snapshot_fences_all_local_changes(monkeypatch, change):
    db = local_db()
    if change == "bson_type": db.rows[ACCOUNTS][0]["mezan_selected"] = 0
    original = choices._snapshot
    calls = 0
    async def changed(*args):
        nonlocal calls
        result = await original(*args); calls += 1
        if calls == 1:
            if change == "owner_password": db.rows["users"][0]["password_updated_at"] = datetime.now(timezone.utc)
            elif change == "owner_update": db.rows["users"][0]["updated_at"] = "new"
            elif change == "account_add": db.rows[ACCOUNTS].append({**db.rows[ACCOUNTS][1], "_id": "added", "external_account_id": "456"})
            elif change == "account_delete": db.rows[ACCOUNTS].pop()
            elif change == "account_name": db.rows[ACCOUNTS][1]["display_name"] = "changed"
            elif change == "provider_revision": db.rows[INTEGRATIONS][0]["permission_observation_id"] = "new"
            elif change == "absent_null": db.rows[ACCOUNTS][0]["login_customer_id"] = None
            elif change == "bson_type": db.rows[ACCOUNTS][0]["mezan_selected"] = False
        return result
    monkeypatch.setattr(choices, "_snapshot", changed)
    response = await call(db)
    assert response.status_code == 409 and "ads_accounts" not in response.json()


@pytest.mark.asyncio
async def test_reordering_does_not_change_identity_but_foreign_owner_rows_do_not_leak(monkeypatch):
    db = local_db(); db.rows[ACCOUNTS].append({**db.rows[ACCOUNTS][0], "user_id": "other-owner", "display_name": "PRIVATE_OTHER_OWNER"})
    original = choices._snapshot
    async def reordered(*args):
        result = await original(*args); db.rows[ACCOUNTS].reverse(); return result
    monkeypatch.setattr(choices, "_snapshot", reordered)
    response = await call(db)
    assert response.status_code == 200 and response.json()["local_account_count"] == 2
    assert "PRIVATE_OTHER_OWNER" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["db", "transaction", "oversize"])
async def test_storage_errors_are_fixed_safe_failures_without_fallback(kind):
    db = local_db()
    if kind == "db": db.fail = True
    elif kind == "transaction": db.tx_fail = True
    else: db.rows[INTEGRATIONS][0]["capability_evidence"] = ["x" * 270000]
    response = await call(db)
    assert response.status_code == 409 and "PRIVATE" not in response.text
    assert "ads_accounts" not in response.json()


@pytest.mark.asyncio
async def test_non_owner_and_caller_supplied_selection_are_rejected():
    assert (await call(local_db(), user={"id": OWNER, "role": "viewer", "is_owner": True})).status_code == 403
    assert (await call(local_db(), params={"account_id": ACCOUNT})).status_code == 422


@pytest.mark.asyncio
async def test_timeout_and_cancellation_do_not_retry_or_return_choices(monkeypatch):
    calls = 0
    async def blocked(*args):
        nonlocal calls
        calls += 1
        await asyncio.Event().wait()
    monkeypatch.setattr(choices, "_snapshot", blocked)
    monkeypatch.setattr(choices, "ROUTE_SECONDS", .01)
    assert (await call(local_db())).status_code == 409 and calls == 1
    monkeypatch.setattr(choices, "ROUTE_SECONDS", 15)
    task = asyncio.create_task(call(local_db()))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
