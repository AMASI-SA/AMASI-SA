import asyncio
import json
from copy import deepcopy
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient

import campaign_ai_monthly_profit_goal_v1 as goal
import campaign_ai_monitor as monitor


class MemoryCollection:
    def __init__(self, rows=None, *, fail_insert=False):
        self.rows = [deepcopy(row) for row in (rows or [])]
        self.insert_history = []
        self.update_history = []
        self.find_history = []
        self.index_calls = 0
        self.fail_insert = fail_insert

    async def create_index(self, *_args, **_kwargs):
        self.index_calls += 1
        return "index"

    async def insert_one(self, value):
        if self.fail_insert:
            raise RuntimeError("synthetic_snapshot_insert_failed")
        stored = deepcopy(value)
        self.insert_history.append(deepcopy(stored))
        self.rows.append(stored)
        return SimpleNamespace(inserted_id=len(self.rows))

    async def find_one(self, query, projection=None, sort=None):
        self.find_history.append((deepcopy(query), deepcopy(projection), deepcopy(sort)))
        matches = [row for row in self.rows if all(row.get(k) == v for k, v in query.items())]
        if sort:
            for key, direction in reversed(sort):
                matches.sort(key=lambda row: row.get(key) or "", reverse=direction < 0)
        if not matches:
            return None
        result = deepcopy(matches[0])
        if projection:
            for key, included in projection.items():
                if included == 0:
                    result.pop(key, None)
        return result

    async def update_one(self, query, update, *, upsert=False, **_kwargs):
        self.update_history.append((deepcopy(query), deepcopy(update), upsert))
        for row in self.rows:
            if all(row.get(k) == v for k, v in query.items()):
                row.update(deepcopy(update.get("$set") or {}))
                for key, value in (update.get("$setOnInsert") or {}).items():
                    row.setdefault(key, deepcopy(value))
                return SimpleNamespace(matched_count=1, modified_count=1)
        if upsert:
            row = {**deepcopy(query), **deepcopy(update.get("$setOnInsert") or {}), **deepcopy(update.get("$set") or {})}
            self.rows.append(row)
            return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=len(self.rows))
        return SimpleNamespace(matched_count=0, modified_count=0)


class MemoryDB:
    def __init__(self, collections=None):
        self.collections = dict(collections or {})

    def __getitem__(self, name):
        return self.collections.setdefault(name, MemoryCollection())


class UnpersistedGoalCollection(MemoryCollection):
    async def update_one(self, query, update, *, upsert=False, **_kwargs):
        self.update_history.append((deepcopy(query), deepcopy(update), upsert))
        return SimpleNamespace(matched_count=0, modified_count=0, upserted_id=None)


class FailingReadCollection(MemoryCollection):
    async def find_one(self, query, projection=None, sort=None):
        raise RuntimeError("synthetic_goal_config_read_failed")


def complete_payload(net_profit=120.0, *, data_through=None):
    envelope = {
        "contract_version": "mezan_profit_envelope_v2",
        "quality": {
            "known": True,
            "complete": True,
            "scale_safe": True,
            "missing_product_cost_count": 0,
            "incomplete_profit_orders_count": 0,
            "component_known": {"advertising": True},
            "issues": [],
        },
    }
    if data_through is not None:
        envelope["data_through"] = data_through
    return {
        "totals": {
            "net_profit": net_profit,
            "total_sales": 200.0,
            "total_orders": 1,
            "total_ads_cost": 20.0,
            "total_product_cost": 60.0,
            "total_payment_fees": 0.0,
            "total_shipping_cost": 0.0,
            "operating_expenses_total": 0.0,
            "missing_product_cost_count": 0,
            "incomplete_profit_orders_count": 0,
        },
        "profit_envelope": envelope,
    }


def goal_row(target=100_000.0, updated_at="2026-09-01T00:00:00+00:00"):
    return {
        "user_id": "u1",
        "minimum_net_profit_sar": target,
        "updated_at": updated_at,
    }


def valid_snapshot_goal(
    current_goal,
    *,
    calculated_at="2026-09-07T08:00:00+00:00",
    evidence_end=date(2026, 9, 7),
    net_profit=40_000.0,
    data_through=None,
):
    return goal._derive_goal_progress(
        goal=current_goal,
        month_to_date={
            "available": True,
            "from": evidence_end.replace(day=1).isoformat(),
            "to": evidence_end.isoformat(),
            "timezone": "Asia/Riyadh",
            "calculated_at": calculated_at,
            "data_through": data_through,
            "data_through_status": (
                "source_watermark" if data_through else "source_watermark_unavailable"
            ),
            "net_profit": net_profit,
            "profit_accounting": {
                "known": True,
                "complete": True,
                "scale_safe": True,
                "missing_product_cost_count": 0,
                "incomplete_profit_orders_count": 0,
                "issues": [],
            },
        },
        end=evidence_end,
    )


def install_empty_monitor_sources(monkeypatch):
    async def empty(*_args, **_kwargs):
        return []

    async def history(*_args, **_kwargs):
        return {}

    async def experiments(*_args, **_kwargs):
        return {"source": "owner_approved_executed_changes_only", "experiments": []}

    monkeypatch.setattr(monitor._policy, "_campaign_entities", empty)
    monkeypatch.setattr(monitor._policy, "_snapchat_child_entities", empty)
    monkeypatch.setattr(monitor._legacy, "_meta_child_entities", empty)
    monkeypatch.setattr(monitor._legacy, "_campaign_history_context", history)
    monkeypatch.setattr(monitor._policy, "_experiment_outcomes_context", experiments)


@pytest.mark.asyncio
async def test_monthly_goal_is_in_initial_snapshot_and_latest_without_second_patch(monkeypatch):
    install_empty_monitor_sources(monkeypatch)
    fixed_now = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(goal, "_now", lambda: fixed_now.isoformat())
    monkeypatch.setattr(monitor._legacy, "_utcnow", lambda: fixed_now)
    db = MemoryDB({goal.COLLECTION: MemoryCollection([goal_row()])})
    calls = []

    async def loader(**kwargs):
        calls.append((kwargs["from_date"], kwargs["to_date"]))
        return complete_payload(data_through="2026-09-05")

    result = await monitor.run_campaign_ai_monitor(
        db,
        "u1",
        now=lambda: fixed_now,
        refresh_meta=False,
        business_context_loader=loader,
    )

    snapshots = db[monitor.RECOMMENDATION_COLLECTION]
    assert len(snapshots.insert_history) == 1
    inserted = snapshots.insert_history[0]
    assert inserted["monthly_profit_goal"]["progress_available"] is True
    assert inserted["monthly_profit_goal"]["net_profit_to_date_sar"] == 120.0
    assert inserted["monthly_profit_goal"]["data_through"] == "2026-09-05"
    assert inserted["monthly_profit_goal"]["provenance"] == {
        "run_id": inserted["run_id"],
        "snapshot_id": inserted["snapshot_id"],
        "snapshot_generated_at": inserted["generated_at"],
    }
    assert snapshots.update_history == []
    assert result["monthly_profit_goal"] == inserted["monthly_profit_goal"]
    assert calls == [
        ("2026-09-01", "2026-09-06"),
        ("2026-09-06", "2026-09-06"),
        ("2026-09-04", "2026-09-06"),
        ("2026-08-31", "2026-09-06"),
        ("2026-08-08", "2026-09-06"),
    ]

    router = APIRouter()
    monkeypatch.setattr(
        monitor._legacy,
        "_utcnow",
        lambda: datetime(2026, 9, 6, 12, tzinfo=timezone.utc),
    )
    monitor.attach_campaign_ai_routes(router, db, lambda: None, lambda user: user)
    latest = next(route.endpoint for route in router.routes if route.path == "/ai-monitor/latest")
    response = await latest(user={"id": "u1"})
    assert response["monthly_profit_goal"]["evidence"]["valid"] is True
    assert response["monthly_profit_goal"]["month_to_date"] == inserted["monthly_profit_goal"]["month_to_date"]
    assert response["monthly_profit_goal"]["net_profit_to_date_sar"] == inserted["monthly_profit_goal"]["net_profit_to_date_sar"]
    assert db[goal.COLLECTION].index_calls == 1  # monitor load only; latest creates no index
    assert goal.current_goal_context() is None


@pytest.mark.asyncio
async def test_calculation_failure_survives_snapshot_latest_and_put_without_exposing_raw_error(monkeypatch):
    install_empty_monitor_sources(monkeypatch)
    fixed_now = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(goal, "_now", lambda: fixed_now.isoformat())
    monkeypatch.setattr(goal, "_utcnow", lambda: fixed_now)
    monkeypatch.setattr(monitor._legacy, "_utcnow", lambda: fixed_now)
    db = MemoryDB({goal.COLLECTION: MemoryCollection([goal_row()])})

    async def loader(**_kwargs):
        raise RuntimeError("raw-provider-customer-detail-must-not-survive")

    result = await monitor.run_campaign_ai_monitor(
        db,
        "u1",
        now=lambda: fixed_now,
        refresh_meta=False,
        business_context_loader=loader,
    )
    snapshots = db[monitor.RECOMMENDATION_COLLECTION]
    assert len(snapshots.insert_history) == 1
    inserted = snapshots.insert_history[0]
    stored_goal = inserted["monthly_profit_goal"]
    expected_reason = "month_to_date_profit_failed:RuntimeError"
    assert stored_goal["progress_state"] == "calculation_failed"
    assert stored_goal["progress_unavailable_reason"] == expected_reason
    assert stored_goal["calculated_at"] is None
    assert stored_goal["calculation_diagnostic"] == {
        "state": "failed",
        "reason": expected_reason,
        "attempted_at": fixed_now.isoformat(),
    }
    assert "raw-provider-customer-detail" not in json.dumps(inserted)
    assert result["monthly_profit_goal"] == stored_goal

    router = APIRouter()
    goal.attach_monthly_profit_goal_routes(router, db, lambda: None, lambda user: user)
    monitor.attach_campaign_ai_routes(router, db, lambda: None, lambda user: user)
    latest = next(route.endpoint for route in router.routes if route.path == "/ai-monitor/latest")
    put = next(
        route.endpoint for route in router.routes
        if route.path == "/ai-monitor/monthly-profit-goal" and "PUT" in route.methods
    )

    latest_goal = (await latest(user={"id": "u1"}))["monthly_profit_goal"]
    saved_goal = await put(
        goal.MonthlyProfitGoalInput(minimum_net_profit_sar=125_000.0),
        user={"id": "u1"},
    )
    for display in (latest_goal, saved_goal):
        assert display["progress_state"] == "calculation_failed"
        assert display["progress_unavailable_reason"] == expected_reason
        assert display["progress_available"] is False
        assert display["net_profit_to_date_sar"] is None
        assert display["remaining_to_target_sar"] is None
        assert display["required_daily_net_profit_sar"] is None
        assert display["projected_month_end_net_profit_sar"] is None
        assert display["scale_execution_allowed_by_profit_accounting"] is False
        assert display["calculation_diagnostic"]["reason"] == expected_reason
        assert display["calculation_diagnostic"]["freshness_status"] == "fresh"
        assert display["calculation_diagnostic"]["current_period"] is True
        assert display["evidence"]["valid"] is False
        assert display["evidence"]["validation_reason"] == (
            "monthly_goal_snapshot_calculated_at_missing_or_invalid"
        )
    assert saved_goal["goal_config_saved"] is True
    assert saved_goal["minimum_net_profit_sar"] == 125_000.0
    assert snapshots.update_history == []


def test_old_other_month_and_corrupt_failure_claims_remain_blocked_but_safe_diagnostic_survives():
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-07T07:00:00+00:00",
    })
    failed = goal._derive_goal_progress(
        goal=current,
        month_to_date={
            "available": False,
            "reason": "month_to_date_profit_failed:RuntimeError",
            "from": "2026-09-01",
            "to": "2026-09-07",
            "timezone": "Asia/Riyadh",
            "calculated_at": None,
            "calculation_attempted_at": "2026-09-06T00:00:00+00:00",
            "data_through": None,
            "data_through_status": "source_watermark_unavailable",
        },
        end=date(2026, 9, 7),
    )
    prior_output = deepcopy(failed)
    prior_output.pop("calculation_diagnostic")
    prior_output["calculation_attempted_at"] = "2026-09-07T10:00:00+00:00"
    prior_output["month_to_date"]["calculation_attempted_at"] = (
        "2026-09-07T10:00:00+00:00"
    )
    compatible = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=prior_output,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-07T13:00:00+00:00",
    )
    assert compatible["progress_state"] == "calculation_failed"
    assert compatible["progress_unavailable_reason"] == (
        "month_to_date_profit_failed:RuntimeError"
    )
    assert compatible["calculation_diagnostic"]["attempted_at"] == (
        "2026-09-07T10:00:00+00:00"
    )
    assert compatible["evidence"]["valid"] is False

    old = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=failed,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-08T13:00:00+00:00",
    )
    assert old["progress_available"] is False
    assert old["progress_state"] == "stale"
    assert old["calculation_diagnostic"]["reason"] == "month_to_date_profit_failed:RuntimeError"
    assert old["calculation_diagnostic"]["freshness_status"] == "stale"

    other_month = deepcopy(failed)
    other_month["month_to_date"].update({"from": "2026-08-01", "to": "2026-08-31"})
    other_month["period"].update({
        "month": "2026-08",
        "from": "2026-08-01",
        "to_requested": "2026-08-31",
    })
    other = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=other_month,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-08T13:00:00+00:00",
    )
    assert other["progress_available"] is False
    assert other["progress_state"] == "stale"
    assert other["calculation_diagnostic"]["reason"] == "month_to_date_profit_failed:RuntimeError"
    assert other["calculation_diagnostic"]["current_period"] is False

    corrupt = deepcopy(failed)
    corrupt["progress_available"] = True
    corrupt["net_profit_to_date_sar"] = 99_999.0
    corrupt["scale_execution_allowed_by_profit_accounting"] = True
    corrupt["calculation_diagnostic"] = {
        "state": "failed",
        "reason": "raw provider secret",
        "attempted_at": "2026-09-07T10:00:00+00:00",
    }
    blocked = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=corrupt,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-07T13:00:00+00:00",
    )
    assert blocked["progress_available"] is False
    assert blocked["progress_state"] == "stale"
    assert blocked["net_profit_to_date_sar"] is None
    assert blocked["scale_execution_allowed_by_profit_accounting"] is False
    assert blocked["calculation_diagnostic"] is None


@pytest.mark.asyncio
async def test_window_failure_keeps_mtd_goal_but_marks_analysis_failed(monkeypatch):
    install_empty_monitor_sources(monkeypatch)
    db = MemoryDB({goal.COLLECTION: MemoryCollection([goal_row()])})
    calls = []

    async def loader(**kwargs):
        period = (kwargs["from_date"], kwargs["to_date"])
        calls.append(period)
        if period == ("2026-08-31", "2026-09-06"):
            raise RuntimeError("synthetic_seven_day_failure")
        return complete_payload()

    result = await monitor.run_campaign_ai_monitor(
        db,
        "u1",
        now=lambda: datetime(2026, 9, 6, 12, tzinfo=timezone.utc),
        refresh_meta=False,
        business_context_loader=loader,
    )

    assert calls[0] == ("2026-09-01", "2026-09-06")
    assert result["monthly_profit_goal"]["progress_available"] is True
    assert result["business_profit_context_available"] is False
    assert result["business_profit_context_status"] == "failed"
    assert "mezan_business_profit" in result["limitations"]
    assert result["recommendations"] == []


@pytest.mark.asyncio
async def test_goal_wrapper_clears_prior_context_on_cancellation(monkeypatch):
    db = MemoryDB({goal.COLLECTION: MemoryCollection([goal_row()])})

    async def loader(**_kwargs):
        return complete_payload()

    async def cancelled_base(*_args, **_kwargs):
        raise asyncio.CancelledError()

    wrapped = goal.wrap_business_profit_context(
        cancelled_base,
        lambda: SimpleNamespace(db=db),
    )
    goal._CURRENT_GOAL_CONTEXT.set({"cycle": "stale"})
    with pytest.raises(asyncio.CancelledError):
        await wrapped(loader, "u1", date(2026, 9, 6))
    assert goal.current_goal_context() is None


@pytest.mark.asyncio
async def test_snapshot_insert_failure_is_not_reported_as_success_and_clears_context(monkeypatch):
    install_empty_monitor_sources(monkeypatch)
    db = MemoryDB({
        goal.COLLECTION: MemoryCollection([goal_row()]),
        monitor.RECOMMENDATION_COLLECTION: MemoryCollection(fail_insert=True),
    })

    async def loader(**_kwargs):
        return complete_payload()

    with pytest.raises(RuntimeError, match="synthetic_snapshot_insert_failed"):
        await monitor.run_campaign_ai_monitor(
            db,
            "u1",
            now=lambda: datetime(2026, 9, 6, 12, tzinfo=timezone.utc),
            refresh_meta=False,
            business_context_loader=loader,
        )
    assert goal.current_goal_context() is None
    run_updates = db[monitor._policy.RUN_COLLECTION].update_history
    assert any(update[1]["$set"].get("status") == "failed" for update in run_updates)


@pytest.mark.asyncio
async def test_goal_save_requires_a_confirmed_match_or_upsert():
    db = MemoryDB({goal.COLLECTION: UnpersistedGoalCollection()})
    with pytest.raises(RuntimeError, match="monthly_profit_goal_save_not_persisted"):
        await goal.save_goal(
            db,
            "u1",
            goal.MonthlyProfitGoalInput(minimum_net_profit_sar=100_000.0),
        )


@pytest.mark.asyncio
async def test_interleaved_users_and_cycles_do_not_leak_goal_context(monkeypatch):
    rows = [
        goal_row(100_000.0),
        {**goal_row(200_000.0), "user_id": "u2"},
    ]
    db = MemoryDB({goal.COLLECTION: MemoryCollection(rows)})

    async def loader(**kwargs):
        await asyncio.sleep(0)
        profit = 11_000.0 if kwargs["user"]["id"] == "u1" else 22_000.0
        return complete_payload(profit)

    observed_context = {}

    async def base(_loader, user_id, _end):
        observed_context[user_id] = goal.current_goal_context()
        await asyncio.sleep(0)
        if user_id == "u2":
            raise RuntimeError("synthetic_business_window_failure")
        return {"available": True}

    wrapped = goal.wrap_business_profit_context(base, lambda: SimpleNamespace(db=db))

    async def run(user_id):
        result = await wrapped(loader, user_id, date(2026, 9, 6))
        return result, goal.current_goal_context()

    first, second = await asyncio.gather(run("u1"), run("u2"))
    assert observed_context["u1"]["minimum_net_profit_sar"] == 100_000.0
    assert observed_context["u1"]["net_profit_to_date_sar"] == 11_000.0
    assert observed_context["u2"]["minimum_net_profit_sar"] == 200_000.0
    assert observed_context["u2"]["net_profit_to_date_sar"] == 22_000.0
    assert second[0]["analysis_status"] == "failed"
    assert first[1] is None and second[1] is None


@pytest.mark.asyncio
async def test_invalid_stored_goal_config_fails_closed_without_default_target():
    db = MemoryDB({goal.COLLECTION: MemoryCollection([{
        "user_id": "u1",
        "minimum_net_profit_sar": None,
        "updated_at": "2026-09-01T00:00:00+00:00",
    }])})
    with pytest.raises(RuntimeError, match="monthly_profit_goal_config_invalid"):
        await goal.load_goal(db, "u1", ensure_index=False)


def test_display_rederives_current_target_without_refreshing_profit_evidence():
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-06T10:00:00+00:00",
    })
    stored = goal._derive_goal_progress(
        goal=goal.with_goal_config_identity({
            "minimum_net_profit_sar": 80_000.0,
            "configured": True,
            "source": "owner_configured",
            "updated_at": "2026-09-01T10:00:00+00:00",
        }),
        month_to_date={
            "available": True,
            "from": "2026-09-01",
            "to": "2026-09-07",
            "timezone": "Asia/Riyadh",
            "calculated_at": "2026-09-07T08:00:00+00:00",
            "data_through": "2026-09-05",
            "data_through_status": "source_watermark",
            "net_profit": 50_000.0,
        },
        end=date(2026, 9, 7),
    )
    stored["provenance"] = {
        "run_id": "run-old",
        "snapshot_id": "snapshot-old",
        "snapshot_generated_at": "2026-09-06T08:01:00+00:00",
    }

    display = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=json.loads(json.dumps(stored)),
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-07T13:00:00+00:00",
    )

    assert display["minimum_net_profit_sar"] == 100_000.0
    assert display["net_profit_to_date_sar"] == 50_000.0
    assert display["remaining_to_target_sar"] == 50_000.0
    assert display["progress_state"] == "config_mismatch"
    assert display["calculated_at"] == "2026-09-07T08:00:00+00:00"
    assert display["data_through"] == "2026-09-05"
    assert display["provenance"]["snapshot_id"] == "snapshot-old"
    assert display["historical_recommendation_authority_renewed"] is False


def test_previous_month_and_missing_snapshot_are_explicit_and_do_not_turn_null_to_zero():
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-10-01T00:00:00+00:00",
    })
    old = {
        "month": "2026-09",
        "minimum_net_profit_sar": 80_000.0,
        "progress_available": True,
        "net_profit_to_date_sar": 70_000.0,
    }
    stale = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=old,
        current_month=date(2026, 10, 1),
    )
    missing = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=None,
        current_month=date(2026, 10, 1),
    )
    assert stale["progress_state"] == "stale"
    assert stale["progress_available"] is False
    assert stale["net_profit_to_date_sar"] is None
    assert stale["minimum_net_profit_sar"] == 100_000.0
    assert missing["progress_state"] == "missing"
    assert missing["progress_unavailable_reason"] == "monthly_goal_snapshot_missing"


def test_same_month_snapshot_past_next_run_is_explicitly_stale():
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-01T00:00:00+00:00",
    })
    snapshot = valid_snapshot_goal(current)
    stale = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=snapshot,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-07T09:00:00+00:00",
    )
    assert stale["progress_state"] == "stale"
    assert stale["progress_unavailable_reason"] == "monthly_goal_snapshot_past_next_run_at"
    assert stale["net_profit_to_date_sar"] is None


@pytest.mark.parametrize(
    ("next_run_at", "reason"),
    [
        (None, "monthly_goal_snapshot_next_run_at_missing"),
        ("not-a-timestamp", "monthly_goal_snapshot_next_run_at_invalid"),
    ],
)
def test_same_config_never_treats_missing_or_invalid_next_run_as_fresh(
    next_run_at,
    reason,
):
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-01T00:00:00+00:00",
    })
    snapshot = valid_snapshot_goal(
        current,
        calculated_at="2026-09-01T08:00:00+00:00",
        evidence_end=date(2026, 9, 1),
        data_through=None,
    )

    display = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=snapshot,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
        snapshot_next_run_at=next_run_at,
    )

    assert display["progress_available"] is False
    assert display["progress_state"] == "stale"
    assert display["progress_unavailable_reason"] == reason
    assert display["calculated_at"] == "2026-09-01T08:00:00+00:00"
    assert display["data_through"] is None
    assert display["data_through_status"] == "source_watermark_unavailable"
    assert display["evidence"]["valid"] is False


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            {"from": "2026-08-01", "to": "2026-08-31"},
            "monthly_goal_snapshot_mtd_month_mismatch",
        ),
        (
            {"from": "2026-09-02"},
            "monthly_goal_snapshot_mtd_start_not_month_start",
        ),
        (
            {"timezone": "UTC"},
            "monthly_goal_snapshot_mtd_timezone_mismatch",
        ),
    ],
)
def test_fast_path_rejects_invalid_internal_calendar_mtd(mutation, reason):
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-07T07:00:00+00:00",
    })
    snapshot = valid_snapshot_goal(current)
    snapshot["month_to_date"].update(mutation)

    display = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=snapshot,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-07T13:00:00+00:00",
    )

    assert display["progress_available"] is False
    assert display["progress_state"] == "stale"
    assert display["progress_unavailable_reason"] == reason
    assert display["evidence"]["valid"] is False


@pytest.mark.parametrize("calculated_at", [None, "invalid", "2026-09-07T08:00:00"])
def test_missing_invalid_or_naive_calculated_at_is_not_fresh(calculated_at):
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-07T07:00:00+00:00",
    })
    snapshot = valid_snapshot_goal(current)
    snapshot["calculated_at"] = calculated_at
    snapshot["month_to_date"]["calculated_at"] = calculated_at
    display = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=snapshot,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-07T13:00:00+00:00",
    )
    assert display["progress_available"] is False
    assert display["progress_unavailable_reason"] == (
        "monthly_goal_snapshot_calculated_at_missing_or_invalid"
    )
    assert display["evidence"]["calculated_at"] == calculated_at


@pytest.mark.parametrize(
    ("mutation", "reason"),
    [
        (
            {"mtd_to": "2026-09-06"},
            "monthly_goal_snapshot_mtd_end_not_current_date",
        ),
        (
            {"period_from": "2026-09-02"},
            "monthly_goal_snapshot_period_contract_mismatch",
        ),
    ],
)
def test_requested_period_must_match_current_calendar_mtd(mutation, reason):
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-07T07:00:00+00:00",
    })
    snapshot = valid_snapshot_goal(current)
    if "mtd_to" in mutation:
        snapshot["month_to_date"]["to"] = mutation["mtd_to"]
        snapshot["period"]["to_requested"] = mutation["mtd_to"]
    if "period_from" in mutation:
        snapshot["period"]["from"] = mutation["period_from"]
    display = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=snapshot,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-07T13:00:00+00:00",
    )
    assert display["progress_available"] is False
    assert display["progress_unavailable_reason"] == reason


def test_calculated_at_age_is_bounded_independently_of_next_run():
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-01T00:00:00+00:00",
    })
    snapshot = valid_snapshot_goal(
        current,
        calculated_at="2026-09-01T08:00:00+00:00",
        evidence_end=date(2026, 9, 1),
    )

    display = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=snapshot,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-08T12:00:00+00:00",
    )

    assert display["progress_available"] is False
    assert display["progress_unavailable_reason"] == "monthly_goal_snapshot_calculated_at_too_old"
    assert display["evidence"]["age_seconds"] == 6 * 24 * 60 * 60 + 4 * 60 * 60
    assert display["evidence"]["max_age_seconds"] == goal.GOAL_EVIDENCE_MAX_AGE_SECONDS


def test_valid_current_evidence_supports_fast_path_and_newer_goal_rederivation():
    stored_config = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 80_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-07T06:00:00+00:00",
    })
    snapshot = valid_snapshot_goal(stored_config, data_through=None)
    original = deepcopy(snapshot)
    common = {
        "snapshot_goal": snapshot,
        "current_month": date(2026, 9, 7),
        "current_time": datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        "snapshot_next_run_at": "2026-09-07T13:00:00+00:00",
    }

    direct = goal.reconcile_goal_for_display(current_goal=stored_config, **common)
    newer_config = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-07T09:00:00+00:00",
    })
    rederived = goal.reconcile_goal_for_display(current_goal=newer_config, **common)

    assert direct["progress_available"] is True
    assert direct["evidence"]["valid"] is True
    assert direct["evidence"]["age_seconds"] == 2 * 60 * 60
    assert direct["data_through"] is None
    assert direct["data_through_status"] == "source_watermark_unavailable"
    assert rederived["progress_available"] is True
    assert rederived["progress_state"] == "config_mismatch"
    assert rederived["minimum_net_profit_sar"] == 100_000.0
    assert rederived["remaining_to_target_sar"] == 60_000.0
    assert rederived["evidence"]["valid"] is True
    assert snapshot == original


def test_legacy_snapshot_without_evidence_metadata_is_not_upgraded_on_read():
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-07T07:00:00+00:00",
    })
    legacy = {
        **current,
        "month": "2026-09",
        "progress_available": True,
        "net_profit_to_date_sar": 40_000.0,
    }
    display = goal.reconcile_goal_for_display(
        current_goal=current,
        snapshot_goal=legacy,
        current_month=date(2026, 9, 7),
        current_time=datetime(2026, 9, 7, 10, tzinfo=timezone.utc),
        snapshot_next_run_at="2026-09-07T13:00:00+00:00",
    )
    assert display["progress_available"] is False
    assert display["progress_state"] == "stale"
    assert display["progress_unavailable_reason"] == "monthly_goal_snapshot_contract_missing_or_unsupported"
    assert display["evidence"]["calculated_at"] is None
    assert display["evidence"]["period"] is None


@pytest.mark.asyncio
async def test_save_goal_rederives_from_snapshot_without_financial_recalculation(monkeypatch):
    monkeypatch.setattr(
        goal,
        "_utcnow",
        lambda: datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
    )
    old_config = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 80_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-01T00:00:00+00:00",
    })
    stored_goal = goal._derive_goal_progress(
        goal=old_config,
        month_to_date={
            "available": True,
            "from": "2026-09-01",
            "to": "2026-09-07",
            "timezone": "Asia/Riyadh",
            "calculated_at": "2026-09-07T08:00:00+00:00",
            "data_through": None,
            "data_through_status": "source_watermark_unavailable",
            "net_profit": 40_000.0,
        },
        end=date(2026, 9, 7),
    )
    snapshot = {
        "user_id": "u1",
        "snapshot_id": "snapshot-old",
        "generated_at": "2026-09-06T08:01:00+00:00",
        "next_run_at": "2026-09-07T13:00:00+00:00",
        "monthly_profit_goal": deepcopy(stored_goal),
    }
    db = MemoryDB({
        goal.COLLECTION: MemoryCollection([goal_row(80_000.0, "2026-09-01T00:00:00+00:00")]),
        monitor.RECOMMENDATION_COLLECTION: MemoryCollection([snapshot]),
    })
    router = APIRouter()
    goal.attach_monthly_profit_goal_routes(router, db, lambda: None, lambda user: user)
    put = next(
        route.endpoint for route in router.routes
        if route.path == "/ai-monitor/monthly-profit-goal" and "PUT" in route.methods
    )
    response = await put(goal.MonthlyProfitGoalInput(minimum_net_profit_sar=100_000.0), user={"id": "u1"})

    assert response["minimum_net_profit_sar"] == 100_000.0
    assert response["net_profit_to_date_sar"] == 40_000.0
    assert response["remaining_to_target_sar"] == 60_000.0
    assert response["calculated_at"] == "2026-09-07T08:00:00+00:00"
    assert response["progress_state"] == "config_mismatch"
    assert db[monitor.RECOMMENDATION_COLLECTION].rows[0] == snapshot


@pytest.mark.asyncio
async def test_successful_goal_save_is_reported_when_snapshot_read_fails(monkeypatch):
    monkeypatch.setattr(
        goal,
        "_utcnow",
        lambda: datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
    )
    db = MemoryDB({
        goal.COLLECTION: MemoryCollection(),
        monitor.RECOMMENDATION_COLLECTION: FailingReadCollection(),
    })
    router = APIRouter()
    goal.attach_monthly_profit_goal_routes(router, db, lambda: None, lambda user: user)
    put = next(
        route.endpoint for route in router.routes
        if route.path == "/ai-monitor/monthly-profit-goal" and "PUT" in route.methods
    )

    response = await put(
        goal.MonthlyProfitGoalInput(minimum_net_profit_sar=125_000.0),
        user={"id": "u1"},
    )

    assert response["goal_config_saved"] is True
    assert response["goal_config_save_status"] == "saved"
    assert response["minimum_net_profit_sar"] == 125_000.0
    assert response["progress_available"] is False
    assert response["progress_state"] == "calculation_failed"
    assert response["progress_unavailable_reason"] == (
        "snapshot_read_failed_after_goal_save:RuntimeError"
    )
    assert db[goal.COLLECTION].rows[0]["minimum_net_profit_sar"] == 125_000.0


@pytest.mark.asyncio
async def test_latest_goal_config_read_failure_does_not_fall_back_to_default(monkeypatch):
    monkeypatch.setattr(
        monitor._legacy,
        "_utcnow",
        lambda: datetime(2026, 9, 7, 12, tzinfo=timezone.utc),
    )
    db = MemoryDB({
        goal.COLLECTION: FailingReadCollection(),
        monitor.RECOMMENDATION_COLLECTION: MemoryCollection([{
            "user_id": "u1",
            "snapshot_id": "snapshot-old",
            "generated_at": "2026-09-07T10:00:00+00:00",
            "monthly_profit_goal": {
                "month": "2026-09",
                "minimum_net_profit_sar": 100_000.0,
                "progress_available": True,
                "net_profit_to_date_sar": 50_000.0,
            },
        }]),
    })
    router = APIRouter()
    monitor.attach_campaign_ai_routes(router, db, lambda: None, lambda user: user)
    latest = next(route.endpoint for route in router.routes if route.path == "/ai-monitor/latest")
    response = await latest(user={"id": "u1"})
    display = response["monthly_profit_goal"]
    assert display["minimum_net_profit_sar"] is None
    assert display["progress_state"] == "calculation_failed"
    assert display["progress_unavailable_reason"] == "goal_config_read_failed:RuntimeError"


@pytest.mark.asyncio
async def test_salla_only_and_incomplete_advertising_flow_through_actual_goal(monkeypatch):
    import mezan_campaign_profit_loader
    from tests import test_profit_cost_financial_completeness as cost_fixture

    order = cost_fixture._order({
        "product_id": "p-salla",
        "quantity": 2,
        "price": 100.0,
        "total": 200.0,
    })
    db = cost_fixture._install_isolated_profit_io(
        monkeypatch,
        orders=[order],
        products=[cost_fixture._product("p-salla", salla_cost=30.0)],
    )
    loader = mezan_campaign_profit_loader.make_mezan_campaign_profit_loader(db)
    mtd = await goal._month_to_date_totals(loader, "u1", date(2026, 9, 1))
    result = goal._derive_goal_progress(
        goal=goal.with_goal_config_identity({
            "minimum_net_profit_sar": 100_000.0,
            "configured": True,
            "source": "owner_configured",
            "updated_at": "2026-09-01T00:00:00+00:00",
        }),
        month_to_date=mtd,
        end=date(2026, 9, 1),
    )
    assert mtd["total_product_cost"] == 60.0
    assert mtd["net_profit"] == 120.0
    assert result["progress_available"] is True

    incomplete_db = cost_fixture._install_isolated_profit_io(
        monkeypatch,
        orders=[order],
        products=[cost_fixture._product("p-salla", salla_cost=30.0)],
        ads={
            "total": 20.0,
            "spend_quality": {"amount_complete": False},
            "source_contract": {"source": "isolated_fixture"},
        },
    )
    incomplete_loader = mezan_campaign_profit_loader.make_mezan_campaign_profit_loader(incomplete_db)
    incomplete_mtd = await goal._month_to_date_totals(
        incomplete_loader, "u1", date(2026, 9, 1)
    )
    incomplete = goal._derive_goal_progress(
        goal=result,
        month_to_date=incomplete_mtd,
        end=date(2026, 9, 1),
    )
    assert incomplete_mtd["net_profit"] is None
    assert incomplete["progress_available"] is False
    assert incomplete["progress_state"] == "quality_incomplete"
    assert "unknown_component:advertising" in incomplete["progress_unavailable_reason"]
    assert incomplete["scale_execution_allowed_by_profit_accounting"] is False


@pytest.mark.parametrize(
    ("net_profit", "status", "remaining"),
    [
        (0.0, "behind_target", 100_000.0),
        (-5_000.0, "behind_target", 105_000.0),
        (100_000.0, "minimum_target_covered", 0.0),
        (120_000.0, "minimum_target_covered", 0.0),
    ],
)
def test_zero_loss_reached_and_exceeded_remain_numeric(net_profit, status, remaining):
    result = goal._derive_goal_progress(
        goal=goal.with_goal_config_identity({
            "minimum_net_profit_sar": 100_000.0,
            "configured": True,
            "source": "owner_configured",
            "updated_at": "2026-09-01T00:00:00+00:00",
        }),
        month_to_date={
            "available": True,
            "net_profit": net_profit,
            "profit_accounting": {
                "known": True,
                "complete": True,
                "scale_safe": True,
            },
        },
        end=date(2026, 9, 7),
    )
    assert result["progress_available"] is True
    assert result["net_profit_to_date_sar"] == net_profit
    assert result["status"] == status
    assert result["remaining_to_target_sar"] == remaining


def test_numeric_profit_with_incomplete_quality_is_visible_but_never_scale_safe():
    result = goal._derive_goal_progress(
        goal=goal.with_goal_config_identity({
            "minimum_net_profit_sar": 100_000.0,
            "configured": True,
            "source": "owner_configured",
            "updated_at": "2026-09-01T00:00:00+00:00",
        }),
        month_to_date={
            "available": True,
            "net_profit": -500.0,
            "profit_accounting": {
                "known": True,
                "complete": False,
                "scale_safe": False,
                "issues": ["unknown_component:advertising"],
            },
        },
        end=date(2026, 9, 7),
    )
    assert result["progress_available"] is True
    assert result["net_profit_to_date_sar"] == -500.0
    assert result["progress_state"] == "quality_incomplete"
    assert result["status"] == "profit_accounting_incomplete"
    assert result["scale_execution_allowed_by_profit_accounting"] is False


def test_legacy_and_new_goal_contracts_survive_json_round_trip():
    current = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 100_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-01T00:00:00+00:00",
    })
    legacy = goal._derive_goal_progress(
        goal=current,
        month_to_date={
            "net_profit": 10_000.0,
            "missing_product_cost_count": 0,
            "incomplete_profit_orders_count": 0,
        },
        end=date(2026, 9, 2),
    )
    modern = goal._derive_goal_progress(
        goal=current,
        month_to_date={
            "net_profit": 10_000.0,
            "profit_accounting": {
                "known": True,
                "complete": True,
                "scale_safe": True,
                "missing_product_cost_count": 0,
                "incomplete_profit_orders_count": 0,
            },
        },
        end=date(2026, 9, 2),
    )
    assert json.loads(json.dumps(legacy)) == legacy
    assert json.loads(json.dumps(modern)) == modern


@pytest.mark.asyncio
async def test_http_save_then_latest_rederives_without_mutating_stored_snapshot(monkeypatch):
    fixed_now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(goal, "_utcnow", lambda: fixed_now)
    monkeypatch.setattr(monitor._legacy, "_utcnow", lambda: fixed_now)
    old_config = goal.with_goal_config_identity({
        "minimum_net_profit_sar": 80_000.0,
        "configured": True,
        "source": "owner_configured",
        "updated_at": "2026-09-01T00:00:00+00:00",
    })
    stored_goal = goal.with_snapshot_provenance(
        goal._derive_goal_progress(
            goal=old_config,
            month_to_date={
                "available": True,
                "from": "2026-09-01",
                "to": "2026-09-07",
                "timezone": "Asia/Riyadh",
                "calculated_at": "2026-09-07T08:00:00+00:00",
                "data_through": "2026-09-05",
                "data_through_status": "source_watermark",
                "net_profit": 40_000.0,
            },
            end=date(2026, 9, 7),
        ),
        run_id="run-old",
        snapshot_id="snapshot-old",
        snapshot_generated_at="2026-09-06T08:01:00+00:00",
    )
    stored_snapshot = {
        "user_id": "u1",
        "snapshot_id": "snapshot-old",
        "generated_at": "2026-09-06T08:01:00+00:00",
        "next_run_at": "2026-09-07T13:00:00+00:00",
        "monthly_profit_goal": stored_goal,
        "recommendations": [],
    }
    db = MemoryDB({
        goal.COLLECTION: MemoryCollection([goal_row(80_000.0, "2026-09-01T00:00:00+00:00")]),
        monitor.RECOMMENDATION_COLLECTION: MemoryCollection([stored_snapshot]),
    })

    async def current_user():
        return {"id": "u1"}

    router = APIRouter(prefix="/ads-manager")
    goal.attach_monthly_profit_goal_routes(router, db, current_user, lambda user: user)
    monitor.attach_campaign_ai_routes(router, db, current_user, lambda user: user)
    app = FastAPI()
    app.include_router(router)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://isolated.test",
    ) as client:
        saved = await client.put(
            "/ads-manager/ai-monitor/monthly-profit-goal",
            json={"minimum_net_profit_sar": 100_000.0},
        )
        latest = await client.get("/ads-manager/ai-monitor/latest")

    assert saved.status_code == 200
    assert latest.status_code == 200
    assert saved.json()["net_profit_to_date_sar"] == 40_000.0
    assert saved.json()["minimum_net_profit_sar"] == 100_000.0
    assert latest.json()["monthly_profit_goal"]["net_profit_to_date_sar"] == 40_000.0
    assert latest.json()["monthly_profit_goal"]["minimum_net_profit_sar"] == 100_000.0
    assert latest.json()["monthly_profit_goal"]["calculated_at"] == "2026-09-07T08:00:00+00:00"
    assert db[monitor.RECOMMENDATION_COLLECTION].rows[0] == stored_snapshot
