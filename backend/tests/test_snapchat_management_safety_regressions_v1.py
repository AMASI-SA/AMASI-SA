from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest
from fastapi import HTTPException

from integrations_control_center import snapchat_campaign_management as management
from integrations_control_center.snapchat_adaptive_decision_ai import (
    judge_adaptive_snapchat_decision,
)


@pytest.fixture(autouse=True)
def fresh_management_settings(monkeypatch):
    async def settings(
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
            "ad_account_id": "account-1",
            "mapping_status": "verified",
            "mapping_verified": True,
            "settings_status": "settings_complete",
            "financial_controls_allowed": True,
            "financial_field_controls": {
                "daily_budget": {"allowed": True, "reason": "available"},
                "bid": {"allowed": True, "reason": "available"},
            },
            "account_currency": "USD",
            "daily_budget_micro": 60_000_000,
            "daily_budget_usd": 60.0,
            "bid_micro": 10_000_000,
            "bid_usd": 10.0,
            "bid_strategy": "TARGET_COST",
        }

    monkeypatch.setattr(management, "resolve_financial_management_settings", settings)


class _Result:
    def __init__(self, matched_count: int = 1):
        self.matched_count = matched_count


def _matches(row: dict, query: dict) -> bool:
    return all(row.get(key) == value for key, value in query.items())


class _Collection:
    def __init__(self):
        self.rows: list[dict] = []

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name")

    async def insert_one(self, row: dict):
        self.rows.append(deepcopy(row))

    async def find_one(self, query: dict, projection=None):
        for row in self.rows:
            if _matches(row, query):
                output = deepcopy(row)
                if projection and projection.get("_id") == 0:
                    output.pop("_id", None)
                return output
        return None

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        for row in self.rows:
            if not _matches(row, query):
                continue
            row.update(deepcopy(update.get("$set") or {}))
            for key, value in (update.get("$inc") or {}).items():
                row[key] = row.get(key, 0) + value
            return _Result(1)
        return _Result(0)


class _DB:
    def __init__(self):
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection())


class _Provider:
    def __init__(self):
        self.entities: dict[tuple[str, str], dict] = {}
        self.executions: list[dict] = []

    async def management_role(self, account, action):
        return {"allowed": True, "role": "general", "reason": None}

    async def read_entity(self, entity_type: str, entity_id: str):
        return deepcopy(self.entities[(entity_type, entity_id)])

    async def execute(self, operation: dict):
        self.executions.append(deepcopy(operation))
        entity_type = str(operation["entity_type"])
        entity_id = str(operation["target_id"])
        current = self.entities[(entity_type, entity_id)]
        for patch in operation["body"]:
            current[patch["path"].lstrip("/")] = deepcopy(patch.get("value"))
        return deepcopy(current)


class _Responses:
    def __init__(self, payload: dict):
        self.payload = payload

    async def create(self, **kwargs):
        return type(
            "Response",
            (),
            {"output_text": json.dumps(self.payload)},
        )()


class _AIClient:
    def __init__(self, payload: dict):
        self.responses = _Responses(payload)


def _budget_increase(
    *,
    idempotency_key: str,
    budget_micro: int = 80_000_000,
    products: list[dict] | None = None,
) -> management.SnapchatManagementProposalInput:
    return management.SnapchatManagementProposalInput(
        action="campaign.update",
        account_id="account-1",
        target_id="campaign-1",
        payload={"daily_budget_micro": budget_micro},
        reason="زيادة مدروسة لاختبار حواجز التنفيذ الآمن",
        idempotency_key=idempotency_key,
        products=products or [],
    )


def _safe_baseline(*, products: list[dict] | None = None) -> dict:
    inventory = []
    if products:
        inventory = [
            {
                "salla_product_id": products[0]["product_id"],
                "product_variant_id": products[0].get("product_variant_id"),
                "variant_found": True,
                "status": "sale",
                "quantity": 20,
                "freshness_status": "fresh",
                "in_decision_product_scope": True,
                "delivery_blocked": False,
            }
        ]
    return {
        "campaign_id": "campaign-1",
        "windows": [{"days": 14}],
        "recent_trend": {"recent_improving": False},
        "inventory": inventory,
        "inventory_delivery_blocked": False,
        "inventory_verification_status": "verified" if products else "not_linked",
    }


def _blocked_inventory_baseline(product: dict) -> dict:
    baseline = _safe_baseline(products=[product])
    baseline["inventory"][0].update(
        {
            "quantity": 0,
            "delivery_blocked": True,
        }
    )
    baseline["inventory_delivery_blocked"] = True
    baseline["inventory_verification_status"] = "verified"
    return baseline


def _prepare_management_dependencies(monkeypatch, baseline):
    async def selected(*args, **kwargs):
        return {
            "ad_account_id": "account-1",
            "timezone": "Asia/Riyadh",
            "currency": "USD",
        }

    async def capture(*args, **kwargs):
        return deepcopy(baseline)

    async def settings(
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
            "ad_account_id": "account-1",
            "mapping_status": "verified",
            "mapping_verified": True,
            "settings_status": "settings_complete",
            "financial_controls_allowed": True,
            "financial_field_controls": {
                "daily_budget": {"allowed": True, "reason": "available"},
                "bid": {"allowed": True, "reason": "available"},
            },
            "account_currency": "USD",
            "daily_budget_micro": 60_000_000,
            "daily_budget_usd": 60.0,
            "bid_micro": 10_000_000,
            "bid_usd": 10.0,
            "bid_strategy": "TARGET_COST",
        }

    monkeypatch.setattr(management, "_selected_account", selected)
    monkeypatch.setattr(management, "_capture_proposal_baseline", capture)
    monkeypatch.setattr(management, "resolve_financial_management_settings", settings)


async def _approve(db: _DB, preview: dict) -> None:
    await management.approve_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        preview["proposal_id"],
        management.SnapchatManagementApprovalInput(
            confirm_token=preview["confirm_token"],
            expected_revision=preview["revision"],
        ),
    )


@pytest.mark.asyncio
async def test_same_idempotency_key_with_different_body_returns_409(monkeypatch):
    db = _DB()
    provider = _Provider()
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "status": "ACTIVE",
        "daily_budget_micro": 60_000_000,
    }
    _prepare_management_dependencies(monkeypatch, _safe_baseline())

    await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        _budget_increase(
            idempotency_key="same-key-different-body",
            budget_micro=70_000_000,
        ),
        provider=provider,
    )

    with pytest.raises(HTTPException) as raised:
        await management.create_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            _budget_increase(
                idempotency_key="same-key-different-body",
                budget_micro=80_000_000,
            ),
            provider=provider,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "snapchat_management_idempotency_conflict"
    assert len(db[management.PROPOSAL_COLLECTION].rows) == 1
    assert provider.executions == []


@pytest.mark.asyncio
async def test_provider_state_conflict_blocks_before_write(monkeypatch):
    db = _DB()
    provider = _Provider()
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "status": "ACTIVE",
        "daily_budget_micro": 60_000_000,
    }
    _prepare_management_dependencies(monkeypatch, _safe_baseline())
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")

    preview = await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        _budget_increase(idempotency_key="provider-state-changed"),
        provider=provider,
    )
    await _approve(db, preview)

    # A direct Snapchat edit after preview must invalidate the old snapshot.
    provider.entities[("campaign", "campaign-1")]["daily_budget_micro"] = 65_000_000

    with pytest.raises(HTTPException) as raised:
        await management.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            preview["proposal_id"],
            provider=provider,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "snapchat_management_provider_state_conflict",
        "message": (
            "تغيرت حالة Snapchat بعد المعاينة؛ أُوقف التنفيذ وأنشئ "
            "معاينة جديدة من الحالة الحالية."
        ),
        "changed_fields": ["daily_budget_micro"],
    }
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "approved"
    assert stored["provider_write_reached"] is False
    assert provider.executions == []


@pytest.mark.asyncio
async def test_failed_product_link_refresh_blocks_delivery_increase(monkeypatch):
    db = _DB()
    provider = _Provider()
    product = {
        "product_id": "710474094",
        "product_variant_id": "comb-black",
        "product_name": "مشط شنب ولحية معدني مخصص بالاسم",
    }
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "status": "ACTIVE",
        "daily_budget_micro": 60_000_000,
    }
    _prepare_management_dependencies(monkeypatch, _safe_baseline(products=[product]))

    async def initial_link_succeeds(*args, **kwargs):
        return True

    monkeypatch.setattr(
        management,
        "_ensure_proposal_product_links",
        initial_link_succeeds,
    )
    preview = await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        _budget_increase(
            idempotency_key="product-link-refresh-fails",
            products=[product],
        ),
        provider=provider,
    )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    stored["product_link_state"] = "deferred"
    await _approve(db, preview)

    async def refresh_link_fails(*args, **kwargs):
        return False

    monkeypatch.setattr(
        management,
        "_ensure_proposal_product_links",
        refresh_link_fails,
    )
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")

    with pytest.raises(HTTPException) as raised:
        await management.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            preview["proposal_id"],
            provider=provider,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == "snapchat_management_product_link_unavailable"
    assert stored["status"] == "approved"
    assert stored["provider_write_reached"] is False
    assert provider.executions == []


@pytest.mark.asyncio
async def test_stale_selected_variant_inventory_blocks_delivery_increase(monkeypatch):
    db = _DB()
    provider = _Provider()
    product = {
        "product_id": "710474094",
        "product_variant_id": "comb-black",
        "product_name": "مشط شنب ولحية معدني مخصص بالاسم",
    }
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "status": "ACTIVE",
        "daily_budget_micro": 60_000_000,
    }
    baseline_calls: list[dict] = []

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1", "timezone": "Asia/Riyadh"}

    async def capture(*args, **kwargs):
        baseline_calls.append(deepcopy(kwargs))
        if len(baseline_calls) == 1:
            return _safe_baseline(products=[product])
        return {
            "campaign_id": "campaign-1",
            "windows": [{"days": 14}],
            "recent_trend": {"recent_improving": False},
            "inventory": [
                {
                    "salla_product_id": "710474094",
                    "product_variant_id": "comb-black",
                    "variant_found": True,
                    "status": "sale",
                    "quantity": 20,
                    "freshness_status": "stale_or_unknown",
                    "in_decision_product_scope": True,
                    "delivery_blocked": True,
                }
            ],
            "inventory_delivery_blocked": True,
            "inventory_verification_status": "incomplete",
        }

    async def link_succeeds(*args, **kwargs):
        return True

    monkeypatch.setattr(management, "_selected_account", selected)
    monkeypatch.setattr(management, "_capture_proposal_baseline", capture)
    monkeypatch.setattr(
        management,
        "_ensure_proposal_product_links",
        link_succeeds,
    )
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")

    preview = await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        _budget_increase(
            idempotency_key="selected-variant-inventory-stale",
            products=[product],
        ),
        provider=provider,
    )
    db[management.PROPOSAL_COLLECTION].rows[0]["product_link_state"] = "confirmed"
    await _approve(db, preview)

    with pytest.raises(HTTPException) as raised:
        await management.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            preview["proposal_id"],
            provider=provider,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail == {
        "code": "snapchat_management_inventory_changed_before_execution",
        "message": (
            "تعذر التحقق من المخزون الحالي أو لم يعد يسمح بزيادة "
            "التسليم؛ لم يصل أي تعديل إلى Snapchat."
        ),
        "inventory_verification_status": "incomplete",
    }
    assert [call["products"][0]["product_variant_id"] for call in baseline_calls] == [
        "comb-black",
        "comb-black",
    ]
    assert provider.executions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "target_id", "parent_id", "budget_field", "old_value", "new_value"),
    [
        (
            "campaign.update",
            "campaign-1",
            None,
            "lifetime_spend_cap_micro",
            600_000_000,
            800_000_000,
        ),
        (
            "ad_squad.update",
            "squad-1",
            "campaign-1",
            "lifetime_budget_micro",
            300_000_000,
            450_000_000,
        ),
    ],
)
async def test_lifetime_budget_or_cap_increase_enters_inventory_gate(
    monkeypatch,
    action,
    target_id,
    parent_id,
    budget_field,
    old_value,
    new_value,
):
    db = _DB()
    provider = _Provider()
    product = {
        "product_id": "710474094",
        "product_variant_id": "comb-black",
        "product_name": "مشط شنب ولحية معدني مخصص بالاسم",
    }
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "status": "ACTIVE",
        "lifetime_spend_cap_micro": (
            old_value if budget_field == "lifetime_spend_cap_micro" else 900_000_000
        ),
    }
    if action == "ad_squad.update":
        provider.entities[("ad_squad", "squad-1")] = {
            "id": "squad-1",
            "campaign_id": "campaign-1",
            "status": "ACTIVE",
            "lifetime_budget_micro": old_value,
        }
    _prepare_management_dependencies(
        monkeypatch,
        _blocked_inventory_baseline(product),
    )

    proposal = management.SnapchatManagementProposalInput(
        action=action,
        account_id="account-1",
        target_id=target_id,
        parent_id=parent_id,
        payload={budget_field: new_value},
        reason="اختبار منع رفع تسليم lifetime عند نفاد المخزون",
        idempotency_key=f"inventory-{budget_field}",
        products=[product],
    )
    with pytest.raises(HTTPException) as raised:
        await management.create_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            proposal,
            provider=provider,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == (
        "snapchat_management_inventory_blocks_delivery_increase"
    )
    assert db[management.PROPOSAL_COLLECTION].rows == []
    assert provider.executions == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "target_id", "parent_id", "payload", "expected_scope"),
    [
        (
            "ad_squad.update",
            "squad-1",
            "campaign-1",
            {"daily_budget_micro": 80_000_000},
            {"ad_squad_id": "squad-1", "ad_id": None},
        ),
        (
            "ad.update",
            "ad-1",
            "squad-1",
            {"status": "ACTIVE"},
            {"ad_squad_id": "squad-1", "ad_id": "ad-1"},
        ),
    ],
)
async def test_squad_or_ad_scope_reaches_execution_inventory_recapture(
    monkeypatch,
    action,
    target_id,
    parent_id,
    payload,
    expected_scope,
):
    db = _DB()
    provider = _Provider()
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "status": "ACTIVE",
    }
    provider.entities[("ad_squad", "squad-1")] = {
        "id": "squad-1",
        "campaign_id": "campaign-1",
        "status": "ACTIVE",
        "daily_budget_micro": 60_000_000,
    }
    provider.entities[("ad", "ad-1")] = {
        "id": "ad-1",
        "ad_squad_id": "squad-1",
        "status": "PAUSED",
    }

    capture_calls: list[dict] = []

    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1", "timezone": "Asia/Riyadh"}

    async def capture(*args, **kwargs):
        capture_calls.append(deepcopy(kwargs))
        baseline = _safe_baseline()
        baseline["inventory_verification_status"] = "verified"
        if len(capture_calls) == 2:
            baseline["inventory_delivery_blocked"] = True
        return baseline

    monkeypatch.setattr(management, "_selected_account", selected)
    monkeypatch.setattr(management, "_capture_proposal_baseline", capture)
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")
    monkeypatch.setenv(management.ACTIVATION_ENABLED_ENV, "true")

    preview = await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        management.SnapchatManagementProposalInput(
            action=action,
            account_id="account-1",
            target_id=target_id,
            parent_id=parent_id,
            payload=payload,
            reason="اختبار وصول نطاق روابط المنتج لإعادة فحص المخزون",
            idempotency_key=f"scope-recapture-{target_id}",
            activation_acknowledged=action == "ad.update",
        ),
        provider=provider,
    )
    await _approve(db, preview)

    with pytest.raises(HTTPException) as raised:
        await management.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            preview["proposal_id"],
            provider=provider,
        )

    assert raised.value.detail["code"] == (
        "snapchat_management_inventory_changed_before_execution"
    )
    assert len(capture_calls) == 2
    for call in capture_calls:
        assert {
            "ad_squad_id": call["ad_squad_id"],
            "ad_id": call["ad_id"],
        } == expected_scope
    assert provider.executions == []


@pytest.mark.asyncio
async def test_provider_conflict_is_checked_after_execution_lock_before_write(
    monkeypatch,
):
    db = _DB()
    provider = _Provider()
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "name": "Original campaign name",
        "status": "ACTIVE",
        "daily_budget_micro": 60_000_000,
    }
    _prepare_management_dependencies(monkeypatch, _safe_baseline())
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")

    preview = await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        management.SnapchatManagementProposalInput(
            action="campaign.update",
            account_id="account-1",
            target_id="campaign-1",
            payload={"name": "New campaign name"},
            reason="اختبار إغلاق نافذة التعارض قبل كتابة المزود",
            idempotency_key="provider-conflict-after-lock",
        ),
        provider=provider,
    )
    await _approve(db, preview)

    events: list[str] = []
    original_read = provider.read_entity

    async def observed_read(entity_type, entity_id):
        result = await original_read(entity_type, entity_id)
        events.append(f"read:{result.get('daily_budget_micro')}")
        return result

    provider.read_entity = observed_read
    proposals = db[management.PROPOSAL_COLLECTION]
    original_update = proposals.update_one

    async def mutate_provider_when_execution_lock_is_acquired(
        query,
        update,
        upsert=False,
    ):
        result = await original_update(query, update, upsert=upsert)
        if (
            getattr(result, "matched_count", 0)
            and (update.get("$set") or {}).get("status") == "executing"
        ):
            events.append("lock")
            provider.entities[("campaign", "campaign-1")][
                "daily_budget_micro"
            ] = 65_000_000
        return result

    proposals.update_one = mutate_provider_when_execution_lock_is_acquired

    with pytest.raises(HTTPException) as raised:
        await management.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            preview["proposal_id"],
            provider=provider,
        )

    assert raised.value.status_code == 409
    assert raised.value.detail["code"] == (
        "snapchat_management_provider_state_conflict"
    )
    lock_index = events.index("lock")
    assert any(
        index > lock_index and event == "read:65000000"
        for index, event in enumerate(events)
    )
    assert provider.executions == []


@pytest.mark.asyncio
async def test_final_provider_read_failure_releases_execution_lock(monkeypatch):
    db = _DB()
    provider = _Provider()
    provider.entities[("campaign", "campaign-1")] = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "name": "Original campaign name",
        "status": "ACTIVE",
        "daily_budget_micro": 60_000_000,
    }
    _prepare_management_dependencies(monkeypatch, _safe_baseline())
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")

    preview = await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        management.SnapchatManagementProposalInput(
            action="campaign.update",
            account_id="account-1",
            target_id="campaign-1",
            payload={"name": "New campaign name"},
            reason="اختبار تحرير قفل التنفيذ عند تعذر القراءة النهائية",
            idempotency_key="provider-final-read-failure",
        ),
        provider=provider,
    )
    await _approve(db, preview)

    reads = 0
    original_read = provider.read_entity

    async def fail_on_final_read(entity_type, entity_id):
        nonlocal reads
        reads += 1
        if reads == 2:
            raise RuntimeError("transient provider read failure")
        return await original_read(entity_type, entity_id)

    provider.read_entity = fail_on_final_read

    with pytest.raises(RuntimeError, match="transient provider read failure"):
        await management.execute_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            preview["proposal_id"],
            provider=provider,
        )

    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "approved"
    assert stored["provider_write_reached"] is False
    assert stored["provider_write_state"] == "not_attempted"
    assert stored["provider_write_uncertain"] is False
    assert stored["execution_started_at"] is None
    assert provider.executions == []


@pytest.mark.asyncio
async def test_adaptive_ai_pins_entity_identity_and_never_marks_proposal_safe():
    model_payload = {
        "recommended_action": "increase_budget",
        "entity_type": "campaign",
        "entity_id": "model-invented-id",
        "confidence": 0.91,
        "reason_ar": "اقتراح النمو مبني على الأدلة المقاسة.",
        "primary_objective": "grow_sales_while_protecting_contribution_profit",
        "expected_outcome": [],
        "evidence_used": ["entity_evidence.metrics"],
        "evidence_not_used": [],
        "uncertainties": [],
        "recent_improvement_treatment": "تمت مراعاة الاتجاه الحديث.",
        "safe_to_prepare_proposal": True,
    }
    client = _AIClient(model_payload)

    result = await judge_adaptive_snapchat_decision(
        {
            "entity_evidence": {
                "entity_type": "ad_squad",
                "entity_id": "squad-from-evidence",
            }
        },
        client_factory=lambda: client,
    )

    judgment = result["judgment"]
    assert judgment["entity_type"] == "ad_squad"
    assert judgment["entity_id"] == "squad-from-evidence"
    assert judgment["model_suggested_safe_to_prepare_proposal"] is True
    assert judgment["safe_to_prepare_proposal"] is False
    assert result["proposal_created"] is False
    assert result["provider_write_reached"] is False
