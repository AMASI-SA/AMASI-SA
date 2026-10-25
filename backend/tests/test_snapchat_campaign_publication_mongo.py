"""Actual Mongo parent fencing and child adoption through real HTTP adapters.

Only OAuth token resolution and HTTP transport are synthetic. Publication,
management approval/execution, inventory, product links and all Mongo CAS writes
use the production implementations. Storage adapters below delay real read
acknowledgements; they never implement or replace compare-and-swap behavior.
"""
import asyncio
import json
import os
import uuid
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorClient

from integrations_control_center import snapchat_campaign_management as management
from integrations_control_center import snapchat_campaign_publication as publication
from integrations_control_center import snapchat_media_upload as media
from product_v2_routes import PRODUCTS


OWNER = "publication-mongo-owner"
ACCOUNT = "account-1"
MEDIA_UPLOAD = "a" * 64
KINDS = {"campaigns": "campaign", "adsquads": "adsquad", "creatives": "creative", "ads": "ad"}
ACTIONS = ["campaign.create", "ad_squad.create", "creative.create", "ad.create"]


@pytest_asyncio.fixture
async def publication_db():
    uri = os.environ.get("SNAPCHAT_PUBLICATION_TEST_MONGO_URL")
    if not uri:
        if os.environ.get("REQUIRE_SNAPCHAT_PUBLICATION_MONGO") == "1":
            pytest.fail("dedicated isolated publication Mongo test URI is required")
        pytest.skip("dedicated isolated publication Mongo test URI not configured")
    try:
        hostname = urlsplit(uri).hostname
    except ValueError:
        pytest.fail("publication tests require a valid loopback Mongo URI")
    if hostname not in {"localhost", "127.0.0.1", "::1"}:
        pytest.fail("publication tests require loopback-only disposable Mongo")
    # Default BSON datetime decoding matches the production Motor client.
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database = client["snap_publication_" + uuid.uuid4().hex]
    try:
        await client.admin.command("ping")
        await management.ensure_snapchat_management_indexes(database)
        yield database
    finally:
        try:
            await client.drop_database(database.name)
        finally:
            client.close()


class ProviderTransport:
    def __init__(self):
        self.entities = {plural: {} for plural in KINDS}
        self.creates = []
        self.paths = []
        self.hold_campaign = False
        self.campaign_entered = asyncio.Event()
        self.release_campaign = asyncio.Event()

    @staticmethod
    def response(plural, singular, entities):
        return httpx.Response(200, json={"request_status": "SUCCESS", plural: [
            {"sub_request_status": "SUCCESS", singular: deepcopy(entity)} for entity in entities
        ]})

    async def handle(self, request):
        assert request.url.host in {"adsapi.snapchat.com", "businessapi.snapchat.com"}
        assert request.url.scheme == "https"
        assert request.url.path.startswith("/v1/")
        path = request.url.path[len("/v1"):]
        self.paths.append((request.method, path))
        if request.method == "GET":
            if path == f"/adaccounts/{ACCOUNT}":
                return self.response("adaccounts", "adaccount", [{"id": ACCOUNT, "currency": "SAR"}])
            if path == "/me/organizations":
                return self.response("organizations", "organization", [{"id": "org-1", "my_member_id": "member-1"}])
            if path == "/members/member-1/roles":
                return self.response("roles", "role", [{"id": "role-1", "type": "admin", "ad_account_id": ACCOUNT}])
            if path == f"/adaccounts/{ACCOUNT}/pixels":
                return self.response("pixels", "pixel", [{"id": "pixel-1"}])
            if path == "/pixels/pixel-1/campaign_eligibilities":
                return self.response("campaign_eligibilities", "campaign_eligibility", [{
                    "optimization_goal": "PIXEL_PURCHASE", "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
                    "eligibility_status": "ELIGIBLE",
                }])
            if path == "/media/media-1":
                return self.response("media", "media", [{"id": "media-1", "ad_account_id": ACCOUNT,
                    "type": "VIDEO", "media_status": "READY"}])
            if path == f"/adaccounts/{ACCOUNT}/sharing_policies":
                assert request.url.host == "businessapi.snapchat.com"
                return httpx.Response(200, json={"request_status": "SUCCESS", "sharing_policies": [{
                    "source": {"resource_type": "public_profiles", "resource_id": "profile-1"},
                }]})
            pieces = path.strip("/").split("/")
            if len(pieces) == 2 and pieces[0] in KINDS:
                plural, entity_id = pieces
                assert entity_id in self.entities[plural], f"Unknown synthetic entity: {path}"
                return self.response(plural, KINDS[plural], [self.entities[plural][entity_id]])
            if len(pieces) == 3 and pieces[:2] == ["adaccounts", ACCOUNT] and pieces[2] in KINDS:
                plural = pieces[2]
                return self.response(plural, KINDS[plural], list(self.entities[plural].values()))
            raise AssertionError(f"Unexpected synthetic provider GET: {path}")
        assert request.method == "POST", f"Unexpected synthetic provider method: {request.method}"
        plural = path.rsplit("/", 1)[-1]
        assert plural in KINDS
        body = json.loads(request.content)
        assert set(body) == {plural} and len(body[plural]) == 1
        entity = deepcopy(body[plural][0])
        action = {"campaigns": ACTIONS[0], "adsquads": ACTIONS[1], "creatives": ACTIONS[2], "ads": ACTIONS[3]}[plural]
        self.creates.append(action)
        if plural == "campaigns" and self.hold_campaign:
            self.campaign_entered.set()
            await self.release_campaign.wait()
        entity_id = f"provider-{KINDS[plural]}-{len(self.entities[plural]) + 1}"
        entity.update({"id": entity_id, "ad_account_id": ACCOUNT, "created_at": media._iso()})
        if plural == "adsquads":
            assert path == "/campaigns/provider-campaign-1/adsquads"
            entity.setdefault("campaign_id", "provider-campaign-1")
        elif plural == "ads":
            assert path == "/adsquads/provider-adsquad-1/ads"
            entity.setdefault("ad_squad_id", "provider-adsquad-1")
        else:
            assert path == f"/adaccounts/{ACCOUNT}/{plural}"
        if plural == "creatives":
            entity.update({"packaging_status": "SUCCESS", "review_status": "PENDING_REVIEW"})
        self.entities[plural][entity_id] = entity
        return self.response(plural, KINDS[plural], [entity])


@pytest_asyncio.fixture
async def publication_environment(publication_db, monkeypatch):
    database = publication_db
    synced_at = (media._now() - timedelta(minutes=1)).isoformat()
    await database["mezan_integration_accounts_v2"].insert_one({
        "user_id": OWNER, "provider": "snapchat_ads", "ad_account_id": ACCOUNT,
        "external_account_id": ACCOUNT, "organization_id": "org-1", "currency": "SAR",
        "timezone": "Asia/Riyadh", "mezan_selected": True, "connection_status": "connected",
        "connection_provenance": "api_connection",
    })
    await database[PRODUCTS].insert_one({
        "user_id": OWNER, "salla_product_id": "product-1", "mezan_product_id": "product-1",
        "name": "Synthetic publication product", "status": "active", "archived": False,
        "quantity": 25, "unlimited_quantity": False, "last_synced_at": synced_at,
        "details_synced_at": synced_at,
        "variants": [{"id": "variant-1", "quantity": 25, "unlimited_quantity": False}],
    })
    await database[media.MEDIA_UPLOAD_COLLECTION].insert_one({
        "_id": MEDIA_UPLOAD, "upload_id": MEDIA_UPLOAD, "user_id": OWNER,
        "account_id": ACCOUNT, "provider_media_id": "media-1", "status": "ready",
        "source_verified": True, "provider_readback_verified": True,
    })
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")
    monkeypatch.setenv(management.ACTIVATION_ENABLED_ENV, "true")
    monkeypatch.setenv("MEZAN_ATTRIBUTION_SIGNING_SECRET", "publication-isolated-mongo-test-only-secret")

    async def token(context, **options):
        assert context.user_id == OWNER
        return "synthetic-publication-token"

    monkeypatch.setattr(management.SnapchatSyncContext, "access_token", token)
    provider = ProviderTransport()
    transport = httpx.MockTransport(provider.handle)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **options: real_client(transport=transport, **options))
    return SimpleNamespace(db=database, provider=provider)


def plan():
    return publication.PublicationInput(
        account_id=ACCOUNT, currency="SAR", product_id="product-1", product_variant_id="variant-1",
        media_upload_id=MEDIA_UPLOAD, profile_id="profile-1", pixel_id="pixel-1",
        campaign_name="Synthetic campaign", group_name="Synthetic ad group", creative_name="Synthetic creative",
        ad_name="Synthetic ad", headline="Synthetic product", destination_url="https://store.example/product",
        daily_budget_micro=25_000_000, start_time=(media._now() + timedelta(days=1)).isoformat(),
        country="SA", conversion_window="SWIPE_28DAY_VIEW_1DAY", reason="Explicit synthetic owner publication",
        idempotency_key="publication-mongo-stable-key",
    )


async def approved(environment):
    preview = await publication.preview_publication(environment.db, OWNER, plan())
    return await publication.approve_publication(environment.db, OWNER, preview["publication_id"],
        publication.PublicationApproval(confirm_token=preview["confirm_token"], expected_revision=preview["revision"]))


class ReadBarrierCollection:
    """Both callers receive actual Mongo rows at the same immutable revision."""
    def __init__(self, collection, publication_id, status):
        self.collection, self.publication_id, self.status = collection, publication_id, status
        self.arrivals = 0
        self.ready = asyncio.Event()

    def __getattr__(self, name):
        return getattr(self.collection, name)

    async def find_one(self, *args, **options):
        row = await self.collection.find_one(*args, **options)
        if row and row.get("publication_id") == self.publication_id and row.get("status") == self.status and self.arrivals < 2:
            self.arrivals += 1
            if self.arrivals == 2:
                self.ready.set()
            await asyncio.wait_for(self.ready.wait(), timeout=10)
        return row


class ReadAdapterDatabase:
    def __init__(self, database, collection_name, adapter):
        self.database, self.collection_name, self.adapter = database, collection_name, adapter

    def __getitem__(self, name):
        return self.adapter if name == self.collection_name else self.database[name]

    def __getattr__(self, name):
        return getattr(self.database, name)


async def assert_completed_chain(environment, publication_id):
    parent = await environment.db[publication.PUBLICATION_COLLECTION].find_one({"_id": publication_id, "user_id": OWNER})
    children = await environment.db[management.PROPOSAL_COLLECTION].find({"user_id": OWNER}).to_list(length=5)
    assert parent["status"] == "completed" and parent["current_step"] == 4 and parent["lease_token"] is None
    assert len(children) == 4 and Counter(environment.provider.creates) == Counter(ACTIONS)
    assert environment.provider.creates == ACTIONS
    assert len({child["proposal_id"] for child in children}) == 4
    assert {child["idempotency_key"] for child in children} == {step["idempotency_key"] for step in parent["steps"]}
    for index, step in enumerate(parent["steps"]):
        child = next(child for child in children if child["idempotency_key"] == step["idempotency_key"])
        assert child["publication_id"] == publication_id and child["account_id"] == ACCOUNT
        assert child["action"] == ACTIONS[index] and child["status"] == "completed"
        assert child["approved_by"] == OWNER and child["provider_write_reached"] is True
        assert child["verification"]["verified"] is True
        assert step["proposal_id"] == child["proposal_id"]
        assert step["provider_entity_id"] == child["provider_entity_id"]
        assert child["publication_creation_proof"]["contract"] == publication.PROOF_CONTRACT
    return parent, children


@pytest.mark.asyncio
async def test_real_mongo_publication_approval_revision_has_one_winner(publication_environment):
    environment = publication_environment
    preview = await publication.preview_publication(environment.db, OWNER, plan())
    barrier = ReadBarrierCollection(environment.db[publication.PUBLICATION_COLLECTION], preview["publication_id"], "previewed")
    database = ReadAdapterDatabase(environment.db, publication.PUBLICATION_COLLECTION, barrier)
    approval = publication.PublicationApproval(confirm_token=preview["confirm_token"], expected_revision=preview["revision"])
    outcomes = await asyncio.gather(*[
        publication.approve_publication(database, OWNER, preview["publication_id"], approval) for _ in range(2)
    ], return_exceptions=True)
    assert barrier.arrivals == 2
    assert sum(isinstance(outcome, dict) and outcome["status"] == "approved" for outcome in outcomes) == 1
    failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert len(failures) == 1 and isinstance(failures[0], HTTPException)
    assert failures[0].detail["code"] == "snapchat_publication_state_changed"
    parent = await environment.db[publication.PUBLICATION_COLLECTION].find_one({"_id": preview["publication_id"]})
    assert parent["revision"] == preview["revision"] + 1
    assert parent["approval_revision"] == parent["revision"] and parent["approved_by"] == OWNER
    assert parent["confirm_token_hash"] is None and parent["approved_plan_fingerprint"] == parent["plan_fingerprint"]
    assert sum(event["event"] == "plan_approved" for event in parent["events"]) == 1
    assert environment.provider.creates == []


@pytest.mark.asyncio
async def test_real_mongo_concurrent_parent_launch_has_one_lease_and_four_creates(publication_environment):
    environment = publication_environment
    job = await approved(environment)
    barrier = ReadBarrierCollection(environment.db[publication.PUBLICATION_COLLECTION], job["publication_id"], "approved")
    database = ReadAdapterDatabase(environment.db, publication.PUBLICATION_COLLECTION, barrier)
    environment.provider.hold_campaign = True
    tasks = [asyncio.create_task(publication.run_publication(database, OWNER, job["publication_id"])) for _ in range(2)]
    try:
        await asyncio.wait_for(environment.provider.campaign_entered.wait(), timeout=10)
        done, pending = await asyncio.wait(tasks, timeout=10, return_when=asyncio.FIRST_COMPLETED)
        assert barrier.arrivals == 2 and len(done) == 1 and len(pending) == 1
        loser = next(iter(done)).exception()
        assert isinstance(loser, HTTPException) and loser.detail["code"] == "snapchat_publication_state_changed"
        parent = await environment.db[publication.PUBLICATION_COLLECTION].find_one({"_id": job["publication_id"]})
        assert parent["status"] == "running" and parent["worker_phase"] == "running"
        assert parent["lease_token"] and publication._server_date(parent["lease_expires_at"]) > media._now()
        assert parent["revision"] > job["revision"] and parent["current_step"] == 0
        assert environment.provider.creates == ["campaign.create"]
        assert await environment.db[management.PROPOSAL_COLLECTION].count_documents({"user_id": OWNER}) == 1
        environment.provider.release_campaign.set()
        outcomes = await asyncio.gather(*tasks, return_exceptions=True)
        assert sum(isinstance(outcome, dict) and outcome["status"] == "completed" for outcome in outcomes) == 1
        terminal, children = await assert_completed_chain(environment, job["publication_id"])
        assert terminal["revision"] > parent["revision"]
        assert terminal["approval_revision"] == job["approval_revision"]
        assert len({child["idempotency_key"] for child in children}) == 4
    finally:
        environment.provider.release_campaign.set()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_real_mongo_completed_child_without_parent_checkpoint_resumes_same_child(publication_environment):
    environment = publication_environment
    job = await approved(environment)
    real_children = environment.db[management.PROPOSAL_COLLECTION]
    child_persisted = asyncio.Event()
    never_acknowledge = asyncio.Event()

    class LostCompletedReadAcknowledgement:
        armed = True
        observed_child = None

        def __getattr__(self, name):
            return getattr(real_children, name)

        async def find_one(self, *args, **options):
            child = await real_children.find_one(*args, **options)
            if (self.armed and child and child.get("user_id") == OWNER
                    and child.get("publication_id") == job["publication_id"]
                    and child.get("action") == "campaign.create" and child.get("status") == "completed"):
                self.armed = False
                self.observed_child = deepcopy(child)
                child_persisted.set()
                await never_acknowledge.wait()
            return child

    adapter = LostCompletedReadAcknowledgement()
    database = ReadAdapterDatabase(environment.db, management.PROPOSAL_COLLECTION, adapter)
    task = asyncio.create_task(publication.run_publication(database, OWNER, job["publication_id"]))
    try:
        await asyncio.wait_for(child_persisted.wait(), timeout=10)
        child = await real_children.find_one({"proposal_id": adapter.observed_child["proposal_id"], "user_id": OWNER})
        parent_before = await environment.db[publication.PUBLICATION_COLLECTION].find_one({"_id": job["publication_id"]})
        assert child["status"] == "completed" and child["verification"]["verified"] is True
        assert parent_before["current_step"] == 0 and parent_before["steps"][0]["status"] == "pending"
        assert parent_before["steps"][0]["proposal_id"] == child["proposal_id"]
        assert environment.provider.creates == ["campaign.create"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        interrupted = await environment.db[publication.PUBLICATION_COLLECTION].find_one({"_id": job["publication_id"]})
        assert interrupted["status"] == "interrupted" and interrupted["lease_token"] is None
        assert interrupted["current_step"] == 0 and interrupted["revision"] > parent_before["revision"]
        result = await publication.run_publication(environment.db, OWNER, job["publication_id"])
        assert result["status"] == "completed"
        terminal, children = await assert_completed_chain(environment, job["publication_id"])
        same_child = next(row for row in children if row["action"] == "campaign.create")
        assert same_child["proposal_id"] == child["proposal_id"]
        assert same_child["idempotency_key"] == child["idempotency_key"]
        assert same_child["provider_entity_id"] == child["provider_entity_id"]
        assert terminal["steps"][0]["provider_entity_id"] == child["provider_entity_id"]
        assert await real_children.count_documents({"user_id": OWNER, "idempotency_key": child["idempotency_key"]}) == 1
        previous_creates = list(environment.provider.creates)
        assert (await publication.run_publication(environment.db, OWNER, job["publication_id"]))["status"] == "completed"
        assert environment.provider.creates == previous_creates
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
