"""Owner account settings use the same bounded transactional CLI core."""
from fastapi import APIRouter, FastAPI
import httpx
import pytest

from integrations_control_center import google_account_selection as module
from integrations_control_center.routes import _require_owner
from tests.test_google_reporting_account_selection import DB, IDS, OWNER, seed, snapshot


async def request(db, method="GET", *, user=None, payload=None):
    app = FastAPI()
    router = APIRouter(prefix="/integrations-v2")
    async def current_user():
        return user if user is not None else {"id": OWNER, "role": "owner"}
    module.attach_google_account_selection_routes(router, db, current_user, _require_owner)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://test") as client:
        return await client.request(method, "/integrations-v2/google_ads/accounts-selection",
                                    **({"json": payload} if payload is not None else {}))


def choice(**overrides):
    return {"selected_account_ids": [IDS[0]], "expected_account_ids": list(IDS), **overrides}


@pytest.mark.asyncio
async def test_get_is_bounded_owner_scoped_read_only_and_exposes_literal_unconfigured_flags():
    db = DB()
    await seed(db)
    before = await snapshot(db.raw)
    response = await request(db)
    assert response.status_code == 200
    result = response.json()
    assert result["provider"] == "google_ads" and result["expected_account_ids"] == list(IDS)
    assert result["selected_count"] == 0 and result["selection_configured"] is False
    assert result["selection_required"] is True and result["missing_selected_account_ids"] == []
    assert all(row["selected"] is False for row in result["accounts"])
    assert result["source_only"] is True
    assert all(result[key] is False for key in module.NO_EXTERNAL_WRITES)
    assert "PRIVATE" not in response.text and "opaque_payload" not in response.text
    assert await snapshot(db.raw) == before and db.transactions == 0


@pytest.mark.asyncio
async def test_put_saves_exact_selection_and_returns_verified_public_state_without_provider_calls():
    db = DB()
    await seed(db)
    before = await snapshot(db.raw)
    response = await request(db, "PUT", payload=choice())
    assert response.status_code == 200
    result = response.json()
    assert result["changed"] is True and result["selected_count"] == 1
    assert result["selection_configured"] is True and result["selection_required"] is False
    assert [row["account_id"] for row in result["accounts"] if row["selected"]] == [IDS[0]]
    after = await snapshot(db.raw)
    for name in before.keys() - {module.ACCOUNTS, module.INTEGRATIONS}:
        assert after[name] == before[name]
    assert len(after[module.ACCOUNTS]) == len(before[module.ACCOUNTS])
    assert await db.raw[module.AUDIT].count_documents({}) == 1
    again = await request(db, "PUT", payload=choice())
    assert again.status_code == 200 and again.json()["changed"] is False
    assert await snapshot(db.raw) == after


@pytest.mark.asyncio
@pytest.mark.parametrize("user", [
    {"id": OWNER, "role": "staff"}, {"id": OWNER, "role": "meta_reviewer"},
    {"id": "other-owner", "role": "owner"},
])
async def test_only_fresh_active_owner_can_read_or_save(user):
    db = DB()
    await seed(db)
    before = await snapshot(db.raw)
    for method in ("GET", "PUT"):
        response = await request(db, method, user=user, payload=choice() if method == "PUT" else None)
        assert response.status_code == 403
    assert await snapshot(db.raw) == before and db.update_attempts == 0


@pytest.mark.asyncio
async def test_disabled_owner_cannot_save_despite_owner_session_role():
    db = DB()
    await seed(db)
    await db.raw.users.update_one({"id": OWNER}, {"$set": {"disabled": True}})
    response = await request(db, "PUT", payload=choice())
    assert response.status_code == 403 and db.update_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [
    choice(owner_id="another-owner"), choice(provider="meta_ads"),
    choice(selected_account_ids=[]), choice(expected_account_ids=[]),
    choice(selected_account_ids=[IDS[0], IDS[0]]),
    choice(expected_account_ids=[*IDS, IDS[0]]),
    choice(selected_account_ids=["9999999901"]),
    choice(selected_account_ids=[True]),
    choice(selected_account_ids=[str(7777777700 + i) for i in range(51)]),
])
async def test_invalid_or_owner_overriding_payload_never_writes(payload):
    db = DB()
    await seed(db)
    before = await snapshot(db.raw)
    response = await request(db, "PUT", payload=payload)
    assert response.status_code == 422
    assert await snapshot(db.raw) == before and db.update_attempts == 0


@pytest.mark.asyncio
async def test_stale_complete_account_set_returns_conflict_without_partial_save():
    db = DB()
    await seed(db)
    before = await snapshot(db.raw)
    response = await request(db, "PUT", payload=choice(expected_account_ids=[IDS[0]]))
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "expected_google_account_scope_mismatch"
    assert response.json()["detail"]["apply_outcome_unknown"] is False
    assert await snapshot(db.raw) == before and db.update_attempts == 0


@pytest.mark.asyncio
async def test_parent_policy_is_rechecked_inside_transaction():
    db = DB()
    await seed(db)
    async def change_policy(raw):
        await raw[module.INTEGRATIONS].update_one(
            {"user_id": OWNER, "provider": "google_ads"},
            {"$set": {"account_selection_configured": True, "selected_account_ids": [IDS[1]]}},
        )
    db.before_transaction = change_policy
    response = await request(db, "PUT", payload=choice())
    assert response.status_code == 409 and db.aborts == 1 and db.update_attempts == 0
    assert await db.raw[module.AUDIT].count_documents({}) == 0


@pytest.mark.asyncio
async def test_unsupported_transaction_returns_sanitized_error_without_fallback():
    db = DB()
    await seed(db)
    before = await snapshot(db.raw)
    db.session_failure = True
    response = await request(db, "PUT", payload=choice())
    assert response.status_code == 503
    assert response.json()["detail"]["apply_outcome_unknown"] is True
    assert "PRIVATE" not in response.text
    assert await snapshot(db.raw) == before and db.update_attempts == 0


@pytest.mark.asyncio
async def test_get_missing_policy_account_is_visible_and_owner_can_replace_with_available_subset():
    db = DB()
    await seed(db)
    await request(db, "PUT", payload=choice(selected_account_ids=[IDS[0], IDS[1]]))
    await db.raw[module.ACCOUNTS].update_one({"_id": "account-" + IDS[0]}, {"$set": {"connection_status": "disconnected"}})
    response = await request(db)
    result = response.json()
    assert result["expected_account_ids"] == list(IDS[1:])
    assert result["missing_selected_account_ids"] == [IDS[0]]
    assert result["selected_count"] == 1 and result["selection_required"] is True
    replacement = await request(db, "PUT", payload={
        "selected_account_ids": [IDS[1]], "expected_account_ids": list(IDS[1:]),
    })
    assert replacement.status_code == 200 and replacement.json()["selection_required"] is False
    parent = await db.raw[module.INTEGRATIONS].find_one({"user_id": OWNER, "provider": "google_ads"})
    assert parent["selected_account_ids"] == [IDS[1]]


@pytest.mark.asyncio
async def test_get_never_leaks_driver_exception_details(monkeypatch):
    db = DB()
    async def failure(*args, **kwargs):
        raise RuntimeError("mongodb://PRIVATE_HOST/SECRET")
    monkeypatch.setattr(module, "_read_scope", failure)
    response = await request(db)
    assert response.status_code == 503 and "PRIVATE" not in response.text and "SECRET" not in response.text


@pytest.mark.asyncio
async def test_unconnected_owner_can_read_empty_selection_without_creating_records():
    db = DB()
    await db.raw.users.insert_one({"id": OWNER, "role": "owner"})
    before = await snapshot(db.raw)
    response = await request(db)
    assert response.status_code == 200 and response.json()["accounts"] == []
    assert response.json()["expected_account_ids"] == []
    assert response.json()["selection_required"] is True
    assert await snapshot(db.raw) == before
