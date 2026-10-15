from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from integrations_control_center import snapchat_intraday_waste_monitor as monitor

NOW = datetime(2026, 8, 12, 12, tzinfo=timezone.utc)


def entity(entity_type="campaign", entity_id="campaign-1", **overrides):
    row = {
        "entity_type": entity_type,
        "external_id": entity_id,
        "display_name": "New managed entity",
        "ad_account_id": "account-1",
        "campaign_id": "campaign-1",
        "ad_squad_id": "squad-1" if entity_type == "ad" else None,
        "status": "ACTIVE",
        "created_at_provider": (NOW - timedelta(hours=4)).isoformat(),
        "start_time": (NOW - timedelta(hours=3)).isoformat(),
        "daily_budget_micro": 100_000_000,
    }
    row.update(overrides)
    return row


def fact(*, spend=400, orders=0, minutes_old=2, funnel=0, **overrides):
    row = {
        "entity_type": "campaign",
        "external_id": "campaign-1",
        "campaign_id": "campaign-1",
        "ad_account_id": "account-1",
        "spend_sar": spend,
        "purchases": orders,
        "purchase_value_sar": 0,
        "metrics": {
            "impressions": 10_000,
            "swipes": 100,
            "conversion_add_cart": funnel,
            "conversion_start_checkout": funnel,
            "conversion_add_billing": 0,
            "conversion_purchases": orders,
        },
        "action_report_time": "conversion",
        "provider_window_end": (NOW - timedelta(minutes=5)).isoformat(),
        "updated_at": (NOW - timedelta(minutes=minutes_old)).isoformat(),
    }
    row.update(overrides)
    return row


def freshness(*, complete=True):
    processed = NOW if complete else NOW - timedelta(minutes=30)
    return {"conversion_data_processed_end_time": processed.isoformat()}


def economics(*, target=100, delay=60, verified=True):
    return {
        "verified": verified,
        "target_cpa_sar": target,
        "expected_conversion_delay_minutes": delay,
    }


def evaluate(**overrides):
    values = {
        "entity": entity(),
        "fact": fact(),
        "historical": {},
        "economics": economics(),
        "management": {},
        "freshness": freshness(),
        "now": NOW,
    }
    values.update(overrides)
    return monitor.evaluate_intraday_entity(**values)


def test_incomplete_conversion_reporting_never_treats_zero_as_failure():
    row = evaluate(freshness=freshness(complete=False))

    assert row["recommendation"]["code"] == "wait_reporting_completion"
    assert row["proposal_candidate"] is None
    assert row["evidence"]["safety_complete"] is False
    assert row["provider_write_reached"] is False


def test_conversion_delay_blocks_adverse_candidate_even_when_spend_is_high():
    row = evaluate(
        entity=entity(start_time=(NOW - timedelta(minutes=30)).isoformat()),
        economics=economics(target=50, delay=90),
    )

    assert row["recommendation"]["code"] == "observe_conversion_lag"
    assert row["proposal_candidate"] is None
    assert row["conversion_delay"]["mature"] is False


def test_dynamic_cpa_is_continuous_evidence_not_a_pause_rule():
    row = evaluate(economics=economics(target=100, delay=60))

    assert row["recommendation"]["code"] == "adaptive_review"
    assert row["proposal_candidate"]["ready_for_proposal"] is False
    assert row["proposal_candidate"]["payload"] is None
    assert row["evidence"]["expected_orders_at_target"] == 4
    assert row["evidence"]["underperformance_confidence"] > 0.95
    assert row["provider_write_reached"] is False


def test_statistical_signal_moves_with_economics_but_never_authorizes_a_write():
    low_target = evaluate(economics=economics(target=50))
    high_target = evaluate(economics=economics(target=300))

    assert low_target["recommendation"]["code"] == "adaptive_review"
    assert low_target["proposal_candidate"]["ready_for_proposal"] is False
    assert high_target["recommendation"]["code"] == "watch_accumulating_evidence"
    assert high_target["proposal_candidate"] is None


def test_downstream_funnel_potential_prevents_pause_and_preserves_sales_option():
    row = evaluate(fact=fact(funnel=3))

    assert row["recommendation"]["code"] == "adaptive_review"
    assert row["proposal_candidate"]["ready_for_proposal"] is False
    assert row["proposal_candidate"]["requires_budget_choice"] is True


def test_observed_sales_prevent_early_monitor_from_proposing_a_full_pause():
    row = evaluate(
        fact=fact(spend=700, orders=1),
        economics=economics(target=100),
    )

    assert row["evidence"]["underperformance_confidence"] > 0.95
    assert row["recommendation"]["code"] == "adaptive_review"
    assert row["proposal_candidate"]["ready_for_proposal"] is False
    assert row["proposal_candidate"]["payload"] is None


def test_historical_cpa_without_verified_economics_cannot_authorize_pause():
    row = evaluate(
        economics={},
        historical={
            "weighted_cpa_sar": 100,
            "expected_conversion_delay_minutes": 60,
            "conversion_delay_verified": True,
        },
    )

    assert row["effective_target"]["source"] == "historical_weighted_cpa"
    assert row["effective_target"]["profit_authoritative"] is False
    assert row["recommendation"]["code"] == "investigate_efficiency"
    assert row["proposal_candidate"] is None


def test_managed_entities_get_exact_start_anchor_but_no_invented_first_spend():
    activated = NOW - timedelta(hours=2)
    proposals = [
        {
            "proposal_id": "create-1",
            "status": "completed",
            "action": "campaign.create",
            "provider_entity_id": "campaign-1",
            "executed_at": (NOW - timedelta(hours=4)).isoformat(),
            "operation": {
                "entity_type": "campaign",
                "plural": "campaigns",
                "body": {"campaigns": [{"status": "PAUSED"}]},
            },
        },
        {
            "proposal_id": "activate-1",
            "status": "completed",
            "action": "campaign.update",
            "target_id": "campaign-1",
            "executed_at": activated.isoformat(),
            "operation": {
                "entity_type": "campaign",
                "changes": {"status": "ACTIVE"},
            },
        },
    ]
    anchors = monitor.build_management_anchors(proposals)
    row = evaluate(
        entity=entity(start_time=None),
        management=anchors[("campaign", "campaign-1")],
    )

    assert row["tracking"]["monitoring_started_at"] == activated.isoformat()
    assert row["tracking"]["monitoring_anchor_source"] == "mezan_verified_activation"
    assert row["tracking"]["first_spend_observed_at"] is None
    assert row["tracking"]["first_spend_time_precision"] == (
        "unavailable_from_cumulative_day_snapshot"
    )


def test_rolled_back_management_uses_verified_rollback_status_and_time():
    rolled_back_at = NOW - timedelta(hours=1)
    proposals = [
        {
            "proposal_id": "activate-then-rollback",
            "status": "rolled_back",
            "action": "campaign.update",
            "target_id": "campaign-1",
            "executed_at": (NOW - timedelta(hours=4)).isoformat(),
            "operation": {
                "entity_type": "campaign",
                "changes": {"status": "ACTIVE"},
            },
            "rollback": {
                "status": "verified",
                "after": {"id": "campaign-1", "status": "PAUSED"},
                "rolled_back_at": rolled_back_at.isoformat(),
            },
        }
    ]

    anchor = monitor.build_management_anchors(proposals)[
        ("campaign", "campaign-1")
    ]
    row = evaluate(entity=entity(status="ACTIVE"), management=anchor)

    assert anchor["latest_managed_status"] == "PAUSED"
    assert anchor["latest_managed_status_at"] == rolled_back_at.isoformat()
    assert row["recommendation"]["code"] == "inactive_observation"
    assert row["proposal_candidate"] is None


def test_all_three_levels_are_evaluated_and_lineage_is_preserved():
    entities = [
        entity(),
        entity("ad_squad", "squad-1", campaign_id="campaign-1"),
        entity(
            "ad",
            "ad-1",
            campaign_id=None,
            ad_squad_id="squad-1",
        ),
    ]
    facts = []
    for row in entities:
        current = fact()
        current.update(
            {
                "entity_type": row["entity_type"],
                "external_id": row["external_id"],
                "campaign_id": "campaign-1",
            }
        )
        facts.append(current)
    rows = monitor.evaluate_intraday_facts(
        entities=entities,
        current_facts=facts,
        historical_facts=[],
        proposals=[],
        freshness_by_account={"account-1": freshness()},
        economics_by_campaign={"campaign-1": economics()},
        now=NOW,
    )

    assert {row["entity_type"] for row in rows} == {"campaign", "ad_squad", "ad"}
    assert all(row["campaign_id"] == "campaign-1" for row in rows)
    assert all(row["proposal_candidate"] is not None for row in rows)
    campaign = next(row for row in rows if row["entity_type"] == "campaign")
    ad = next(row for row in rows if row["entity_type"] == "ad")
    assert campaign["proposal_candidate"]["ready_for_proposal"] is False
    assert "requires_hierarchy_choice" not in campaign["proposal_candidate"]
    assert ad["proposal_candidate"]["ready_for_proposal"] is False


def test_wrong_attribution_mode_and_bounded_scan_guard_withhold_candidates():
    wrong_mode = evaluate(fact=fact(action_report_time="impression"))
    bounded = evaluate(global_coverage_complete=False)

    assert wrong_mode["recommendation"]["code"] == "withhold_wrong_reporting_mode"
    assert wrong_mode["proposal_candidate"] is None
    assert bounded["recommendation"]["code"] == "withhold_incomplete_scan"
    assert bounded["proposal_candidate"] is None


@pytest.mark.parametrize(
    ("observed", "expected", "probability"),
    [(0, 1, 0.367879), (0, 4, 0.018316), (1, 4, 0.091578)],
)
def test_poisson_lower_tail_contract(observed, expected, probability):
    assert monitor.poisson_lower_tail(observed, expected) == pytest.approx(
        probability, abs=0.000001
    )


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected and str(actual or "") < str(expected["$gte"]):
                return False
        elif actual != expected:
            return False
    return True


class ReadOnlyCursor:
    def __init__(self, rows):
        self.rows = rows

    async def to_list(self, length=None):
        return list(self.rows if length is None else self.rows[:length])


class ReadOnlyCollection:
    def __init__(self, rows):
        self.rows = rows
        self.find_calls = 0

    def find(self, query, projection=None):
        self.find_calls += 1
        return ReadOnlyCursor([row for row in self.rows if _matches(row, query)])

    async def insert_one(self, *args, **kwargs):  # pragma: no cover - invariant
        raise AssertionError("read-only monitor attempted an insert")

    async def update_one(self, *args, **kwargs):  # pragma: no cover - invariant
        raise AssertionError("read-only monitor attempted an update")


class ReadOnlyDB:
    def __init__(self, collections):
        self.collections = {
            name: ReadOnlyCollection(rows) for name, rows in collections.items()
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, ReadOnlyCollection([]))

    def __getattr__(self, name):
        return self[name]


@pytest.mark.asyncio
async def test_bounded_loader_reads_local_facts_only_and_returns_candidates(
    monkeypatch,
):
    current = fact()
    current.update(
        {
            "date": "2026-08-12",
            "source_mode": monitor.account_local_source_mode("conversion"),
            "provider": "snapchat_ads",
            "user_id": "owner-1",
        }
    )
    previous = {
        **current,
        "date": "2026-08-11",
        "spend_sar": 100,
        "purchases": 1,
    }
    managed = {
        "user_id": "owner-1",
        "account_id": "account-1",
        "proposal_id": "activate-1",
        "status": "completed",
        "action": "campaign.update",
        "target_id": "campaign-1",
        "executed_at": (NOW - timedelta(hours=3)).isoformat(),
        "operation": {
            "entity_type": "campaign",
            "changes": {"status": "ACTIVE"},
        },
    }
    catalog = {
        **entity(),
        "user_id": "owner-1",
        "provider": "snapchat_ads",
    }
    db = ReadOnlyDB(
        {
            monitor.SNAPCHAT_ENTITY_COLLECTION: [catalog],
            monitor.SNAPCHAT_ACCOUNT_LOCAL_PERFORMANCE_COLLECTION: [
                current,
                previous,
            ],
            monitor.PROPOSAL_COLLECTION: [managed],
            monitor.FRESHNESS_COLLECTION: [
                {
                    "user_id": "owner-1",
                    "provider": "snapchat_ads",
                    "ad_account_id": "account-1",
                    **freshness(),
                }
            ],
        }
    )

    async def selected(*args, **kwargs):
        return [{"ad_account_id": "account-1", "timezone": "UTC"}]

    monkeypatch.setattr(monitor, "_load_selected_accounts", selected)
    result = await monitor.monitor_snapchat_intraday_waste(
        db,
        "owner-1",
        economics_by_campaign={"campaign-1": economics()},
        now=NOW,
    )

    assert result["coverage"]["complete"] is True
    assert result["summary"]["entities_evaluated"] == 1
    assert result["summary"]["proposal_ready"] == 0
    assert result["summary"]["adaptive_review_signals"] == 1
    assert result["items"][0]["tracking"]["monitoring_anchor_source"] == (
        "mezan_verified_activation"
    )
    assert result["provider_read_reached"] is False
    assert result["provider_write_reached"] is False
    assert all(collection.find_calls <= 1 for collection in db.collections.values())
