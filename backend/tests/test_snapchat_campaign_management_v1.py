from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from integrations_control_center import snapchat_campaign_management as module


class Result:
    def __init__(self, matched_count=1):
        self.matched_count = matched_count


def _matches(row, query):
    return all(row.get(key) == value for key, value in query.items())


class Collection:
    def __init__(self):
        self.rows = []

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name")

    async def insert_one(self, row):
        self.rows.append(deepcopy(row))

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if _matches(row, query):
                output = deepcopy(row)
                if projection and projection.get("_id") == 0:
                    output.pop("_id", None)
                return output
        return None

    async def update_one(self, query, update, upsert=False):
        for row in self.rows:
            if not _matches(row, query):
                continue
            row.update(deepcopy(update.get("$set") or {}))
            for key, value in (update.get("$inc") or {}).items():
                row[key] = row.get(key, 0) + value
            return Result(1)
        return Result(0)


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())


class Provider:
    def __init__(self):
        self.entities = {}
        self.executions = []

    async def management_role(self, account, action):
        return {"allowed": True, "role": "general", "reason": None}

    async def read_entity(self, entity_type, entity_id):
        return deepcopy(self.entities[(entity_type, entity_id)])

    async def execute(self, operation):
        self.executions.append(deepcopy(operation))
        if operation["method"] == "POST":
            source = deepcopy(operation["body"][operation["plural"]][0])
            entity_id = f"created-{operation['entity_type']}"
            source["id"] = entity_id
            self.entities[(operation["entity_type"], entity_id)] = source
            return deepcopy(source)
        entity_id = operation.get("target_id")
        if not entity_id:
            entity_id = operation["path"].rsplit("/", 1)[-1]
        current = self.entities[(operation["entity_type"], entity_id)]
        for patch in operation["body"]:
            key = patch["path"].lstrip("/")
            if patch["op"] == "remove":
                current.pop(key, None)
            else:
                current[key] = deepcopy(patch.get("value"))
        return deepcopy(current)


def campaign_create(**overrides):
    values = {
        "action": "campaign.create",
        "account_id": "account-1",
        "payload": {
            "name": "Mezan safe campaign",
            "start_time": "2026-08-11T00:00:00Z",
            "objective_v2_properties": {"objective_v2_type": "SALES"},
            "daily_budget_micro": 40_000_000,
        },
        "reason": "اختبار إنشاء آمن ومتوقف",
        "idempotency_key": "campaign-create-001",
    }
    values.update(overrides)
    return module.SnapchatManagementProposalInput(**values)


def test_campaign_creation_is_forced_paused_and_uses_fixed_snap_path():
    operation = module.build_snapchat_operation(campaign_create())
    entity = operation["body"]["campaigns"][0]
    assert operation["method"] == "POST"
    assert operation["path"] == "/adaccounts/account-1/campaigns"
    assert entity["status"] == "PAUSED"
    assert entity["ad_account_id"] == "account-1"
    assert operation["activates_delivery"] is False


def test_active_create_is_rejected_even_when_activation_is_acknowledged():
    with pytest.raises(ValidationError):
        campaign_create(
            payload={
                "name": "unsafe",
                "start_time": "2026-08-11T00:00:00Z",
                "status": "ACTIVE",
                "objective_v2_properties": {"objective_v2_type": "SALES"},
            },
            activation_acknowledged=True,
        )


def test_campaign_update_uses_json_patch_not_put():
    payload = module.SnapchatManagementProposalInput(
        action="campaign.update",
        account_id="account-1",
        target_id="campaign-1",
        payload={"status": "PAUSED", "daily_budget_micro": 55_000_000},
        reason="خفض الميزانية وإيقاف الحملة",
        idempotency_key="campaign-update-001",
    )
    operation = module.build_snapchat_operation(payload)
    assert operation["method"] == "PATCH"
    assert operation["path"] == "/adaccounts/account-1/campaigns/campaign-1"
    assert {patch["path"] for patch in operation["body"]} == {
        "/daily_budget_micro", "/status",
    }


def test_budget_above_runtime_ceiling_is_rejected(monkeypatch):
    monkeypatch.setenv(module.MAX_DAILY_BUDGET_ENV, "10000000")
    with pytest.raises(ValueError, match="safety range|between"):
        module.build_snapchat_operation(campaign_create(payload={
            "name": "too high",
            "start_time": "2026-08-11T00:00:00Z",
            "objective_v2_properties": {"objective_v2_type": "SALES"},
            "daily_budget_micro": 11_000_000,
        }))


def test_creative_requires_profile_and_does_not_accept_status():
    with pytest.raises(ValueError, match="profile_properties.profile_id"):
        module.build_snapchat_operation(module.SnapchatManagementProposalInput(
            action="creative.create",
            account_id="account-1",
            payload={
                "name": "creative",
                "type": "SNAP_AD",
                "headline": "متجر أماسي",
                "top_snap_media_id": "media-1",
            },
            reason="اختبار متطلبات الإبداع",
            idempotency_key="creative-profile-required",
        ))

    with pytest.raises(ValueError, match="unsupported fields: status"):
        module.build_snapchat_operation(module.SnapchatManagementProposalInput(
            action="creative.create",
            account_id="account-1",
            payload={
                "name": "creative",
                "type": "SNAP_AD",
                "headline": "متجر أماسي",
                "top_snap_media_id": "media-1",
                "profile_properties": {"profile_id": "profile-1"},
                "status": "PAUSED",
            },
            reason="اختبار منع حقول غير مدعومة",
            idempotency_key="creative-status-rejected",
        ))


@pytest.mark.asyncio
async def test_full_create_approval_execute_and_verified_rollback(monkeypatch):
    db = DB()
    provider = Provider()

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1", "display_name": "AMASI"}

    async def upsert(*args, **kwargs):
        return True

    monkeypatch.setattr(module, "_selected_account", selected)
    monkeypatch.setattr(module, "_upsert_entity", upsert)
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")

    preview = await module.create_snapchat_management_proposal(
        db, "owner-1", "owner-1", campaign_create(), provider=provider,
    )
    assert preview["status"] == "previewed"
    assert preview["creates_paused"] is True
    assert preview["provider_write_reached"] is False

    approved = await module.approve_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        preview["proposal_id"],
        module.SnapchatManagementApprovalInput(
            confirm_token=preview["confirm_token"],
            expected_revision=preview["revision"],
        ),
    )
    assert approved["status"] == "approved"

    completed = await module.execute_snapchat_management_proposal(
        db, "owner-1", "owner-1", preview["proposal_id"], provider=provider,
    )
    assert completed["status"] == "completed"
    assert completed["verification"]["verified"] is True
    assert completed["verification"]["status"] == "PAUSED"

    rolled_back = await module.rollback_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        preview["proposal_id"],
        module.SnapchatManagementRollbackInput(
            confirmation_phrase=completed["confirmation_phrase"],
            reason="إنهاء اختبار الحملة المتوقفة",
        ),
        provider=provider,
    )
    assert rolled_back["status"] == "rolled_back"
    assert provider.entities[("campaign", "created-campaign")]["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_execution_kill_switch_blocks_before_provider_write(monkeypatch):
    db = DB()
    proposal_id = "01234567-0000-0000-0000-000000000000"
    db[module.PROPOSAL_COLLECTION].rows.append({
        "proposal_id": proposal_id,
        "user_id": "owner-1",
        "status": "approved",
    })
    monkeypatch.delenv(module.MUTATIONS_ENABLED_ENV, raising=False)
    with pytest.raises(HTTPException) as raised:
        await module.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", proposal_id, provider=Provider(),
        )
    assert raised.value.detail["code"] == "snapchat_campaign_mutations_disabled"


@pytest.mark.asyncio
async def test_activation_has_independent_kill_switch(monkeypatch):
    db = DB()
    proposal_id = "11234567-0000-0000-0000-000000000000"
    activation_operation = module.build_snapchat_operation(
        module.SnapchatManagementProposalInput(
            action="campaign.update",
            account_id="account-1",
            target_id="campaign-1",
            payload={"status": "ACTIVE"},
            reason="اختبار مفتاح تشغيل مستقل",
            idempotency_key="activation-switch-test",
            activation_acknowledged=True,
        )
    )
    db[module.PROPOSAL_COLLECTION].rows.append({
        "proposal_id": proposal_id,
        "user_id": "owner-1",
        "status": "approved",
        "account_id": "account-1",
        "target_id": "campaign-1",
        "action": "campaign.update",
        "operation": activation_operation,
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat(),
    })
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")
    monkeypatch.delenv(module.ACTIVATION_ENABLED_ENV, raising=False)
    with pytest.raises(HTTPException) as raised:
        await module.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", proposal_id, provider=Provider(),
        )
    assert raised.value.detail["code"] == "snapchat_campaign_activation_disabled"


@pytest.mark.asyncio
async def test_update_proposal_rejects_campaign_from_other_account(monkeypatch):
    db = DB()
    provider = Provider()
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1", "ad_account_id": "another-account", "status": "PAUSED",
    }

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1"}

    monkeypatch.setattr(module, "_selected_account", selected)
    payload = module.SnapchatManagementProposalInput(
        action="campaign.update",
        account_id="account-1",
        target_id="campaign-1",
        payload={"status": "PAUSED"},
        reason="اختبار عزل الحسابات الإعلانية",
        idempotency_key="campaign-update-other-account",
    )
    with pytest.raises(HTTPException) as raised:
        await module.create_snapchat_management_proposal(
            db, "owner-1", "owner-1", payload, provider=provider,
        )
    assert raised.value.detail["code"] == "snapchat_management_target_account_mismatch"


@pytest.mark.asyncio
async def test_failed_verification_can_neutralize_confirmed_created_entity(monkeypatch):
    db = DB()
    provider = Provider()
    proposal_id = "21234567-0000-0000-0000-000000000000"
    provider.entities[("campaign", "created-campaign")] = {
        "id": "created-campaign",
        "ad_account_id": "account-1",
        "status": "ACTIVE",
    }
    operation = module.build_snapchat_operation(campaign_create())
    db[module.PROPOSAL_COLLECTION].rows.append({
        "proposal_id": proposal_id,
        "user_id": "owner-1",
        "status": "failed",
        "account_id": "account-1",
        "action": "campaign.create",
        "provider_write_reached": True,
        "provider_entity_id": "created-campaign",
        "operation": operation,
    })

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1"}

    async def upsert(*args, **kwargs):
        return True

    monkeypatch.setattr(module, "_selected_account", selected)
    monkeypatch.setattr(module, "_upsert_entity", upsert)
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")
    result = await module.rollback_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        proposal_id,
        module.SnapchatManagementRollbackInput(
            confirmation_phrase="تراجع 21234567",
            reason="إيقاف كيان وصل إلى Snapchat قبل فشل التحقق",
        ),
        provider=provider,
    )
    assert result["status"] == "rolled_back"
    assert provider.entities[("campaign", "created-campaign")]["status"] == "PAUSED"


@pytest.mark.asyncio
async def test_execution_rejects_stale_approved_proposal(monkeypatch):
    db = DB()
    proposal_id = "31234567-0000-0000-0000-000000000000"
    db[module.PROPOSAL_COLLECTION].rows.append({
        "proposal_id": proposal_id,
        "user_id": "owner-1",
        "status": "approved",
        "expires_at": (
            datetime.now(timezone.utc) - timedelta(minutes=1)
        ).isoformat(),
    })
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")
    with pytest.raises(HTTPException) as raised:
        await module.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", proposal_id, provider=Provider(),
        )
    assert raised.value.detail["code"] == "snapchat_management_proposal_expired"


@pytest.mark.asyncio
async def test_execution_rejects_tampered_provider_path(monkeypatch):
    db = DB()
    proposal_id = "41234567-0000-0000-0000-000000000000"
    operation = module.build_snapchat_operation(campaign_create())
    operation["path"] = "/adaccounts/another-account/campaigns"
    db[module.PROPOSAL_COLLECTION].rows.append({
        "proposal_id": proposal_id,
        "user_id": "owner-1",
        "status": "approved",
        "account_id": "account-1",
        "action": "campaign.create",
        "operation": operation,
        "expires_at": (
            datetime.now(timezone.utc) + timedelta(minutes=10)
        ).isoformat(),
    })
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")
    with pytest.raises(HTTPException) as raised:
        await module.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", proposal_id, provider=Provider(),
        )
    assert raised.value.detail["code"] == "snapchat_management_operation_integrity_failed"
