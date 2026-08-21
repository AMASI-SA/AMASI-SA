from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import inspect
import logging

from fastapi import APIRouter
import pytest

from integrations_control_center import snapchat_ad_performance as ad_performance
from integrations_control_center import ads_auto_sync_scheduler as scheduler
from integrations_control_center import snapchat_account_selection as selection
from integrations_control_center import snapchat_adsquad_performance as adsquad_performance
from integrations_control_center import snapchat_native_performance_sync as performance


def _matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(row, branch) for branch in expected):
                return False
            continue
        if key == "$and":
            if not all(_matches(row, branch) for branch in expected):
                return False
            continue
        actual = row
        exists = True
        for part in key.split("."):
            if not isinstance(actual, dict) or part not in actual:
                exists = False
                actual = None
                break
            actual = actual[part]
        if not isinstance(expected, dict):
            if actual != expected:
                return False
            continue
        for operator, operand in expected.items():
            if operator == "$in" and (not exists or actual not in operand):
                return False
            if operator == "$exists" and exists is not bool(operand):
                return False
            if operator == "$ne" and exists and actual == operand:
                return False
            if operator == "$gte" and (not exists or actual < operand):
                return False
            if operator == "$lte" and (not exists or actual > operand):
                return False
            if operator == "$gt" and (not exists or actual <= operand):
                return False
            if operator == "$lt" and (not exists or actual >= operand):
                return False
        if not expected:
            return False
    return True


class _Cursor:
    def __init__(self, rows):
        self.rows = deepcopy(rows)

    def sort(self, key, direction=None):
        fields = key if isinstance(key, list) else [(key, direction)]
        for field, order in reversed(fields):
            self.rows.sort(
                key=lambda row: row.get(field) or "",
                reverse=order < 0,
            )
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows[:length])

    def __aiter__(self):
        async def iterate():
            for row in self.rows:
                yield deepcopy(row)

        return iterate()


class _Collection:
    def __init__(self, rows, *, name=None, calls=None, projections=None):
        self.rows = rows
        self.name = name
        self.calls = calls
        self.projections = projections

    async def find_one(self, query, projection=None, sort=None):
        if self.calls is not None:
            self.calls.append((self.name, "find_one", deepcopy(query)))
        if self.projections is not None:
            self.projections.append((self.name, "find_one", deepcopy(projection)))
        rows = [row for row in self.rows if _matches(row, query)]
        if sort:
            for key, direction in reversed(sort):
                rows.sort(
                    key=lambda row: row.get(key) or "",
                    reverse=direction < 0,
                )
        return deepcopy(rows[0]) if rows else None

    def find(self, query, projection=None):
        if self.calls is not None:
            self.calls.append((self.name, "find", deepcopy(query)))
        if self.projections is not None:
            self.projections.append((self.name, "find", deepcopy(projection)))
        return _Cursor([row for row in self.rows if _matches(row, query)])

    async def insert_one(self, document):
        self.rows.append(deepcopy(document))

    async def update_one(self, query, update, upsert=False):
        row = next((item for item in self.rows if _matches(item, query)), None)
        inserted = row is None and upsert
        if row is None:
            if not upsert:
                return type("UpdateResult", (), {"matched_count": 0})()
            row = {
                key: deepcopy(value)
                for key, value in query.items()
                if not key.startswith("$") and not isinstance(value, dict)
            }
            self.rows.append(row)
        if inserted:
            row.update(deepcopy(update.get("$setOnInsert") or {}))
        row.update(deepcopy(update.get("$set") or {}))
        for key in (update.get("$unset") or {}):
            row.pop(key, None)
        return type("UpdateResult", (), {"matched_count": 0 if inserted else 1})()

    async def update_many(self, query, update):
        matched = 0
        for row in self.rows:
            if _matches(row, query):
                row.update(deepcopy(update.get("$set") or {}))
                for key in (update.get("$unset") or {}):
                    row.pop(key, None)
                matched += 1
        return type("UpdateResult", (), {"matched_count": matched})()

    async def count_documents(self, query):
        return sum(_matches(row, query) for row in self.rows)

    async def create_index(self, *args, **kwargs):
        return "test-index"


class _DB:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []
        self.projections = []

    def __getitem__(self, name):
        return _Collection(
            self.rows.setdefault(name, []),
            name=name,
            calls=self.calls,
            projections=self.projections,
        )

    def __getattr__(self, name):
        return self[name]


def test_defaults_to_enabled_five_minutes_and_two_days(monkeypatch):
    monkeypatch.delenv(scheduler.ENABLED_ENV, raising=False)
    monkeypatch.delenv(scheduler.INTERVAL_ENV, raising=False)
    monkeypatch.delenv(scheduler.ROLLING_DAYS_ENV, raising=False)

    assert scheduler.auto_sync_enabled() is True
    assert scheduler.interval_seconds() == 300
    assert scheduler.rolling_days() == 2


def test_interval_cannot_be_faster_than_five_minutes(monkeypatch):
    monkeypatch.setenv(scheduler.INTERVAL_ENV, "30")
    assert scheduler.interval_seconds() == 300

    monkeypatch.setenv(scheduler.INTERVAL_ENV, "900")
    assert scheduler.interval_seconds() == 900


def test_scheduler_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv(scheduler.ENABLED_ENV, "false")
    assert scheduler.auto_sync_enabled() is False


def test_exception_type_diagnostic_is_a_bounded_ascii_identifier():
    unsafe_error = type(
        "Unsafe-Type\nBearerSecretCanary",
        (RuntimeError,),
        {},
    )

    assert scheduler._safe_exception_type(RuntimeError()) == "RuntimeError"
    assert scheduler._safe_exception_type(unsafe_error()) == "Exception"


def test_failure_location_uses_only_the_deepest_scheduler_package_frame():
    secret = "Authorization: Bearer traceback-local-secret"

    class ExplodingRun(dict):
        def get(self, *_args, **_kwargs):
            local_payload_canary = secret
            raise RuntimeError(local_payload_canary)

    try:
        scheduler._safe_provider_run_status(ExplodingRun())
    except RuntimeError as exc:
        location = scheduler._safe_failure_location(exc)
        current = exc.__traceback__
        expected_line = None
        while current is not None:
            if current.tb_frame.f_code.co_name == "_safe_provider_run_status":
                expected_line = current.tb_lineno
            current = current.tb_next
    else:  # pragma: no cover - the test fixture always raises
        raise AssertionError("expected the synthetic scheduler failure")

    assert location.keys() == {
        "failure_module",
        "failure_function",
        "failure_line",
    }
    assert location["failure_module"] == (
        "integrations_control_center.ads_auto_sync_scheduler"
    )
    assert location["failure_function"] == "_safe_provider_run_status"
    assert expected_line is not None
    assert location["failure_line"] == expected_line
    assert secret not in repr(location)
    assert str(scheduler.__file__) not in repr(location)


@pytest.mark.parametrize(
    ("module", "function", "line"),
    [
        ("C:/private/integrations_control_center/scheduler.py", "refresh", 10),
        ("other_package.scheduler", "refresh", 10),
        ("integrations_control_center.scheduler", "refresh\nsecret", 10),
        ("integrations_control_center.scheduler", "refresh", True),
        ("integrations_control_center.scheduler", "refresh", 0),
        (
            "integrations_control_center.scheduler",
            "refresh",
            scheduler.SNAPCHAT_FAILURE_LINE_LIMIT + 1,
        ),
    ],
)
def test_failure_location_is_atomic_and_rejects_untrusted_values(
    module,
    function,
    line,
):
    assert scheduler._safe_failure_location_values(module, function, line) == {}


@pytest.mark.asyncio
async def test_decision_outcome_deferred_error_never_logs_exception_text(
    monkeypatch,
    caplog,
):
    from integrations_control_center import snapchat_decision_outcomes

    canaries = (
        "Authorization: Bearer outcome-evaluator-secret",
        "outcome-evaluator-local-payload",
        str(scheduler.__file__),
    )
    secret = " ".join(canaries)

    async def explode(*_args, **_kwargs):
        local_payload_canary = secret
        raise RuntimeError(local_payload_canary)

    monkeypatch.setattr(
        snapchat_decision_outcomes,
        "evaluate_due_ad_decisions",
        explode,
    )
    caplog.set_level(logging.ERROR, logger=scheduler.__name__)

    result = await scheduler._evaluate_snapchat_outcomes_after_sync(
        object(),
        "owner-outcome-evaluator",
        now=datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc),
    )

    assert result == {
        "status": "deferred",
        "retryable": True,
        "error_type": "RuntimeError",
    }
    assert "failure_stage=decision_outcomes_evaluation" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    for canary in canaries:
        assert canary not in caplog.text


@pytest.mark.asyncio
async def test_provider_read_and_fact_write_report_distinct_failure_stages():
    stages = []
    db = _DB({})
    context = scheduler.SnapchatSyncContext(
        db,
        "owner-stage-hooks",
        failure_stage_observer=stages.append,
    )

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {}

    class Client:
        @staticmethod
        async def get(*_args, **_kwargs):
            return Response()

    await context.get_json(
        Client(),
        "https://adsapi.snapchat.com/v1/test",
        headers={"Authorization": "redacted"},
    )
    await performance._upsert_performance(
        context,
        account={
            "ad_account_id": "snap-stage-hooks",
            "currency": "SAR",
            "timezone": "Asia/Riyadh",
        },
        entity_type="ad_account",
        external_id="snap-stage-hooks",
        date_string="2026-08-21",
        metrics={"spend": 0, "conversion_purchases": 0},
    )
    await ad_performance._upsert_projection(
        context,
        collection_name="test_ad_facts",
        account={
            "ad_account_id": "snap-stage-hooks",
            "currency": "SAR",
            "timezone": "Asia/Riyadh",
        },
        timezone_name="Asia/Riyadh",
        stored_granularity="RIYADH_DAY",
        campaign_id="campaign-stage-hooks",
        ad_id="ad-stage-hooks",
        date_string="2026-08-21",
        bucket=performance._new_bucket(ad_performance.TOTAL_STAT_FIELDS),
        action_report_time=ad_performance.ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME,
    )
    await adsquad_performance._upsert_projection(
        context,
        collection_name="test_adsquad_facts",
        account={
            "ad_account_id": "snap-stage-hooks",
            "currency": "SAR",
            "timezone": "Asia/Riyadh",
        },
        timezone_name="Asia/Riyadh",
        stored_granularity="RIYADH_DAY",
        campaign_id="campaign-stage-hooks",
        adsquad_id="adsquad-stage-hooks",
        date_string="2026-08-21",
        bucket=performance._new_bucket(adsquad_performance.TOTAL_STAT_FIELDS),
        action_report_time=(
            adsquad_performance.ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
        ),
    )

    assert stages == [
        "provider_refresh",
        "fact_write",
        "fact_write",
        "fact_write",
    ]


def test_rolling_window_uses_riyadh_calendar_day():
    # 21:30 UTC is 00:30 in Riyadh on Aug 1.
    start, end = scheduler.riyadh_date_range(
        datetime(2026, 7, 31, 21, 30, tzinfo=timezone.utc),
        2,
    )
    assert start.isoformat() == "2026-07-31"
    assert end.isoformat() == "2026-08-01"


@pytest.mark.asyncio
async def test_connected_snapchat_targets_are_reproved_before_the_writer(
    monkeypatch,
):
    db = _DB(
        {
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-api",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                },
                {
                    "user_id": "owner-migrated",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "connection_provenance": "legacy_integration",
                },
                {
                    "user_id": "owner-unproven",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                },
                {
                    "user_id": "owner-disconnected",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "needs_reauth",
                },
                {
                    "user_id": "owner-malformed",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                },
                {
                    "user_id": 7,
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                },
                {
                    "user_id": " owner-spaced ",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                },
                {
                    "user_id": "meta-owner",
                    "provider": scheduler.META_PROVIDER_ID,
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                },
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": "owner-api",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-api",
                    "external_account_id": "snap-api",
                },
                {
                    "user_id": "owner-migrated",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-migrated",
                    "external_account_id": "snap-migrated",
                    "organization_id": "org-owner",
                },
                {
                    "user_id": "owner-unproven",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-unproven",
                    "external_account_id": "snap-unproven",
                },
                {
                    "user_id": "foreign-owner",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "foreign-account",
                    "external_account_id": "foreign-account",
                },
                {
                    "user_id": "owner-malformed",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-malformed",
                    "external_account_id": "snap-malformed",
                },
                {
                    "user_id": "7",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-numeric-tenant",
                    "external_account_id": "snap-numeric-tenant",
                },
                {
                    "user_id": "owner-spaced",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-spaced-tenant",
                    "external_account_id": "snap-spaced-tenant",
                },
            ],
            selection.SNAPCHAT_CREDENTIALS_COLLECTION: [
                {
                    "user_id": "owner-api",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "refresh_token_ciphertext": b"api-refresh",
                },
                {
                    "user_id": "owner-migrated",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "refresh_token_ciphertext": b"tenant-refresh",
                    "organization_ids": ["org-owner"],
                },
                {
                    "user_id": "foreign-owner",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "refresh_token_ciphertext": b"foreign-refresh",
                },
                {
                    "user_id": "owner-malformed",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "refresh_token_ciphertext": b"tenant-refresh",
                    "organization_ids": 123,
                },
                {
                    "user_id": "7",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "refresh_token_ciphertext": b"tenant-refresh",
                },
                {
                    "user_id": "owner-spaced",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "refresh_token_ciphertext": b"tenant-refresh",
                },
            ],
        }
    )
    assert await scheduler._targets(db) == [
        ("meta-owner", scheduler.META_PROVIDER_ID),
        ("owner-api", scheduler.SNAPCHAT_PROVIDER_ID),
        ("owner-malformed", scheduler.SNAPCHAT_PROVIDER_ID),
        ("owner-migrated", scheduler.SNAPCHAT_PROVIDER_ID),
        ("owner-unproven", scheduler.SNAPCHAT_PROVIDER_ID),
    ]
    reads = [(name, operation) for name, operation, _query in db.calls]
    assert reads.count(("mezan_integrations_v2", "find")) == 2
    assert reads.count(("mezan_integration_accounts_v2", "find")) == 0
    assert reads.count((selection.SNAPCHAT_CREDENTIALS_COLLECTION, "find")) == 0
    assert not any(operation == "find_one" for _name, operation in reads)
    integration_queries = [
        query
        for name, operation, query in db.calls
        if name == "mezan_integrations_v2" and operation == "find"
    ]
    assert scheduler.SNAPCHAT_PROVIDER_ID not in (
        integration_queries[0]["provider"]["$in"]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("spend_micro", "expected_state", "expected_spend"),
    [
        (5_000_000, "confirmed_data", 5.0),
        (0, "confirmed_zero", 0.0),
    ],
)
async def test_migrated_account_emits_canonical_fact_and_terminal_run_proof(
    monkeypatch,
    spend_micro,
    expected_state,
    expected_spend,
):
    from cryptography.fernet import Fernet

    from integrations_control_center import snapchat_oauth_security
    import dashboard_snapchat_spend

    run_started = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    write_time = run_started + timedelta(minutes=2)
    report_date = date(2026, 8, 20)
    monkeypatch.setenv(
        "SNAPCHAT_TOKEN_ENC_KEY",
        Fernet.generate_key().decode("ascii"),
    )
    credential = {
        "user_id": "owner-migrated",
        "provider": scheduler.SNAPCHAT_PROVIDER_ID,
        "access_token_ciphertext": snapchat_oauth_security.encrypt_snapchat_token(
            "access-token"
        ),
        "refresh_token_ciphertext": snapchat_oauth_security.encrypt_snapchat_token(
            "refresh-token"
        ),
        "access_token_expires_at": write_time + timedelta(hours=2),
        "organization_ids": ["org-owner"],
    }
    db = _DB(
        {
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-migrated",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "connection_provenance": "legacy_integration",
                }
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": "owner-migrated",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "connection_provenance": "legacy_integration",
                    "mezan_selected": True,
                    "mezan_integration_account_id": "integration-account-1",
                    "ad_account_id": "snap-migrated",
                    "external_account_id": "snap-migrated",
                    "organization_id": "org-owner",
                    "timezone": "Asia/Riyadh",
                    "currency": "SAR",
                }
            ],
            selection.SNAPCHAT_CREDENTIALS_COLLECTION: [credential],
        }
    )
    metrics = {
        key: (spend_micro if key == "spend" else 0)
        for key in scheduler.snapchat_hourly.STAT_FIELDS
    }
    provider_payload = {
        "request_status": "SUCCESS",
        "timeseries_stats": [
            {
                "sub_request_status": "SUCCESS",
                "timeseries_stat": {
                    "granularity": "HOUR",
                    "breakdown_stats": {
                        "campaign": [
                            {
                                "id": "campaign-1",
                                "timeseries": [
                                    {
                                        "start_time": "2026-08-20T00:00:00+03:00",
                                        "end_time": "2026-08-20T01:00:00+03:00",
                                        "stats": metrics,
                                    }
                                ],
                            }
                        ]
                    },
                },
            }
        ],
    }

    class Response:
        status_code = 200
        text = ""

        def json(self):
            return deepcopy(provider_payload)

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def get(self, *args, **kwargs):
            return Response()

    real_refresh = scheduler.snapchat_hourly.refresh_snapchat_account_hours

    async def refresh_with_production_children(*args, **kwargs):
        item = await real_refresh(*args, **kwargs)
        args[0].provider_calls += 2
        child = {
            "errors_count": 0,
            "errors": [],
            "provider_calls": 1,
            "coverage": _complete_coverage("confirmed_no_data"),
        }
        return {
            **item,
            "ad_squad_performance": deepcopy(child),
            "ad_performance": deepcopy(child),
        }

    async def noop(*args, **kwargs):
        return None

    async def outcomes(*args, **kwargs):
        return {"status": "complete", "evaluated": 0}

    monkeypatch.setattr(scheduler, "snapchat_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "snapchat_native_sync_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_utcnow", lambda: write_time)
    monkeypatch.setattr(scheduler, "ensure_snapchat_native_sync_indexes", noop)
    monkeypatch.setattr(
        scheduler,
        "_evaluate_snapchat_outcomes_after_sync",
        outcomes,
    )
    monkeypatch.setattr(scheduler.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        scheduler.snapchat_hourly,
        "refresh_snapchat_account_hours",
        refresh_with_production_children,
    )

    assert await scheduler._targets(db) == [
        ("owner-migrated", scheduler.SNAPCHAT_PROVIDER_ID)
    ]
    result = await scheduler._refresh_snapchat(
        db,
        user_id="owner-migrated",
        start_date=report_date,
        end_date=report_date,
        now=run_started,
    )

    assert result["status"] == "complete"
    assert result["accounts_attempted"] == result["accounts_complete"] == 1
    assert result["rows_saved"] == 2
    assert result["campaign_rows_saved"] == 1
    assert result["errors_count"] == 0
    assert result["provider_calls"] == 3
    assert result["account_provider_calls"] == [
        {"ad_account_id": "snap-migrated", "provider_calls": 3}
    ]
    assert result["coverage"] == {
        "status": "complete",
        "data_state": expected_state,
        "expected_requests": 3,
        "completed_requests": 3,
    }

    facts = db.rows["mezan_snapchat_performance_daily_v2"]
    assert len(facts) == 2
    account_fact = next(row for row in facts if row["entity_type"] == "ad_account")
    assert account_fact["user_id"] == "owner-migrated"
    assert account_fact["ad_account_id"] == "snap-migrated"
    assert account_fact["mezan_integration_account_id"] == "integration-account-1"
    assert account_fact["date"] == report_date.isoformat()
    assert account_fact["date_timezone"] == "Asia/Riyadh"
    assert account_fact["source_mode"] == scheduler.ACCOUNT_REFRESH_SOURCE_MODE
    assert account_fact["spend_native"] == expected_spend
    assert account_fact["updated_at"] == write_time.isoformat()
    campaign_fact = next(row for row in facts if row["entity_type"] == "campaign")
    assert campaign_fact["source_mode"] == (
        scheduler.snapchat_hourly.CAMPAIGN_FACTS_SOURCE_MODE
    )

    runs = db.rows[scheduler.RUNS_COLLECTION]
    assert len(runs) == 1
    run = runs[0]
    assert run["run_id"] == result["run_id"]
    assert run["status"] == "complete"
    assert run["source_mode"] == scheduler.ACCOUNT_REFRESH_SOURCE_MODE
    assert run["started_at"] == run_started.isoformat()
    assert run["finished_at"] == write_time.isoformat()
    assert run["summary"]["date_from"] == report_date.isoformat()
    assert run["summary"]["date_to"] == report_date.isoformat()
    assert run["summary"]["accounts_attempted"] == 1
    assert run["summary"]["accounts_complete"] == 1
    assert run["summary"]["coverage"] == result["coverage"]
    assert run["summary"]["account_provider_calls"] == (
        result["account_provider_calls"]
    )

    account = db.rows["mezan_integration_accounts_v2"][0]
    assert account["connection_provenance"] == "legacy_integration"
    assert account["source_mode"] == scheduler.ACCOUNT_REFRESH_SOURCE_MODE
    assert account["coverage"] == _complete_coverage(expected_state)
    assert account["last_sync_at"] == write_time.isoformat()
    integration = db.rows["mezan_integrations_v2"][0]
    assert integration["connection_provenance"] == "legacy_integration"
    assert integration["source_mode"] == scheduler.ACCOUNT_REFRESH_SOURCE_MODE
    assert integration["coverage"] == result["coverage"]
    assert integration["last_sync_at"] == write_time.isoformat()
    assert db.rows[selection.SNAPCHAT_CREDENTIALS_COLLECTION] == [credential]

    dashboard = await dashboard_snapchat_spend.load_snapchat_dashboard_spend(
        db,
        "owner-migrated",
        start=report_date,
        end=report_date,
        now=write_time + timedelta(minutes=5),
    )
    assert dashboard["total_sar"] == expected_spend
    assert dashboard["daily_sar"][report_date.isoformat()] == expected_spend
    assert dashboard["quality"]["data_state"] == expected_state
    assert dashboard["quality"]["coverage_complete"] is True
    assert dashboard["quality"]["amount_complete"] is True
    assert dashboard["quality"]["reason_codes"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credential_mode", "expected_code"),
    [
        ("missing", "snapchat_needs_reauth"),
        ("malformed_org", "snapchat_credential_metadata_invalid"),
    ],
)
@pytest.mark.parametrize("error_record_fails", [False, True])
async def test_invalid_snapchat_credential_terminalizes_and_invalidates_health(
    monkeypatch,
    credential_mode,
    expected_code,
    error_record_fails,
):
    current = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    old_sync = "2026-08-21T11:30:00+00:00"
    db = _DB(
        {
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-revoked",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "data_quality": "complete",
                    "data_delay_minutes": 0,
                    "health_score": 100,
                    "last_sync_at": old_sync,
                }
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": "owner-revoked",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-revoked",
                    "external_account_id": "snap-revoked",
                    "organization_id": "org-owner",
                    "data_quality": "complete",
                    "data_delay_minutes": 0,
                    "health_score": 100,
                    "last_sync_at": old_sync,
                }
            ],
            selection.SNAPCHAT_CREDENTIALS_COLLECTION: (
                [
                    {
                        "user_id": "owner-revoked",
                        "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                        "refresh_token_ciphertext": b"refresh-token",
                        "organization_ids": 123,
                    }
                ]
                if credential_mode == "malformed_org"
                else []
            ),
        }
    )
    monkeypatch.setattr(scheduler, "snapchat_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "snapchat_native_sync_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_utcnow", lambda: current)
    monkeypatch.setattr(
        selection,
        "decrypt_snapchat_token",
        lambda _value: "refresh-token",
    )
    if error_record_fails:
        async def fail_error_record(*_args, **_kwargs):
            raise RuntimeError("error collection unavailable")

        monkeypatch.setattr(scheduler, "_record_error", fail_error_record)

    assert await scheduler._targets(db) == [
        ("owner-revoked", scheduler.SNAPCHAT_PROVIDER_ID)
    ]
    result = await scheduler._refresh_snapchat(
        db,
        user_id="owner-revoked",
        start_date=date(2026, 8, 20),
        end_date=date(2026, 8, 21),
        now=current,
    )

    assert result["status"] == "failed"
    assert result["code"] == expected_code
    run = db.rows[scheduler.RUNS_COLLECTION][0]
    assert run["status"] == "failed"
    assert run["summary"]["date_from"] == "2026-08-20"
    assert run["summary"]["date_to"] == "2026-08-21"
    integration = db.rows["mezan_integrations_v2"][0]
    assert integration["connection_status"] == "needs_reauth"
    assert integration["data_quality"] == "unavailable"
    assert integration["data_delay_minutes"] is None
    assert integration["health_score"] == 0
    assert integration["last_sync_at"] == old_sync
    account = db.rows["mezan_integration_accounts_v2"][0]
    assert account["data_quality"] == "incomplete"
    assert account["data_delay_minutes"] is None
    assert account["health_score"] == 70
    assert account["last_sync_at"] == old_sync
    assert db.rows.get("mezan_snapchat_performance_daily_v2", []) == []


@pytest.mark.asyncio
async def test_snapchat_disconnect_after_discovery_blocks_provider_and_fact(
    monkeypatch,
):
    current = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    db = _DB(
        {
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-race",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "data_quality": "complete",
                    "data_delay_minutes": 0,
                    "health_score": 100,
                }
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": "owner-race",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-race",
                    "external_account_id": "snap-race",
                }
            ],
            selection.SNAPCHAT_CREDENTIALS_COLLECTION: [
                {
                    "user_id": "owner-race",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "refresh_token_ciphertext": b"refresh-token",
                }
            ],
        }
    )
    monkeypatch.setattr(scheduler, "snapchat_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "snapchat_native_sync_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_utcnow", lambda: current)
    monkeypatch.setattr(
        selection,
        "decrypt_snapchat_token",
        lambda _value: "refresh-token",
    )

    assert await scheduler._targets(db) == [
        ("owner-race", scheduler.SNAPCHAT_PROVIDER_ID)
    ]
    db.rows["mezan_integrations_v2"][0]["connection_status"] = "needs_reauth"
    result = await scheduler._refresh_snapchat(
        db,
        user_id="owner-race",
        start_date=date(2026, 8, 20),
        end_date=date(2026, 8, 21),
        now=current,
    )

    assert result["status"] == "failed"
    assert result["code"] == "snapchat_integration_not_connected"
    assert db.rows[scheduler.RUNS_COLLECTION][0]["status"] == "failed"
    integration = db.rows["mezan_integrations_v2"][0]
    assert integration["connection_status"] == "needs_reauth"
    assert integration["data_quality"] == "incomplete"
    assert integration["health_score"] == 70
    assert db.rows.get("mezan_snapchat_performance_daily_v2", []) == []


@pytest.mark.asyncio
async def test_snapchat_disconnect_during_provider_call_cannot_restore_freshness(
    monkeypatch,
):
    current = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    db = _DB(
        {
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-midflight",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "connection_provenance": "legacy_integration",
                    "data_quality": "complete",
                    "data_delay_minutes": 0,
                    "health_score": 100,
                }
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": "owner-midflight",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-midflight",
                    "external_account_id": "snap-midflight",
                }
            ],
        }
    )
    account = deepcopy(db.rows["mezan_integration_accounts_v2"][0])

    async def load_accounts(*_args, **_kwargs):
        return [account]

    async def noop(*_args, **_kwargs):
        return None

    async def refresh(context, *_args, **_kwargs):
        context.provider_calls = 3
        db.rows["mezan_integrations_v2"][0]["connection_status"] = (
            "needs_reauth"
        )
        return {
            "ad_account_id": "snap-midflight",
            "rows_saved": 1,
            "campaign_rows_saved": 1,
            "campaign_facts_source_mode": (
                scheduler.snapchat_hourly.CAMPAIGN_FACTS_SOURCE_MODE
            ),
            "campaign_facts_schema_version": (
                scheduler.snapchat_hourly.CAMPAIGN_FACTS_SCHEMA_VERSION
            ),
            "errors_count": 0,
            "errors": [],
            "coverage": _complete_coverage(),
            "ad_squad_performance": {
                "errors_count": 0,
                "errors": [],
                "coverage": _complete_coverage("confirmed_no_data"),
            },
            "ad_performance": {
                "errors_count": 0,
                "errors": [],
                "coverage": _complete_coverage("confirmed_no_data"),
            },
        }

    class Context:
        def __init__(self, *_args, **_kwargs):
            self.provider_calls = 0

        async def access_token(self):
            return "access-token"

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(scheduler, "snapchat_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "snapchat_native_sync_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_utcnow", lambda: current)
    monkeypatch.setattr(
        scheduler,
        "_load_canonical_scheduler_accounts",
        load_accounts,
    )
    monkeypatch.setattr(scheduler, "ensure_snapchat_native_sync_indexes", noop)
    monkeypatch.setattr(scheduler, "SnapchatSyncContext", Context)
    monkeypatch.setattr(scheduler.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        scheduler.snapchat_hourly,
        "refresh_snapchat_account_hours",
        refresh,
    )

    result = await scheduler._refresh_snapchat(
        db,
        user_id="owner-midflight",
        start_date=date(2026, 8, 20),
        end_date=date(2026, 8, 21),
        now=current,
    )

    assert result["status"] == "partial"
    assert result["coverage"]["status"] == "incomplete"
    run = db.rows[scheduler.RUNS_COLLECTION][0]
    assert run["status"] == "partial"
    integration = db.rows["mezan_integrations_v2"][0]
    assert integration["connection_status"] == "needs_reauth"
    assert integration["connection_provenance"] == "legacy_integration"
    assert integration["data_quality"] == "incomplete"
    assert integration["health_score"] == 70
    assert "last_sync_at" not in integration
    account_row = db.rows["mezan_integration_accounts_v2"][0]
    assert account_row["data_quality"] == "incomplete"
    assert account_row["health_score"] == 70
    assert account_row["data_delay_minutes"] is None


@pytest.mark.asyncio
async def test_unexpected_snapchat_runtime_error_terminalizes_failed_run(
    monkeypatch,
    caplog,
):
    current = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    db = _DB(
        {
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-runtime",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "data_quality": "complete",
                    "data_delay_minutes": 0,
                    "health_score": 100,
                }
            ],
            "mezan_integration_accounts_v2": [],
        }
    )

    canaries = (
        "Authorization: Bearer access-token-canary",
        "decrypted-refresh-token-canary",
        "response-payload-canary",
        "traceback-local-canary",
        str(scheduler.__file__),
    )
    secret = " ".join(canaries)

    async def explode(*_args, **_kwargs):
        local_payload_canary = secret
        raise RuntimeError(local_payload_canary)

    monkeypatch.setattr(scheduler, "snapchat_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "snapchat_native_sync_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_utcnow", lambda: current)
    monkeypatch.setattr(
        scheduler,
        "_load_canonical_scheduler_accounts",
        explode,
    )
    caplog.set_level(logging.ERROR, logger=scheduler.__name__)

    result = await scheduler._refresh_snapchat(
        db,
        user_id="owner-runtime",
        start_date=date(2026, 8, 20),
        end_date=date(2026, 8, 21),
        now=current,
    )

    assert result == {
        "provider": scheduler.SNAPCHAT_PROVIDER_ID,
        "run_id": db.rows[scheduler.RUNS_COLLECTION][0]["run_id"],
        "status": "failed",
        "code": "snapchat_scheduler_runtime_error",
    }
    run = db.rows[scheduler.RUNS_COLLECTION][0]
    assert run["status"] == "failed"
    assert run["summary"]["date_from"] == "2026-08-20"
    assert run["summary"]["date_to"] == "2026-08-21"
    assert run["summary"]["coverage"] == {
        "status": "incomplete",
        "data_state": "unknown_incomplete",
        "expected_requests": 1,
        "completed_requests": 0,
    }
    failure_line = run["error"]["failure_line"]
    assert run["error"] == {
        "error_id": db.rows[scheduler.ERRORS_COLLECTION][0]["error_id"],
        "code": "snapchat_scheduler_runtime_error",
        "message": "Snapchat scheduler refresh failed unexpectedly.",
        "retryable": True,
        "failure_stage": "integration_account_credential_proof",
        "exception_type": "RuntimeError",
        "run_id": run["run_id"],
        "failure_module": "integrations_control_center.ads_auto_sync_scheduler",
        "failure_function": "_refresh_snapchat",
        "failure_line": failure_line,
    }
    source_lines, source_start = inspect.getsourcelines(scheduler._refresh_snapchat)
    assert source_start <= failure_line < source_start + len(source_lines)
    assert "failure_stage=integration_account_credential_proof" in caplog.text
    assert "exception_type=RuntimeError" in caplog.text
    for canary in canaries:
        assert canary not in repr(result)
        assert canary not in repr(db.rows)
        assert canary not in caplog.text
    integration = db.rows["mezan_integrations_v2"][0]
    assert integration["data_quality"] == "incomplete"
    assert integration["data_delay_minutes"] is None
    assert integration["health_score"] == 70


@pytest.mark.asyncio
async def test_fact_write_runtime_error_persists_only_safe_diagnostics(
    monkeypatch,
    caplog,
):
    current = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    db = _DB(
        {
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-fact-runtime",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                }
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": "owner-fact-runtime",
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "connection_status": "connected",
                    "mezan_selected": True,
                    "ad_account_id": "snap-fact-runtime",
                    "external_account_id": "snap-fact-runtime",
                }
            ],
        }
    )
    account = deepcopy(db.rows["mezan_integration_accounts_v2"][0])
    secret = (
        "Bearer fact-write-secret raw-response-secret "
        f"full-path-canary={scheduler.__file__}"
    )

    async def load_accounts(*_args, **_kwargs):
        return [account]

    async def noop(*_args, **_kwargs):
        return None

    async def refresh(context, *_args, **_kwargs):
        await ad_performance._upsert_projection(
            context,
            collection_name="test_ad_facts",
            account={
                "ad_account_id": "snap-fact-runtime",
                "currency": "SAR",
                "timezone": "Asia/Riyadh",
            },
            timezone_name="Asia/Riyadh",
            stored_granularity="RIYADH_DAY",
            campaign_id="campaign-fact-runtime",
            ad_id="ad-fact-runtime",
            date_string="2026-08-21",
            bucket=performance._new_bucket(ad_performance.TOTAL_STAT_FIELDS),
            action_report_time=(
                ad_performance.ADS_MANAGER_DEFAULT_ACTION_REPORT_TIME
            ),
        )

    class FailingFactCollection:
        @staticmethod
        async def update_one(*_args, **_kwargs):
            raise ValueError(secret)

    class Context:
        def __init__(self, db_value, user_id, **_kwargs):
            self.db = db_value
            self.user_id = user_id
            self.provider_calls = 0

        async def access_token(self):
            return "test-token"

        def now_iso(self):
            return current.isoformat()

        async def to_sar(self, value, _currency):
            return value

        def observe_failure_stage(self, stage):
            self.failure_stage_observer(stage)

    class Client:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    monkeypatch.setattr(scheduler, "snapchat_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "snapchat_native_sync_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_utcnow", lambda: current)
    monkeypatch.setattr(
        scheduler,
        "_load_canonical_scheduler_accounts",
        load_accounts,
    )
    monkeypatch.setattr(scheduler, "ensure_snapchat_native_sync_indexes", noop)
    monkeypatch.setattr(scheduler, "SnapchatSyncContext", Context)
    monkeypatch.setattr(scheduler.httpx, "AsyncClient", Client)
    monkeypatch.setattr(
        ad_performance,
        "_collection",
        lambda *_args, **_kwargs: FailingFactCollection(),
    )
    monkeypatch.setattr(
        scheduler.snapchat_hourly,
        "refresh_snapchat_account_hours",
        refresh,
    )
    caplog.set_level(logging.ERROR, logger=scheduler.__name__)

    result = await scheduler._refresh_snapchat(
        db,
        user_id="owner-fact-runtime",
        start_date=date(2026, 8, 20),
        end_date=date(2026, 8, 21),
        now=current,
    )

    run = db.rows[scheduler.RUNS_COLLECTION][0]
    assert result["status"] == "failed"
    assert run["status"] == "failed"
    assert run["summary"]["coverage"]["data_state"] == "unknown_incomplete"
    assert run["error"]["failure_stage"] == "fact_write"
    assert run["error"]["exception_type"] == "ValueError"
    assert run["error"]["run_id"] == run["run_id"]
    assert run["error"]["failure_module"] == (
        "integrations_control_center.snapchat_ad_performance"
    )
    assert run["error"]["failure_function"] == "_upsert_projection"
    assert 1 <= run["error"]["failure_line"] <= scheduler.SNAPCHAT_FAILURE_LINE_LIMIT
    assert secret not in repr(db.rows)
    assert secret not in repr(result)
    assert secret not in caplog.text


def test_campaign_ai_proof_window_is_three_days_only_for_meta_and_snapchat(
    monkeypatch,
):
    observed = {}

    async def targets(_db):
        return [
            ("owner", scheduler.META_PROVIDER_ID),
            ("owner", scheduler.SNAPCHAT_PROVIDER_ID),
            ("owner", scheduler.TIKTOK_PROVIDER_ID),
            ("owner", scheduler.GOOGLE_ADS_PROVIDER_ID),
        ]

    def refresh(provider):
        async def run(_db, **kwargs):
            observed[provider] = (
                kwargs["start_date"], kwargs["end_date"]
            )
            return {"provider": provider, "status": "complete"}

        return run

    monkeypatch.setattr(scheduler, "_targets", targets)
    monkeypatch.setattr(
        scheduler, "_refresh_meta", refresh(scheduler.META_PROVIDER_ID)
    )
    monkeypatch.setattr(
        scheduler,
        "_refresh_snapchat",
        refresh(scheduler.SNAPCHAT_PROVIDER_ID),
    )
    monkeypatch.setattr(
        scheduler, "_refresh_tiktok", refresh(scheduler.TIKTOK_PROVIDER_ID)
    )
    monkeypatch.setattr(
        scheduler, "_refresh_google", refresh(scheduler.GOOGLE_ADS_PROVIDER_ID)
    )

    current = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    asyncio.run(scheduler.run_auto_sync_cycle(object(), now=lambda: current))

    assert observed[scheduler.META_PROVIDER_ID][0].isoformat() == "2026-08-19"
    assert observed[scheduler.SNAPCHAT_PROVIDER_ID][0].isoformat() == "2026-08-19"
    assert observed[scheduler.TIKTOK_PROVIDER_ID][0].isoformat() == "2026-08-20"
    assert observed[scheduler.GOOGLE_ADS_PROVIDER_ID][0].isoformat() == "2026-08-20"
    assert {window[1].isoformat() for window in observed.values()} == {
        "2026-08-21"
    }


def test_router_registers_status_and_backend_lifecycle(monkeypatch):
    monkeypatch.setenv(scheduler.ENABLED_ENV, "true")
    router = APIRouter(prefix="/integrations-v2")

    async def current_user():
        return {"id": "owner-1", "role": "owner"}

    def require_owner(user):
        return user

    before_startup = len(router.on_startup)
    before_shutdown = len(router.on_shutdown)
    scheduler.attach_ads_auto_sync_scheduler(
        router,
        object(),
        current_user,
        require_owner,
    )

    assert any(
        route.path == "/integrations-v2/ads-auto-sync/status"
        for route in router.routes
    )
    assert len(router.on_startup) == before_startup + 1
    assert len(router.on_shutdown) == before_shutdown + 1


def test_safe_summary_preserves_account_error_samples():
    summary = scheduler._safe_summary({
        "errors_count": 1,
        "error_samples": [{
            "error_id": "err-1",
            "ad_account_id": "account-1",
            "code": "snapchat_request_failed",
            "message": "provider rejected the range",
            "retryable": True,
            "secret": "must-not-leak",
        }],
    })

    assert summary["error_samples"] == [{
        "error_id": "err-1",
        "ad_account_id": "account-1",
        "code": "snapchat_request_failed",
        "message": "provider rejected the range",
        "retryable": True,
    }]


def test_snapchat_call_budget_is_isolated_per_selected_account():
    source = inspect.getsource(scheduler._refresh_snapchat)

    assert "token_context = SnapchatSyncContext" in source
    assert "account_context = SnapchatSyncContext" in source
    assert "provider_calls_total += int(account_context.provider_calls)" in source
    assert '"provider_call_budget_scope": "per_selected_account"' in source
    assert '"provider_calls": provider_calls_total' in source


def test_safe_summary_preserves_per_account_provider_calls():
    summary = scheduler._safe_summary({
        "provider_calls": 263,
        "provider_call_budget_scope": "per_selected_account",
        "campaign_rows_saved": 48,
        "campaign_facts_source_mode": (
            scheduler.snapchat_hourly.CAMPAIGN_FACTS_SOURCE_MODE
        ),
        "campaign_facts_schema_version": 4,
        "account_provider_calls": [
            {"ad_account_id": "account-usd", "provider_calls": 132},
            {"ad_account_id": "account-sar", "provider_calls": 131},
        ],
    })

    assert summary["provider_calls"] == 263
    assert summary["provider_call_budget_scope"] == "per_selected_account"
    assert summary["account_provider_calls"] == [
        {"ad_account_id": "account-usd", "provider_calls": 132},
        {"ad_account_id": "account-sar", "provider_calls": 131},
    ]
    assert summary["campaign_rows_saved"] == 48
    assert summary["campaign_facts_source_mode"] == (
        scheduler.snapchat_hourly.CAMPAIGN_FACTS_SOURCE_MODE
    )
    assert summary["campaign_facts_schema_version"] == 4


def _complete_coverage(data_state="confirmed_data"):
    return {
        "status": "complete",
        "data_state": data_state,
        "expected_requests": 1,
        "completed_requests": 1,
    }


@pytest.mark.parametrize("nested_key", scheduler.SNAPCHAT_PERFORMANCE_RESULT_KEYS)
def test_nested_snapchat_performance_error_fails_account_coverage(nested_key):
    item = {
        "coverage": _complete_coverage(),
        "errors_count": 0,
        "errors": [],
        "ad_squad_performance": {
            "coverage": _complete_coverage(),
            "errors_count": 0,
            "errors": [],
        },
        "ad_performance": {
            "coverage": _complete_coverage(),
            "errors_count": 0,
            "errors": [],
        },
    }
    item[nested_key] = {
        "coverage": {
            "status": "incomplete",
            "data_state": "unknown_incomplete",
            "expected_requests": 2,
            "completed_requests": 1,
        },
        "errors_count": 1,
        "errors": [{"code": "nested_provider_error"}],
    }

    assert scheduler._snapchat_item_complete(item) is False
    errors = scheduler._snapchat_item_errors(item)
    assert any(error.get("code") == "nested_provider_error" for error in errors)
    assert any(error.get("kind") == nested_key for error in errors)


def test_cached_child_coverage_does_not_inflate_current_run_request_counts():
    item = {
        "coverage": _complete_coverage(),
        "errors_count": 0,
        "errors": [],
        "ad_squad_performance": {
            "skipped": True,
            "skip_reason": "fresh_within_15_minutes",
            "coverage": {
                **_complete_coverage("confirmed_data"),
                "expected_requests": 5,
                "completed_requests": 5,
            },
            "errors_count": 0,
            "errors": [],
        },
        "ad_performance": {
            "skipped": True,
            "skip_reason": "fresh_within_15_minutes",
            "coverage": {
                **_complete_coverage("confirmed_zero"),
                "expected_requests": 7,
                "completed_requests": 7,
            },
            "errors_count": 0,
            "errors": [],
        },
    }

    assert scheduler._snapchat_item_complete(item) is True
    assert scheduler._snapchat_run_coverage(item and [item], accounts_expected=1) == {
        "status": "complete",
        "data_state": "confirmed_data",
        "expected_requests": 1,
        "completed_requests": 1,
    }


def test_cached_child_incomplete_coverage_still_blocks_run():
    item = {
        "coverage": _complete_coverage(),
        "errors_count": 0,
        "errors": [],
        "ad_squad_performance": {
            "skipped": True,
            "skip_reason": "fresh_within_15_minutes",
            "coverage": _complete_coverage("confirmed_no_data"),
            "errors_count": 0,
            "errors": [],
        },
        "ad_performance": {
            "skipped": True,
            "skip_reason": "fresh_within_15_minutes",
            "coverage": {
                "status": "incomplete",
                "data_state": "unknown_incomplete",
                "expected_requests": 7,
                "completed_requests": 6,
            },
            "errors_count": 0,
            "errors": [],
        },
    }

    coverage = scheduler._snapchat_run_coverage([item], accounts_expected=1)
    assert coverage["status"] == "incomplete"
    assert coverage["data_state"] == "unknown_incomplete"


@pytest.mark.parametrize(
    "coverage",
    [
        {
            "status": "complete",
            "data_state": "confirmed_zero",
            "expected_requests": 0,
            "completed_requests": 0,
        },
        {
            "status": "complete",
            "data_state": "confirmed_data",
            "expected_requests": "1",
            "completed_requests": "1",
        },
        {
            "status": "complete",
            "data_state": "confirmed_data",
            "expected_requests": True,
            "completed_requests": True,
        },
    ],
)
def test_snapchat_coverage_rejects_zero_or_malformed_request_counts(coverage):
    item = {
        "coverage": coverage,
        "errors_count": 0,
        "errors": [],
        "ad_squad_performance": {
            "coverage": _complete_coverage(),
            "errors_count": 0,
            "errors": [],
        },
        "ad_performance": {
            "coverage": _complete_coverage(),
            "errors_count": 0,
            "errors": [],
        },
    }

    assert scheduler._snapchat_coverage_complete(coverage) is False
    assert scheduler._snapchat_item_complete(item) is False
    aggregate = scheduler._snapchat_run_coverage([item], accounts_expected=1)
    assert aggregate["status"] == "incomplete"
    assert aggregate["data_state"] == "unknown_incomplete"


@pytest.mark.parametrize("target", ["top", "nested"])
def test_snapchat_malformed_error_envelope_blocks_complete_proof(target):
    item = {
        "coverage": _complete_coverage(),
        "errors_count": 0,
        "errors": [],
        "ad_squad_performance": {
            "coverage": _complete_coverage(),
            "errors_count": 0,
            "errors": [],
        },
        "ad_performance": {
            "coverage": _complete_coverage(),
            "errors_count": 0,
            "errors": [],
        },
    }
    if target == "top":
        item["errors"] = {"code": "malformed"}
    else:
        item["ad_performance"]["errors"] = ["malformed"]

    errors = scheduler._snapchat_item_errors(item)
    assert scheduler._snapchat_item_complete(item) is False
    assert any("error_envelope_invalid" in error["code"] for error in errors)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raise_error", "complete_item", "outcome_failure"),
    [
        (False, False, False),
        (True, False, False),
        (False, True, False),
        (False, True, True),
    ],
)
async def test_snapchat_refresh_advances_proof_only_for_complete_account(
    monkeypatch,
    caplog,
    raise_error,
    complete_item,
    outcome_failure,
):
    account = {
        "ad_account_id": "account-1",
        "timezone": "Asia/Riyadh",
        "currency": "SAR",
        "connection_provenance": "legacy_integration",
    }
    item = {
        "ad_account_id": "account-1",
        "rows_saved": 0,
        "campaign_rows_saved": 0,
        "campaign_facts_source_mode": scheduler.snapchat_hourly.CAMPAIGN_FACTS_SOURCE_MODE,
        "campaign_facts_schema_version": scheduler.snapchat_hourly.CAMPAIGN_FACTS_SCHEMA_VERSION,
        "errors_count": 0,
        "errors": [],
        "coverage": {
            "status": "incomplete",
            "data_state": "unknown_incomplete",
            "expected_requests": 2,
            "completed_requests": 1,
        },
        "ad_squad_performance": {
            "coverage": _complete_coverage("confirmed_no_data"),
            "errors_count": 0,
            "errors": [],
        },
        "ad_performance": {
            "coverage": _complete_coverage("confirmed_no_data"),
            "errors_count": 0,
            "errors": [],
        },
    }
    if complete_item:
        item["rows_saved"] = 1
        item["campaign_rows_saved"] = 1
        item["coverage"] = _complete_coverage()

    async def no_active(*args, **kwargs):
        return None

    async def start_run(*args, **kwargs):
        return "run-1"

    async def load_accounts(*args, **kwargs):
        return [account]

    async def noop(*args, **kwargs):
        return None

    finished = []

    async def finish_run(*args, **kwargs):
        finished.append(deepcopy(kwargs))

    async def record_error(*args, **kwargs):
        return "error-1"

    async def refresh(*args, **kwargs):
        args[0].provider_calls = 3
        if raise_error:
            raise scheduler.SnapchatNativeSyncError(
                "snapchat_account_hour_timeseries_stat_missing",
                "Malformed successful response.",
                status_code=502,
                retryable=True,
                result={"coverage": deepcopy(item["coverage"])},
            )
        return deepcopy(item)

    outcome_secret = "Bearer decision-outcomes-secret raw-outcome-payload"

    async def evaluate_outcomes(*args, **kwargs):
        if outcome_failure:
            raise RuntimeError(outcome_secret)
        return {"status": "complete", "evaluated": 0}

    class Context:
        def __init__(self, db, user_id, now=None):
            self.provider_calls = 0

        async def access_token(self):
            return "access-token"

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

    updates = {}

    class Collection:
        def __init__(self, name):
            self.name = name

        async def update_one(self, query, update, upsert=False):
            updates.setdefault(self.name, []).append({
                "query": deepcopy(query),
                "update": deepcopy(update),
                "upsert": upsert,
            })
            return type("UpdateResult", (), {"matched_count": 1})()

        async def update_many(self, query, update):
            updates.setdefault(self.name, []).append({
                "query": deepcopy(query),
                "update": deepcopy(update),
                "upsert": False,
            })
            return type("UpdateResult", (), {"matched_count": 1})()

    monkeypatch.setattr(scheduler, "snapchat_oauth_configured", lambda: True)
    monkeypatch.setattr(scheduler, "snapchat_native_sync_enabled", lambda: True)
    monkeypatch.setattr(scheduler, "_active_run", no_active)
    monkeypatch.setattr(scheduler, "_start_run", start_run)
    monkeypatch.setattr(
        scheduler,
        "_load_canonical_scheduler_accounts",
        load_accounts,
    )
    monkeypatch.setattr(scheduler, "ensure_snapchat_native_sync_indexes", noop)
    monkeypatch.setattr(scheduler, "_finish_run", finish_run)
    monkeypatch.setattr(scheduler, "_record_error", record_error)
    monkeypatch.setattr(
        scheduler,
        "_evaluate_snapchat_outcomes_after_sync",
        evaluate_outcomes,
    )
    monkeypatch.setattr(scheduler, "SnapchatSyncContext", Context)
    monkeypatch.setattr(scheduler.httpx, "AsyncClient", Client)
    monkeypatch.setattr(scheduler.snapchat_hourly, "refresh_snapchat_account_hours", refresh)
    monkeypatch.setattr(
        scheduler,
        "_collection",
        lambda db, name: Collection(name),
    )
    caplog.set_level(logging.ERROR, logger=scheduler.__name__)

    result = await scheduler._refresh_snapchat(
        object(),
        user_id="tenant-1",
        start_date=datetime(2026, 8, 1, tzinfo=timezone.utc).date(),
        end_date=datetime(2026, 8, 2, tzinfo=timezone.utc).date(),
        now=datetime(2026, 8, 3, tzinfo=timezone.utc),
    )

    account_patch = updates["mezan_integration_accounts_v2"][0]["update"]["$set"]
    integration_patch = updates["mezan_integrations_v2"][0]["update"]["$set"]
    if outcome_failure:
        assert result["status"] == "failed"
        assert result["code"] == "snapchat_scheduler_runtime_error"
        assert finished[0]["status"] == "complete"
        terminal_failure = finished[-1]
        assert terminal_failure["status"] == "failed"
        assert terminal_failure["result"]["coverage"] == {
            "status": "incomplete",
            "data_state": "unknown_incomplete",
            "expected_requests": 1,
            "completed_requests": 0,
        }
        assert terminal_failure["error"]["failure_stage"] == (
            "decision_outcomes_evaluation"
        )
        assert terminal_failure["error"]["exception_type"] == "RuntimeError"
        assert terminal_failure["error"]["failure_module"] == (
            "integrations_control_center.ads_auto_sync_scheduler"
        )
        assert terminal_failure["error"]["failure_function"] == (
            "_refresh_snapchat"
        )
        assert terminal_failure["error"]["failure_line"] > 0
        assert outcome_secret not in repr(finished)
        assert outcome_secret not in repr(result)
        assert outcome_secret not in caplog.text
        return
    if complete_item:
        assert result["status"] == "complete"
        assert result["coverage"] == {
            "status": "complete",
            "data_state": "confirmed_data",
            "expected_requests": 3,
            "completed_requests": 3,
        }
        assert result["accounts_attempted"] == 1
        assert result["accounts_complete"] == 1
        assert result["account_provider_calls"] == [
            {"ad_account_id": "account-1", "provider_calls": 3}
        ]
        assert finished[0]["status"] == "complete"
        assert finished[0]["result"]["coverage"] == result["coverage"]
        assert account_patch["last_sync_at"]
        assert account_patch["last_observed_at"]
        assert integration_patch["last_sync_at"]
        assert account_patch["health_score"] == 100
        assert integration_patch["health_score"] == 100
    else:
        assert result["status"] == "partial"
        assert result["coverage"]["status"] == "incomplete"
        for patch in (account_patch, integration_patch):
            assert "last_sync_at" not in patch
            assert "last_observed_at" not in patch
            assert patch["health_score"] != 100
            assert patch["data_delay_minutes"] is None


def test_status_never_exposes_global_results_from_another_tenant():
    owner_secret = "Authorization: Bearer owner-status-secret"
    db = _DB(
        {
            scheduler.SCHEDULER_COLLECTION: [
                {
                    "_id": scheduler.SCHEDULER_ID,
                    "status": "complete",
                    "last_started_at": "2026-08-12T12:00:00+00:00",
                    "last_finished_at": "2026-08-12T12:01:00+00:00",
                    "next_due_at": "2026-08-12T12:05:00+00:00",
                    "last_result": {
                        "status": "complete",
                        "started_at": "2026-08-12T12:00:00+00:00",
                        "finished_at": "2026-08-12T12:01:00+00:00",
                        "results": [
                            {
                                "run_id": "foreign-run-b",
                                "account_provider_calls": [
                                    {
                                        "ad_account_id": "foreign-account-b",
                                        "provider_calls": 7,
                                    }
                                ],
                                "error_samples": [
                                    {"error_id": "foreign-error-b"}
                                ],
                            }
                        ],
                    },
                    "last_error": {
                        "code": "ads_auto_sync_cycle_failed",
                        "message": "foreign-account-b failed",
                        "retryable": True,
                    },
                }
            ],
            scheduler.RUNS_COLLECTION: [
                {
                    "user_id": "tenant-a",
                    "trigger": scheduler.TRIGGER,
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "run_id": "tenant-a-run",
                    "status": "failed",
                    "started_at": "2026-08-12T12:00:00+00:00",
                    "access_token": owner_secret,
                    "summary": {
                        "raw_response_payload": owner_secret,
                        "coverage": {
                            "status": "incomplete",
                            "data_state": "unknown_incomplete",
                            "expected_requests": 1,
                            "completed_requests": 0,
                        }
                    },
                    "error": {
                        "code": "snapchat_scheduler_runtime_error",
                        "message": (
                            "Snapchat scheduler refresh failed unexpectedly."
                        ),
                        "exception_message": owner_secret,
                        "retryable": True,
                        "failure_stage": "provider_refresh",
                        "exception_type": "RuntimeError",
                        "run_id": "tenant-a-run",
                        "failure_module": (
                            "integrations_control_center.ads_auto_sync_scheduler"
                        ),
                        "failure_function": "_refresh_snapchat",
                        "failure_line": 1422,
                        "failure_path": owner_secret,
                        "traceback": owner_secret,
                        "locals": {"authorization": owner_secret},
                    },
                },
                {
                    "user_id": "tenant-b",
                    "trigger": scheduler.TRIGGER,
                    "provider": scheduler.SNAPCHAT_PROVIDER_ID,
                    "run_id": "foreign-run-b",
                    "started_at": "2026-08-12T12:00:00+00:00",
                    "summary": {
                        "account_provider_calls": [
                            {"ad_account_id": "foreign-account-b"}
                        ]
                    },
                    "error": {"error_id": "foreign-error-b"},
                },
            ],
        }
    )

    result = asyncio.run(scheduler.auto_sync_status(db, "tenant-a"))

    runs_projection = next(
        projection
        for name, operation, projection in db.projections
        if name == scheduler.RUNS_COLLECTION and operation == "find"
    )
    assert runs_projection["error.failure_module"] == 1
    assert runs_projection["error.failure_function"] == 1
    assert runs_projection["error.failure_line"] == 1

    assert result["providers"][scheduler.SNAPCHAT_PROVIDER_ID]["run_id"] == (
        "tenant-a-run"
    )
    owner_run = result["providers"][scheduler.SNAPCHAT_PROVIDER_ID]
    assert owner_run["status"] == "failed"
    assert owner_run["summary"]["coverage"]["data_state"] == (
        "unknown_incomplete"
    )
    assert owner_run["error"]["failure_stage"] == "provider_refresh"
    assert owner_run["error"]["exception_type"] == "RuntimeError"
    assert owner_run["error"]["run_id"] == "tenant-a-run"
    assert owner_run["error"]["failure_module"] == (
        "integrations_control_center.ads_auto_sync_scheduler"
    )
    assert owner_run["error"]["failure_function"] == "_refresh_snapchat"
    assert owner_run["error"]["failure_line"] == 1422
    assert "failure_path" not in owner_run["error"]
    assert "traceback" not in owner_run["error"]
    assert "locals" not in owner_run["error"]
    assert "message" not in owner_run["error"]
    assert "access_token" not in owner_run
    assert "raw_response_payload" not in owner_run["summary"]
    assert result["scheduler"]["last_result"] == {
        "status": "complete",
        "started_at": "2026-08-12T12:00:00+00:00",
        "finished_at": "2026-08-12T12:01:00+00:00",
    }
    assert result["scheduler"]["last_error"] == {
        "code": "ads_auto_sync_cycle_failed",
        "retryable": True,
    }
    serialized = repr(result)
    assert "foreign-run-b" not in serialized
    assert "foreign-account-b" not in serialized
    assert "foreign-error-b" not in serialized
    assert owner_secret not in serialized

    malicious_location_run = deepcopy(
        db.rows[scheduler.RUNS_COLLECTION][0]
    )
    token_shaped_exception_type = "eyJhbGciOiJIUzI1NiJ9.secret.signature"
    malicious_location_run["error"].update({
        "failure_module": f"C:/private/{owner_secret}/scheduler.py",
        "failure_function": f"refresh\n{owner_secret}",
        "failure_line": True,
        "exception_type": token_shaped_exception_type,
    })
    sanitized = scheduler._safe_provider_run_status(malicious_location_run)
    assert "exception_type" not in sanitized["error"]
    assert "failure_module" not in sanitized["error"]
    assert "failure_function" not in sanitized["error"]
    assert "failure_line" not in sanitized["error"]
    assert owner_secret not in repr(sanitized)
    assert token_shaped_exception_type not in repr(sanitized)

