"""Reproduce UI fixtures through the actual management HTTP writer and readers.

Provider data and Mongo are test doubles here; the separate saved-fields and
native-inventory workflows additionally exercise actual disposable Mongo.
"""
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path

import httpx
import pytest
from fastapi import APIRouter, FastAPI, Request

from integrations_control_center import google_commercial_plan, google_linked_campaign_routes, google_live_category_reader, google_native_inventory_reader, google_prior_season_routes
from integrations_control_center.google_ads_routes import attach_google_ads_management_routes
from integrations_control_center.google_live_category_routes import attach_google_live_category_routes
from integrations_control_center.google_native_inventory_routes import attach_google_native_inventory_routes
from integrations_control_center.google_commercial_plan import PLANS
from tests.test_google_saved_plan_fields_contract import Plans
from tests.test_google_linked_campaign_reader import database, event, NOW
from tests.test_google_live_category_reader import Integration, Native as CategoryNative, envelope, paged
from tests.test_google_commercial_plan import body
from tests.test_google_prior_season_routes import OwnerUsers
from tests.test_google_plan_store_observations import Orders
from tests.test_google_prior_season_observations import prior_order
from tests.test_google_prior_season_context import context

FIXTURES = Path(__file__).resolve().parents[2] / "frontend/src/services/__fixtures__"
PARENT_FIELDS = dict(instruction="ركز على هدايا الشتاء", season="الشتاء", starts_on="2026-11-01", ends_on="2026-12-31",
    market="SA", audience="رجال يبحثون عن هدية", message="دفء الشتاء", category_url="https://amasi-sa.com/shawls",
    category_ids=[], pinned_product_ids=["123"], excluded_product_ids=[], exclusions=[], selection_mode="pinned_only", calendar="gregorian", limits=None)


class DB(dict):
    def __init__(self):
        late = "2026-10-29T00:00:00+00:00"
        super().__init__(database([event("123"), event("456"), event("123", event_id="late-123", association_key="late-123", product_variant_id="new-size",
            valid_from=late, recorded_at=late, evidence={"source": "manual", "verification_status": "verified", "observed_at": late})]))
        self[PLANS] = Plans()
        self.salla_integrations = Integration()
        self.users = OwnerUsers()
        self["unified_orders"] = Orders([prior_order()])


def app_for(monkeypatch, db, native):
    class FixedDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW
    for module in (google_commercial_plan, google_linked_campaign_routes, google_live_category_reader, google_native_inventory_reader, google_prior_season_routes):
        monkeypatch.setattr(module, "datetime", FixedDatetime)
    monkeypatch.setitem(attach_google_live_category_routes.__kwdefaults__, "caller", native)
    monkeypatch.setitem(attach_google_native_inventory_routes.__kwdefaults__, "caller", native)
    import auth
    monkeypatch.setattr(auth, "get_jwt_secret", lambda: "synthetic-only")
    monkeypatch.setattr(auth.jwt, "decode", lambda *args, **kwargs: {"sub": "owner", "type": "access", "mfa": True})
    async def current(request: Request):
        return await auth.get_current_user_from_db(request, db)
    class NoAdsProvider:
        def __init__(self, *args, **kwargs):
            raise AssertionError("ads provider must not be constructed")
    app, router = FastAPI(), APIRouter(prefix="/integrations-v2")
    attach_google_ads_management_routes(router, db, current, client_factory=NoAdsProvider, ai_client_factory=NoAdsProvider)
    assert sum(route.path.endswith("/native-inventory-preview") for route in router.routes) == 1
    assert sum(route.path.endswith("/prior-season-observations") for route in router.routes) == 1
    app.include_router(router)
    return app


async def save(client, db, plan_id, fields, revision):
    for expected in range(revision):
        saved = await put(client, db, plan_id, fields, expected)
    return saved


async def put(client, db, plan_id, fields, expected):
    response = await client.put(f"/integrations-v2/google_ads/commercial-plans/{plan_id}", json={**fields, "expected_revision": expected, "acknowledge_review": True})
    assert response.status_code == 200, response.text
    saved = response.json()
    assert "acknowledge_review" not in saved["fields"] and "expected_revision" not in saved["fields"]
    assert saved["fields"] == (await db[PLANS].find_one({"user_id": "owner", "plan_id": plan_id}))["fields"]
    return saved


async def generate(monkeypatch):
    db, native = DB(), CategoryNative()
    app = app_for(monkeypatch, db, native)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test", headers={"Authorization": "Bearer synthetic-token"}) as client:
        async def get(plan, suffix, query=""):
            before = deepcopy(db[PLANS].rows)
            response = await client.get(f"/integrations-v2/google_ads/commercial-plans/{plan['plan_id']}/{suffix}?expected_revision={plan['revision']}" + query)
            assert response.status_code == 200, response.text
            assert db[PLANS].rows == before
            return response.json()
        linked_plan = await save(client, db, "winter", body(pinned_product_ids=["123", "456"], excluded_product_ids=["789"], category_url=None).model_dump(exclude={"acknowledge_review", "expected_revision"}), 3)
        linked = {"generated_by": "actual_management_http_put_then_read", "plan": linked_plan, "response": await get(linked_plan, "linked-campaign-evidence")}
        category_plan = await save(client, db, "category-winter", body(category_ids=["8"], category_url=None, excluded_product_ids=["1", "internal-product"]).model_dump(exclude={"acknowledge_review", "expected_revision"}), 1)
        category = {"generated_by": "actual_management_http_put_then_read", "plan": category_plan, "response": await get(category_plan, "live-category-preview", "&category_id=8")}
        parent = await save(client, db, "seasonal-focus", PARENT_FIELDS, 3)
        linked.update(parent_plan=parent, parent_response=await get(parent, "linked-campaign-evidence"))
        db.salla_integrations.row["store_domain"] = "https://amasi-sa.com"
        native.responses["/store/info"] = envelope({"id": 9, "domain": "https://amasi-sa.com"})
        native.responses["/categories/8"]["data"]["urls"]["customer"] = PARENT_FIELDS["category_url"]
        native.responses["/categories"] = paged([native.responses["/categories/8"]["data"]])
        category.update(parent_plan=parent, parent_response=await get(parent, "live-category-preview"))
        for product_id in (1, 2, 3):
            native.responses[f"/products/{product_id}"] = envelope({"id": product_id, "status": "sale", "is_available": True,
                "options": [], "skus": [], "urls": {"customer": f"https://amasi-sa.com/item-{product_id}"}, **({"quantity": 0} if product_id == 1 else {"unlimited_quantity": True} if product_id == 2 else {"quantity": 0, "unlimited_quantity": False})})
            native.responses[f"/products/{product_id}/variants"] = paged([])
        native.responses["/products/1"]["data"]["options"] = [{"id": 8, "name": "Size", "values": [{"id": 9, "option_id": 8, "name": "Large", "is_out_of_stock": True}]}]
        native.responses["/products/1/variants"] = paged([{"id": 7, "stock_quantity": 2, "related_option_values": [9], "related_options": [8]}])
        native.responses["/products/2/variants"] = paged([{"id": 8, "quantity": 0}])
        before = deepcopy(db[PLANS].rows)
        response = await client.post("/integrations-v2/google_ads/commercial-plans/seasonal-focus/native-inventory-preview", json={"expected_revision": 3, "category_id": "8", "selected_product_ids": ["1", "2", "3"]})
        assert response.status_code == 200, response.text
        assert db[PLANS].rows == before
        inventory = {"generated_by": "actual_management_http_put_category_get_inventory_post", "plan": parent, "category": category["parent_response"], "response": response.json()}
        prior_parent = await put(client, db, parent["plan_id"], {**PARENT_FIELDS, "prior_season_context": context(calendar_reference_text=None)}, 3)
        prior = {"generated_by": "actual_authenticated_management_http_put_then_linked_category_native_prior", "initial_plan": parent,
            "plan": prior_parent, "response": await get(prior_parent, "prior-season-observations"),
            "linked_response": await get(prior_parent, "linked-campaign-evidence"), "category_response": await get(prior_parent, "live-category-preview")}
        before = deepcopy(db[PLANS].rows)
        response = await client.post("/integrations-v2/google_ads/commercial-plans/seasonal-focus/native-inventory-preview", json={"expected_revision": 4, "category_id": "8", "selected_product_ids": ["1"]})
        assert response.status_code == 200, response.text
        assert db[PLANS].rows == before
        prior["native_response"] = response.json()
        prior["unrelated_plan"] = await put(client, db, parent["plan_id"], {**prior_parent["fields"], "instruction": "راجع موسم الهدايا"}, 4)
        assert prior["unrelated_plan"]["fields"]["prior_season_context"] == prior_parent["fields"]["prior_season_context"]
        prior["removed_plan"] = await put(client, db, parent["plan_id"], {**prior["unrelated_plan"]["fields"], "prior_season_context": None}, 5)
        db["unified_orders"].query = None
        prior["removed_response"] = await get(prior["removed_plan"], "prior-season-observations")
        assert db["unified_orders"].query is None
        prior["reference_plan"] = await put(client, db, parent["plan_id"], {**prior["removed_plan"]["fields"], "prior_season_context": context()}, 6)
    return {"googleLinkedCampaignEvidence.json": linked, "googleLiveCategoryPreview.json": category, "googleNativeInventoryPreview.json": inventory, "googleCommercialPlanPriorContext.json": prior}


@pytest.mark.asyncio
@pytest.mark.parametrize("filename", ["googleLinkedCampaignEvidence.json", "googleLiveCategoryPreview.json", "googleNativeInventoryPreview.json", "googleCommercialPlanPriorContext.json"])
async def test_checked_ui_fixture_equals_actual_management_writer_then_reader(monkeypatch, filename):
    generated = await generate(monkeypatch)
    assert json.loads((FIXTURES / filename).read_text()) == generated[filename]
