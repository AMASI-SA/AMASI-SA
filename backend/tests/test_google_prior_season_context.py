from copy import deepcopy
import httpx
import pytest
from fastapi import APIRouter, FastAPI
from integrations_control_center.google_commercial_plan import attach_google_commercial_plan_routes
from tests.test_google_commercial_plan import DB, body


def context(**changes):
    return {"date_from": "2025-11-01", "date_to": "2025-12-31", "season_label": "شتاء العام السابق",
            "alignment_note": "فترة اختارها المالك للمراجعة", "calendar_reference_text": "مرجع المالك", **changes}


async def save(prior):
    db, app, router = DB(), FastAPI(), APIRouter()
    async def current_user():
        return {"id": "owner", "role": "owner"}
    attach_google_commercial_plan_routes(router, db, current_user)
    app.include_router(router)
    payload = body(calendar="hijri_season").model_dump(mode="json")
    payload["prior_season_context"] = prior
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/google_ads/commercial-plans/winter", json=payload)
    return response, db


@pytest.mark.asyncio
async def test_actual_owner_save_keeps_explicit_dates_without_verifying_hijri_alignment():
    response, db = await save(context())
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["fields"]["prior_season_context"] == context()
    assert data["review_context"]["prior_season"]["alignment_verification"] == "unknown"
    assert data["execution_authorized"] is False
    assert "acknowledge_review" not in data["fields"]
    assert db.plans.rows[0]["reviews"][0]["fields"]["prior_season_context"] == context()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"date_from": "2025-02-30"}, {"date_to": "2026-11-01"},
    {"date_from": "2024-01-01", "date_to": "2025-01-01"}, {"alignment_note": " "}, {"alignment_verification": "verified"}])
async def test_invalid_or_forged_owner_prior_context_is_not_saved(change):
    response, db = await save(context(**change))
    assert response.status_code == 422
    assert db.plans.rows == []


@pytest.mark.asyncio
async def test_actual_owner_can_save_366_inclusive_days_without_claiming_year_alignment():
    response, _ = await save(context(date_from="2024-01-01", date_to="2024-12-31"))
    assert response.status_code == 200
    assert response.json()["review_context"]["prior_season"]["alignment_verification"] == "unknown"
