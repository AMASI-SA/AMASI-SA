from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import dashboard_v2_routes
from integrations_control_center import campaign_product_associations as product_links
from integrations_control_center import snapchat_campaign_profitability as profitability
from integrations_control_center import (
    snapchat_campaign_result_source_routes as result_source,
)
from integrations_control_center import snapchat_decision_ledger as ledger
from integrations_control_center import snapchat_decision_metrics as metrics
from integrations_control_center import snapchat_decision_outcomes as outcomes


DECISION_AT = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)


def _matches(row, query):
    return all(row.get(key) == value for key, value in query.items())


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

    async def insert_one(self, row):
        if any(
            current.get("user_id") == row.get("user_id")
            and current.get("source_event_key") == row.get("source_event_key")
            for current in self.rows
        ):
            raise RuntimeError("duplicate")
        self.rows.append(deepcopy(row))
        return object()

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if _matches(row, query):
                result = deepcopy(row)
                if projection and projection.get("_id") == 0:
                    result.pop("_id", None)
                return result
        return None

    def find(self, query, projection=None):
        return Cursor(row for row in self.rows if _matches(row, query))

    async def update_one(self, query, update, upsert=False):
        row = next((item for item in self.rows if _matches(item, query)), None)
        if row is None:
            if not upsert:
                return object()
            row = deepcopy(query)
            self.rows.append(row)
        for key, value in (update.get("$set") or {}).items():
            row[key] = deepcopy(value)
        return object()


class DB:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, Collection())

    def __getattr__(self, name):
        return self[name]


def metric(*, orders, sales, spend, profit, roas=4.0, complete=True):
    return {
        "orders": orders,
        "sales_sar": sales,
        "spend_sar": spend,
        "product_cost_sar": sales - spend - profit if complete else None,
        "known_product_cost_sar": sales - spend - profit,
        "contribution_profit_sar": profit if complete else None,
        "profit_margin_pct": profit / sales * 100 if complete and sales else None,
        "roas": roas,
        "cpa_sar": spend / orders if orders else None,
        "cost_complete": complete,
        "missing_cost_orders": 0 if complete else 1,
    }


def store_metric(*, orders, sales, profit, complete=True):
    return {
        "orders": orders,
        "sales_sar": sales,
        "product_cost_sar": sales - profit if complete else None,
        "known_product_cost_sar": sales - profit,
        "gross_profit_before_marketing_sar": profit if complete else None,
        "gross_margin_before_marketing_pct": (
            profit / sales * 100 if complete and sales else None
        ),
        "cost_complete": complete,
        "missing_cost_orders": 0 if complete else 1,
    }


def window(days, *, multiplier=1, complete=True, products=None, partial=False):
    end_day = datetime.fromisoformat("2026-08-01" if partial else "2026-07-31").date()
    return {
        "days": days,
        "date_from": (end_day - timedelta(days=days - 1)).isoformat(),
        "date_to": end_day.isoformat(),
        "includes_partial_current_day": partial,
        "coverage": {
            "complete": True,
            "status": "test_durable_campaign_performance_proof",
        },
        "campaign": metric(
            orders=4 * days * multiplier,
            sales=200 * days * multiplier,
            spend=50 * days * multiplier,
            profit=100 * days * multiplier,
            complete=complete,
        ),
        "account": metric(
            orders=5 * days * multiplier,
            sales=250 * days * multiplier,
            spend=60 * days * multiplier,
            profit=120 * days * multiplier,
            complete=complete,
        ),
        "store": store_metric(
            orders=10 * days * multiplier,
            sales=500 * days * multiplier,
            profit=300 * days * multiplier,
            complete=complete,
        ),
        "product_sales_comparison": products or [],
        "attribution_caution": False,
    }


def snapshot(*, multiplier=1, complete=True, products=None, partial=False):
    return {
        "source_mode": "test_snapshot",
        "campaign_id": "campaign-1",
        "account_timezone": "Asia/Riyadh",
        "coverage": {"complete": True, "current_day_partial": partial},
        "windows": [
            window(
                days,
                multiplier=multiplier,
                complete=complete,
                products=products,
                partial=partial,
            )
            for days in (1, 3, 7)
        ],
    }


async def make_decision(db, *, decision_id="decision-1", expected=None, baseline=None):
    proposal = {
        "user_id": "owner-1",
        "proposal_id": decision_id,
        "account_id": "account-1",
        "target_id": "campaign-1",
        "provider_entity_id": "campaign-1",
        "action": "campaign.update",
        "status": "completed",
        "reason": "measured test decision",
        "created_at": (DECISION_AT - timedelta(minutes=5)).isoformat(),
        "executed_at": DECISION_AT.isoformat(),
        "original_snapshot": {
            "id": "campaign-1",
            "status": "ACTIVE",
            "daily_budget_micro": 60_000_000,
        },
        "operation": {
            "entity_type": "campaign",
            "changes": {"daily_budget_micro": 80_000_000},
        },
        "verification": {
            "provider_snapshot": {
                "id": "campaign-1",
                "status": "ACTIVE",
                "daily_budget_micro": 80_000_000,
            },
        },
        "baseline": baseline or snapshot(),
        "expected": expected,
    }
    return await ledger.record_management_decision(db, "owner-1", proposal)


def _prepare_real_snapshot_dependencies(monkeypatch):
    async def no_rows(*args, **kwargs):
        return []

    async def no_costs(*args, **kwargs):
        return {}

    monkeypatch.setattr(dashboard_v2_routes, "_filtered_orders", no_rows)
    monkeypatch.setattr(profitability, "_load_cost_context", no_costs)
    monkeypatch.setattr(result_source, "_campaign_identities", no_rows)
    monkeypatch.setattr(product_links, "list_effective_campaign_products", no_rows)


def _with_window_dates(snapshot_value, *, date_from, date_to):
    value = deepcopy(snapshot_value)
    for key in ("windows", "completed_windows"):
        for item in value.get(key) or []:
            item["date_from"] = date_from
            item["date_to"] = date_to
    return value


@pytest.mark.asyncio
async def test_actual_capture_without_native_range_proof_is_inconclusive(
    monkeypatch,
):
    db = DB()
    _prepare_real_snapshot_dependencies(monkeypatch)
    baseline = await metrics.capture_decision_baseline(
        db,
        "owner-1",
        account_id="account-1",
        campaign_id="campaign-1",
        account_timezone="Asia/Riyadh",
        captured_at=DECISION_AT,
    )
    decision = await make_decision(
        db,
        decision_id="missing-native-coverage",
        expected={"sales_direction": "increase"},
        baseline=baseline,
    )

    result = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        decision,
        now=DECISION_AT + timedelta(hours=48),
    )

    evaluation = result["evaluations"][0]
    assert baseline["coverage"]["complete"] is False
    assert evaluation["outcome_status"] == "inconclusive"
    assert evaluation["data_completeness"]["complete"] is False
    assert "baseline_coverage_incomplete" in evaluation["caveats"]
    assert "post_coverage_incomplete" in evaluation["caveats"]
    assert evaluation["expected_vs_actual"]["basis"] == "data_completeness_gate"


@pytest.mark.asyncio
async def test_one_day_outcome_uses_one_day_proof_not_global_fourteen_day_gate(
    monkeypatch,
):
    db = DB()
    one_day = window(1)
    one_day["coverage"] = {
        "complete": True,
        "status": "verified_complete_campaign_performance_sync_union",
    }
    partial_snapshot = {
        "source_mode": "test_snapshot",
        "campaign_id": "campaign-1",
        "account_timezone": "Asia/Riyadh",
        # Global coverage describes the 14-day snapshot and is intentionally
        # incomplete; it must not veto an independently proven 1-day window.
        "coverage": {"complete": False},
        "windows": [one_day],
    }
    decision = await make_decision(
        db,
        decision_id="one-day-specific-coverage",
        expected={"sales_direction": "increase"},
        baseline=partial_snapshot,
    )

    async def capture(*args, **kwargs):
        post = deepcopy(partial_snapshot)
        post["windows"] = [window(1, multiplier=1.2)]
        return post

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    result = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        decision,
        now=DECISION_AT + timedelta(hours=48),
    )

    one_day_evaluation = result["evaluations"][0]
    assert one_day_evaluation["horizon"] == "1d"
    assert one_day_evaluation["data_completeness"]["complete"] is True
    assert one_day_evaluation["outcome_status"] == "successful"


@pytest.mark.asyncio
async def test_legacy_window_without_explicit_coverage_is_inconclusive(monkeypatch):
    db = DB()
    legacy = snapshot()
    for row in legacy["windows"]:
        row.pop("coverage", None)
    decision = await make_decision(
        db,
        decision_id="legacy-no-window-proof",
        expected={"sales_direction": "increase"},
        baseline=legacy,
    )

    async def capture(*args, **kwargs):
        return deepcopy(legacy)

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    result = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        decision,
        now=DECISION_AT + timedelta(hours=48),
    )

    evaluation = result["evaluations"][0]
    assert evaluation["outcome_status"] == "inconclusive"
    assert evaluation["data_completeness"]["complete"] is False
    assert "baseline_coverage_incomplete" in evaluation["caveats"]
    assert "post_coverage_incomplete" in evaluation["caveats"]


@pytest.mark.asyncio
async def test_does_not_capture_or_append_before_first_24_hour_horizon(monkeypatch):
    db = DB()
    decision = await make_decision(
        db,
        expected={"profit_direction": "increase"},
    )

    async def must_not_capture(*args, **kwargs):
        raise AssertionError("a pending horizon must not read post-decision data")

    monkeypatch.setattr(outcomes, "capture_decision_baseline", must_not_capture)
    result = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        decision,
        now=DECISION_AT + timedelta(hours=23),
    )

    assert result["outcome_status"] == "pending"
    assert result["evaluated"] == 0
    assert result["pending"] == 3
    assert len(db[ledger.DECISION_LEDGER_COLLECTION].rows) == 1


@pytest.mark.asyncio
async def test_elapsed_horizons_append_explicit_expected_assessments_idempotently(
    monkeypatch,
):
    db = DB()
    decision = await make_decision(
        db,
        expected={"profit_direction": "increase"},
    )

    async def capture(*args, **kwargs):
        return snapshot(multiplier=1.4)

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    current = DECISION_AT + timedelta(hours=96)
    first = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        decision,
        now=current,
    )
    second = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        decision,
        now=current,
    )

    assert first["evaluated"] == 2
    assert first["outcome_status"] == "successful"
    assert first["evaluations"][0]["expected_vs_actual"]["heuristic_used"] is False
    assert (
        first["evaluations"][1]["campaign_delta"]["contribution_profit_sar"]["actual"]
        == 420.0
    )
    assert second["evaluated"] == 0
    assert second["already_recorded"] == 2
    evaluation_rows = [
        row
        for row in db[ledger.DECISION_LEDGER_COLLECTION].rows
        if row["entry_type"] == "evaluation"
    ]
    assert {row["source_event_key"] for row in evaluation_rows} == {
        "snapchat-decision-outcome:v1:decision-1:24h",
        "snapchat-decision-outcome:v1:decision-1:72h",
    }


@pytest.mark.asyncio
async def test_missing_expectation_is_unscored_and_preserves_source_attribution(
    monkeypatch,
):
    db = DB()
    baseline = snapshot()
    proposal = await make_decision(db, decision_id="pause-1", baseline=baseline)
    stored = db[ledger.DECISION_LEDGER_COLLECTION].rows[0]
    stored["before"]["daily_budget_micro"] = 100_000_000
    stored["after"]["daily_budget_micro"] = 50_000_000
    stored["after"]["status"] = "PAUSED"
    proposal = await ledger.get_ad_decision(db, "owner-1", "pause-1")
    products = [
        {
            "identity": "product-1",
            "name": "منتج",
            "campaign_attributed_units": 5,
            "whole_store_product_units": 20,
            "observed_order_sources": [
                {"source": "meta", "units": 4},
                {"source": "manual", "units": 3},
                {"source": "whatsapp", "units": 2},
                {"source": "unknown", "units": 11},
            ],
        }
    ]
    post = snapshot(multiplier=1, products=products)
    post["windows"][0]["campaign"] = metric(
        orders=4,
        sales=190,
        spend=0,
        profit=90,
    )

    async def capture(*args, **kwargs):
        return post

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    result = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        proposal,
        now=DECISION_AT + timedelta(hours=48),
    )
    measured = result["evaluations"][0]
    attribution = measured["attribution_product_comparison"]

    assert measured["outcome_status"] == "inconclusive"
    assert measured["expected_vs_actual"]["heuristic_used"] is False
    assert measured["expected_vs_actual"]["basis"] == "no_explicit_expected_outcome"
    assert (
        "no_fixed_success_rule_was_applied_without_recorded_expectations"
        in measured["caveats"]
    )
    assert attribution["verified_cross_platform_units_excluded"] == 4
    assert attribution["manual_or_salla_units_unresolved"] == 3
    assert attribution["explicit_whatsapp_units"] == 2
    assert attribution["products"][0]["snapchat_contribution_units"] == 5
    assert attribution["manual_source_rule"] == "manual_is_not_assumed_to_be_whatsapp"


@pytest.mark.asyncio
async def test_explicit_adaptive_range_is_scored_without_global_threshold(monkeypatch):
    db = DB()
    decision = await make_decision(
        db,
        decision_id="range-1",
        expected={
            "metrics": [
                {
                    "scope": "campaign",
                    "metric": "sales_sar",
                    "direction": "increase",
                    "expected_min": 250,
                    "expected_max": 400,
                    "value_basis": "actual",
                }
            ]
        },
    )

    async def capture(*args, **kwargs):
        return snapshot(multiplier=1.4)

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    result = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        decision,
        now=DECISION_AT + timedelta(hours=48),
    )
    check = result["evaluations"][0]["expected_vs_actual"]["checks"][0]

    assert result["evaluations"][0]["outcome_status"] == "successful"
    assert check["actual"] == 280.0
    assert check["direction_met"] is True
    assert check["range_met"] is True
    assert check["met"] is True


def test_missing_expected_metric_prevents_partial_success_verdict():
    expected = {
        "metrics": [
            {
                "scope": "campaign",
                "metric": "sales_sar",
                "direction": "increase",
            },
            {
                "scope": "campaign",
                "metric": "roas",
                "direction": "increase",
            },
        ]
    }
    deltas = {
        "campaign": {
            "sales_sar": {
                "baseline": 100,
                "actual": 140,
                "absolute_delta": 40,
                "delta_pct": 40,
                "direction": "increase",
            }
            # ROAS is intentionally unavailable, for example when spend is 0.
        }
    }

    status, assessment = outcomes._explicit_assessment(expected, deltas)

    assert status == "inconclusive"
    assert assessment["requested_checks"] == 2
    assert assessment["comparable_checks"] == 1
    assert assessment["unavailable_checks"] == 1
    assert {row["metric"]: row["met"] for row in assessment["checks"]} == {
        "sales_sar": True,
        "roas": None,
    }


@pytest.mark.asyncio
async def test_incomplete_cost_data_is_recorded_as_inconclusive(monkeypatch):
    db = DB()
    decision = await make_decision(
        db,
        expected={"profit_direction": "increase"},
    )

    async def capture(*args, **kwargs):
        return snapshot(multiplier=1.2, complete=False)

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    result = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        decision,
        now=DECISION_AT + timedelta(hours=48),
    )
    evaluation = result["evaluations"][0]

    assert evaluation["outcome_status"] == "inconclusive"
    assert evaluation["data_completeness"]["complete"] is False
    assert evaluation["current_day_partial"] == {
        "baseline": False,
        "post": False,
        "treatment": (
            "retained_as_a_caveat; equal-length windows use corresponding "
            "calendar-day cutoffs"
        ),
    }
    assert evaluation["appended"] is False
    assert evaluation["retryable"] is True
    assert "post_campaign_metrics_incomplete" in evaluation["caveats"]


@pytest.mark.asyncio
async def test_due_runner_is_tenant_scoped_and_can_target_one_decision(monkeypatch):
    db = DB()
    await make_decision(db, decision_id="one", expected={"sales_direction": "increase"})
    await make_decision(db, decision_id="two", expected={"sales_direction": "increase"})

    async def capture(*args, **kwargs):
        return snapshot(multiplier=1.1)

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    result = await outcomes.evaluate_due_ad_decisions(
        db,
        "owner-1",
        decision_id="two",
        now=DECISION_AT + timedelta(hours=48),
    )

    assert result["scanned"] == 1
    assert result["evaluated"] == 1
    assert result["results"][0]["decision_id"] == "two"
    evaluations = [
        row
        for row in db[ledger.DECISION_LEDGER_COLLECTION].rows
        if row["entry_type"] == "evaluation"
    ]
    assert {row["decision_id"] for row in evaluations} == {"two"}


@pytest.mark.asyncio
async def test_bounded_due_runner_skips_finalized_history_before_applying_limit(
    monkeypatch,
):
    db = DB()
    finalized = {
        f"old-{index}": {
            "decision_id": f"old-{index}",
            "execution_status": "completed",
            "effective_at": "2026-06-01T08:00:00+00:00",
            "latest_evaluation": {
                "horizon": "7d",
                "source": outcomes.OUTCOME_SOURCE,
            },
            "evaluations": [
                {
                    "horizon": label,
                    "source": outcomes.OUTCOME_SOURCE,
                }
                for label in ("1d", "3d", "7d")
            ],
        }
        for index in range(30)
    }
    due = {
        "decision_id": "new-due",
        "execution_status": "completed",
        "effective_at": "2026-08-01T08:00:00+00:00",
        "latest_evaluation": None,
        "baseline": {"account_timezone": "Asia/Riyadh"},
    }

    async def tenant_entries(*args, **kwargs):
        return []

    def aggregate(*args, **kwargs):
        return {**finalized, "new-due": due}

    async def detail(*args, **kwargs):
        return due

    evaluated = []

    async def evaluate(*args, **kwargs):
        evaluated.append(args[2]["decision_id"])
        return {
            "evaluated": 1,
            "already_recorded": 0,
            "pending": 0,
        }

    monkeypatch.setattr(ledger, "_tenant_entries", tenant_entries)
    monkeypatch.setattr(ledger, "_aggregate_decisions", aggregate)
    monkeypatch.setattr(ledger, "get_ad_decision", detail)
    monkeypatch.setattr(outcomes, "evaluate_ad_decision", evaluate)

    result = await outcomes.evaluate_due_ad_decisions(
        db,
        "owner-1",
        now=datetime(2026, 8, 12, tzinfo=timezone.utc),
        limit=1,
    )

    assert evaluated == ["new-due"]
    assert result["eligible_due"] == 1
    assert result["deferred_due"] == 0


@pytest.mark.asyncio
async def test_bounded_due_runner_rotates_past_retryable_rows_per_tenant(monkeypatch):
    db = DB()
    summaries = {
        f"decision-{index}": {
            "decision_id": f"decision-{index}",
            "execution_status": "completed",
            "effective_at": "2026-08-01T08:00:00+00:00",
            "latest_evaluation": None,
            "evaluations": [],
            "baseline": {"account_timezone": "Asia/Riyadh"},
        }
        for index in range(1, 7)
    }

    async def tenant_entries(*args, **kwargs):
        return []

    def aggregate(*args, **kwargs):
        return summaries

    async def detail(db_value, tenant, identity):
        return summaries[identity]

    calls = []

    async def retryable(db_value, tenant, decision, **kwargs):
        calls.append((tenant, decision["decision_id"]))
        return {
            "decision_id": decision["decision_id"],
            "evaluated": 0,
            "already_recorded": 0,
            "pending": 0,
            "evaluations": [{"horizon": "1d", "retryable": True}],
        }

    monkeypatch.setattr(ledger, "_tenant_entries", tenant_entries)
    monkeypatch.setattr(ledger, "_aggregate_decisions", aggregate)
    monkeypatch.setattr(ledger, "get_ad_decision", detail)
    monkeypatch.setattr(outcomes, "evaluate_ad_decision", retryable)

    current = datetime(2026, 8, 12, tzinfo=timezone.utc)
    first = await outcomes.evaluate_due_ad_decisions(
        db, "owner-1", now=current, limit=5
    )
    second = await outcomes.evaluate_due_ad_decisions(
        db, "owner-1", now=current, limit=5
    )
    await outcomes.evaluate_due_ad_decisions(
        db, "owner-2", now=current, limit=5
    )

    assert [identity for tenant, identity in calls[:5]] == [
        "decision-1",
        "decision-2",
        "decision-3",
        "decision-4",
        "decision-5",
    ]
    assert calls[5] == ("owner-1", "decision-6")
    assert [identity for tenant, identity in calls[10:15]] == [
        "decision-1",
        "decision-2",
        "decision-3",
        "decision-4",
        "decision-5",
    ]
    assert first["eligible_due"] == second["eligible_due"] == 6
    assert first["deferred_due"] == second["deferred_due"] == 1
    assert len(db[outcomes.OUTCOME_WORKER_STATE_COLLECTION].rows) == 2


@pytest.mark.asyncio
async def test_due_runner_retries_missing_1d_after_3d_and_7d_are_recorded(monkeypatch):
    db = DB()
    await make_decision(
        db,
        decision_id="out-of-order-horizons",
        expected={"sales_direction": "increase"},
    )
    for label, hours in (("3d", 72), ("7d", 168)):
        await ledger.append_decision_evaluation(
            db,
            "owner-1",
            "out-of-order-horizons",
            {
                "horizon": label,
                "horizon_hours": hours,
                "outcome_status": "successful",
                "summary": f"recorded {label}",
            },
            outcome_status="successful",
            source=outcomes.OUTCOME_SOURCE,
            source_event_key=(
                f"snapchat-decision-outcome:v1:out-of-order-horizons:{hours}h"
            ),
            evaluated_at=DECISION_AT + timedelta(hours=hours + 24),
        )

    async def capture(*args, **kwargs):
        return snapshot(multiplier=1.2)

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    result = await outcomes.evaluate_due_ad_decisions(
        db,
        "owner-1",
        now=DECISION_AT + timedelta(days=10),
        limit=5,
    )

    assert result["eligible_due"] == 1
    assert result["evaluated"] == 1
    assert result["results"][0]["evaluations"][0]["horizon"] == "1d"
    assert result["results"][0]["evaluations"][0]["appended"] is True
    evaluation_rows = [
        row
        for row in db[ledger.DECISION_LEDGER_COLLECTION].rows
        if row["entry_type"] == "evaluation"
    ]
    assert {
        row["evaluation"]["horizon"] for row in evaluation_rows
    } == {"1d", "3d", "7d"}


@pytest.mark.asyncio
async def test_real_partial_decision_snapshot_uses_immutable_completed_windows(
    monkeypatch,
):
    db = DB()
    baseline = snapshot(partial=True)
    baseline["completed_windows"] = snapshot(partial=False)["windows"]
    decision = await make_decision(
        db,
        decision_id="completed-window-1",
        expected={"sales_direction": "increase"},
        baseline=baseline,
    )

    async def capture(*args, **kwargs):
        assert kwargs["completed_days_only"] is True
        return snapshot(multiplier=1.2, partial=False)

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    result = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        decision,
        now=DECISION_AT + timedelta(hours=48),
    )

    evaluation = result["evaluations"][0]
    assert evaluation["appended"] is True
    assert evaluation["window"]["baseline_source"] == (
        "decision_time_immutable_completed_local_days"
    )
    assert evaluation["current_day_partial"] == {
        "baseline": False,
        "post": False,
        "treatment": (
            "retained_as_a_caveat; equal-length windows use corresponding "
            "calendar-day cutoffs"
        ),
    }


@pytest.mark.asyncio
async def test_midnight_between_preview_and_execution_rejects_unaligned_completed_windows(
    monkeypatch,
):
    """Preview-day completed windows must not become execution-day baseline.

    The preview is created at 23:57 Riyadh on Aug 1, while provider execution
    becomes effective at 00:02 on Aug 2.  Therefore the immediately-prior
    baseline day is Aug 1, not the preview snapshot's Jul 31 completed day.
    """
    db = DB()
    effective_at = datetime(2026, 8, 1, 21, 2, tzinfo=timezone.utc)
    preview_at = datetime(2026, 8, 1, 20, 57, tzinfo=timezone.utc)
    stale = snapshot(partial=True)
    stale["captured_at"] = preview_at.isoformat()
    stale["completed_windows"] = _with_window_dates(
        snapshot(partial=False),
        date_from="2026-07-31",
        date_to="2026-07-31",
    )["windows"]

    proposal = {
        "user_id": "owner-1",
        "proposal_id": "midnight-alignment-1",
        "account_id": "account-1",
        "target_id": "campaign-1",
        "provider_entity_id": "campaign-1",
        "action": "campaign.update",
        "status": "completed",
        "reason": "preview and execution crossed local midnight",
        "created_at": preview_at.isoformat(),
        "executed_at": effective_at.isoformat(),
        "original_snapshot": {
            "id": "campaign-1",
            "status": "ACTIVE",
            "daily_budget_micro": 60_000_000,
        },
        "operation": {
            "entity_type": "campaign",
            "changes": {"daily_budget_micro": 80_000_000},
        },
        "verification": {
            "provider_snapshot": {
                "id": "campaign-1",
                "status": "ACTIVE",
                "daily_budget_micro": 80_000_000,
            },
        },
        "baseline": stale,
        "expected": {"sales_direction": "increase"},
    }
    decision = await ledger.record_management_decision(db, "owner-1", proposal)
    captures = []

    async def capture(*args, **kwargs):
        captured_at = kwargs["captured_at"]
        captures.append(captured_at)
        if captured_at == datetime(2026, 8, 1, 21, tzinfo=timezone.utc):
            return _with_window_dates(
                snapshot(partial=False),
                date_from="2026-08-01",
                date_to="2026-08-01",
            )
        return _with_window_dates(
            snapshot(multiplier=1.2, partial=False),
            date_from="2026-08-03",
            date_to="2026-08-03",
        )

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    result = await outcomes.evaluate_ad_decision(
        db,
        "owner-1",
        decision,
        now=datetime(2026, 8, 4, 1, tzinfo=timezone.utc),
    )

    evaluation = result["evaluations"][0]
    assert captures[0] == datetime(2026, 8, 1, 21, tzinfo=timezone.utc)
    assert evaluation["window"]["baseline_source"] == (
        "historical_completed_local_days_before_decision"
    )
    assert evaluation["window"]["baseline_date_to"] == "2026-08-01"
    assert evaluation["window"]["baseline_date_to"] != "2026-07-31"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("entity_type", "entity_id", "parent_id", "expected_scope"),
    [
        (
            "ad_squad",
            "squad-1",
            "campaign-1",
            {"ad_squad_id": "squad-1", "ad_id": None},
        ),
        ("ad", "ad-1", "squad-1", {"ad_squad_id": "squad-1", "ad_id": "ad-1"}),
    ],
)
async def test_outcome_recapture_preserves_squad_and_ad_product_scope(
    monkeypatch,
    entity_type,
    entity_id,
    parent_id,
    expected_scope,
):
    captured = []

    async def capture(*args, **kwargs):
        captured.append(deepcopy(kwargs))
        return snapshot()

    monkeypatch.setattr(outcomes, "capture_decision_baseline", capture)
    decision = {
        "account_id": "account-1",
        "entity_type": entity_type,
        "entity_id": entity_id,
        "before": {"ad_squad_id": parent_id} if entity_type == "ad" else {},
        "after": {},
        "evidence": {"parent_id": parent_id},
        "baseline": {"account_timezone": "Asia/Riyadh"},
    }

    await outcomes._capture_snapshot(
        DB(),
        "owner-1",
        decision,
        campaign_id="campaign-1",
        captured_at=DECISION_AT,
    )

    assert captured[0]["campaign_id"] == "campaign-1"
    assert {
        "ad_squad_id": captured[0]["ad_squad_id"],
        "ad_id": captured[0]["ad_id"],
    } == expected_scope
