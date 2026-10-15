from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import APIRouter, BackgroundTasks, HTTPException

from integrations_control_center import snapchat_campaign_management as management
from integrations_control_center import snapchat_decision_ledger as decision_ledger


class DuplicateKeyError(Exception):
    pass


class _Result:
    def __init__(self, matched_count: int = 1):
        self.matched_count = matched_count


class _Cursor:
    def __init__(self, rows):
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, key, direction=None):
        specs = key if isinstance(key, list) else [(key, direction)]
        for field, order in reversed(specs):
            self.rows.sort(
                key=lambda row: str(row.get(field) or ""),
                reverse=bool(order and order < 0),
            )
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length=None):
        rows = self.rows if length is None else self.rows[:length]
        return deepcopy(rows)


def _matches(row: dict, query: dict) -> bool:
    return all(row.get(key) == value for key, value in query.items())


class _Collection:
    def __init__(self, name: str):
        self.name = name
        self.rows: list[dict] = []
        self.on_update = None

    async def create_index(self, *args, **kwargs):
        return kwargs.get("name")

    async def insert_one(self, row: dict):
        if self.name == management.ENTITY_LEASE_COLLECTION and row.get("active"):
            key = tuple(
                row.get(field)
                for field in ("user_id", "account_id", "entity_type", "entity_id")
            )
            for existing in self.rows:
                existing_key = tuple(
                    existing.get(field)
                    for field in (
                        "user_id",
                        "account_id",
                        "entity_type",
                        "entity_id",
                    )
                )
                if existing.get("active") and existing_key == key:
                    raise DuplicateKeyError("active entity lease exists")
        self.rows.append(deepcopy(row))

    async def find_one(self, query: dict, projection=None):
        for row in self.rows:
            if _matches(row, query):
                result = deepcopy(row)
                if projection and projection.get("_id") == 0:
                    result.pop("_id", None)
                return result
        return None

    def find(self, query: dict, projection=None):
        return _Cursor(row for row in self.rows if _matches(row, query))

    async def update_one(self, query: dict, update: dict, upsert: bool = False):
        for row in self.rows:
            if not _matches(row, query):
                continue
            row.update(deepcopy(update.get("$set") or {}))
            if self.on_update:
                await self.on_update(query, update)
            return _Result(1)
        return _Result(0)


class _DB:
    def __init__(self):
        self.collections: dict[str, _Collection] = {}

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.setdefault(name, _Collection(name))


class _Provider:
    def __init__(self, entity: dict, *, outcome: str = "success"):
        self.entity = deepcopy(entity)
        self.outcome = outcome
        self.executions: list[dict] = []
        self.reads = 0

    async def management_role(self, account, action):
        return {"allowed": True, "role": "general"}

    async def read_entity(self, entity_type: str, entity_id: str):
        self.reads += 1
        return deepcopy(self.entity)

    async def execute(self, operation: dict):
        self.executions.append(deepcopy(operation))
        if self.outcome in {"success", "timeout_applied"}:
            for patch in operation["body"]:
                key = patch["path"].lstrip("/")
                if patch["op"] == "remove":
                    self.entity.pop(key, None)
                else:
                    self.entity[key] = deepcopy(patch.get("value"))
        elif self.outcome == "timeout_mixed":
            self.entity["name"] = "Direct provider edit"
        if self.outcome.startswith("timeout"):
            raise TimeoutError("provider response timed out")
        return deepcopy(self.entity)


class _BlockingWriteProvider(_Provider):
    def __init__(self, entity: dict):
        super().__init__(entity)
        self.write_started = asyncio.Event()

    async def execute(self, operation: dict):
        self.executions.append(deepcopy(operation))
        self.write_started.set()
        await asyncio.Event().wait()


class _CreateUncertainProvider:
    def __init__(self):
        self.entity: dict | None = None
        self.executions: list[dict] = []
        self.fail_read = True

    async def management_role(self, account, action):
        return {"allowed": True, "role": "general"}

    async def execute(self, operation: dict):
        self.executions.append(deepcopy(operation))
        entity = deepcopy(operation["body"][operation["plural"]][0])
        entity["id"] = "created-campaign"
        self.entity = entity
        return deepcopy(entity)

    async def read_entity(self, entity_type: str, entity_id: str):
        if self.fail_read:
            raise TimeoutError("verification and immediate reconciliation timed out")
        return deepcopy(self.entity)


class _CreateMismatchProvider:
    def __init__(self):
        self.entity: dict | None = None
        self.executions: list[dict] = []

    async def management_role(self, account, action):
        return {"allowed": True, "role": "general"}

    async def execute(self, operation: dict):
        self.executions.append(deepcopy(operation))
        entity = deepcopy(operation["body"][operation["plural"]][0])
        entity["id"] = "created-campaign-mismatch"
        entity["objective_v2_properties"] = {"objective_v2_type": "AWARENESS"}
        self.entity = entity
        return deepcopy(entity)

    async def read_entity(self, entity_type: str, entity_id: str):
        return deepcopy(self.entity)


def _operation(name: str = "Planned name") -> dict:
    return management.build_snapchat_operation(
        management.SnapchatManagementProposalInput(
            action="campaign.update",
            account_id="account-1",
            target_id="campaign-1",
            payload={"name": name},
            reason="اختبار حواجز التنفيذ والاستعادة",
            idempotency_key="hardening-campaign-name-update",
        )
    )


def _approved_row(proposal_id: str, operation: dict) -> dict:
    original = {
        "id": "campaign-1",
        "ad_account_id": "account-1",
        "name": "Original name",
        "status": "ACTIVE",
        "daily_budget_micro": 60_000_000,
    }
    return {
        "proposal_id": proposal_id,
        "user_id": "owner-1",
        "status": "approved",
        "account_id": "account-1",
        "target_id": "campaign-1",
        "action": "campaign.update",
        "operation": operation,
        "original_snapshot": original,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }


def _prepare(monkeypatch):
    async def selected(*args, **kwargs):
        return {"ad_account_id": "account-1"}

    async def upsert(*args, **kwargs):
        return True

    monkeypatch.setattr(management, "_selected_account", selected)
    monkeypatch.setattr(management, "_upsert_entity", upsert)
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")


def test_none_to_positive_lifetime_budget_and_cap_are_delivery_increases():
    original = {"lifetime_budget_micro": None, "lifetime_spend_cap_micro": None}
    assert management._is_delivery_increase(
        {"changes": {"lifetime_budget_micro": 1}}, original
    )
    assert management._is_delivery_increase(
        {"changes": {"lifetime_spend_cap_micro": 1}}, original
    )


@pytest.mark.asyncio
async def test_entity_lease_is_tenant_scoped_and_cas_released():
    db = _DB()
    operation = _operation()
    owner_one = _approved_row("proposal-1", operation)
    owner_two = {**owner_one, "proposal_id": "proposal-2", "user_id": "owner-2"}

    token = await management._acquire_entity_lease(
        db,
        user_id="owner-1",
        row=owner_one,
        operation=operation,
        operation_kind="execute",
    )
    with pytest.raises(HTTPException) as busy:
        await management._acquire_entity_lease(
            db,
            user_id="owner-1",
            row={**owner_one, "proposal_id": "proposal-3"},
            operation=operation,
            operation_kind="rollback",
        )
    assert busy.value.detail["code"] == "snapchat_management_entity_busy"

    other_token = await management._acquire_entity_lease(
        db,
        user_id="owner-2",
        row=owner_two,
        operation=operation,
        operation_kind="execute",
    )
    await management._release_entity_lease(
        db,
        user_id="owner-1",
        row=owner_one,
        operation=operation,
        lease_token="wrong-token",
    )
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is True
    await management._release_entity_lease(
        db,
        user_id="owner-1",
        row=owner_one,
        operation=operation,
        lease_token=token,
    )
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is False
    assert other_token


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_status", "uncertain", "lease_active"),
    [
        ("timeout_applied", "completed", False, False),
        ("timeout_not_applied", "failed", False, False),
        ("timeout_mixed", "failed", True, True),
    ],
)
async def test_execution_timeout_is_reconciled_without_blind_retry(
    monkeypatch, outcome, expected_status, uncertain, lease_active
):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row(f"proposal-{outcome}", operation)
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _Provider(row["original_snapshot"], outcome=outcome)

    if outcome == "timeout_applied":
        result = await management.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", row["proposal_id"], provider=provider
        )
        assert result["status"] == "completed"
    else:
        with pytest.raises(TimeoutError):
            await management.execute_snapchat_management_proposal(
                db, "owner-1", "owner-1", row["proposal_id"], provider=provider
            )

    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == expected_status
    assert stored["provider_write_uncertain"] is uncertain
    assert stored.get("automatic_retry_allowed") is False
    leases = db[management.ENTITY_LEASE_COLLECTION].rows
    assert leases[0]["active"] is lease_active
    if outcome == "timeout_not_applied":
        assert stored["provider_write_state"] == "confirmed_not_applied"
        assert stored["recovery_action"] == "create_new_preview"


@pytest.mark.asyncio
async def test_post_lock_kill_switch_recheck_blocks_provider_write(monkeypatch):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("proposal-kill-switch", operation)
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _Provider(row["original_snapshot"])

    async def disable_after_lock(query, update):
        if (update.get("$set") or {}).get("status") == "executing":
            monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "false")

    db[management.PROPOSAL_COLLECTION].on_update = disable_after_lock
    with pytest.raises(HTTPException) as blocked:
        await management.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", row["proposal_id"], provider=provider
        )
    assert blocked.value.detail["code"] == "snapchat_campaign_mutations_disabled"
    assert provider.executions == []
    assert db[management.PROPOSAL_COLLECTION].rows[0]["status"] == "approved"
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is False


@pytest.mark.asyncio
async def test_uncertain_execution_has_read_only_recovery_endpoint(monkeypatch):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("proposal-reconcile", operation)
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _Provider(row["original_snapshot"], outcome="timeout_mixed")
    with pytest.raises(TimeoutError):
        await management.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", row["proposal_id"], provider=provider
        )
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is True

    provider.entity["name"] = "Planned name"
    executions_before = len(provider.executions)
    result = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert result["status"] == "completed"
    assert len(provider.executions) == executions_before
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is False


@pytest.mark.asyncio
async def test_uncertain_create_reconciliation_releases_original_pending_lease(
    monkeypatch,
):
    _prepare(monkeypatch)
    db = _DB()
    operation = management.build_snapchat_operation(
        management.SnapchatManagementProposalInput(
            action="campaign.create",
            account_id="account-1",
            payload={
                "name": "Created campaign",
                "start_time": "2026-08-13T00:00:00Z",
                "objective_v2_properties": {"objective_v2_type": "SALES"},
                "daily_budget_micro": 40_000_000,
            },
            reason="اختبار مطابقة إنشاء غير مؤكد",
            idempotency_key="hardening-uncertain-create",
        )
    )
    row = {
        "proposal_id": "proposal-create-uncertain",
        "user_id": "owner-1",
        "status": "approved",
        "account_id": "account-1",
        "action": "campaign.create",
        "operation": operation,
        "original_snapshot": None,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _CreateUncertainProvider()

    with pytest.raises(TimeoutError):
        await management.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", row["proposal_id"], provider=provider
        )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["provider_entity_id"] == "created-campaign"
    lease = db[management.ENTITY_LEASE_COLLECTION].rows[0]
    assert lease["entity_id"] == f"pending:{row['proposal_id']}"
    assert lease["active"] is True

    provider.fail_read = False
    result = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert result["status"] == "completed"
    assert lease["active"] is False


@pytest.mark.asyncio
async def test_rollback_blocks_direct_provider_drift_and_releases_lease(monkeypatch):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("proposal-rollback-drift", operation)
    row.update(
        {
            "status": "completed",
            "provider_entity_id": "campaign-1",
            "verification": {
                "verified": True,
                "provider_snapshot": {
                    **row["original_snapshot"],
                    "name": "Planned name",
                },
            },
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _Provider({**row["original_snapshot"], "name": "Direct edit"})

    with pytest.raises(HTTPException) as blocked:
        await management.rollback_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            row["proposal_id"],
            management.SnapchatManagementRollbackInput(
                confirmation_phrase="تراجع proposal",
                reason="اختبار حماية التعديل المباشر",
            ),
            provider=provider,
        )
    assert blocked.value.detail["code"] == "snapchat_management_rollback_provider_drift"
    assert provider.executions == []
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("outcome", "expected_status", "uncertain", "lease_active"),
    [
        ("timeout_applied", "rolled_back", False, False),
        ("timeout_not_applied", "completed", False, False),
        ("timeout_mixed", "completed", True, True),
    ],
)
async def test_rollback_timeout_uses_readback_and_never_retries_blindly(
    monkeypatch, outcome, expected_status, uncertain, lease_active
):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row(f"rollback-{outcome}", operation)
    verified_after = {**row["original_snapshot"], "name": "Planned name"}
    row.update(
        {
            "status": "completed",
            "provider_entity_id": "campaign-1",
            "verification": {
                "verified": True,
                "provider_snapshot": verified_after,
            },
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _Provider(verified_after, outcome=outcome)

    if outcome == "timeout_applied":
        result = await management.rollback_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            row["proposal_id"],
            management.SnapchatManagementRollbackInput(
                confirmation_phrase=f"تراجع {row['proposal_id'][:8]}",
                reason="اختبار مطابقة نتيجة التراجع بعد timeout",
            ),
            provider=provider,
        )
        assert result["status"] == "rolled_back"
    else:
        with pytest.raises(TimeoutError):
            await management.rollback_snapchat_management_proposal(
                db,
                "owner-1",
                "owner-1",
                row["proposal_id"],
                management.SnapchatManagementRollbackInput(
                    confirmation_phrase=f"تراجع {row['proposal_id'][:8]}",
                    reason="اختبار منع إعادة التراجع بشكل أعمى",
                ),
                provider=provider,
            )

    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == expected_status
    assert stored["rollback_write_uncertain"] is uncertain
    assert stored.get("rollback_automatic_retry_allowed") is not True
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is lease_active


@pytest.mark.asyncio
async def test_expired_execute_request_fails_synchronously_and_is_documented():
    db = _DB()
    proposal_id = "expired-proposal"
    db[management.PROPOSAL_COLLECTION].rows.append(
        {
            "proposal_id": proposal_id,
            "user_id": "owner-1",
            "status": "approved",
            "expires_at": (
                datetime.now(timezone.utc) - timedelta(minutes=1)
            ).isoformat(),
        }
    )
    router = APIRouter()
    management.attach_snapchat_campaign_management_routes(
        router, db, lambda: {"id": "owner-1"}, lambda user: user
    )
    route = next(
        item
        for item in router.routes
        if item.path.endswith("/management/proposals/{proposal_id}/execute")
    )
    with pytest.raises(HTTPException) as expired:
        await route.endpoint(
            proposal_id=proposal_id,
            background_tasks=BackgroundTasks(),
            user={"id": "owner-1"},
        )
    assert expired.value.detail["code"] == "snapchat_management_proposal_expired"
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "failed"
    assert stored["failure"]["code"] == "snapchat_management_proposal_expired"
    assert stored["recovery_action"] == "create_new_preview"


@pytest.mark.asyncio
async def test_create_objective_mismatch_cannot_be_marked_completed(monkeypatch):
    _prepare(monkeypatch)
    db = _DB()
    operation = management.build_snapchat_operation(
        management.SnapchatManagementProposalInput(
            action="campaign.create",
            account_id="account-1",
            payload={
                "name": "Canonical create verification",
                "start_time": "2026-08-13T00:00:00Z",
                "objective_v2_properties": {"objective_v2_type": "SALES"},
                "daily_budget_micro": 40_000_000,
            },
            reason="اختبار منع اعتماد هدف حملة مختلف",
            idempotency_key="canonical-create-objective",
        )
    )
    row = {
        "proposal_id": "proposal-create-objective-mismatch",
        "user_id": "owner-1",
        "status": "approved",
        "account_id": "account-1",
        "action": "campaign.create",
        "operation": operation,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
    }
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _CreateMismatchProvider()

    with pytest.raises(HTTPException) as mismatch:
        await management.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", row["proposal_id"], provider=provider
        )
    assert mismatch.value.detail["code"] == "snapchat_management_verification_failed"
    assert "objective_v2_properties" in mismatch.value.detail["mismatched_fields"]
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "failed"
    assert stored["provider_write_uncertain"] is True
    assert stored.get("verification") is None
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is True


@pytest.mark.asyncio
async def test_verified_rollback_survives_local_cache_refresh_failure(monkeypatch):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("rollback-cache-failure", operation)
    verified_after = {**row["original_snapshot"], "name": "Planned name"}
    row.update(
        {
            "status": "completed",
            "provider_entity_id": "campaign-1",
            "verification": {
                "verified": True,
                "provider_snapshot": verified_after,
            },
            "baseline": {"campaign_id": "campaign-1"},
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _Provider(verified_after)

    async def fail_cache(*args, **kwargs):
        raise RuntimeError("local entity cache unavailable")

    async def rollback_baseline(*args, **kwargs):
        return {"campaign_id": "campaign-1", "captured_for": "rollback"}

    monkeypatch.setattr(management, "_upsert_entity", fail_cache)
    monkeypatch.setattr(management, "_capture_proposal_baseline", rollback_baseline)
    result = await management.rollback_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        row["proposal_id"],
        management.SnapchatManagementRollbackInput(
            confirmation_phrase=f"تراجع {row['proposal_id'][:8]}",
            reason="التراجع الموثق مستقل عن تحديث الكاش المحلي",
        ),
        provider=provider,
    )

    assert result["status"] == "rolled_back"
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["rollback"]["status"] == "verified"
    assert stored["rollback_baseline"]["captured_for"] == "rollback"
    assert stored["rollback_write_uncertain"] is False
    events = [row["event"] for row in db[management.AUDIT_COLLECTION].rows]
    assert "rollback_entity_cache_refresh_deferred" in events
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is False


@pytest.mark.asyncio
async def test_execute_then_rollback_records_exactly_two_idempotent_decisions(
    monkeypatch,
):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("rollback-ledger-integration", operation)
    row["reason"] = "تعديل أمامي موثق قبل اختبار قرار التراجع"
    row["baseline"] = {"campaign_id": "campaign-1"}
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _Provider(row["original_snapshot"])

    async def rollback_baseline(*args, **kwargs):
        return {
            "campaign_id": "campaign-1",
            "windows": [{"days": 1, "campaign": {"sales_sar": 70}}],
        }

    monkeypatch.setattr(management, "_capture_proposal_baseline", rollback_baseline)

    executed = await management.execute_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert executed["status"] == "completed"

    rolled_back = await management.rollback_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        row["proposal_id"],
        management.SnapchatManagementRollbackInput(
            confirmation_phrase=f"تراجع {row['proposal_id'][:8]}",
            reason="التراجع لأن نتيجة التعديل الأمامي لم تحقق الهدف",
        ),
        provider=provider,
    )
    assert rolled_back["status"] == "rolled_back"

    ledger_rows = db[decision_ledger.DECISION_LEDGER_COLLECTION].rows
    changes = [entry for entry in ledger_rows if entry.get("entry_type") == "change"]
    assert len(changes) == 2
    assert sorted(entry["action"] for entry in changes) == [
        "campaign.update",
        "campaign.update.rollback",
    ]
    rollback_entry = next(
        entry for entry in changes if entry["action"] == "campaign.update.rollback"
    )
    assert rollback_entry["before"]["name"] == "Planned name"
    assert rollback_entry["after"]["name"] == "Original name"
    assert rollback_entry["reason"] == (
        "التراجع لأن نتيجة التعديل الأمامي لم تحقق الهدف"
    )

    reconciled = await decision_ledger.reconcile_snapchat_management_decisions(
        db, "owner-1"
    )
    assert reconciled["inserted"] == 0
    changes_after_reconcile = [
        entry
        for entry in db[decision_ledger.DECISION_LEDGER_COLLECTION].rows
        if entry.get("entry_type") == "change"
    ]
    assert len(changes_after_reconcile) == 2


@pytest.mark.asyncio
async def test_cancelled_execute_preserves_fence_and_enters_read_only_recovery(
    monkeypatch,
):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("cancelled-provider-execute", operation)
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _BlockingWriteProvider(row["original_snapshot"])

    task = asyncio.create_task(
        management.execute_snapchat_management_proposal(
            db, "owner-1", "owner-1", row["proposal_id"], provider=provider
        )
    )
    await provider.write_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "failed"
    assert stored["provider_write_state"] == "unknown_needs_reconciliation"
    assert stored["provider_write_uncertain"] is True
    lease = db[management.ENTITY_LEASE_COLLECTION].rows[0]
    assert lease["active"] is True

    reconciled = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert reconciled["status"] == "failed"
    assert db[management.PROPOSAL_COLLECTION].rows[0]["provider_write_state"] == (
        "confirmed_not_applied"
    )
    assert lease["active"] is False


@pytest.mark.asyncio
async def test_cancelled_rollback_write_preserves_fence_for_read_only_recovery(
    monkeypatch,
):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("cancelled-provider-rollback", operation)
    verified_after = {**row["original_snapshot"], "name": "Planned name"}
    row.update(
        {
            "status": "completed",
            "provider_entity_id": "campaign-1",
            "verification": {"verified": True, "provider_snapshot": verified_after},
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _BlockingWriteProvider(verified_after)

    async def rollback_baseline(*args, **kwargs):
        return {"campaign_id": "campaign-1"}

    monkeypatch.setattr(management, "_capture_proposal_baseline", rollback_baseline)
    task = asyncio.create_task(
        management.rollback_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            row["proposal_id"],
            management.SnapchatManagementRollbackInput(
                confirmation_phrase=f"تراجع {row['proposal_id'][:8]}",
                reason="اختبار إلغاء العامل أثناء كتابة التراجع",
            ),
            provider=provider,
        )
    )
    await provider.write_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "rolling_back"
    assert stored["rollback_write_state"] == "unknown_needs_reconciliation"
    assert stored["rollback_write_uncertain"] is True
    lease = db[management.ENTITY_LEASE_COLLECTION].rows[0]
    assert lease["active"] is True

    reconciled = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert reconciled["status"] == "completed"
    assert db[management.PROPOSAL_COLLECTION].rows[0]["rollback_write_state"] == (
        "confirmed_not_applied"
    )
    assert lease["active"] is False


@pytest.mark.asyncio
async def test_cancelled_rollback_baseline_restores_known_no_write_state(monkeypatch):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("cancelled-rollback-baseline", operation)
    verified_after = {**row["original_snapshot"], "name": "Planned name"}
    row.update(
        {
            "status": "completed",
            "provider_entity_id": "campaign-1",
            "verification": {"verified": True, "provider_snapshot": verified_after},
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _Provider(verified_after)
    baseline_started = asyncio.Event()

    async def blocking_baseline(*args, **kwargs):
        baseline_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(management, "_capture_proposal_baseline", blocking_baseline)
    task = asyncio.create_task(
        management.rollback_snapchat_management_proposal(
            db,
            "owner-1",
            "owner-1",
            row["proposal_id"],
            management.SnapchatManagementRollbackInput(
                confirmation_phrase=f"تراجع {row['proposal_id'][:8]}",
                reason="اختبار إلغاء العامل قبل كتابة التراجع",
            ),
            provider=provider,
        )
    )
    await baseline_started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "completed"
    assert stored["rollback_write_state"] == "not_attempted"
    assert stored["rollback_write_uncertain"] is False
    assert provider.executions == []
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is False


@pytest.mark.asyncio
async def test_terminal_retry_adopts_deferred_products_without_provider_write(
    monkeypatch,
):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("terminal-product-adoption", operation)
    verified = {**row["original_snapshot"], "name": "Planned name"}
    row.update(
        {
            "status": "completed",
            "provider_entity_id": "campaign-1",
            "provider_write_state": "confirmed",
            "provider_write_uncertain": False,
            "verification": {"verified": True, "provider_snapshot": verified},
            "products": [{"product_id": "product-1", "sku": "COMB-1"}],
            "product_link_state": "adoption_deferred",
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    provider = _Provider(verified)
    adoption_calls = 0

    async def adopt_locally(db_arg, user_id, actor_id, proposal_row):
        nonlocal adoption_calls
        adoption_calls += 1
        await db_arg[management.PROPOSAL_COLLECTION].update_one(
            {
                "user_id": user_id,
                "proposal_id": proposal_row["proposal_id"],
            },
            {"$set": {"product_link_state": "adopted"}},
        )

    monkeypatch.setattr(
        management, "_adopt_proposal_products_deferred_safe", adopt_locally
    )

    first = await management.execute_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    second = await management.execute_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert first["product_link_state"] == "adopted"
    assert second["product_link_state"] == "adopted"
    assert adoption_calls == 1
    assert provider.reads == 0
    assert provider.executions == []

    db[management.PROPOSAL_COLLECTION].rows[0][
        "product_link_state"
    ] = "adoption_deferred"
    reconciled = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert reconciled["product_link_state"] == "adopted"
    assert adoption_calls == 2
    assert provider.reads == 0
    assert provider.executions == []


@pytest.mark.asyncio
async def test_expired_approved_no_write_lease_is_safely_released():
    db = _DB()
    operation = _operation()
    row = _approved_row("orphan-before-proposal-claim", operation)
    row.update(
        {
            "provider_write_state": "not_attempted",
            "provider_write_reached": False,
            "provider_write_uncertain": False,
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    await management._acquire_entity_lease(
        db,
        user_id="owner-1",
        row=row,
        operation=operation,
        operation_kind="execute",
    )
    lease = db[management.ENTITY_LEASE_COLLECTION].rows[0]
    lease["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()

    result = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"]
    )
    assert result["status"] == "approved"
    assert lease["active"] is False
    replacement = await management._acquire_entity_lease(
        db,
        user_id="owner-1",
        row={**row, "proposal_id": "replacement-proposal"},
        operation=operation,
        operation_kind="execute",
    )
    assert replacement


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "expected_status", "expected_state"),
    [
        ("Planned name", "completed", "confirmed"),
        ("Original name", "failed", "confirmed_not_applied"),
    ],
)
async def test_expired_executing_orphan_uses_read_only_reconciliation(
    monkeypatch, provider_name, expected_status, expected_state
):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("orphan-executing-attempt", operation)
    row.update(
        {
            "status": "executing",
            "provider_write_state": "attempting",
            "provider_write_reached": False,
            "provider_write_uncertain": False,
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    await management._acquire_entity_lease(
        db,
        user_id="owner-1",
        row=row,
        operation=operation,
        operation_kind="execute",
    )
    lease = db[management.ENTITY_LEASE_COLLECTION].rows[0]
    lease["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    provider = _Provider({**row["original_snapshot"], "name": provider_name})

    result = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert result["status"] == expected_status
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["provider_write_state"] == expected_state
    assert stored["provider_write_uncertain"] is False
    assert provider.executions == []
    assert lease["active"] is False


@pytest.mark.asyncio
async def test_unexpired_attempting_orphan_is_not_reconciled(monkeypatch):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("orphan-still-owned", operation)
    row.update(
        {
            "status": "executing",
            "provider_write_state": "attempting",
            "provider_write_uncertain": False,
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    await management._acquire_entity_lease(
        db,
        user_id="owner-1",
        row=row,
        operation=operation,
        operation_kind="execute",
    )
    provider = _Provider({**row["original_snapshot"], "name": "Planned name"})

    result = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert result["status"] == "executing"
    assert provider.reads == 0
    assert db[management.ENTITY_LEASE_COLLECTION].rows[0]["active"] is True


@pytest.mark.asyncio
async def test_expired_attempting_create_without_entity_id_stays_fenced(monkeypatch):
    _prepare(monkeypatch)
    db = _DB()
    operation = management.build_snapchat_operation(
        management.SnapchatManagementProposalInput(
            action="campaign.create",
            account_id="account-1",
            payload={
                "name": "Unknown timed out create",
                "start_time": "2026-08-13T00:00:00Z",
                "objective_v2_properties": {"objective_v2_type": "SALES"},
            },
            reason="لا نخمن هوية إنشاء انقطع عامله",
            idempotency_key="orphan-create-no-provider-id",
        )
    )
    row = {
        "proposal_id": "orphan-create-no-id",
        "user_id": "owner-1",
        "status": "executing",
        "account_id": "account-1",
        "action": "campaign.create",
        "operation": operation,
        "provider_write_state": "attempting",
        "provider_write_uncertain": False,
    }
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    await management._acquire_entity_lease(
        db,
        user_id="owner-1",
        row=row,
        operation=operation,
        operation_kind="execute",
    )
    lease = db[management.ENTITY_LEASE_COLLECTION].rows[0]
    lease["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()

    with pytest.raises(HTTPException) as unknown:
        await management.reconcile_snapchat_management_proposal(
            db, "owner-1", "owner-1", row["proposal_id"]
        )
    assert unknown.value.detail["code"] == (
        "snapchat_management_reconciliation_entity_unknown"
    )
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["status"] == "failed"
    assert stored["provider_write_uncertain"] is True
    assert lease["active"] is True


@pytest.mark.asyncio
async def test_expired_rollback_no_write_orphan_restores_previous_status():
    db = _DB()
    operation = _operation()
    row = _approved_row("rollback-orphan-no-write", operation)
    row.update(
        {
            "status": "rolling_back",
            "rollback_from_status": "completed",
            "rollback_write_state": "not_attempted",
            "rollback_write_uncertain": False,
            "provider_entity_id": "campaign-1",
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    await management._acquire_entity_lease(
        db,
        user_id="owner-1",
        row=row,
        operation=operation,
        operation_kind="rollback",
    )
    lease = db[management.ENTITY_LEASE_COLLECTION].rows[0]
    lease["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()

    result = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"]
    )
    assert result["status"] == "completed"
    assert lease["active"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("provider_name", "expected_status", "expected_state"),
    [
        ("Original name", "rolled_back", "confirmed"),
        ("Planned name", "completed", "confirmed_not_applied"),
    ],
)
async def test_expired_rollback_attempt_orphan_uses_read_only_reconciliation(
    monkeypatch, provider_name, expected_status, expected_state
):
    _prepare(monkeypatch)
    db = _DB()
    operation = _operation()
    row = _approved_row("rollback-orphan-attempt", operation)
    verified_after = {**row["original_snapshot"], "name": "Planned name"}
    row.update(
        {
            "status": "rolling_back",
            "rollback_from_status": "completed",
            "rollback_write_state": "attempting",
            "rollback_write_uncertain": False,
            "provider_entity_id": "campaign-1",
            "rollback_before_snapshot": verified_after,
            "rollback_requested_reason": "استعادة الاسم السابق",
            "verification": {
                "verified": True,
                "provider_snapshot": verified_after,
            },
        }
    )
    db[management.PROPOSAL_COLLECTION].rows.append(deepcopy(row))
    await management._acquire_entity_lease(
        db,
        user_id="owner-1",
        row=row,
        operation=operation,
        operation_kind="rollback",
    )
    lease = db[management.ENTITY_LEASE_COLLECTION].rows[0]
    lease["expires_at"] = (
        datetime.now(timezone.utc) - timedelta(seconds=1)
    ).isoformat()
    provider = _Provider({**row["original_snapshot"], "name": provider_name})

    result = await management.reconcile_snapchat_management_proposal(
        db, "owner-1", "owner-1", row["proposal_id"], provider=provider
    )
    assert result["status"] == expected_status
    stored = db[management.PROPOSAL_COLLECTION].rows[0]
    assert stored["rollback_write_state"] == expected_state
    assert stored["rollback_write_uncertain"] is False
    assert provider.executions == []
    assert lease["active"] is False
