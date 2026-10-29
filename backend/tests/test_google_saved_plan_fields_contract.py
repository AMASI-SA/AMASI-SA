"""Actual HTTP write shape must be readable without synthesizing write consent."""
from copy import deepcopy

import httpx
import pytest
from fastapi import APIRouter, FastAPI

from integrations_control_center.google_commercial_plan import attach_google_commercial_plan_routes, CommercialPlanFields, PLANS
from pydantic import ValidationError
from integrations_control_center.google_linked_campaign_routes import attach_google_linked_campaign_routes
from integrations_control_center.google_live_category_routes import attach_google_live_category_routes
from integrations_control_center.google_native_inventory_routes import attach_google_native_inventory_routes
from tests.test_google_commercial_plan import Collection, body
from tests.test_google_linked_campaign_reader import Collection as BoundedCollection
from tests.test_google_native_inventory_reader import Integration, Native


class Plans(Collection):
    def aggregate(self, *args, **kwargs):
        return BoundedCollection(self.rows).aggregate(*args, **kwargs)


class DB:
    def __init__(self):
        self.plans = Plans()
        self.salla_integrations = Integration()
    def __getitem__(self, key):
        assert key == PLANS
        return self.plans


def request_body():
    return body(category_ids=["8"], category_url=None, pinned_product_ids=[]).model_dump(mode="json")


def app_for(db, native):
    app, router = FastAPI(), APIRouter()
    async def current_user():
        return {"id": "owner", "role": "owner"}
    attach_google_commercial_plan_routes(router, db, current_user)
    attach_google_linked_campaign_routes(router, db, current_user)
    attach_google_live_category_routes(router, db, current_user, caller=native)
    attach_google_native_inventory_routes(router, db, current_user, caller=native)
    app.include_router(router)
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("reader", ["linked", "category", "native"])
async def test_actual_commercial_plan_put_output_is_readable_by_each_evidence_route(reader):
    db, native = DB(), Native()
    app = app_for(db, native)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        saved = await client.put("/google_ads/commercial-plans/winter", json=request_body())
        assert saved.status_code == 200, saved.text
        fields = saved.json()["fields"]
        assert "acknowledge_review" not in fields and "expected_revision" not in fields
        original = deepcopy(db.plans.rows)
        if reader == "linked":
            result = await client.get("/google_ads/commercial-plans/winter/linked-campaign-evidence?expected_revision=1")
        elif reader == "category":
            result = await client.get("/google_ads/commercial-plans/winter/live-category-preview?expected_revision=1&category_id=8")
        else:
            result = await client.post("/google_ads/commercial-plans/winter/native-inventory-preview", json={
                "expected_revision": 1, "category_id": "8", "selected_product_ids": ["1"]})
    assert result.status_code == 200, result.text
    assert result.json()["execution_authorized"] is False
    assert db.plans.rows == original


@pytest.mark.asyncio
@pytest.mark.parametrize("ack", [None, False])
async def test_actual_write_still_requires_explicit_true_review_acknowledgement(ack):
    db, native = DB(), Native()
    payload = request_body()
    if ack is None:
        payload.pop("acknowledge_review")
    else:
        payload["acknowledge_review"] = ack
    app = app_for(db, native)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        response = await client.put("/google_ads/commercial-plans/winter", json=payload)
    assert response.status_code == 422
    assert db.plans.rows == [] and native.calls == []


@pytest.mark.parametrize("control", [{"expected_revision": 1}, {"acknowledge_review": True}])
def test_persisted_fields_model_forbids_write_only_controls(control):
    fields = {k: v for k, v in request_body().items() if k not in {"expected_revision", "acknowledge_review"}}
    with pytest.raises(ValidationError):
        CommercialPlanFields.model_validate(fields | control)
