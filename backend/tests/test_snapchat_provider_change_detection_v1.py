from __future__ import annotations

from copy import deepcopy

import pytest

from integrations_control_center import snapchat_native_entities_sync as module
from integrations_control_center.snapchat_campaign_management import (
    CREATIVE_CREATE_FIELDS,
)
from integrations_control_center.snapchat_native_data_common import (
    SNAPCHAT_ENTITY_COLLECTION,
)


def _matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(row, clause) for clause in expected):
                return False
            continue
        actual = row.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class Cursor:
    def __init__(self, rows):
        self.rows = [deepcopy(row) for row in rows]

    def sort(self, key, direction):
        self.rows.sort(key=lambda row: row.get(key) or "", reverse=direction < 0)
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class Collection:
    def __init__(self, rows):
        self.rows = rows

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None

    def find(self, query, projection=None):
        return Cursor([row for row in self.rows if _matches(row, query)])

    async def update_one(self, query, update, upsert=False):
        row = next((row for row in self.rows if _matches(row, query)), None)
        if row is None and upsert:
            row = deepcopy(query)
            row.update(deepcopy(update.get("$setOnInsert") or {}))
            self.rows.append(row)
        if row is not None:
            row.update(deepcopy(update.get("$set") or {}))
            for key in update.get("$unset") or {}:
                row.pop(key, None)
        return object()


class DB:
    def __init__(self):
        self.rows = {}

    def __getitem__(self, name):
        return Collection(self.rows.setdefault(name, []))


class Context:
    def __init__(self):
        self.db = DB()
        self.user_id = "owner-1"

    def now_iso(self):
        return "2026-08-12T12:00:00+00:00"


ACCOUNT = {
    "ad_account_id": "account-1",
    "mezan_integration_account_id": "integration-account-1",
}


def campaign(**overrides):
    row = {
        "id": "campaign-1",
        "name": "Campaign 1",
        "status": "ACTIVE",
        "daily_budget_micro": 60_000_000,
        "objective": "SALES",
        "objective_v2_properties": {"objective_v2_type": "SALES"},
        "delivery_status": ["ACTIVE"],
        "updated_at": "2026-08-12T11:59:00Z",
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_first_provider_discovery_is_baseline_without_change_event(monkeypatch):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)

    assert await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(),
        detect_provider_changes=True,
    )

    assert recorded == []
    assert len(context.db.rows[SNAPCHAT_ENTITY_COLLECTION]) == 1


@pytest.mark.asyncio
async def test_new_entity_after_explicit_catalog_baseline_is_recorded(monkeypatch):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(deepcopy(kwargs))

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)

    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(),
        detect_provider_changes=True,
        provider_diff_baseline_ready=True,
    )

    assert len(recorded) == 1
    assert recorded[0]["changed_fields"] == ["entity_created"]
    assert recorded[0]["before_snapshot"]["entity_created"] is False
    assert recorded[0]["after_snapshot"]["entity_created"] is True


@pytest.mark.asyncio
async def test_managed_create_is_not_mislabeled_as_direct_provider_create(monkeypatch):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(deepcopy(kwargs))

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)
    context.db.rows.setdefault(module.PROPOSAL_COLLECTION, []).append(
        {
            "user_id": "owner-1",
            "proposal_id": "managed-create",
            "account_id": "account-1",
            "provider_entity_id": "campaign-1",
            "action": "campaign.create",
            "status": "completed",
            "executed_at": "2026-08-12T11:58:00+00:00",
            "operation": {"entity_type": "campaign", "action": "campaign.create"},
        }
    )

    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(),
        detect_provider_changes=True,
        provider_diff_baseline_ready=True,
    )

    assert recorded == []


@pytest.mark.asyncio
async def test_provider_status_and_budget_change_is_recorded_once(monkeypatch):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(deepcopy(kwargs))

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(),
        detect_provider_changes=True,
    )
    changed = campaign(status="PAUSED", daily_budget_micro=40_000_000)
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=changed,
        detect_provider_changes=True,
    )
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=changed,
        detect_provider_changes=True,
    )

    assert len(recorded) == 1
    event = recorded[0]
    assert event["user_id"] == "owner-1"
    assert event["changed_fields"] == ["status", "daily_budget_micro"]
    assert event["before_snapshot"]["status"] == "ACTIVE"
    assert event["after_snapshot"]["status"] == "PAUSED"
    assert event["before_snapshot"]["daily_budget_micro"] == 60_000_000
    assert event["after_snapshot"]["daily_budget_micro"] == 40_000_000
    assert event["provider_updated_at"] == "2026-08-12T11:59:00Z"


@pytest.mark.asyncio
async def test_provider_lifetime_budget_changes_are_recorded(monkeypatch):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(deepcopy(kwargs))

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(lifetime_spend_cap_micro=900_000_000),
        detect_provider_changes=True,
    )
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(lifetime_spend_cap_micro=1_200_000_000),
        detect_provider_changes=True,
    )

    assert len(recorded) == 1
    event = recorded[0]
    assert event["changed_fields"] == ["lifetime_spend_cap_micro"]
    assert event["before_snapshot"]["lifetime_spend_cap_micro"] == 900_000_000
    assert event["after_snapshot"]["lifetime_spend_cap_micro"] == 1_200_000_000


@pytest.mark.asyncio
async def test_new_detector_version_rebaselines_existing_rows_without_false_diff(
    monkeypatch,
):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(deepcopy(kwargs))

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(),
        detect_provider_changes=True,
    )
    # A newly monitored field appears while detector version 2 is establishing
    # its baseline.  It is state discovery, not evidence of a new direct edit.
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(start_time="2026-08-12T13:00:00Z"),
        detect_provider_changes=True,
        provider_diff_baseline_ready=False,
    )

    assert recorded == []


@pytest.mark.asyncio
async def test_explicit_provider_delete_is_recorded_after_baseline(monkeypatch):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(deepcopy(kwargs))

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="ad",
        entity={
            "id": "ad-1",
            "name": "Ad 1",
            "status": "ACTIVE",
            "deleted": False,
            "creative_id": "creative-1",
            "type": "REMOTE_WEBPAGE",
        },
        detect_provider_changes=True,
    )
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="ad",
        entity={
            "id": "ad-1",
            "name": "Ad 1",
            "status": "PAUSED",
            "deleted": True,
            "creative_id": "creative-1",
            "type": "REMOTE_WEBPAGE",
        },
        detect_provider_changes=True,
        provider_diff_baseline_ready=True,
    )

    assert len(recorded) == 1
    assert "deleted" in recorded[0]["changed_fields"]


@pytest.mark.asyncio
async def test_delivery_and_review_drift_does_not_create_decision(monkeypatch):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(),
        detect_provider_changes=True,
    )
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(
            delivery_status=["BUDGET_CONSTRAINED"],
            review_status="APPROVED",
            updated_at="2026-08-12T12:01:00Z",
        ),
        detect_provider_changes=True,
    )

    assert recorded == []


@pytest.mark.asyncio
async def test_exact_in_flight_mezan_proposal_suppresses_external_event(monkeypatch):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(),
        detect_provider_changes=True,
    )
    context.db.rows.setdefault(module.PROPOSAL_COLLECTION, []).append(
        {
            "user_id": "owner-1",
            "proposal_id": "proposal-1",
            "account_id": "account-1",
            "target_id": "campaign-1",
            "status": "executing",
            "execution_started_at": "2026-08-12T11:58:00+00:00",
            "original_snapshot": campaign(),
            "operation": {
                "entity_type": "campaign",
                "changes": {
                    "status": "PAUSED",
                    "daily_budget_micro": 40_000_000,
                },
            },
        }
    )

    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(status="PAUSED", daily_budget_micro=40_000_000),
        detect_provider_changes=True,
    )

    assert recorded == []


@pytest.mark.asyncio
async def test_proposal_with_different_field_diff_does_not_hide_external_change(
    monkeypatch,
):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(),
        detect_provider_changes=True,
    )
    context.db.rows.setdefault(module.PROPOSAL_COLLECTION, []).append(
        {
            "user_id": "owner-1",
            "proposal_id": "proposal-budget-only",
            "account_id": "account-1",
            "target_id": "campaign-1",
            "status": "completed",
            "executed_at": "2026-08-12T11:58:00+00:00",
            "original_snapshot": campaign(),
            "operation": {
                "entity_type": "campaign",
                "changes": {"daily_budget_micro": 40_000_000},
            },
        }
    )

    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(status="PAUSED", daily_budget_micro=40_000_000),
        detect_provider_changes=True,
    )

    assert len(recorded) == 1
    assert recorded[0]["changed_fields"] == ["status", "daily_budget_micro"]


@pytest.mark.asyncio
async def test_management_upsert_default_never_emits_provider_change(monkeypatch):
    context = Context()
    recorded = []

    async def capture(*args, **kwargs):
        recorded.append(kwargs)

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(),
    )
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(status="PAUSED"),
    )

    assert recorded == []


@pytest.mark.asyncio
async def test_failed_ledger_emit_is_retried_from_preserved_snapshots(monkeypatch):
    context = Context()
    attempts = []

    async def capture(*args, **kwargs):
        attempts.append(deepcopy(kwargs))
        if len(attempts) == 1:
            raise RuntimeError("ledger temporarily unavailable")
        return True

    monkeypatch.setattr(module, "_record_provider_observed_change", capture)
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=campaign(),
        detect_provider_changes=True,
    )
    changed = campaign(status="PAUSED")
    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=changed,
        detect_provider_changes=True,
    )
    stored = context.db.rows[SNAPCHAT_ENTITY_COLLECTION][0]
    assert len(stored["pending_provider_changes"]) == 1

    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="campaign",
        entity=changed,
        detect_provider_changes=True,
    )

    assert len(attempts) == 2
    assert attempts[1]["before_snapshot"]["status"] == "ACTIVE"
    assert attempts[1]["after_snapshot"]["status"] == "PAUSED"
    assert "pending_provider_changes" not in stored


def test_change_fingerprint_is_order_independent_and_deterministic():
    before = {"status": "ACTIVE", "daily_budget_micro": 60_000_000}
    after = {"status": "PAUSED", "daily_budget_micro": 40_000_000}
    first = module._change_fingerprint(
        account_id="account-1",
        entity_type="campaign",
        entity_id="campaign-1",
        before_snapshot=before,
        after_snapshot=after,
        changed_fields=["status", "daily_budget_micro"],
    )
    second = module._change_fingerprint(
        account_id="account-1",
        entity_type="campaign",
        entity_id="campaign-1",
        before_snapshot=before,
        after_snapshot=after,
        changed_fields=["daily_budget_micro", "status"],
    )

    assert first == second


def test_targeting_list_order_is_canonicalized_to_avoid_false_change():
    first = module._normalized_control_value("targeting", {"geos": ["SA-01", "SA-02"]})
    second = module._normalized_control_value("targeting", {"geos": ["SA-02", "SA-01"]})

    assert first == second


def test_creative_provider_monitor_covers_every_management_create_control():
    assert CREATIVE_CREATE_FIELDS <= set(module.MONITORED_FIELDS["creative"])


@pytest.mark.asyncio
async def test_creative_controls_are_persisted_top_level_and_in_provider_snapshot():
    context = Context()
    creative = {
        "id": "creative-1",
        **{
            field: ({"mode": field} if field.endswith("properties") else field)
            for field in CREATIVE_CREATE_FIELDS
        },
    }

    await module._upsert_entity(
        context,
        account=ACCOUNT,
        entity_type="creative",
        entity=creative,
        detect_provider_changes=True,
    )

    stored = context.db.rows[SNAPCHAT_ENTITY_COLLECTION][0]
    for field in CREATIVE_CREATE_FIELDS:
        assert field in stored
        assert field in stored["provider_snapshot"]
