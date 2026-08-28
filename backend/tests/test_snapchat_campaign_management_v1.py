from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import ValidationError

from integrations_control_center import snapchat_campaign_management as module
from integrations_control_center import snapchat_entity_settings as settings_module


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
        self.reads = []

    async def management_role(self, account, action):
        return {"allowed": True, "role": "general", "reason": None}

    async def read_entity(self, entity_type, entity_id):
        self.reads.append((entity_type, entity_id))
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


@pytest.fixture(autouse=True)
def fresh_management_settings_gate(monkeypatch):
    async def resolve_settings(
        db,
        user_id,
        entity_type,
        unified_entity_id,
        provider_entity_id=None,
        parent_unified_id=None,
        *,
        now=None,
    ):
        return {
            "unified_entity_id": unified_entity_id,
            "provider_entity_id": provider_entity_id or unified_entity_id,
            "provider_parent_id": parent_unified_id,
            "mapping_status": "verified",
            "mapping_verified": True,
            "settings_status": "settings_complete",
            "financial_controls_allowed": True,
            "financial_field_controls": {
                "daily_budget": {"allowed": True, "reason": "available"},
                "bid": {"allowed": True, "reason": "available"},
            },
            "last_synced_at": "2026-08-28T12:00:00+00:00",
            "currency": "USD",
            "daily_budget_micro": 100_000_000,
            "daily_budget_usd": 100.0,
            "bid_micro": 20_000_000,
            "bid_usd": 20.0,
            "bid_strategy": "LOWEST_COST_WITH_MAX_BID",
        }

    async def list_settings(
        db,
        user_id,
        entity_type=None,
        *,
        limit=500,
        now=None,
    ):
        return []

    monkeypatch.setattr(
        module, "resolve_financial_management_settings", resolve_settings
    )
    monkeypatch.setattr(
        settings_module, "list_financial_management_settings", list_settings
    )


class FailingAccessTokenContext:
    def __init__(self, error):
        self.error = error

    async def access_token(self):
        raise self.error


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


@pytest.mark.asyncio
async def test_management_provider_converts_reauth_token_error_to_bounded_json():
    provider = module.SnapchatManagementProvider(DB(), "owner-1")
    provider.context = FailingAccessTokenContext(
        module.SnapchatNativeSyncError(
            "snapchat_needs_reauth",
            "Snapchat authorization must be renewed.",
            status_code=409,
            result={
                "needs_reauth": True,
                "access_token": "must-not-leak",
                "provider_payload": {"secret": "must-not-leak"},
            },
        )
    )

    with pytest.raises(HTTPException) as raised:
        await provider._request("GET", "/me/organizations")

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "snapchat_needs_reauth",
        "message": "Snapchat authorization must be renewed.",
        "retryable": False,
        "needs_reauth": True,
    }
    assert "must-not-leak" not in str(raised.value.detail)


@pytest.mark.asyncio
async def test_management_provider_preserves_retryable_token_failure_contract():
    provider = module.SnapchatManagementProvider(DB(), "owner-1")
    provider.context = FailingAccessTokenContext(
        module.SnapchatNativeSyncError(
            "snapchat_token_refresh_failed",
            "Snapchat token refresh failed.",
            status_code=502,
            retryable=True,
        )
    )

    with pytest.raises(HTTPException) as raised:
        await provider._request("POST", "/adaccounts/account-1/campaigns", body={})

    assert raised.value.status_code == 502
    assert raised.value.detail == {
        "code": "snapchat_token_refresh_failed",
        "message": "Snapchat token refresh failed.",
        "retryable": True,
    }


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
        "/daily_budget_micro",
        "/status",
    }


def test_unverified_context_cannot_be_used_as_decision_basis():
    with pytest.raises(ValidationError, match="only verified evidence"):
        module.SnapchatManagementProposalInput(
            action="campaign.update",
            account_id="account-1",
            target_id="campaign-1",
            payload={"status": "PAUSED"},
            reason="اقتراح موسمي غير متحقق",
            idempotency_key="unverified-evidence-used",
            supporting_evidence=[
                {
                    "kind": "payday",
                    "value": "يوم 27",
                    "source": "اقتراح المستخدم",
                    "verification_status": "user_suggestion",
                    "confidence": 0.4,
                    "used_in_decision": True,
                    "weight": 0.2,
                }
            ],
        )


def test_external_proposal_cannot_self_mark_evidence_verified():
    with pytest.raises(ValidationError, match="Mezan collectors"):
        module.SnapchatManagementProposalInput(
            action="campaign.update",
            account_id="account-1",
            target_id="campaign-1",
            payload={"status": "PAUSED"},
            reason="النتائج والمخزون هما الأساس",
            idempotency_key="verified-supporting-evidence",
            supporting_evidence=[
                {
                    "kind": "inventory",
                    "value": {"quantity": 0},
                    "source": "mezan_products_v2",
                    "verification_status": "verified",
                    "confidence": 1,
                    "used_in_decision": True,
                    "weight": 0.8,
                }
            ],
        )


def test_product_scope_is_explicit_and_separate_from_sales_attribution():
    payload = module.SnapchatManagementProposalInput(
        action="campaign.update",
        account_id="account-1",
        target_id="campaign-1",
        payload={"daily_budget_micro": 70_000_000},
        reason="توسيع مبيعات منتج المشط مع حماية المكسب",
        idempotency_key="comb-product-scope",
        products=[
            {
                "product_id": "710474094",
                "product_name": "مشط شنب ولحية معدني مخصص بالاسم",
            }
        ],
    )

    assert payload.products[0].product_id == "710474094"
    assert payload.products[0].product_name.startswith("مشط شنب")


def test_budget_above_runtime_ceiling_is_rejected(monkeypatch):
    monkeypatch.setenv(module.MAX_DAILY_BUDGET_ENV, "10000000")
    with pytest.raises(ValueError, match="safety range|between"):
        module.build_snapchat_operation(
            campaign_create(
                payload={
                    "name": "too high",
                    "start_time": "2026-08-11T00:00:00Z",
                    "objective_v2_properties": {"objective_v2_type": "SALES"},
                    "daily_budget_micro": 11_000_000,
                }
            )
        )


def test_creative_requires_profile_and_does_not_accept_status():
    with pytest.raises(ValueError, match="profile_properties.profile_id"):
        module.build_snapchat_operation(
            module.SnapchatManagementProposalInput(
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
            )
        )

    with pytest.raises(ValueError, match="unsupported fields: status"):
        module.build_snapchat_operation(
            module.SnapchatManagementProposalInput(
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
            )
        )


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
        db,
        "owner-1",
        "owner-1",
        campaign_create(),
        provider=provider,
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
        db,
        "owner-1",
        "owner-1",
        preview["proposal_id"],
        provider=provider,
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
    db[module.PROPOSAL_COLLECTION].rows.append(
        {
            "proposal_id": proposal_id,
            "user_id": "owner-1",
            "status": "approved",
        }
    )
    monkeypatch.delenv(module.MUTATIONS_ENABLED_ENV, raising=False)
    with pytest.raises(HTTPException) as raised:
        await module.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            proposal_id,
            provider=Provider(),
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
    db[module.PROPOSAL_COLLECTION].rows.append(
        {
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
        }
    )
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")
    monkeypatch.delenv(module.ACTIVATION_ENABLED_ENV, raising=False)
    with pytest.raises(HTTPException) as raised:
        await module.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            proposal_id,
            provider=Provider(),
        )
    assert raised.value.detail["code"] == "snapchat_campaign_activation_disabled"


@pytest.mark.asyncio
async def test_update_proposal_rejects_campaign_from_other_account(monkeypatch):
    db = DB()
    provider = Provider()
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "another-account",
        "status": "PAUSED",
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
            db,
            "owner-1",
            "owner-1",
            payload,
            provider=provider,
        )
    assert raised.value.detail["code"] == "snapchat_management_target_account_mismatch"


@pytest.mark.asyncio
async def test_recent_improvement_is_documented_without_becoming_a_fixed_rule(
    monkeypatch,
):
    db = DB()
    provider = Provider()
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "status": "ACTIVE",
        "daily_budget_micro": 100_000_000,
    }

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1", "timezone": "Asia/Riyadh"}

    async def baseline(*args, **kwargs):
        return {
            "windows": [{"days": 3}, {"days": 7}],
            "recent_trend": {"recent_improving": True},
            "inventory": [],
            "inventory_delivery_blocked": False,
        }

    monkeypatch.setattr(module, "_selected_account", selected)
    monkeypatch.setattr(module, "_capture_proposal_baseline", baseline)
    payload = module.SnapchatManagementProposalInput(
        action="campaign.update",
        account_id="account-1",
        target_id="campaign-1",
        payload={"status": "PAUSED"},
        reason="أفكر في الإيقاف رغم التحسن",
        idempotency_key="recent-improvement-pause",
    )

    preview = await module.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        payload,
        provider=provider,
    )

    assert preview["status"] == "previewed"
    assert preview["trend_review"] == {
        "recent_improvement_observed": True,
        "delivery_decrease_or_pause": True,
        "separate_explanation_recorded": False,
        "policy": ("supporting_observation_only; never_a_fixed_rule_or_primary_basis"),
    }


@pytest.mark.asyncio
async def test_verified_out_of_stock_blocks_budget_increase(monkeypatch):
    db = DB()
    provider = Provider()
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "status": "ACTIVE",
        "daily_budget_micro": 60_000_000,
    }

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1", "timezone": "Asia/Riyadh"}

    async def baseline(*args, **kwargs):
        return {
            "windows": [{"days": 14}],
            "recent_trend": {"recent_improving": False},
            "inventory": [
                {
                    "salla_product_id": "101",
                    "status": "out_of_stock",
                    "quantity": 0,
                    "delivery_blocked": True,
                }
            ],
            "inventory_delivery_blocked": True,
        }

    monkeypatch.setattr(module, "_selected_account", selected)
    monkeypatch.setattr(module, "_capture_proposal_baseline", baseline)
    payload = module.SnapchatManagementProposalInput(
        action="campaign.update",
        account_id="account-1",
        target_id="campaign-1",
        payload={"daily_budget_micro": 80_000_000},
        reason="اختبار منع الرفع مع نفاد المخزون",
        idempotency_key="inventory-blocks-budget-rise",
    )

    with pytest.raises(HTTPException) as raised:
        await module.create_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            payload,
            provider=provider,
        )

    assert raised.value.detail["code"] == (
        "snapchat_management_inventory_blocks_delivery_increase"
    )
    assert raised.value.detail["inventory"][0]["status"] == "out_of_stock"


@pytest.mark.asyncio
async def test_failed_verification_without_verified_after_snapshot_blocks_rollback(
    monkeypatch,
):
    db = DB()
    provider = Provider()
    proposal_id = "21234567-0000-0000-0000-000000000000"
    provider.entities[("campaign", "created-campaign")] = {
        "id": "created-campaign",
        "ad_account_id": "account-1",
        "status": "ACTIVE",
    }
    operation = module.build_snapchat_operation(campaign_create())
    db[module.PROPOSAL_COLLECTION].rows.append(
        {
            "proposal_id": proposal_id,
            "user_id": "owner-1",
            "status": "failed",
            "account_id": "account-1",
            "action": "campaign.create",
            "provider_write_reached": True,
            "provider_entity_id": "created-campaign",
            "operation": operation,
        }
    )

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1"}

    async def upsert(*args, **kwargs):
        return True

    monkeypatch.setattr(module, "_selected_account", selected)
    monkeypatch.setattr(module, "_upsert_entity", upsert)
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")
    with pytest.raises(HTTPException) as raised:
        await module.rollback_snapchat_management_proposal(
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
    assert raised.value.detail["code"] == (
        "snapchat_management_rollback_verified_snapshot_missing"
    )
    assert provider.entities[("campaign", "created-campaign")]["status"] == "ACTIVE"


@pytest.mark.asyncio
async def test_execution_rejects_stale_approved_proposal(monkeypatch):
    db = DB()
    proposal_id = "31234567-0000-0000-0000-000000000000"
    db[module.PROPOSAL_COLLECTION].rows.append(
        {
            "proposal_id": proposal_id,
            "user_id": "owner-1",
            "status": "approved",
            "expires_at": (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
        }
    )
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")
    with pytest.raises(HTTPException) as raised:
        await module.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            proposal_id,
            provider=Provider(),
        )
    assert raised.value.detail["code"] == "snapchat_management_proposal_expired"


@pytest.mark.asyncio
async def test_execution_rejects_tampered_provider_path(monkeypatch):
    db = DB()
    proposal_id = "41234567-0000-0000-0000-000000000000"
    operation = module.build_snapchat_operation(campaign_create())
    operation["path"] = "/adaccounts/another-account/campaigns"
    db[module.PROPOSAL_COLLECTION].rows.append(
        {
            "proposal_id": proposal_id,
            "user_id": "owner-1",
            "status": "approved",
            "account_id": "account-1",
            "action": "campaign.create",
            "operation": operation,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat(),
        }
    )
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")
    with pytest.raises(HTTPException) as raised:
        await module.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            proposal_id,
            provider=Provider(),
        )
    assert (
        raised.value.detail["code"] == "snapchat_management_operation_integrity_failed"
    )


def test_nested_bulk_error_preserves_safe_provider_detail():
    with pytest.raises(HTTPException) as raised:
        module._extract_entity(
            {
                "request_status": "SUCCESS",
                "ads": [
                    {
                        "sub_request_status": "ERROR",
                        "errors": [
                            {
                                "error": {
                                    "error_code": "E_CREATIVE_AD_TYPE",
                                    "error_message": (
                                        "Creative type WEB_VIEW requires REMOTE_WEBPAGE"
                                    ),
                                },
                            }
                        ],
                    }
                ],
            },
            "ads",
            "ad",
        )
    assert raised.value.detail == {
        "code": "snapchat_management_subrequest_failed",
        "message": "رفض Snapchat الكيان داخل العملية.",
        "provider_error_code": "E_CREATIVE_AD_TYPE",
        "provider_error_message": ("Creative type WEB_VIEW requires REMOTE_WEBPAGE"),
    }


@pytest.mark.asyncio
async def test_ad_preview_rejects_web_view_with_snap_ad_before_write(monkeypatch):
    db = DB()
    provider = Provider()
    provider.entities.update(
        {
            ("ad_squad", "squad-1"): {
                "id": "squad-1",
                "campaign_id": "campaign-1",
            },
            ("campaign", "campaign-1"): {
                "id": "campaign-1",
                "ad_account_id": "account-1",
            },
            ("creative", "creative-1"): {
                "id": "creative-1",
                "ad_account_id": "account-1",
                "type": "WEB_VIEW",
            },
        }
    )

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1"}

    monkeypatch.setattr(module, "_selected_account", selected)
    payload = module.SnapchatManagementProposalInput(
        action="ad.create",
        account_id="account-1",
        parent_id="squad-1",
        payload={
            "name": "Safe paused ad",
            "creative_id": "creative-1",
            "type": "SNAP_AD",
        },
        reason="اختبار توافق نوع الإعلان مع الإبداع",
        idempotency_key="web-view-snap-ad-mismatch",
    )
    with pytest.raises(HTTPException) as raised:
        await module.create_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            payload,
            provider=provider,
        )
    assert raised.value.status_code == 422
    assert raised.value.detail == {
        "code": "snapchat_management_creative_ad_type_mismatch",
        "message": ("نوع الإبداع WEB_VIEW يتطلب نوع إعلان REMOTE_WEBPAGE في Snapchat."),
        "creative_type": "WEB_VIEW",
        "requested_ad_type": "SNAP_AD",
        "allowed_ad_types": ["REMOTE_WEBPAGE"],
    }
    assert db[module.PROPOSAL_COLLECTION].rows == []
    assert provider.executions == []


@pytest.mark.asyncio
async def test_ad_preview_accepts_web_view_with_remote_webpage(monkeypatch):
    db = DB()
    provider = Provider()
    provider.entities.update(
        {
            ("ad_squad", "squad-1"): {
                "id": "squad-1",
                "campaign_id": "campaign-1",
            },
            ("campaign", "campaign-1"): {
                "id": "campaign-1",
                "ad_account_id": "account-1",
            },
            ("creative", "creative-1"): {
                "id": "creative-1",
                "ad_account_id": "account-1",
                "type": "WEB_VIEW",
            },
        }
    )

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1"}

    monkeypatch.setattr(module, "_selected_account", selected)
    preview = await module.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        module.SnapchatManagementProposalInput(
            action="ad.create",
            account_id="account-1",
            parent_id="squad-1",
            payload={
                "name": "Safe paused ad",
                "creative_id": "creative-1",
                "type": "REMOTE_WEBPAGE",
            },
            reason="اختبار نوع الإعلان الصحيح للإبداع",
            idempotency_key="web-view-remote-webpage-valid",
        ),
        provider=provider,
    )
    stored = db[module.PROPOSAL_COLLECTION].rows[0]
    entity = stored["operation"]["body"]["ads"][0]
    assert preview["status"] == "previewed"
    assert entity["type"] == "REMOTE_WEBPAGE"
    assert entity["status"] == "PAUSED"
    assert provider.executions == []


def test_public_proposal_exposes_only_safe_failure_state():
    proposal = module._public_proposal(
        {
            "proposal_id": "proposal-1",
            "status": "failed",
            "failed_at": "2026-08-11T19:57:54+00:00",
            "failure": {
                "code": "snapchat_management_request_failed",
                "provider_error_message": "Creative type mismatch",
            },
            "provider_entity_id": "ad-1",
        }
    )
    assert proposal["failed_at"] == "2026-08-11T19:57:54+00:00"
    assert proposal["provider_entity_id"] == "ad-1"
    assert proposal["failure"] == {
        "code": "snapchat_management_request_failed",
        "provider_error_message": "Creative type mismatch",
    }


@pytest.mark.asyncio
async def test_execute_route_queues_exactly_one_background_attempt(monkeypatch):
    router = APIRouter()
    calls = []

    async def current_user():
        return {"id": "owner-1"}

    async def fake_execute(db, user_id, actor_id, proposal_id):
        calls.append((db, user_id, actor_id, proposal_id))

    monkeypatch.setattr(module, "execute_snapchat_management_proposal", fake_execute)
    db = DB()
    module.attach_snapchat_campaign_management_routes(
        router,
        db,
        current_user,
        lambda user: user,
    )
    route = next(
        item
        for item in router.routes
        if item.path.endswith("/management/proposals/{proposal_id}/execute")
    )
    background_tasks = BackgroundTasks()
    proposal_id = "51234567-0000-0000-0000-000000000000"
    db[module.PROPOSAL_COLLECTION].rows.append(
        {
            "proposal_id": proposal_id,
            "user_id": "owner-1",
            "status": "approved",
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(minutes=10)
            ).isoformat(),
        }
    )
    response = await route.endpoint(
        proposal_id=proposal_id,
        background_tasks=background_tasks,
        user={"id": "owner-1"},
    )
    assert route.status_code == 202
    assert response == {
        "provider": module.SNAPCHAT_PROVIDER_ID,
        "proposal_id": proposal_id,
        "status": "executing",
    }
    assert calls == []
    assert len(background_tasks.tasks) == 1
    await background_tasks()
    assert calls == [(db, "owner-1", "owner-1", proposal_id)]


def test_structured_field_changes_preserve_raw_micro_and_exact_usd():
    changes = module._structured_field_changes(
        original={
            "daily_budget_micro": 100_250_000,
            "bid_micro": 25_000_000,
            "bid_strategy": "LOWEST_COST_WITH_MAX_BID",
        },
        requested={
            "daily_budget_micro": 90_125_000,
            "bid_micro": 20_000_000,
            "bid_strategy": "TARGET_COST",
        },
        verified={
            "daily_budget_micro": 90_125_000,
            "bid_micro": 20_000_000,
            "bid_strategy": "TARGET_COST",
        },
        currency="USD",
        actor_id="owner-1",
        provider_entity_id="provider-squad",
        occurred_at="2026-08-28T12:30:00+00:00",
    )

    assert changes["fields"]["daily_budget_micro"] == {
        "before": 100_250_000,
        "after": 90_125_000,
        "before_micro": 100_250_000,
        "after_micro": 90_125_000,
        "before_usd": 100.25,
        "after_usd": 90.125,
        "currency": "USD",
    }
    assert changes["fields"]["bid_micro"]["before_usd"] == 25.0
    assert changes["fields"]["bid_micro"]["after_usd"] == 20.0
    assert changes["fields"]["bid_strategy"] == {
        "before": "LOWEST_COST_WITH_MAX_BID",
        "after": "TARGET_COST",
    }
    assert changes["actor_id"] == "owner-1"
    assert changes["provider_entity_id"] == "provider-squad"
    assert changes["provider_reread_verified"] is True


@pytest.mark.asyncio
async def test_financial_preview_and_execute_use_only_verified_provider_ids(
    monkeypatch,
):
    db = DB()
    provider = Provider()
    provider.entities.update(
        {
            ("ad_squad", "provider-squad"): {
                "id": "provider-squad",
                "campaign_id": "provider-campaign",
                "status": "PAUSED",
                "daily_budget_micro": 100_000_000,
                "bid_micro": 20_000_000,
                "bid_strategy": "LOWEST_COST_WITH_MAX_BID",
            },
            ("campaign", "provider-campaign"): {
                "id": "provider-campaign",
                "ad_account_id": "account-1",
                "status": "PAUSED",
            },
        }
    )

    async def selected(*args, **kwargs):
        return {
            "ad_account_id": "account-1",
            "display_name": "AMASI",
            "currency": "USD",
        }

    async def exact_settings(
        db,
        user_id,
        entity_type,
        unified_entity_id,
        provider_entity_id=None,
        parent_unified_id=None,
        *,
        now=None,
    ):
        assert entity_type == "ad_squad"
        assert unified_entity_id == "unified-squad"
        assert provider_entity_id in {None, "provider-squad"}
        assert parent_unified_id == "unified-campaign"
        return {
            "unified_entity_id": "unified-squad",
            "provider_entity_id": "provider-squad",
            "provider_parent_id": "provider-campaign",
            "mapping_status": "verified",
            "mapping_verified": True,
            "settings_status": "settings_complete",
            "financial_controls_allowed": True,
            "financial_field_controls": {
                "daily_budget": {"allowed": True, "reason": "available"},
                "bid": {"allowed": True, "reason": "available"},
            },
            "last_synced_at": "2026-08-28T12:00:00+00:00",
            "currency": "USD",
            "daily_budget_micro": 100_000_000,
            "daily_budget_usd": 100.0,
            "bid_micro": 20_000_000,
            "bid_usd": 20.0,
            "bid_strategy": "LOWEST_COST_WITH_MAX_BID",
        }

    async def upsert(*args, **kwargs):
        return True

    monkeypatch.setattr(module, "_selected_account", selected)
    monkeypatch.setattr(module, "resolve_financial_management_settings", exact_settings)
    monkeypatch.setattr(module, "_upsert_entity", upsert)
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")

    payload = module.SnapchatManagementProposalInput(
        action="ad_squad.update",
        account_id="account-1",
        target_id="unified-squad",
        parent_id="unified-campaign",
        provider_target_id="provider-squad",
        provider_parent_id="provider-campaign",
        payload={
            "daily_budget_micro": 90_000_000,
            "bid_micro": 15_000_000,
            "bid_strategy": "TARGET_COST",
        },
        reason="اختبار معرف المزود وسجل القيم المالية",
        idempotency_key="provider-id-financial-audit",
    )
    preview = await module.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        payload,
        provider=provider,
    )

    stored = db[module.PROPOSAL_COLLECTION].rows[0]
    assert preview["unified_entity_id"] == "unified-squad"
    assert preview["provider_target_id"] == "provider-squad"
    assert preview["provider_parent_id"] == "provider-campaign"
    assert stored["operation"]["path"] == (
        "/campaigns/provider-campaign/adsquads/provider-squad"
    )
    assert ("ad_squad", "unified-squad") not in provider.reads
    assert ("campaign", "unified-campaign") not in provider.reads
    assert provider.executions == []
    assert (
        preview["field_changes"]["fields"]["daily_budget_micro"]["before_micro"]
        == 100_000_000
    )
    assert preview["field_changes"]["fields"]["daily_budget_micro"]["after_usd"] == 90.0
    assert preview["field_changes"]["fields"]["bid_micro"]["before_usd"] == 20.0
    assert preview["field_changes"]["fields"]["bid_micro"]["after_usd"] == 15.0
    assert preview["field_changes"]["fields"]["bid_strategy"] == {
        "before": "LOWEST_COST_WITH_MAX_BID",
        "after": "TARGET_COST",
    }

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
        db,
        "owner-1",
        "owner-1",
        preview["proposal_id"],
        provider=provider,
    )
    assert completed["status"] == "completed"
    assert completed["provider_entity_id"] == "provider-squad"
    assert provider.executions[0]["path"] == (
        "/campaigns/provider-campaign/adsquads/provider-squad"
    )
    assert "unified-squad" not in str(provider.executions[0])
    assert completed["field_changes"]["provider_reread_verified"] is True
    assert (
        completed["field_changes"]["fields"]["daily_budget_micro"]["after_micro"]
        == 90_000_000
    )
    assert completed["field_changes"]["fields"]["bid_micro"]["after_usd"] == 15.0
    assert completed["verification"]["field_changes_verified"] is True


@pytest.mark.asyncio
async def test_stale_settings_block_financial_preview_before_provider_read_or_write(
    monkeypatch,
):
    db = DB()
    provider = Provider()

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1", "currency": "USD"}

    async def stale_settings(*args, **kwargs):
        return {
            "unified_entity_id": "unified-campaign",
            "provider_entity_id": "provider-campaign",
            "mapping_status": "verified",
            "mapping_verified": True,
            "settings_status": "settings_stale",
            "financial_controls_allowed": False,
            "currency": "USD",
        }

    monkeypatch.setattr(module, "_selected_account", selected)
    monkeypatch.setattr(module, "resolve_financial_management_settings", stale_settings)
    payload = module.SnapchatManagementProposalInput(
        action="campaign.update",
        account_id="account-1",
        target_id="unified-campaign",
        provider_target_id="provider-campaign",
        payload={"daily_budget_micro": 90_000_000},
        reason="اختبار الإغلاق عند تقادم الإعدادات",
        idempotency_key="stale-preview-blocked",
    )

    with pytest.raises(HTTPException) as raised:
        await module.create_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            payload,
            provider=provider,
        )

    assert raised.value.detail == {
        "code": "snapchat_management_financial_settings_unavailable",
        "message": "غير متاح — فشل جلب الإعدادات",
        "settings_status": "settings_stale",
        "financial_controls_allowed": False,
    }
    assert provider.reads == []
    assert provider.executions == []
    assert db[module.PROPOSAL_COLLECTION].rows == []


@pytest.mark.asyncio
async def test_execution_rechecks_settings_freshness_before_provider_write(
    monkeypatch,
):
    db = DB()
    provider = Provider()
    provider.entities[("campaign", "provider-campaign")] = {
        "id": "provider-campaign",
        "ad_account_id": "account-1",
        "status": "PAUSED",
        "daily_budget_micro": 100_000_000,
    }
    gate = {"fresh": True}

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1", "currency": "USD"}

    async def settings(*args, **kwargs):
        return {
            "unified_entity_id": "unified-campaign",
            "provider_entity_id": "provider-campaign",
            "mapping_status": "verified",
            "mapping_verified": True,
            "settings_status": (
                "settings_complete" if gate["fresh"] else "settings_stale"
            ),
            "financial_controls_allowed": gate["fresh"],
            "financial_field_controls": {
                "daily_budget": {
                    "allowed": gate["fresh"],
                    "reason": "available" if gate["fresh"] else "settings_stale",
                },
                "bid": {
                    "allowed": gate["fresh"],
                    "reason": "available" if gate["fresh"] else "settings_stale",
                },
            },
            "currency": "USD",
            "daily_budget_micro": 100_000_000,
            "daily_budget_usd": 100.0,
        }

    monkeypatch.setattr(module, "_selected_account", selected)
    monkeypatch.setattr(module, "resolve_financial_management_settings", settings)
    monkeypatch.setenv(module.MUTATIONS_ENABLED_ENV, "true")
    preview = await module.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        module.SnapchatManagementProposalInput(
            action="campaign.update",
            account_id="account-1",
            target_id="unified-campaign",
            provider_target_id="provider-campaign",
            payload={"daily_budget_micro": 90_000_000},
            reason="اختبار إعادة بوابة الإعدادات قبل التنفيذ",
            idempotency_key="stale-execute-blocked",
        ),
        provider=provider,
    )
    await module.approve_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        preview["proposal_id"],
        module.SnapchatManagementApprovalInput(
            confirm_token=preview["confirm_token"],
            expected_revision=preview["revision"],
        ),
    )
    provider.reads.clear()
    gate["fresh"] = False

    with pytest.raises(HTTPException) as raised:
        await module.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            preview["proposal_id"],
            provider=provider,
        )

    assert raised.value.detail["code"] == (
        "snapchat_management_financial_settings_unavailable"
    )
    assert provider.reads == []
    assert provider.executions == []


@pytest.mark.asyncio
async def test_entity_settings_get_is_database_only_and_never_writes_provider(
    monkeypatch,
):
    db = DB()
    router = APIRouter()
    provider = Provider()
    settings_read_calls = 0

    async def current_user():
        return {"id": "owner-1"}

    def require_owner(user):
        return user

    async def list_settings(
        db,
        user_id,
        entity_type=None,
        unified_entity_id=None,
        *,
        now=None,
        limit=500,
    ):
        nonlocal settings_read_calls
        settings_read_calls += 1
        return {
            "provider": module.SNAPCHAT_PROVIDER_ID,
            "provider_write_calls": 0,
            "items": [
                {
                    "entity_type": entity_type,
                    "unified_entity_id": unified_entity_id,
                    "provider_entity_id": "provider-campaign",
                    "mapping_status": "verified",
                    "mapping_verified": True,
                    "quality": {
                        "settings_status": "complete",
                        "financial_controls_allowed": True,
                    },
                }
            ],
        }

    monkeypatch.setattr(
        settings_module, "list_financial_management_settings", list_settings
    )
    module.attach_snapchat_campaign_management_routes(
        router, db, current_user, require_owner
    )
    endpoint = next(
        route.endpoint
        for route in router.routes
        if route.path == f"/{module.SNAPCHAT_PROVIDER_ID}/management/entity-settings"
        and "GET" in route.methods
    )
    result = await endpoint(
        entity_type="campaign",
        unified_entity_id="unified-campaign",
        limit=500,
        user={"id": "owner-1"},
    )

    assert settings_read_calls == 1
    assert result["items"][0]["provider_entity_id"] == "provider-campaign"
    assert len(provider.executions) == 0
    assert len(provider.reads) == 0
