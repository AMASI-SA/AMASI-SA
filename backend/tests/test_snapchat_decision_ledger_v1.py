from __future__ import annotations

from copy import deepcopy

import pytest

from integrations_control_center import snapchat_decision_ledger as ledger


def _matches(row, query):
    for key, expected in query.items():
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

    def sort(self, key, direction=None):
        specs = key if isinstance(key, list) else [(key, direction)]
        for field, order in reversed(specs):
            self.rows.sort(
                key=lambda row: str(row.get(field) or ""),
                reverse=order < 0,
            )
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length=None):
        return deepcopy(self.rows if length is None else self.rows[:length])


class Collection:
    def __init__(self):
        self.rows = []
        self.indexes = []

    async def create_index(self, keys, **kwargs):
        self.indexes.append((deepcopy(keys), deepcopy(kwargs)))
        return kwargs.get("name")

    async def insert_one(self, row):
        if any(
            existing.get("user_id") == row.get("user_id")
            and existing.get("source_event_key") == row.get("source_event_key")
            for existing in self.rows
        ):
            raise RuntimeError("duplicate source event")
        self.rows.append(deepcopy(row))
        return object()

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if _matches(row, query):
                output = deepcopy(row)
                if projection and projection.get("_id") == 0:
                    output.pop("_id", None)
                return output
        return None

    def find(self, query, projection=None):
        return Cursor(row for row in self.rows if _matches(row, query))


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())

    def __getattr__(self, name):
        return self[name]


def proposal(proposal_id="proposal-1", **overrides):
    row = {
        "user_id": "owner-1",
        "proposal_id": proposal_id,
        "account_id": "account-1",
        "account_name": "AMASI USD",
        "target_id": "campaign-1",
        "provider_entity_id": "campaign-1",
        "action": "campaign.update",
        "status": "completed",
        "reason": "خفض الميزانية لأن الربحية تراجعت",
        "actor_id": "owner-1",
        "executed_by": "owner-1",
        "created_at": "2026-08-12T08:00:00+00:00",
        "executed_at": "2026-08-12T08:05:00+00:00",
        "original_snapshot": {
            "id": "campaign-1",
            "status": "ACTIVE",
            "daily_budget_micro": 60_000_000,
        },
        "operation": {
            "entity_type": "campaign",
            "changes": {
                "status": "PAUSED",
                "daily_budget_micro": 40_000_000,
            },
        },
        "verification": {
            "verified": True,
            "verified_at": "2026-08-12T08:05:00+00:00",
            "provider_snapshot": {
                "id": "campaign-1",
                "status": "PAUSED",
                "daily_budget_micro": 40_000_000,
                "access_token": "must-not-be-stored",
            },
        },
        "provider_write_reached": True,
        "provider_write_state": "confirmed",
        "provider_write_uncertain": False,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_reconcile_backfills_truthful_management_facts_and_is_idempotent():
    db = DB()
    db[ledger.MANAGEMENT_PROPOSAL_COLLECTION].rows.extend(
        [
            proposal(),
            proposal("preview-only", status="previewed", reason="لن يدخل بعد"),
        ]
    )

    first = await ledger.reconcile_snapchat_management_decisions(db, "owner-1")
    second = await ledger.reconcile_snapchat_management_decisions(db, "owner-1")
    detail = await ledger.get_ad_decision(db, "owner-1", "proposal-1")

    assert first == {
        "provider": "snapchat_ads",
        "scanned": 2,
        "inserted": 1,
        "unchanged": 0,
        "limit": 1000,
    }
    assert second["inserted"] == 0
    assert second["unchanged"] == 1
    assert len(db[ledger.DECISION_LEDGER_COLLECTION].rows) == 1
    assert detail["reason"] == "خفض الميزانية لأن الربحية تراجعت"
    assert detail["before"]["daily_budget_micro"] == 60_000_000
    assert detail["after"]["daily_budget_micro"] == 40_000_000
    assert detail["planned_changes"]["status"] == "PAUSED"
    assert detail["execution_status"] == "completed"
    assert detail["outcome_status"] == "not_evaluated"
    assert detail["actor_id"] == "owner-1"
    assert detail["effective_at"] == "2026-08-12T08:05:00+00:00"
    assert "access_token" not in detail["after"]
    assert {item["field"] for item in detail["field_diffs"]} == {
        "daily_budget_micro",
        "status",
    }


@pytest.mark.asyncio
async def test_failed_attempt_does_not_claim_expected_values_became_after_values():
    db = DB()
    failed = proposal(
        "failed-1",
        status="failed",
        failed_at="2026-08-12T09:02:00+00:00",
        verification=None,
        failure={"code": "provider_rejected"},
        provider_write_reached=False,
        provider_write_state="unknown_after_error",
        provider_write_uncertain=True,
    )

    detail = await ledger.record_management_decision(db, "owner-1", failed)

    assert detail["execution_status"] == "failed"
    assert detail["business_outcome"] == "not_evaluated"
    assert detail["after"] is None
    assert detail["field_diffs"] == []
    assert detail["planned_changes"]["daily_budget_micro"] == 40_000_000
    assert detail["evidence"]["failure"]["code"] == "provider_rejected"
    assert detail["evidence"]["provider_write_uncertain"] is True


@pytest.mark.asyncio
async def test_provider_observation_has_unknown_actor_truthful_reason_and_stable_dedupe():
    db = DB()
    kwargs = {
        "account_id": "account-1",
        "entity_type": "campaign",
        "entity_id": "campaign-direct",
        "before_snapshot": {
            "id": "campaign-direct",
            "status": "ACTIVE",
            "daily_budget_micro": 90,
        },
        "after_snapshot": {
            "id": "campaign-direct",
            "status": "PAUSED",
            "daily_budget_micro": 60,
        },
        "observed_at": "2026-08-12T10:00:00+00:00",
        "provider_updated_at": "2026-08-12T09:59:00+00:00",
        "changed_fields": ["status", "daily_budget_micro"],
        "matched_proposal_id": None,
    }
    first = await ledger.record_provider_observed_decision(
        db,
        "owner-1",
        **kwargs,
    )
    # A later poll of the same provider occurrence remains idempotent because
    # provider_updated_at takes precedence over the local observation time.
    kwargs["observed_at"] = "2026-08-12T10:05:00+00:00"
    second = await ledger.record_provider_observed_decision(
        db,
        "owner-1",
        **kwargs,
    )

    assert first["decision_id"] == second["decision_id"]
    assert first["effective_at"] == "2026-08-12T09:59:00+00:00"
    assert len(db[ledger.DECISION_LEDGER_COLLECTION].rows) == 1
    assert first["reason"] == ledger.PROVIDER_OBSERVED_REASON
    assert first["actor_kind"] == "unknown_external"
    assert first["source"] == "snapchat_provider_observed"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("before", "after", "changed_fields", "expected_action"),
    [
        (
            {"id": "campaign-new", "entity_created": False},
            {"id": "campaign-new", "entity_created": True},
            ["entity_created"],
            "provider.observed_create",
        ),
        (
            {"id": "campaign-deleted", "deleted": False},
            {"id": "campaign-deleted", "deleted": True},
            ["deleted"],
            "provider.observed_delete",
        ),
    ],
)
async def test_provider_observation_distinguishes_create_and_delete(
    before,
    after,
    changed_fields,
    expected_action,
):
    db = DB()

    detail = await ledger.record_provider_observed_decision(
        db,
        "owner-1",
        account_id="account-1",
        entity_type="campaign",
        entity_id=after["id"],
        before_snapshot=before,
        after_snapshot=after,
        observed_at="2026-08-12T10:00:00+00:00",
        changed_fields=changed_fields,
    )

    assert detail["action"] == expected_action
    assert detail["evidence"]["detection_coverage"][
        "absence_from_catalog_is_not_assumed_deleted"
    ] is True


@pytest.mark.asyncio
async def test_repeated_direct_transition_on_new_date_is_not_deduplicated():
    db = DB()
    active = {"id": "campaign-1", "status": "ACTIVE"}
    paused = {"id": "campaign-1", "status": "PAUSED"}
    transitions = (
        (active, paused, "2026-08-01T10:00:00+00:00"),
        (paused, active, "2026-08-02T10:00:00+00:00"),
        (active, paused, "2026-08-03T10:00:00+00:00"),
    )
    for before, after, observed_at in transitions:
        await ledger.record_provider_observed_decision(
            db,
            "owner-1",
            account_id="account-1",
            entity_type="campaign",
            entity_id="campaign-1",
            before_snapshot=before,
            after_snapshot=after,
            observed_at=observed_at,
            changed_fields=["status"],
        )

    # Retrying the third occurrence with its preserved timestamp remains
    # idempotent, while the same ACTIVE -> PAUSED transition on Aug 1 and Aug 3
    # is retained as two real provider events.
    await ledger.record_provider_observed_decision(
        db,
        "owner-1",
        account_id="account-1",
        entity_type="campaign",
        entity_id="campaign-1",
        before_snapshot=active,
        after_snapshot=paused,
        observed_at="2026-08-03T10:00:00+00:00",
        changed_fields=["status"],
    )

    changes = [
        row
        for row in db[ledger.DECISION_LEDGER_COLLECTION].rows
        if row["entry_type"] == "change"
    ]
    assert len(changes) == 3
    assert [row["effective_at"][:10] for row in changes] == [
        "2026-08-01",
        "2026-08-02",
        "2026-08-03",
    ]
    assert len({row["source_event_key"] for row in changes}) == 3


@pytest.mark.asyncio
async def test_latest_five_per_account_and_pagination_are_decision_based():
    db = DB()
    for index in range(7):
        await ledger.record_management_decision(
            db,
            "owner-1",
            proposal(
                f"proposal-{index}",
                target_id=f"campaign-{index}",
                provider_entity_id=f"campaign-{index}",
                created_at=f"2026-08-12T0{index}:00:00+00:00",
                executed_at=f"2026-08-12T0{index}:05:00+00:00",
            ),
        )
    await ledger.record_management_decision(
        db,
        "owner-1",
        proposal(
            "other-account",
            account_id="account-2",
            account_name="AMASI SAR",
        ),
    )

    summaries = await ledger.list_account_decision_summaries(db, "owner-1")
    first_page = await ledger.list_ad_decisions(
        db,
        "owner-1",
        "account-1",
        page=1,
        limit=3,
    )
    third_page = await ledger.list_ad_decisions(
        db,
        "owner-1",
        "account-1",
        page=3,
        limit=3,
    )

    account = next(
        row for row in summaries["accounts"] if row["account_id"] == "account-1"
    )
    assert len(account["decisions"]) == 5
    assert account["decisions"][0]["decision_id"] == "proposal-6"
    assert first_page["total"] == 7
    assert first_page["pages"] == 3
    assert len(first_page["items"]) == 3
    assert [row["decision_id"] for row in third_page["items"]] == ["proposal-0"]


@pytest.mark.asyncio
async def test_all_reads_and_child_entries_are_strictly_tenant_scoped():
    db = DB()
    await ledger.record_management_decision(db, "owner-1", proposal())

    assert await ledger.get_ad_decision(db, "owner-2", "proposal-1") is None
    assert (
        await ledger.list_ad_decisions(
            db,
            "owner-2",
            "account-1",
            page=1,
            limit=20,
        )
    )["total"] == 0
    with pytest.raises(ValueError, match="decision not found"):
        await ledger.add_decision_annotation(
            db,
            "owner-2",
            "proposal-1",
            "محاولة عابرة للمستأجر",
        )
    with pytest.raises(ValueError, match="decision not found"):
        await ledger.append_decision_evaluation(
            db,
            "owner-2",
            "proposal-1",
            {"outcome_status": "successful"},
        )


@pytest.mark.asyncio
async def test_content_hash_covers_the_immutable_stored_entry():
    db = DB()
    await ledger.record_management_decision(db, "owner-1", proposal())
    stored = db[ledger.DECISION_LEDGER_COLLECTION].rows[0]

    assert len(stored["content_hash"]) == 64
    assert stored["content_hash"] == ledger._entry_content_hash(stored)
    changed = deepcopy(stored)
    changed["reason"] = "tampered"
    assert ledger._entry_content_hash(changed) != stored["content_hash"]


@pytest.mark.asyncio
async def test_evaluations_are_appended_and_previous_evidence_is_immutable():
    db = DB()
    await ledger.record_management_decision(db, "owner-1", proposal())
    first = await ledger.append_decision_evaluation(
        db,
        "owner-1",
        "proposal-1",
        {
            "outcome_status": "promising",
            "summary": "تحسن أولي",
            "evidence": {"profit_after": 100},
        },
        evaluated_at="2026-08-13T08:00:00+00:00",
        source_event_key="evaluation-proposal-1-day-1",
    )
    first_stored = deepcopy(db[ledger.DECISION_LEDGER_COLLECTION].rows[1])
    second = await ledger.append_decision_evaluation(
        db,
        "owner-1",
        "proposal-1",
        {
            "outcome_status": "unsuccessful",
            "summary": "تراجع لاحق",
            "evidence": {"profit_after": -50},
        },
        evaluated_at="2026-08-14T08:00:00+00:00",
        source_event_key="evaluation-proposal-1-day-2",
    )
    await ledger.add_decision_annotation(
        db,
        "owner-1",
        "proposal-1",
        {"text": "تزامن مع منتصف الشهر", "kind": "market_context"},
        annotated_at="2026-08-14T09:00:00+00:00",
    )
    detail = await ledger.get_ad_decision(db, "owner-1", "proposal-1")
    page = await ledger.list_ad_decisions(db, "owner-1", "account-1", 1, 5)

    assert first["business_outcome"] == "promising"
    assert second["business_outcome"] == "unsuccessful"
    assert db[ledger.DECISION_LEDGER_COLLECTION].rows[1] == first_stored
    assert len(detail["evaluations"]) == 2
    assert detail["evaluations"][0]["evidence"] == {"profit_after": 100}
    assert detail["latest_evaluation"]["summary"] == "تراجع لاحق"
    assert detail["annotations"][0]["text"] == "تزامن مع منتصف الشهر"
    assert page["items"][0]["annotations"][0]["text"] == "تزامن مع منتصف الشهر"


@pytest.mark.asyncio
async def test_missing_historical_reason_is_not_invented_and_indexes_are_created():
    db = DB()
    detail = await ledger.record_management_decision(
        db,
        "owner-1",
        proposal(reason=None),
    )
    await ledger.ensure_ad_decision_indexes(db)

    assert detail["reason"] is None
    assert len(db[ledger.DECISION_LEDGER_COLLECTION].indexes) == 5


@pytest.mark.asyncio
async def test_decision_evidence_trend_override_and_baseline_windows_are_preserved():
    db = DB()
    row = proposal(
        baseline={
            "windows": {"d14": {"profit": 200}, "d3": {"profit": 80}},
            "captured_at": "2026-08-12T07:59:00+00:00",
        },
        expected={"profit_direction": "increase"},
        decision_evidence={"inventory_remaining": 17, "trend_3d": "improving"},
        trend_override_reason="تم تجاهل تحسن يوم واحد لأن حجم الطلبات غير كافٍ",
    )

    detail = await ledger.record_management_decision(db, "owner-1", row)

    assert detail["baseline_windows"] == {
        "d14": {"profit": 200},
        "d3": {"profit": 80},
    }
    assert detail["expected"] == {"profit_direction": "increase"}
    assert detail["evidence"]["decision_evidence"]["inventory_remaining"] == 17
    assert detail["evidence"]["trend_override_reason"].startswith("تم تجاهل")


@pytest.mark.asyncio
async def test_core_works_with_existing_minimal_async_collection_mock_without_find():
    db = DB()
    collection = db[ledger.DECISION_LEDGER_COLLECTION]
    # Campaign-management's focused async mock historically exposes rows,
    # find_one and insert_one but no find cursor.
    collection.find = None

    detail = await ledger.record_management_decision(
        db,
        "owner-1",
        proposal("minimal-mock"),
    )
    fetched = await ledger.get_ad_decision(db, "owner-1", "minimal-mock")

    assert detail["decision_id"] == "minimal-mock"
    assert fetched["execution_status"] == "completed"


@pytest.mark.asyncio
async def test_rollback_is_a_separate_measurable_decision_with_its_own_evidence():
    db = DB()
    forward = proposal()
    await ledger.record_management_decision(db, "owner-1", forward)
    await ledger.append_decision_evaluation(
        db,
        "owner-1",
        "proposal-1",
        {"outcome_status": "successful", "summary": "نجح التعديل الأمامي"},
        source_event_key="forward-evaluation",
    )
    rolled_back = proposal(
        status="rolled_back",
        rolled_back_by="owner-2",
        rollback_baseline={"windows": [{"days": 1, "campaign": {"sales_sar": 70}}]},
        rollback={
            "status": "verified",
            "before": {
                "id": "campaign-1",
                "status": "PAUSED",
                "daily_budget_micro": 40_000_000,
            },
            "after": {
                "id": "campaign-1",
                "status": "ACTIVE",
                "daily_budget_micro": 60_000_000,
            },
            "reason": "إرجاع الميزانية بعد تراجع المبيعات",
            "rolled_back_at": "2026-08-15T08:00:00+00:00",
        },
    )

    rollback_detail = await ledger.record_management_decision(
        db, "owner-1", rolled_back
    )
    page = await ledger.list_ad_decisions(db, "owner-1", "account-1", 1, 5)
    forward_detail = await ledger.get_ad_decision(db, "owner-1", "proposal-1")

    assert page["total"] == 2
    assert rollback_detail["decision_id"] != "proposal-1"
    assert rollback_detail["reverses_decision_id"] == "proposal-1"
    assert rollback_detail["action"] == "campaign.update.rollback"
    assert rollback_detail["execution_status"] == "completed"
    assert rollback_detail["before"]["daily_budget_micro"] == 40_000_000
    assert rollback_detail["after"]["daily_budget_micro"] == 60_000_000
    assert rollback_detail["reason"] == "إرجاع الميزانية بعد تراجع المبيعات"
    assert rollback_detail["actor_id"] == "owner-2"
    assert rollback_detail["baseline_windows"][0]["days"] == 1
    assert rollback_detail["latest_evaluation"] is None
    assert forward_detail["latest_evaluation"]["summary"] == "نجح التعديل الأمامي"
