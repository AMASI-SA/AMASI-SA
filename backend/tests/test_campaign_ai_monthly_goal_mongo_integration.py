from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from datetime import date, datetime, timezone
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import monitoring

import campaign_ai_monthly_profit_goal_v1 as goal
import campaign_ai_monitor as monitor
import dashboard_v2_routes as dashboard
import mezan_campaign_profit_loader
import mezan_profit_engine as profit_engine
from tests import test_campaign_ai_monthly_goal_snapshot_reliability as reliability
from tests import test_profit_cost_financial_completeness as cost_fixture


class MongoCommands(monitoring.CommandListener):
    def __init__(self):
        self.started_commands: list[tuple[str, dict]] = []

    def started(self, event):
        self.started_commands.append((event.command_name, dict(event.command)))

    def succeeded(self, _event):
        return None

    def failed(self, _event):
        return None


@pytest_asyncio.fixture
async def mongo_db():
    url = os.environ.get("GOAL_PROGRESS_TEST_MONGO_URL")
    required = os.environ.get("REQUIRE_GOAL_PROGRESS_MONGO") == "1"
    if not url:
        if required:
            pytest.fail("GOAL_PROGRESS_TEST_MONGO_URL is required")
        pytest.skip("temporary localhost Mongo is not configured")
    parsed = urlsplit(url)
    if parsed.hostname not in {"127.0.0.1", "localhost"}:
        pytest.fail("goal-progress integration permits localhost Mongo only")
    commands = MongoCommands()
    client = AsyncIOMotorClient(
        url,
        serverSelectionTimeoutMS=2_000,
        event_listeners=[commands],
    )
    try:
        await client.admin.command("ping")
    except Exception as exc:
        client.close()
        if required:
            pytest.fail(f"required temporary Mongo unavailable: {type(exc).__name__}")
        pytest.skip(f"temporary localhost Mongo unavailable: {type(exc).__name__}")
    database_name = f"goal_progress_1b_{uuid.uuid4().hex}"
    try:
        yield client[database_name], commands
    finally:
        await client.drop_database(database_name)
        client.close()


def app_for(db, user_id="u1"):
    async def current_user():
        return {"id": user_id}

    router = APIRouter(prefix="/ads-manager")
    goal.attach_monthly_profit_goal_routes(router, db, current_user, lambda user: user)
    monitor.attach_campaign_ai_routes(router, db, current_user, lambda user: user)
    app = FastAPI()
    app.include_router(router)
    return app


def current_config(target=80_000.0, updated_at="2026-09-07T06:00:00+00:00"):
    return goal.with_goal_config_identity({
        "minimum_net_profit_sar": target,
        "configured": True,
        "source": "owner_configured",
        "updated_at": updated_at,
    })


@pytest.mark.asyncio
async def test_real_mongo_initial_snapshot_latest_and_rederive_are_single_write(
    mongo_db,
    monkeypatch,
):
    db, commands = mongo_db
    fixed_now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(goal, "_now", lambda: fixed_now.isoformat())
    monkeypatch.setattr(goal, "_utcnow", lambda: fixed_now)
    monkeypatch.setattr(monitor._legacy, "_utcnow", lambda: fixed_now)
    reliability.install_empty_monitor_sources(monkeypatch)
    await db[goal.COLLECTION].insert_one({
        "user_id": "u1",
        "minimum_net_profit_sar": 80_000.0,
        "updated_at": "2026-09-07T06:00:00+00:00",
    })
    loader_calls = []

    async def loader(**kwargs):
        loader_calls.append((kwargs["from_date"], kwargs["to_date"]))
        return reliability.complete_payload(data_through="2026-09-06")

    result = await monitor.run_campaign_ai_monitor(
        db,
        "u1",
        now=lambda: fixed_now,
        refresh_meta=False,
        business_context_loader=loader,
    )
    stored = await db[monitor.RECOMMENDATION_COLLECTION].find_one(
        {"user_id": "u1"},
        {"_id": 0},
    )
    stored_before_reads = deepcopy(stored)
    assert result["monthly_profit_goal"] == stored["monthly_profit_goal"]
    assert stored["monthly_profit_goal"]["net_profit_to_date_sar"] == 120.0
    assert loader_calls[0] == ("2026-09-01", "2026-09-07")
    assert len(loader_calls) == 5  # one goal MTD, then the established four analysis windows
    calls_after_monitor = list(loader_calls)

    async with AsyncClient(
        transport=ASGITransport(app=app_for(db)),
        base_url="http://isolated.test",
    ) as client:
        latest = await client.get("/ads-manager/ai-monitor/latest")
        saved = await client.put(
            "/ads-manager/ai-monitor/monthly-profit-goal",
            json={"minimum_net_profit_sar": 100_000.0},
        )

    assert latest.status_code == 200
    assert latest.json()["monthly_profit_goal"]["evidence"]["valid"] is True
    assert json.loads(json.dumps(latest.json())) == latest.json()
    assert saved.status_code == 200
    assert saved.json()["goal_config_saved"] is True
    assert saved.json()["progress_state"] == "config_mismatch"
    assert saved.json()["net_profit_to_date_sar"] == 120.0
    assert saved.json()["minimum_net_profit_sar"] == 100_000.0
    assert await db[monitor.RECOMMENDATION_COLLECTION].find_one(
        {"user_id": "u1"}, {"_id": 0}
    ) == stored_before_reads
    assert loader_calls == calls_after_monitor  # latest and PUT never rerun financial loads
    snapshot_updates = [
        command
        for name, command in commands.started_commands
        if name == "update" and command.get("update") == monitor.RECOMMENDATION_COLLECTION
    ]
    assert snapshot_updates == []


@pytest.mark.asyncio
async def test_real_mongo_calculation_failure_reason_survives_insert_latest_and_put(
    mongo_db,
    monkeypatch,
):
    db, commands = mongo_db
    fixed_now = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(goal, "_now", lambda: fixed_now.isoformat())
    monkeypatch.setattr(goal, "_utcnow", lambda: fixed_now)
    monkeypatch.setattr(monitor._legacy, "_utcnow", lambda: fixed_now)
    reliability.install_empty_monitor_sources(monkeypatch)
    await db[goal.COLLECTION].insert_one({
        "user_id": "u1",
        "minimum_net_profit_sar": 80_000.0,
        "updated_at": "2026-09-07T06:00:00+00:00",
    })

    async def loader(**_kwargs):
        raise RuntimeError("raw-provider-customer-detail-must-not-survive")

    await monitor.run_campaign_ai_monitor(
        db,
        "u1",
        now=lambda: fixed_now,
        refresh_meta=False,
        business_context_loader=loader,
    )
    stored = await db[monitor.RECOMMENDATION_COLLECTION].find_one(
        {"user_id": "u1"},
        {"_id": 0},
    )
    expected_reason = "month_to_date_profit_failed:RuntimeError"
    assert stored["monthly_profit_goal"]["calculation_diagnostic"] == {
        "state": "failed",
        "reason": expected_reason,
        "attempted_at": fixed_now.isoformat(),
    }
    assert "raw-provider-customer-detail" not in json.dumps(stored)

    async with AsyncClient(
        transport=ASGITransport(app=app_for(db)),
        base_url="http://isolated.test",
    ) as client:
        latest = await client.get("/ads-manager/ai-monitor/latest")
        saved = await client.put(
            "/ads-manager/ai-monitor/monthly-profit-goal",
            json={"minimum_net_profit_sar": 100_000.0},
        )

    for response in (latest, saved):
        assert response.status_code == 200
    latest_goal = latest.json()["monthly_profit_goal"]
    saved_goal = saved.json()
    for display in (latest_goal, saved_goal):
        assert display["progress_state"] == "calculation_failed"
        assert display["progress_unavailable_reason"] == expected_reason
        assert display["progress_available"] is False
        assert display["net_profit_to_date_sar"] is None
        assert display["scale_execution_allowed_by_profit_accounting"] is False
        assert display["calculation_diagnostic"]["reason"] == expected_reason
        assert display["calculation_diagnostic"]["freshness_status"] == "fresh"
        assert display["evidence"]["valid"] is False
    assert saved_goal["goal_config_saved"] is True
    snapshot_updates = [
        command
        for name, command in commands.started_commands
        if name == "update" and command.get("update") == monitor.RECOMMENDATION_COLLECTION
    ]
    assert snapshot_updates == []


@pytest.mark.parametrize(
    ("case", "expected_reason"),
    [
        ("missing_next", "monthly_goal_snapshot_next_run_at_missing"),
        ("invalid_next", "monthly_goal_snapshot_next_run_at_invalid"),
        ("august_mtd", "monthly_goal_snapshot_mtd_month_mismatch"),
        ("late_start", "monthly_goal_snapshot_mtd_start_not_month_start"),
        ("wrong_timezone", "monthly_goal_snapshot_mtd_timezone_mismatch"),
        ("valid", None),
    ],
)
@pytest.mark.asyncio
async def test_real_mongo_temporal_matrix_flows_through_latest_and_put(
    mongo_db,
    monkeypatch,
    case,
    expected_reason,
):
    db, _commands = mongo_db
    fixed_now = datetime(2026, 9, 7, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(goal, "_now", lambda: fixed_now.isoformat())
    monkeypatch.setattr(goal, "_utcnow", lambda: fixed_now)
    monkeypatch.setattr(monitor._legacy, "_utcnow", lambda: fixed_now)
    config = current_config()
    snapshot_goal = reliability.valid_snapshot_goal(config)
    next_run_at = "2026-09-07T13:00:00+00:00"
    if case == "missing_next":
        next_run_at = None
    elif case == "invalid_next":
        next_run_at = "invalid"
    elif case == "august_mtd":
        snapshot_goal["month_to_date"].update({
            "from": "2026-08-01",
            "to": "2026-08-31",
        })
    elif case == "late_start":
        snapshot_goal["month_to_date"]["from"] = "2026-09-02"
    elif case == "wrong_timezone":
        snapshot_goal["month_to_date"]["timezone"] = "UTC"
    await db[goal.COLLECTION].insert_one({
        "user_id": "u1",
        "minimum_net_profit_sar": 80_000.0,
        "updated_at": "2026-09-07T06:00:00+00:00",
    })
    document = {
        "user_id": "u1",
        "snapshot_id": f"snapshot-{case}",
        "generated_at": "2026-09-07T08:01:00+00:00",
        "recommendations": [],
        "monthly_profit_goal": snapshot_goal,
    }
    if next_run_at is not None:
        document["next_run_at"] = next_run_at
    await db[monitor.RECOMMENDATION_COLLECTION].insert_one(document)

    async with AsyncClient(
        transport=ASGITransport(app=app_for(db)),
        base_url="http://isolated.test",
    ) as client:
        latest = await client.get("/ads-manager/ai-monitor/latest")
        saved = await client.put(
            "/ads-manager/ai-monitor/monthly-profit-goal",
            json={"minimum_net_profit_sar": 100_000.0},
        )

    latest_goal = latest.json()["monthly_profit_goal"]
    saved_goal = saved.json()
    if expected_reason is None:
        assert latest_goal["progress_available"] is True
        assert latest_goal["evidence"]["valid"] is True
        assert saved_goal["progress_state"] == "config_mismatch"
        assert saved_goal["progress_available"] is True
    else:
        assert latest_goal["progress_available"] is False
        assert latest_goal["progress_unavailable_reason"] == expected_reason
        assert saved_goal["progress_available"] is False
        assert saved_goal["progress_unavailable_reason"] == expected_reason
    assert saved_goal["goal_config_saved"] is True


@pytest.mark.asyncio
async def test_real_mongo_snapshot_insert_failure_is_not_success(
    mongo_db,
    monkeypatch,
):
    db, _commands = mongo_db
    fixed_now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(goal, "_now", lambda: fixed_now.isoformat())
    reliability.install_empty_monitor_sources(monkeypatch)
    await db.create_collection(
        monitor.RECOMMENDATION_COLLECTION,
        validator={
            "$jsonSchema": {
                "bsonType": "object",
                "required": ["required_for_synthetic_failure"],
            },
        },
    )
    await db[goal.COLLECTION].insert_one({
        "user_id": "u1",
        "minimum_net_profit_sar": 100_000.0,
        "updated_at": "2026-09-07T06:00:00+00:00",
    })

    async def loader(**_kwargs):
        return reliability.complete_payload()

    with pytest.raises(Exception):
        await monitor.run_campaign_ai_monitor(
            db,
            "u1",
            now=lambda: fixed_now,
            refresh_meta=False,
            business_context_loader=loader,
        )
    run = await db[monitor._policy.RUN_COLLECTION].find_one({"user_id": "u1"})
    assert run["status"] == "failed"
    assert await db[monitor.RECOMMENDATION_COLLECTION].count_documents({}) == 0


class FailingSnapshotCollection:
    async def find_one(self, *_args, **_kwargs):
        raise RuntimeError("synthetic_snapshot_read_failed")


class SnapshotReadFailureDB:
    def __init__(self, actual):
        self.actual = actual

    def __getitem__(self, name):
        if name == monitor.RECOMMENDATION_COLLECTION:
            return FailingSnapshotCollection()
        return self.actual[name]


@pytest.mark.asyncio
async def test_real_mongo_goal_save_survives_snapshot_read_failure(mongo_db, monkeypatch):
    db, _commands = mongo_db
    fixed_now = datetime(2026, 9, 7, 12, tzinfo=timezone.utc)
    monkeypatch.setattr(goal, "_now", lambda: fixed_now.isoformat())
    monkeypatch.setattr(goal, "_utcnow", lambda: fixed_now)
    wrapped = SnapshotReadFailureDB(db)

    async with AsyncClient(
        transport=ASGITransport(app=app_for(wrapped)),
        base_url="http://isolated.test",
    ) as client:
        saved = await client.put(
            "/ads-manager/ai-monitor/monthly-profit-goal",
            json={"minimum_net_profit_sar": 125_000.0},
        )

    assert saved.status_code == 200
    assert saved.json()["goal_config_saved"] is True
    assert saved.json()["progress_available"] is False
    assert saved.json()["progress_unavailable_reason"] == (
        "snapshot_read_failed_after_goal_save:RuntimeError"
    )
    persisted = await db[goal.COLLECTION].find_one({"user_id": "u1"})
    assert persisted["minimum_net_profit_sar"] == 125_000.0


@pytest.mark.asyncio
async def test_real_mongo_salla_only_amounts_and_incomplete_gates_are_preserved(
    mongo_db,
    monkeypatch,
):
    db, _commands = mongo_db
    order = cost_fixture._order({
        "product_id": "p-salla",
        "quantity": 2,
        "price": 100.0,
        "total": 200.0,
    })
    cost_fixture._install_isolated_profit_io(
        monkeypatch,
        orders=[order],
        products=[],
    )
    await db[dashboard.PRODUCTS].insert_one(cost_fixture._product(
        "p-salla",
        salla_cost=30.0,
    ))
    loader = mezan_campaign_profit_loader.make_mezan_campaign_profit_loader(db)
    mtd = await goal._month_to_date_totals(loader, "u1", date(2026, 9, 1))
    derived = goal._derive_goal_progress(
        goal=current_config(100_000.0),
        month_to_date=mtd,
        end=date(2026, 9, 1),
    )
    assert mtd["total_product_cost"] == 60.0
    assert mtd["net_profit"] == 120.0
    assert derived["progress_available"] is True
    assert derived["scale_execution_allowed_by_profit_accounting"] is True

    async def incomplete_ads(*_args, **_kwargs):
        return {
            "total": 20.0,
            "bank_commissions": {"total_fee_sar": 0.0},
            "spend_quality": {"amount_complete": False},
            "source_contract": {"source": "isolated_fixture"},
        }

    monkeypatch.setattr(profit_engine, "build_mezan_v2_ads", incomplete_ads)
    incomplete_mtd = await goal._month_to_date_totals(
        loader,
        "u1",
        date(2026, 9, 1),
    )
    incomplete = goal._derive_goal_progress(
        goal=current_config(100_000.0),
        month_to_date=incomplete_mtd,
        end=date(2026, 9, 1),
    )
    assert incomplete_mtd["net_profit"] is None
    assert incomplete["progress_available"] is False
    assert incomplete["progress_state"] == "quality_incomplete"
    assert incomplete["scale_execution_allowed_by_profit_accounting"] is False
