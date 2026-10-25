"""Publication tests use the real management functions with provider=None."""
import asyncio
import hashlib
import json
from copy import deepcopy
from datetime import timedelta
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import pytest

from integrations_control_center import snapchat_campaign_publication as module
from integrations_control_center import snapchat_campaign_management as management
from integrations_control_center import snapchat_media_upload as media

ACTUAL_PRODUCT_READER = module._product


def matches(row, query):
    for key, value in query.items():
        actual = row.get(key)
        if key == "$or":
            if not any(matches(row, item) for item in value):
                return False
        elif isinstance(value, dict):
            for operator, expected in value.items():
                if operator == "$ne" and actual == expected:
                    return False
                if operator == "$in" and actual not in expected:
                    return False
                if operator == "$nin" and actual in expected:
                    return False
                if operator == "$exists" and (key in row) != expected:
                    return False
        elif actual != value:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, key, direction):
        self.rows.sort(key=lambda row: str(row.get(key) or ""), reverse=direction < 0)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class Collection:
    def __init__(self):
        self.rows = []
        self.after_update = None

    async def create_index(self, *args, **kwargs):
        return None

    def find(self, query, projection=None):
        return Cursor([deepcopy(row) for row in self.rows if matches(row, query)])

    async def find_one(self, query, projection=None):
        return deepcopy(next((row for row in self.rows if matches(row, query)), None))

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))
        return SimpleNamespace(inserted_id=row.get("_id"))

    async def update_one(self, query, update, upsert=False):
        row = next((row for row in self.rows if matches(row, query)), None)
        if row is None:
            if upsert:
                self.rows.append(deepcopy(update.get("$setOnInsert", {})))
            return SimpleNamespace(matched_count=0)
        row.update(deepcopy(update.get("$set", {})))
        for key, amount in update.get("$inc", {}).items():
            row[key] = row.get(key, 0) + amount
        for key, pushed in update.get("$push", {}).items():
            row[key] = (row.get(key, []) + deepcopy(pushed["$each"]))[pushed["$slice"]:]
        for key in update.get("$unset", {}):
            row.pop(key, None)
        if self.after_update:
            await self.after_update(row, update)
        return SimpleNamespace(matched_count=1)

    async def update_many(self, query, update):
        count = 0
        for row in self.rows:
            if matches(row, query):
                row.update(deepcopy(update.get("$set", {})))
                count += 1
        return SimpleNamespace(matched_count=count)


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, key):
        return self.collections.setdefault(key, Collection())


def payload():
    return module.PublicationInput(account_id="account-1", currency="SAR", product_id="product-1",
        product_variant_id="variant-1", media_upload_id="a" * 64, profile_id="profile-1", pixel_id="pixel-1",
        campaign_name="حملة المنتج", group_name="المجموعة", creative_name="الفيديو", ad_name="الإعلان",
        headline="منتج جديد", destination_url="https://store.example/product",
        daily_budget_micro=25_000_000, start_time=(media._now() + timedelta(days=1)).isoformat(),
        country="SA", conversion_window="SWIPE_28DAY_VIEW_1DAY", reason="إنشاء خطة معتمدة من المالك",
        idempotency_key="publication-test-key")


@pytest.fixture
def environment(monkeypatch):
    db, entities, writes = DB(), {}, []
    account = {"ad_account_id": "account-1", "currency": "SAR",
        "connection_status": "connected", "connection_provenance": "api_connection"}
    product = {"name": "منتج", "status": "active", "variants": [{"id": "variant-1"}]}
    db[media.MEDIA_UPLOAD_COLLECTION].rows.append({"_id": "a" * 64, "upload_id": "a" * 64,
        "user_id": "owner", "account_id": "account-1", "provider_media_id": "media-1",
        "status": "ready", "source_verified": True, "provider_readback_verified": True})

    async def selected(*args):
        if args[-1] != "account-1":
            raise HTTPException(409)
        return deepcopy(account)

    async def read_account(self, account_id):
        return {"id": account_id, "currency": "SAR"}

    async def role(self, account, action):
        return {"allowed": True, "role": "admin"}

    async def read_entity(self, kind, entity_id):
        return deepcopy(entities[entity_id])

    async def execute(self, operation, *, final_authorizer=None):
        if final_authorizer:
            await final_authorizer()
        entity = deepcopy(management._operation_create_entity(operation))
        entity_id = "provider-" + operation["entity_type"] + "-1"
        entity.update({"id": entity_id, "created_at": media._iso()})
        if operation["entity_type"] == "creative":
            entity.update({"packaging_status": "SUCCESS", "review_status": "PENDING_REVIEW"})
        entities[entity_id] = entity
        writes.append(operation["action"])
        return deepcopy(entity)

    async def request(self, method, path, **kwargs):
        assert method == "GET"
        if path == "/media/media-1":
            return {"request_status": "SUCCESS", "media": [{"sub_request_status": "SUCCESS",
                "media": {"id": "media-1", "ad_account_id": "account-1", "type": "VIDEO", "media_status": "READY"}}]}
        if path.startswith("/adaccounts/account-1/creatives"):
            return {"request_status": "SUCCESS", "creatives": [{"sub_request_status": "SUCCESS",
                "creative": deepcopy(value)} for value in entities.values() if value.get("type") == "WEB_VIEW"]}
        if path.split("?", 1)[0] == "/adaccounts/account-1/ads":
            return {"request_status": "SUCCESS", "ads": [{"sub_request_status": "SUCCESS",
                "ad": deepcopy(value)} for value in entities.values() if value.get("type") == "REMOTE_WEBPAGE"]}
        raise AssertionError(path)

    async def retry(db, user_id, actor_id, row):
        return row

    monkeypatch.setattr(management, "_selected_account", selected)
    monkeypatch.setattr(module, "_product", AsyncMock(return_value=product))
    monkeypatch.setattr(management.SnapchatManagementProvider, "read_account", read_account)
    monkeypatch.setattr(management.SnapchatManagementProvider, "management_role", role)
    monkeypatch.setattr(management.SnapchatManagementProvider, "read_entity", read_entity)
    monkeypatch.setattr(management.SnapchatManagementProvider, "execute", execute)
    monkeypatch.setattr(management.SnapchatManagementProvider, "_request", request)
    monkeypatch.setattr(management.SnapchatManagementProvider, "list_account_pixels", AsyncMock(return_value=[{"id": "pixel-1"}]))
    monkeypatch.setattr(management.SnapchatManagementProvider, "pixel_eligibility", AsyncMock(return_value={
        "verified": True, "account_id": "account-1", "pixel_id": "pixel-1",
        "optimization_goal": "PIXEL_PURCHASE", "conversion_window": "SWIPE_28DAY_VIEW_1DAY",
        "eligibility_status": "ELIGIBLE"}))
    monkeypatch.setattr(media, "available_public_profile_ids", AsyncMock(return_value={"profile-1"}))
    monkeypatch.setattr(management, "snapchat_campaign_mutations_enabled", lambda: True)
    monkeypatch.setattr(management, "snapchat_campaign_activation_enabled", lambda: True)
    monkeypatch.setattr(management, "_capture_proposal_baseline", AsyncMock(return_value={
        "windows": [], "coverage": {"complete": False, "cost_profit_status": "unavailable"},
        "status": "unavailable", "inventory_verification_status": "verified", "inventory_delivery_blocked": False}))
    for name in ("_ensure_proposal_product_links", "_refresh_verified_entity_cache_deferred_safe",
            "_adopt_proposal_products_deferred_safe", "_record_management_decision_deferred_safe"):
        monkeypatch.setattr(management, name, AsyncMock(return_value=True))
    monkeypatch.setattr(management, "_retry_terminal_product_adoption", retry)
    return SimpleNamespace(db=db, entities=entities, writes=writes, account=account, product=product, execute=execute)


async def approved(env):
    preview = await module.preview_publication(env.db, "owner", payload())
    return await module.approve_publication(env.db, "owner", preview["publication_id"],
        module.PublicationApproval(confirm_token=preview["confirm_token"], expected_revision=preview["revision"]))


@pytest.mark.asyncio
async def test_real_management_provider_none_all_four_stages_with_unavailable_baseline(environment):
    env = environment
    job = await approved(env)
    result = await module.run_publication(env.db, "owner", job["publication_id"])
    assert result["status"] == "completed"
    assert env.writes == list(module.ACTIONS)
    assert result["delivery_status"] == "ACTIVE"
    assert env.entities["provider-campaign-1"]["creation_state"] == "PUBLISHED"
    assert result["review"]["creative_review_status"] == "PENDING_REVIEW"
    children = env.db[management.PROPOSAL_COLLECTION].rows
    assert len(children) == 4
    for child in children:
        assert child["publication_id"] == job["publication_id"]
        assert child["baseline"]["windows"] == []
        assert child["publication_creation_proof"]["contract"] == module.PROOF_CONTRACT
        assert child["approved_by"] == "owner"
        if child["action"] != "creative.create":
            assert child["verification"]["status"] == "ACTIVE"
        assert child["baseline"]["coverage"]["complete"] is False
    assert (await module.run_publication(env.db, "owner", job["publication_id"]))["status"] == "completed"
    assert len(env.writes) == 4


@pytest.mark.asyncio
@pytest.mark.parametrize("client_evidence", [None, {
    "source": "snapchat_active_publication_v1", "baseline_available": True,
    "publication_creation_proof": {"active_delivery": True, "approved_by": "owner"},
}])
async def test_ordinary_create_still_refuses_unavailable_baseline(environment, client_evidence):
    request = management.SnapchatManagementProposalInput(action="campaign.create", account_id="account-1",
        payload={"name": "ordinary", "start_time": payload().start_time,
            "objective_v2_properties": {"objective_v2_type": "SALES"}},
        reason="ordinary owner preview", idempotency_key="ordinary-no-proof",
        expected_outcome=client_evidence)
    with pytest.raises(HTTPException) as raised:
        await management.create_snapchat_management_proposal(environment.db, "owner", "owner", request)
    assert raised.value.detail["code"] == "snapchat_management_decision_baseline_unavailable"
    assert environment.writes == []


def test_default_publication_field_preserves_exact_legacy_request_fingerprint():
    legacy = {
        "action": "campaign.create", "account_id": "account-1", "target_id": None,
        "parent_id": None, "provider_target_id": None, "provider_parent_id": None,
        "payload": {"name": "Legacy campaign", "start_time": "2027-01-01T12:00:00Z",
            "objective_v2_properties": {"objective_v2_type": "SALES"}},
        "reason": "Legacy owner request", "idempotency_key": "legacy-request-key",
        "activation_acknowledged": False, "settings_proof": None, "expected_outcome": None,
        "supporting_evidence": [], "products": [], "trend_override_reason": None,
        "safety_protocol_version": 1,
    }
    expected = hashlib.sha256(json.dumps(legacy, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
    absent = management.SnapchatManagementProposalInput(**legacy)
    explicit_none = management.SnapchatManagementProposalInput(**legacy, publication_id=None)
    assert management.snapchat_management_request_fingerprint(absent) == expected
    assert management.snapchat_management_request_fingerprint(explicit_none) == expected
    with_reference = management.SnapchatManagementProposalInput(**legacy, publication_id="a" * 64)
    assert management.snapchat_management_request_fingerprint(with_reference) != expected


@pytest.mark.parametrize("action", ["campaign.update", "ad_squad.update", "ad.update"])
def test_publication_reference_cannot_authorize_updates_or_activation(action):
    with pytest.raises(ValueError):
        management.SnapchatManagementProposalInput(action=action, account_id="account-1",
            target_id="target-1", parent_id="parent-1", publication_id="a" * 64,
            payload={"status": "ACTIVE"}, activation_acknowledged=True,
            reason="update with fake publication reference", idempotency_key="forged-update-key")


@pytest.mark.asyncio
async def test_foreign_owner_changed_plan_and_unapproved_parent_refuse(environment):
    preview = await module.preview_publication(environment.db, "owner", payload())
    with pytest.raises(HTTPException):
        await module.run_publication(environment.db, "other", preview["publication_id"])
    with pytest.raises(HTTPException):
        await module.run_publication(environment.db, "owner", preview["publication_id"])
    row = environment.db[module.PUBLICATION_COLLECTION].rows[0]
    row["plan"]["daily_budget_micro"] = 999_000_000
    with pytest.raises(HTTPException):
        await module._load(environment.db, "owner", preview["publication_id"])
    assert environment.writes == []


@pytest.mark.asyncio
async def test_concurrent_resume_has_only_one_four_create_sequence(environment):
    job = await approved(environment)
    results = await asyncio.gather(
        module.run_publication(environment.db, "owner", job["publication_id"]),
        module.run_publication(environment.db, "owner", job["publication_id"]), return_exceptions=True)
    assert any(isinstance(item, dict) and item["status"] == "completed" for item in results)
    assert environment.writes == list(module.ACTIONS)


@pytest.mark.asyncio
async def test_bson_naive_utc_approval_and_lease_dates_are_supported(environment):
    job = await approved(environment)
    row = environment.db[module.PUBLICATION_COLLECTION].rows[0]
    row["expires_at"] = media._date(row["expires_at"]).replace(tzinfo=None)
    row["lease_expires_at"] = (media._now() - timedelta(seconds=1)).replace(tzinfo=None)
    row["lease_token"] = "expired-server-claim"
    row["worker_phase"] = "running"
    assert (await module.run_publication(environment.db, "owner", job["publication_id"]))["status"] == "completed"


def test_owner_start_time_still_requires_explicit_timezone():
    with pytest.raises(ValueError):
        module.PublicationInput(**{**payload().model_dump(), "start_time": "2027-01-01T12:00:00"})


@pytest.mark.asyncio
async def test_lost_initial_claim_ack_stabilizes_only_its_own_token(environment):
    job = await approved(environment)
    collection = environment.db[module.PUBLICATION_COLLECTION]
    interrupted = False

    async def lost_ack(row, update):
        nonlocal interrupted
        if not interrupted and update.get("$set", {}).get("worker_phase") == "claimed":
            interrupted = True
            raise asyncio.CancelledError()

    collection.after_update = lost_ack
    with pytest.raises(asyncio.CancelledError):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    row = await module._load(environment.db, "owner", job["publication_id"])
    assert row["status"] == "interrupted" and row["lease_token"] is None
    assert environment.writes == []
    collection.after_update = None
    assert (await module.run_publication(environment.db, "owner", job["publication_id"]))["status"] == "completed"


@pytest.mark.asyncio
async def test_cancelled_loser_never_clears_a_different_worker_token(environment, monkeypatch):
    job = await approved(environment)
    original = module._save
    collection = environment.db[module.PUBLICATION_COLLECTION]

    async def lose(db, row, values):
        if values.get("worker_phase") == "claimed":
            stored = collection.rows[0]
            stored.update({"status": "running", "lease_token": "other-winner",
                "lease_expires_at": (media._now() + timedelta(minutes=5)).isoformat()})
            raise asyncio.CancelledError()
        return await original(db, row, values)

    monkeypatch.setattr(module, "_save", lose)
    with pytest.raises(asyncio.CancelledError):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    assert collection.rows[0]["lease_token"] == "other-winner"
    assert collection.rows[0]["status"] == "running" and environment.writes == []


@pytest.mark.asyncio
async def test_stale_unstarted_claim_is_fenced_before_resume(environment):
    job = await approved(environment)
    row = environment.db[module.PUBLICATION_COLLECTION].rows[0]
    row.update({"status": "running", "worker_phase": "claimed", "lease_token": "old-unstarted",
        "claimed_at": (media._now() - timedelta(seconds=61)).replace(tzinfo=None),
        "lease_expires_at": (media._now() + timedelta(minutes=5)).replace(tzinfo=None)})
    old = deepcopy(row)
    assert (await module.run_publication(environment.db, "owner", job["publication_id"]))["status"] == "completed"
    with pytest.raises(HTTPException):
        await module._save(environment.db, old, {"worker_phase": "running"})
    assert environment.writes == list(module.ACTIONS)


@pytest.mark.asyncio
async def test_unknown_creative_positive_adoption_never_recreates(environment, monkeypatch):
    env = environment

    async def lost(self, operation, *, final_authorizer=None):
        result = await env.execute(self, operation, final_authorizer=final_authorizer)
        if operation["action"] == "creative.create":
            raise HTTPException(502, detail={"code": "lost_creative_ack"})
        return result

    monkeypatch.setattr(management.SnapchatManagementProvider, "execute", lost)
    job = await approved(env)
    with pytest.raises(HTTPException):
        await module.run_publication(env.db, "owner", job["publication_id"])
    assert env.writes == list(module.ACTIONS[:3])
    recovered = await module.reconcile_publication(env.db, "owner", job["publication_id"])
    assert recovered["current_step"] == 3
    monkeypatch.setattr(management.SnapchatManagementProvider, "execute", env.execute)
    result = await module.run_publication(env.db, "owner", job["publication_id"])
    assert result["status"] == "completed" and env.writes == list(module.ACTIONS)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["missing", "duplicate", "body_drift"])
async def test_unknown_creative_zero_ambiguous_or_drift_never_recreates(environment, monkeypatch, change):
    env = environment

    async def lost(self, operation, *, final_authorizer=None):
        result = await env.execute(self, operation, final_authorizer=final_authorizer)
        if operation["action"] == "creative.create":
            raise HTTPException(502, detail={"code": "lost_ack"})
        return result

    monkeypatch.setattr(management.SnapchatManagementProvider, "execute", lost)
    job = await approved(env)
    with pytest.raises(HTTPException):
        await module.run_publication(env.db, "owner", job["publication_id"])
    entity = env.entities["provider-creative-1"]
    if change == "missing":
        del env.entities["provider-creative-1"]
    elif change == "duplicate":
        env.entities["duplicate-creative"] = {**deepcopy(entity), "id": "duplicate-creative"}
    else:
        entity["headline"] = "changed"
    with pytest.raises(HTTPException):
        await module.reconcile_publication(env.db, "owner", job["publication_id"])
    with pytest.raises(HTTPException):
        await module.run_publication(env.db, "owner", job["publication_id"])
    assert env.writes == list(module.ACTIONS[:3])


@pytest.mark.asyncio
async def test_expired_parent_requires_explicit_review_and_preserves_completed_ids(environment):
    job = await approved(environment)
    row = environment.db[module.PUBLICATION_COLLECTION].rows[0]
    row["expires_at"] = (media._now() - timedelta(seconds=1)).replace(tzinfo=None)
    with pytest.raises(HTTPException):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    reviewed = await module.review_publication(environment.db, "owner", job["publication_id"])
    assert reviewed["publication_id"] == job["publication_id"] and reviewed["status"] == "previewed"
    with pytest.raises(HTTPException):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    renewed = await module.approve_publication(environment.db, "owner", job["publication_id"],
        module.PublicationApproval(confirm_token=reviewed["confirm_token"], expected_revision=reviewed["revision"]))
    assert (await module.run_publication(environment.db, "owner", renewed["publication_id"]))["status"] == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("provider_write_state", "attempting"), ("provider_write_uncertain", True),
    ("provider_write_reached", True),
])
async def test_child_refresh_refuses_any_uncertain_or_sent_marker(environment, field, value):
    job = await approved(environment)
    parent = await module._load(environment.db, "owner", job["publication_id"])
    parent = await module._save(environment.db, parent, {"status": "running", "lease_token": "test-claim",
        "lease_expires_at": (media._now() + timedelta(minutes=5)).isoformat()})
    child = await management.create_snapchat_management_proposal(environment.db, "owner", "owner",
        management.SnapchatManagementProposalInput(**module._request(parent, 0)))
    stored = environment.db[management.PROPOSAL_COLLECTION].rows[0]
    stored[field] = value
    with pytest.raises(HTTPException):
        await module.refresh_publication_child(environment.db, parent, deepcopy(stored), 0)
    assert environment.writes == []


@pytest.mark.asyncio
async def test_same_expired_never_sent_child_is_refreshed_without_a_replacement_key(environment):
    job = await approved(environment)
    parent = await module._load(environment.db, "owner", job["publication_id"])
    parent = await module._save(environment.db, parent, {"status": "running", "lease_token": "test-claim",
        "lease_expires_at": (media._now() + timedelta(minutes=5)).isoformat()})
    child = await management.create_snapchat_management_proposal(environment.db, "owner", "owner",
        management.SnapchatManagementProposalInput(**module._request(parent, 0)))
    environment.db[management.PROPOSAL_COLLECTION].rows[0]["expires_at"] = (media._now() - timedelta(seconds=1)).replace(tzinfo=None)
    await module._save(environment.db, parent, {"status": "interrupted", "lease_token": None})
    result = await module.run_publication(environment.db, "owner", job["publication_id"])
    assert result["status"] == "completed"
    assert len(environment.db[management.PROPOSAL_COLLECTION].rows) == 4
    assert environment.db[management.PROPOSAL_COLLECTION].rows[0]["proposal_id"] == child["proposal_id"]
    assert environment.writes == list(module.ACTIONS)


@pytest.mark.asyncio
async def test_kill_switch_and_currency_drift_prevent_publication_writes(environment, monkeypatch):
    job = await approved(environment)
    monkeypatch.setattr(management, "snapchat_campaign_mutations_enabled", lambda: False)
    with pytest.raises(HTTPException):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    monkeypatch.setattr(management, "snapchat_campaign_mutations_enabled", lambda: True)
    environment.account["currency"] = "USD"
    with pytest.raises(HTTPException):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    assert environment.writes == []


@pytest.mark.asyncio
async def test_final_fence_refuses_changed_budget_or_foreign_plan_reference(environment):
    job = await approved(environment)
    row = await module._load(environment.db, "owner", job["publication_id"])
    row = await module._save(environment.db, row, {"status": "running", "lease_token": "test-claim",
        "lease_expires_at": (media._now() + timedelta(minutes=5)).isoformat()})
    request = management.SnapchatManagementProposalInput(**module._request(row, 0))
    changed = request.model_copy(update={"reason": "different owner intent"})
    with pytest.raises(HTTPException):
        await module.validate_publication_creation_proof(environment.db, "owner", changed,
            management.build_snapchat_operation(changed))
    with pytest.raises(HTTPException):
        await module.validate_publication_creation_proof(environment.db, "other-owner", request,
            management.build_snapchat_operation(request))
    assert environment.writes == []


@pytest.mark.asyncio
async def test_product_and_variant_are_resolved_in_the_owner_catalog(monkeypatch):
    monkeypatch.setitem(sys.modules, "product_v2_routes", SimpleNamespace(PRODUCTS="mezan_products_v2"))
    db = DB()
    db["mezan_products_v2"].rows.append({"user_id": "owner", "salla_product_id": "product-1",
        "name": "Product", "status": "active", "variants": [{"id": "variant-1"}]})
    assert (await ACTUAL_PRODUCT_READER(db, "owner", payload().model_dump()))["name"] == "Product"
    with pytest.raises(HTTPException):
        await ACTUAL_PRODUCT_READER(db, "foreign", payload().model_dump())
    with pytest.raises(HTTPException):
        await ACTUAL_PRODUCT_READER(db, "owner", {**payload().model_dump(), "product_variant_id": "foreign"})


@pytest.mark.asyncio
@pytest.mark.parametrize("dependency", ["role", "profile", "pixel"])
async def test_missing_dependencies_block_preview_before_any_create(environment, monkeypatch, dependency):
    if dependency == "role":
        monkeypatch.setattr(management.SnapchatManagementProvider, "management_role", AsyncMock(return_value={"allowed": False}))
    elif dependency == "profile":
        monkeypatch.setattr(media, "available_public_profile_ids", AsyncMock(return_value={"other-profile"}))
    else:
        monkeypatch.setattr(management.SnapchatManagementProvider, "list_account_pixels", AsyncMock(return_value=[{"id": "foreign-pixel"}]))
    with pytest.raises(HTTPException):
        await module.preview_publication(environment.db, "owner", payload())
    assert environment.writes == [] and environment.db[module.PUBLICATION_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_concurrent_loser_cannot_interrupt_running_winner(environment, monkeypatch):
    env = environment
    entered, release = asyncio.Event(), asyncio.Event()

    async def pause(self, operation, *, final_authorizer=None):
        if operation["action"] == "campaign.create":
            entered.set()
            await release.wait()
        return await env.execute(self, operation, final_authorizer=final_authorizer)

    monkeypatch.setattr(management.SnapchatManagementProvider, "execute", pause)
    job = await approved(env)
    first = asyncio.create_task(module.run_publication(env.db, "owner", job["publication_id"]))
    try:
        await asyncio.wait_for(entered.wait(), 1)
        token = (await module._load(env.db, "owner", job["publication_id"]))["lease_token"]
        with pytest.raises(HTTPException):
            await module.run_publication(env.db, "owner", job["publication_id"])
        assert (await module._load(env.db, "owner", job["publication_id"]))["lease_token"] == token
    finally:
        release.set()
        result = await first
    assert result["status"] == "completed" and env.writes == list(module.ACTIONS)


@pytest.mark.asyncio
async def test_cancelled_initial_find_does_not_claim_or_interrupt_parent(environment, monkeypatch):
    job = await approved(environment)
    collection = environment.db[module.PUBLICATION_COLLECTION]
    original = collection.find_one

    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(collection, "find_one", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    monkeypatch.setattr(collection, "find_one", original)
    row = await module._load(environment.db, "owner", job["publication_id"])
    assert row["status"] == "approved" and row["lease_token"] is None and environment.writes == []


@pytest.mark.asyncio
async def test_lost_parent_insert_ack_recovers_same_plan_and_key(environment, monkeypatch):
    request = payload()
    collection = environment.db[module.PUBLICATION_COLLECTION]
    original = collection.update_one
    lost = False

    async def lose_ack(query, update, **kwargs):
        nonlocal lost
        result = await original(query, update, **kwargs)
        if "$setOnInsert" in update and not lost:
            lost = True
            raise asyncio.CancelledError()
        return result

    monkeypatch.setattr(collection, "update_one", lose_ack)
    with pytest.raises(asyncio.CancelledError):
        await module.preview_publication(environment.db, "owner", request)
    recovered = await module.preview_publication(environment.db, "owner", request)
    assert recovered["status"] == "previewed" and recovered["confirm_token"]
    assert len(collection.rows) == 1 and environment.writes == []


@pytest.mark.asyncio
async def test_completed_child_survives_lost_parent_checkpoint_without_duplicate_create(environment, monkeypatch):
    job = await approved(environment)
    original, interrupted = module._save, False

    async def lose_checkpoint(db, row, values):
        nonlocal interrupted
        if values.get("current_step") == 1 and not interrupted:
            interrupted = True
            raise asyncio.CancelledError()
        return await original(db, row, values)

    monkeypatch.setattr(module, "_save", lose_checkpoint)
    with pytest.raises(asyncio.CancelledError):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    assert environment.writes == ["campaign.create"]
    result = await module.run_publication(environment.db, "owner", job["publication_id"])
    assert result["status"] == "completed" and environment.writes == list(module.ACTIONS)
    assert result["steps"][0]["provider_entity_id"] == "provider-campaign-1"


@pytest.mark.asyncio
async def test_child_approval_before_worker_cancellation_resumes_same_proposal(environment, monkeypatch):
    job = await approved(environment)
    original = management.execute_snapchat_management_proposal
    cancelled = False

    async def before_execute(*args, **kwargs):
        nonlocal cancelled
        if not cancelled:
            cancelled = True
            raise asyncio.CancelledError()
        return await original(*args, **kwargs)

    monkeypatch.setattr(management, "execute_snapchat_management_proposal", before_execute)
    with pytest.raises(asyncio.CancelledError):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    first_id = environment.db[management.PROPOSAL_COLLECTION].rows[0]["proposal_id"]
    assert environment.writes == []
    result = await module.run_publication(environment.db, "owner", job["publication_id"])
    assert result["status"] == "completed" and environment.writes == list(module.ACTIONS)
    assert environment.db[management.PROPOSAL_COLLECTION].rows[0]["proposal_id"] == first_id


@pytest.mark.asyncio
@pytest.mark.parametrize("next_link", [
    "https://foreign.example/v1/adaccounts/account-1/creatives?cursor=x",
    "https://adsapi.snapchat.com/v1/adaccounts/foreign/creatives?cursor=x",
    "https://adsapi.snapchat.com/v1/adaccounts/account-1/creatives?cursor=x&type=WEB_VIEW",
])
async def test_creative_recovery_catalog_cannot_escape_or_filter_account(next_link):
    provider = SimpleNamespace(_request=AsyncMock(return_value={"request_status": "SUCCESS",
        "creatives": [], "paging": {"next_link": next_link}}))
    with pytest.raises(HTTPException):
        await module._creative_catalog(provider, "account-1")
    provider._request.assert_awaited_once_with("GET", "/adaccounts/account-1/creatives", params={"limit": 1000})


@pytest.mark.asyncio
async def test_parent_review_keeps_previous_approval_events_without_secret_material(environment):
    job = await approved(environment)
    first_revision = job["approval_revision"]
    for _ in range(2):
        reviewed = await module.review_publication(environment.db, "owner", job["publication_id"])
        assert reviewed["approval_revision"] is None
        job = await module.approve_publication(environment.db, "owner", job["publication_id"],
            module.PublicationApproval(confirm_token=reviewed["confirm_token"], expected_revision=reviewed["revision"]))
    approvals = [event for event in job["events"] if event["event"] == "plan_approved"]
    assert len(approvals) == 3 and approvals[0]["approval_revision"] == first_revision
    assert len({event["approval_revision"] for event in approvals}) == 3
    assert all(event["actor_id"] == "owner" for event in job["events"])
    assert sum(event["event"] == "remaining_plan_reviewed" for event in job["events"]) == 2
    assert all(not {"confirm_token", "confirm_token_hash", "lease_token"}.intersection(event)
        for event in job["events"])


@pytest.mark.asyncio
async def test_parent_audit_is_atomically_bounded_to_one_hundred_events(environment):
    job = await approved(environment)
    row = await module._load(environment.db, "owner", job["publication_id"])
    for index in range(105):
        row = await module._save(environment.db, row, {"last_event": {"event": "checkpoint", "step": index}})
    persisted = await module._load(environment.db, "owner", job["publication_id"])
    assert len(persisted["events"]) == 100
    assert persisted["events"][-1]["revision"] == persisted["revision"]
    assert persisted["events"] == row["events"]


@pytest.mark.asyncio
@pytest.mark.parametrize("inventory", [
    {"inventory_verification_status": "incomplete", "inventory_delivery_blocked": False},
    {"inventory_verification_status": "verified", "inventory_delivery_blocked": True},
    {},
])
async def test_active_launch_refuses_unverified_or_blocked_current_inventory(environment, monkeypatch, inventory):
    monkeypatch.setattr(management, "_capture_proposal_baseline", AsyncMock(return_value={
        "windows": [], "coverage": {"complete": False}, **inventory}))
    with pytest.raises(HTTPException) as raised:
        await module.preview_publication(environment.db, "owner", payload())
    assert raised.value.detail["code"] == "snapchat_publication_current_inventory_unverified_or_blocked"
    assert environment.writes == []


@pytest.mark.asyncio
async def test_direct_active_requires_both_flags_and_fresh_inventory_at_write_fence(environment, monkeypatch):
    job = await approved(environment)
    monkeypatch.setattr(management, "snapchat_campaign_activation_enabled", lambda: False)
    with pytest.raises(HTTPException):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    assert environment.writes == []
    monkeypatch.setattr(management, "snapchat_campaign_activation_enabled", lambda: True)
    original = module._dependencies
    calls = 0

    async def lose_inventory(*args, **kwargs):
        nonlocal calls
        await original(*args, **kwargs)
        calls += 1
        if calls >= 2:
            monkeypatch.setattr(management, "_capture_proposal_baseline", AsyncMock(return_value={
                "windows": [], "inventory_verification_status": "incomplete", "inventory_delivery_blocked": False}))

    monkeypatch.setattr(module, "_dependencies", lose_inventory)
    with pytest.raises(HTTPException):
        await module.run_publication(environment.db, "owner", job["publication_id"])
    assert environment.writes == []


@pytest.mark.asyncio
async def test_unknown_active_ad_dispatch_never_repeats_and_reconciles_actual_active_status(environment, monkeypatch):
    env = environment

    async def lose_ad(self, operation, *, final_authorizer=None):
        result = await env.execute(self, operation, final_authorizer=final_authorizer)
        if operation["action"] == "ad.create":
            env.entities["provider-ad-1"].update({"review_status": "PENDING", "delivery_status": ["PENDING_REVIEW"]})
            raise HTTPException(502, detail={"code": "lost_active_ad_ack"})
        return result

    monkeypatch.setattr(management.SnapchatManagementProvider, "execute", lose_ad)
    job = await approved(env)
    with pytest.raises(HTTPException):
        await module.run_publication(env.db, "owner", job["publication_id"])
    partial = module._public(await module._load(env.db, "owner", job["publication_id"]))
    assert partial["delivery_outcome_unknown"] is True
    assert partial["steps"][0]["configured_status"] == "ACTIVE"
    assert partial["steps"][1]["configured_status"] == "ACTIVE"
    with pytest.raises(HTTPException):
        await module.run_publication(env.db, "owner", job["publication_id"])
    assert env.writes == list(module.ACTIONS)
    recovered = await module.reconcile_publication(env.db, "owner", job["publication_id"])
    assert recovered["current_step"] == 4
    final = await module.reconcile_publication(env.db, "owner", job["publication_id"])
    assert final["status"] == "completed" and final["delivery_status"] == "ACTIVE"
    assert final["review"]["ad_review_status"] == "PENDING"
    assert final["review"]["ad_delivery_status"] == ["PENDING_REVIEW"]
    assert env.writes == list(module.ACTIONS)


@pytest.mark.asyncio
@pytest.mark.parametrize("corruption", ["publication_id", "account_id", "request_fingerprint", "creative_body", "bound_id"])
async def test_wrong_preexisting_completed_creative_cannot_be_adopted_or_create_an_ad(environment, monkeypatch, corruption):
    env = environment
    original = module._save
    interrupted = False

    async def stop_after_creative(db, row, values):
        nonlocal interrupted
        if values.get("current_step") == 3 and not interrupted:
            interrupted = True
            raise asyncio.CancelledError()
        return await original(db, row, values)

    monkeypatch.setattr(module, "_save", stop_after_creative)
    job = await approved(env)
    with pytest.raises(asyncio.CancelledError):
        await module.run_publication(env.db, "owner", job["publication_id"])
    child = next(row for row in env.db[management.PROPOSAL_COLLECTION].rows if row["action"] == "creative.create")
    if corruption == "creative_body":
        child["operation"]["body"]["creatives"][0]["headline"] = "unapproved creative"
    elif corruption == "bound_id":
        env.db[module.PUBLICATION_COLLECTION].rows[0]["steps"][2]["proposal_id"] = "another-proposal"
    else:
        child[corruption] = "wrong-value"
    with pytest.raises(HTTPException):
        await module.run_publication(env.db, "owner", job["publication_id"])
    with pytest.raises(HTTPException):
        await module.reconcile_publication(env.db, "owner", job["publication_id"])
    assert env.writes == list(module.ACTIONS[:3])


@pytest.mark.asyncio
async def test_signed_tracking_binding_accepts_a_real_token_after_clock_advances(environment, monkeypatch):
    from first_party_attribution import core

    original = management.approve_snapchat_management_proposal
    clock = core._now

    async def approve_then_advance(*args, **kwargs):
        child = await original(*args, **kwargs)
        if child["action"] == "creative.create":
            monkeypatch.setattr(core, "_now", lambda: clock() + timedelta(minutes=2))
        return child

    monkeypatch.setattr(management, "approve_snapchat_management_proposal", approve_then_advance)
    job = await approved(environment)
    result = await module.run_publication(environment.db, "owner", job["publication_id"])
    assert result["status"] == "completed" and environment.writes == list(module.ACTIONS)


def test_untrusted_reference_alone_does_not_change_the_default_native_builder():
    request = management.SnapchatManagementProposalInput(action="campaign.create", account_id="account-1",
        publication_id="a" * 64, activation_acknowledged=True,
        payload={"name": "Untrusted active reference", "status": "ACTIVE", "start_time": payload().start_time,
            "objective_v2_properties": {"objective_v2_type": "SALES"}},
        reason="owner reference is not server authorization", idempotency_key="untrusted-active-key")
    assert management.build_snapchat_operation(request)["body"]["campaigns"][0]["status"] == "PAUSED"

