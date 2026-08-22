from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timezone

import pytest

import dashboard_snapchat_spend as module
import dashboard_v2_routes as executive_module
from integrations_control_center import dashboard_ads_platform_refresh as refresh_module
from integrations_control_center import dashboard_ads_platform_spend_routes as chart_module


SOURCE = module.snapchat_hourly.ACCOUNT_REFRESH_SOURCE_MODE
NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
DAY = date(2026, 8, 20)


@pytest.mark.asyncio
async def test_dashboard_refresh_never_writes_unproven_snapchat_projection():
    class WriteTrap:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected dashboard refresh access: {name}")

    result = await refresh_module._refresh_snapchat(
        WriteTrap(),
        "owner-1",
        DAY,
        DAY,
    )
    assert result == {
        "provider": "snapchat_ads",
        "status": "incomplete",
        "reason": "canonical_dashboard_scheduler_required",
        "projection_write_reached": False,
        "rows_saved": 0,
        "errors_count": 0,
    }


def _path(document, dotted):
    value = document
    for part in dotted.split("."):
        if not isinstance(value, dict) or part not in value:
            return None, False
        value = value[part]
    return value, True


def _matches(document, query):
    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in condition):
                return False
            continue
        value, exists = _path(document, key)
        if not isinstance(condition, dict):
            if condition is None:
                if exists and value is not None:
                    return False
            elif not exists or value != condition:
                return False
            continue
        for operator, expected in condition.items():
            if operator == "$in":
                if not exists or value not in expected:
                    return False
            elif operator == "$gte":
                if not exists or value < expected:
                    return False
            elif operator == "$lte":
                if not exists or value > expected:
                    return False
            elif operator == "$ne":
                if exists and value == expected:
                    return False
            elif operator == "$exists":
                if exists is not bool(expected):
                    return False
            else:
                raise AssertionError(f"unsupported operator: {operator}")
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(list(rows))

    def sort(self, key, direction=None):
        field, order = key[0] if isinstance(key, list) else (key, direction)
        self.rows.sort(
            key=lambda row: _path(row, field)[0] or "",
            reverse=order == -1,
        )
        return self

    def limit(self, length):
        self.rows = self.rows[:length]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, query, projection=None):
        return FakeCursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query, projection=None):
        for row in self.rows:
            if _matches(row, query):
                return deepcopy(row)
        return None


class FakeDB:
    def __init__(self, collections):
        self.collections = {
            name: FakeCollection(rows) for name, rows in collections.items()
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    def __getattr__(self, name):
        return self[name]


def _coverage(state="confirmed_data"):
    return {
        "status": "complete",
        "data_state": state,
        "expected_requests": 1,
        "completed_requests": 1,
    }


def _run(
    *,
    run_id="run-good",
    state="confirmed_data",
    status="complete",
    started="2026-08-21T08:00:00+00:00",
    finished="2026-08-21T08:05:00+00:00",
    account_id="snap-1",
):
    return {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "run_type": "analytics_refresh",
        "run_id": run_id,
        "status": status,
        "source_mode": SOURCE,
        "started_at": started,
        "finished_at": finished,
        "summary": {
            "date_from": DAY.isoformat(),
            "date_to": DAY.isoformat(),
            "coverage": _coverage(state),
            "accounts_attempted": 1,
            "accounts_complete": 1,
            "provider_calls": 2,
            "errors_count": 0,
            "account_provider_calls": [
                {"ad_account_id": account_id, "provider_calls": 2}
            ],
        },
    }


def _account(account_id="snap-1", *, provenance=True):
    value = {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "connection_status": "connected",
        "mezan_selected": True,
        "ad_account_id": account_id,
        "external_account_id": account_id,
        "mezan_integration_account_id": f"identity-{account_id}",
        "currency": "SAR",
        "source_mode": SOURCE,
        "data_quality": "complete",
        "coverage": _coverage(),
        "last_sync_at": "2026-08-21T08:04:00+00:00",
    }
    if provenance:
        value["connection_provenance"] = "api_connection"
    return value


def _integration():
    return {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "connection_status": "connected",
        "source_mode": SOURCE,
        "data_quality": "complete",
        "coverage": _coverage(),
        "last_sync_at": "2026-08-21T08:04:00+00:00",
    }


def _fact(*, spend=10, currency="SAR", updated="2026-08-21T08:02:00+00:00"):
    return {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "ad_account_id": "snap-1",
        "entity_type": "ad_account",
        "external_id": "snap-1",
        "date": DAY.isoformat(),
        "attribution_model": module.ATTRIBUTION_MODEL,
        "date_timezone": "Asia/Riyadh",
        "business_timezone": "Asia/Riyadh",
        "stored_granularity": "RIYADH_DAY",
        "provider_granularity": "HOUR",
        "provider_breakdown": "campaign",
        "currency": currency,
        "spend_native": spend,
        "spend_sar": 999999,
        "provider_window_start": "2026-08-19T21:00:00+00:00",
        "provider_window_end": "2026-08-20T21:00:00+00:00",
        "source_mode": SOURCE,
        "updated_at": updated,
    }


def _db(*, runs=None, facts=None, accounts=None, settings=None, integration=None):
    return FakeDB(
        {
            "mezan_integrations_v2": [integration or _integration()],
            "mezan_integration_accounts_v2": [_account()] if accounts is None else accounts,
            "mezan_integration_sync_runs_v2": [_run()] if runs is None else runs,
            "mezan_snapchat_performance_daily_v2": [_fact()] if facts is None else facts,
            "mezan_ad_account_cost_settings_v2": [] if settings is None else settings,
        }
    )


async def _load(db, *, day=DAY, now=NOW):
    return await module.load_snapchat_dashboard_spend(
        db,
        "owner-1",
        start=day,
        end=day,
        now=now,
    )


@pytest.mark.asyncio
async def test_valid_native_fact_ignores_stored_sar_and_returns_confirmed_data():
    result = await _load(_db())
    assert result["total_sar"] == 10.0
    assert result["quality"]["data_state"] == "confirmed_data"
    assert result["quality"]["amount_complete"] is True


@pytest.mark.asyncio
async def test_explicit_financial_proof_survives_partial_projection_run():
    run = _run(status="partial")
    run["summary"].update({
        "accounts_complete": 0,
        "errors_count": 4,
        "coverage": {
            "status": "incomplete",
            "data_state": "unknown_incomplete",
            "expected_requests": 234,
            "completed_requests": 92,
        },
        "financial_proof": {
            "version": 1,
            "status": "complete",
            "accounts_complete": 1,
            "errors_count": 0,
            "coverage": _coverage(),
        },
    })

    account = _account()
    account.update({
        "financial_data_quality": "complete",
        "financial_source_mode": SOURCE,
        "financial_coverage": _coverage(),
        "financial_last_sync_at": account["last_sync_at"],
    })
    integration = _integration()
    integration.update({
        "financial_data_quality": "complete",
        "financial_source_mode": SOURCE,
        "financial_coverage": _coverage(),
        "financial_last_sync_at": integration["last_sync_at"],
    })

    result = await _load(
        _db(runs=[run], accounts=[account], integration=integration)
    )

    assert result["total_sar"] == 10.0
    assert result["quality"]["data_state"] == "confirmed_data"
    assert result["quality"]["proof_runs"][0]["run_id"] == "run-good"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", 2),
        ("status", "partial"),
        ("accounts_complete", 0),
        ("errors_count", 1),
        ("coverage", {"status": "incomplete"}),
    ],
)
async def test_partial_run_rejects_malformed_financial_proof(field, value):
    run = _run(status="partial")
    run["summary"]["financial_proof"] = {
        "version": 1,
        "status": "complete",
        "accounts_complete": 1,
        "errors_count": 0,
        "coverage": _coverage(),
        field: value,
    }

    result = await _load(_db(runs=[run]))

    assert result["total_sar"] is None
    assert result["quality"]["data_state"] == "unknown_incomplete"


@pytest.mark.asyncio
async def test_usd_uses_mezan2_account_fx_exactly_once():
    account = _account()
    account["currency"] = "USD"
    setting = {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "external_account_id": "snap-1",
        "mezan_integration_account_id": "identity-snap-1",
        "native_currency": "USD",
        "exchange_rate_to_sar": 3.75,
        "bank_commission_pct": 1.5,
        "apply_bank_commission": True,
    }
    result = await _load(
        _db(accounts=[account], facts=[_fact(spend=10, currency="USD")], settings=[setting])
    )
    assert result["total_sar"] == 37.5
    assert result["rows"][0]["effective_spend_sar"] == 37.5


@pytest.mark.asyncio
async def test_fact_cannot_borrow_another_selected_accounts_fx_identity():
    accounts = [_account("snap-1"), _account("snap-2")]
    for account in accounts:
        account["currency"] = "USD"
    fact_one = _fact(spend=10, currency="USD")
    fact_one["mezan_integration_account_id"] = "identity-snap-2"
    fact_two = _fact(spend=10, currency="USD")
    fact_two.update({
        "ad_account_id": "snap-2",
        "external_id": "snap-2",
        "mezan_integration_account_id": "identity-snap-2",
    })
    run = _run()
    run["summary"].update({
        "accounts_attempted": 2,
        "accounts_complete": 2,
        "provider_calls": 4,
        "account_provider_calls": [
            {"ad_account_id": "snap-1", "provider_calls": 2},
            {"ad_account_id": "snap-2", "provider_calls": 2},
        ],
    })
    settings = [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "external_account_id": account_id,
            "mezan_integration_account_id": f"identity-{account_id}",
            "native_currency": "USD",
            "exchange_rate_to_sar": rate,
            "bank_commission_pct": 1.5,
            "apply_bank_commission": True,
        }
        for account_id, rate in (("snap-1", 2.0), ("snap-2", 5.0))
    ]
    result = await _load(
        _db(
            runs=[run],
            facts=[fact_one, fact_two],
            accounts=accounts,
            settings=settings,
        )
    )
    assert result["total_sar"] is None
    assert result["quality"]["data_state"] == "unknown_incomplete"


@pytest.mark.asyncio
async def test_confirmed_zero_is_numeric_but_no_data_is_explicit_null():
    zero = await _load(_db(runs=[_run(state="confirmed_zero")], facts=[_fact(spend=0)]))
    assert zero["total_sar"] == 0.0
    assert zero["quality"]["data_state"] == "confirmed_zero"

    no_data = await _load(_db(runs=[_run(state="confirmed_no_data")], facts=[]))
    assert no_data["total_sar"] is None
    assert no_data["quality"]["data_state"] == "confirmed_no_data"
    assert no_data["quality"]["coverage_complete"] is True


@pytest.mark.asyncio
async def test_missing_or_malformed_run_coverage_never_becomes_zero():
    run = _run()
    run["summary"].pop("coverage")
    result = await _load(_db(runs=[run]))
    assert result["total_sar"] is None
    assert result["quality"]["data_state"] == "unknown_incomplete"

    malformed = _run()
    malformed["summary"]["provider_calls"] = "1.5"
    malformed_result = await _load(_db(runs=[malformed]))
    assert malformed_result["total_sar"] is None
    assert malformed_result["quality"]["data_state"] == "unknown_incomplete"

    impossible_coverage = _run()
    impossible_coverage["summary"]["coverage"].update({
        "expected_requests": 100,
        "completed_requests": 100,
    })
    impossible_result = await _load(_db(runs=[impossible_coverage]))
    assert impossible_result["total_sar"] is None
    assert impossible_result["quality"]["data_state"] == "unknown_incomplete"


@pytest.mark.asyncio
async def test_malformed_fact_date_suffix_is_never_canonical_evidence():
    malformed = _fact()
    malformed["date"] = f"{DAY.isoformat()}junk"
    result = await module.load_snapchat_dashboard_spend(
        _db(facts=[malformed]),
        "owner-1",
        start=DAY,
        end=date(2026, 8, 21),
        now=NOW,
    )
    assert result["daily_sar"][DAY.isoformat()] is None
    assert result["daily_state"][DAY.isoformat()] == "unknown_incomplete"


@pytest.mark.asyncio
async def test_current_fact_with_old_provider_window_is_unknown():
    current_day = date(2026, 8, 21)
    run = _run(
        started="2026-08-21T11:55:00+00:00",
        finished="2026-08-21T12:00:00+00:00",
    )
    run["summary"]["date_from"] = current_day.isoformat()
    run["summary"]["date_to"] = current_day.isoformat()
    account = _account()
    account["last_sync_at"] = "2026-08-21T11:59:00+00:00"
    integration = _integration()
    integration["last_sync_at"] = "2026-08-21T11:59:00+00:00"
    fact = _fact(updated="2026-08-21T11:58:00+00:00")
    fact["date"] = current_day.isoformat()
    fact["provider_window_start"] = "2026-08-20T21:00:00+00:00"
    fact["provider_window_end"] = "2026-08-21T11:00:00+00:00"
    result = await _load(
        _db(runs=[run], facts=[fact], accounts=[account], integration=integration),
        day=current_day,
        now=datetime(2026, 8, 21, 12, 1, tzinfo=timezone.utc),
    )
    assert result["total_sar"] is None
    assert result["quality"]["data_state"] == "unknown_incomplete"

    fact["provider_window_end"] = "2026-08-21T12:00:00+00:00"
    fresh = await _load(
        _db(runs=[run], facts=[fact], accounts=[account], integration=integration),
        day=current_day,
        now=datetime(2026, 8, 21, 12, 1, tzinfo=timezone.utc),
    )
    assert fresh["total_sar"] == 10.0


@pytest.mark.asyncio
async def test_current_no_data_requires_a_provably_current_provider_hour():
    current_day = date(2026, 8, 21)
    stale = _run(
        state="confirmed_no_data",
        started="2026-08-21T11:59:00+00:00",
        finished="2026-08-21T12:01:00+00:00",
    )
    stale["summary"]["date_from"] = current_day.isoformat()
    stale["summary"]["date_to"] = current_day.isoformat()
    account = _account()
    account["coverage"] = _coverage("confirmed_no_data")
    account["last_sync_at"] = "2026-08-21T12:00:30+00:00"
    integration = _integration()
    integration["coverage"] = _coverage("confirmed_no_data")
    integration["last_sync_at"] = "2026-08-21T12:00:30+00:00"
    stale_result = await _load(
        _db(runs=[stale], facts=[], accounts=[account], integration=integration),
        day=current_day,
        now=datetime(2026, 8, 21, 12, 6, tzinfo=timezone.utc),
    )
    assert stale_result["quality"]["data_state"] == "unknown_incomplete"

    current = deepcopy(stale)
    current["started_at"] = "2026-08-21T12:01:00+00:00"
    current["finished_at"] = "2026-08-21T12:03:00+00:00"
    account["last_sync_at"] = "2026-08-21T12:02:00+00:00"
    integration["last_sync_at"] = "2026-08-21T12:02:00+00:00"
    current_result = await _load(
        _db(runs=[current], facts=[], accounts=[account], integration=integration),
        day=current_day,
        now=datetime(2026, 8, 21, 12, 6, tzinfo=timezone.utc),
    )
    assert current_result["total_sar"] is None
    assert current_result["quality"]["data_state"] == "confirmed_no_data"


@pytest.mark.asyncio
async def test_overlapping_possible_writer_blocks_but_strict_gap_allows():
    good = _run(
        started="2026-08-21T08:02:00+00:00",
        finished="2026-08-21T08:05:00+00:00",
    )
    overlapping = _run(
        run_id="run-partial",
        status="partial",
        started="2026-08-21T08:00:00+00:00",
        finished="2026-08-21T08:04:00+00:00",
    )
    ambiguous = await _load(
        _db(runs=[good, overlapping], facts=[_fact(updated="2026-08-21T08:03:00+00:00")])
    )
    assert ambiguous["total_sar"] is None

    nonoverlap = deepcopy(overlapping)
    nonoverlap["finished_at"] = "2026-08-21T08:01:00+00:00"
    proven = await _load(
        _db(runs=[good, nonoverlap], facts=[_fact(updated="2026-08-21T08:03:00+00:00")])
    )
    assert proven["total_sar"] == 10.0


@pytest.mark.asyncio
async def test_duplicate_run_id_never_hides_an_overlapping_attempt():
    good = _run(
        run_id="duplicated-run-id",
        started="2026-08-21T08:02:00+00:00",
        finished="2026-08-21T08:05:00+00:00",
    )
    overlapping = _run(
        run_id="duplicated-run-id",
        status="partial",
        started="2026-08-21T08:00:00+00:00",
        finished="2026-08-21T08:04:00+00:00",
    )
    result = await _load(
        _db(
            runs=[good, overlapping],
            facts=[_fact(updated="2026-08-21T08:03:00+00:00")],
        )
    )
    assert result["total_sar"] is None
    assert result["quality"]["data_state"] == "unknown_incomplete"


@pytest.mark.asyncio
async def test_newer_incomplete_attempt_blocks_older_complete_fact():
    newer = _run(
        run_id="run-newer",
        status="partial",
        started="2026-08-21T09:00:00+00:00",
        finished="2026-08-21T09:03:00+00:00",
    )
    result = await _load(_db(runs=[_run(), newer]))
    assert result["total_sar"] is None
    assert result["quality"]["data_state"] == "unknown_incomplete"


@pytest.mark.asyncio
async def test_failed_diagnostic_run_still_rejects_an_existing_fact():
    failed = _run(status="failed")
    failed["error"] = {
        "code": "snapchat_scheduler_runtime_error",
        "message": "Snapchat scheduler refresh failed unexpectedly.",
        "retryable": True,
        "failure_stage": "decision_outcomes_evaluation",
        "exception_type": "RuntimeError",
        "run_id": failed["run_id"],
        "failure_module": "integrations_control_center.ads_auto_sync_scheduler",
        "failure_function": "_refresh_snapchat",
        "failure_line": 1364,
    }

    result = await _load(_db(runs=[failed], facts=[_fact()]))

    assert result["total_sar"] is None
    assert result["daily_sar"][DAY.isoformat()] is None
    assert result["quality"]["data_state"] == "unknown_incomplete"
    assert result["quality"]["amount_complete"] is False


@pytest.mark.asyncio
async def test_selected_migrated_account_without_provenance_is_not_silently_omitted():
    migrated = _account("snap-migrated", provenance=False)
    result = await _load(_db(accounts=[_account(), migrated]))
    assert result["total_sar"] is None
    assert "selected_account_not_in_run" in result["quality"]["reason_codes"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "spend", "migrated_spend", "expected_total"),
    [
        ("confirmed_data", 10, 20, 30.0),
        ("confirmed_zero", 0, 0, 0.0),
    ],
)
async def test_selected_migrated_account_is_numeric_when_canonical_proof_exists(
    state,
    spend,
    migrated_spend,
    expected_total,
):
    accounts = [_account(), _account("snap-migrated", provenance=False)]
    for account in accounts:
        account["coverage"] = _coverage(state)
    run = _run(state=state)
    run["summary"].update({
        "accounts_attempted": 2,
        "accounts_complete": 2,
        "provider_calls": 4,
        "account_provider_calls": [
            {"ad_account_id": "snap-1", "provider_calls": 2},
            {"ad_account_id": "snap-migrated", "provider_calls": 2},
        ],
    })
    migrated_fact = _fact(spend=migrated_spend)
    migrated_fact.update({
        "ad_account_id": "snap-migrated",
        "external_id": "snap-migrated",
    })
    integration = _integration()
    integration["coverage"] = _coverage(state)
    result = await _load(
        _db(
            accounts=accounts,
            runs=[run],
            facts=[_fact(spend=spend), migrated_fact],
            integration=integration,
        )
    )
    assert result["quality"]["selected_account_count"] == 2
    assert result["total_sar"] == expected_total
    assert result["daily_sar"][DAY.isoformat()] == expected_total
    assert result["daily_state"][DAY.isoformat()] == state
    assert result["quality"]["data_state"] == state
    assert result["quality"]["coverage_complete"] is True
    assert result["quality"]["amount_complete"] is True
    assert result["quality"]["reason_codes"] == []


@pytest.mark.asyncio
async def test_selected_account_without_canonical_identity_cannot_be_silently_omitted():
    broken = _account("broken")
    broken.pop("ad_account_id")
    broken.pop("external_account_id")
    result = await _load(_db(accounts=[_account(), broken]))
    assert result["total_sar"] is None
    assert result["quality"]["data_state"] == "unknown_incomplete"
    assert "selected_account_identity_missing" in result["quality"]["reason_codes"]


@pytest.mark.asyncio
async def test_selected_account_with_conflicting_canonical_identities_fails_closed():
    broken = _account("snap-1")
    broken["external_account_id"] = "different-external-id"
    result = await _load(_db(accounts=[broken]))
    assert result["total_sar"] is None
    assert result["quality"]["data_state"] == "unknown_incomplete"
    assert "selected_account_identity_ambiguous" in result["quality"]["reason_codes"]


@pytest.mark.asyncio
async def test_no_data_conflicting_fact_and_other_tenant_evidence_fail_closed():
    malformed_fact = _fact()
    malformed_fact["currency"] = "EUR"
    conflict = await _load(
        _db(runs=[_run(state="confirmed_no_data")], facts=[malformed_fact])
    )
    assert conflict["total_sar"] is None
    assert conflict["quality"]["data_state"] == "unknown_incomplete"

    other_run = _run(run_id="foreign")
    other_run["user_id"] = "owner-2"
    other_fact = _fact(spend=500)
    other_fact["user_id"] = "owner-2"
    isolated = await _load(_db(runs=[_run(), other_run], facts=[_fact(), other_fact]))
    assert isolated["total_sar"] == 10.0


def _canonical_snapshot(state, amount):
    amount_complete = state in {"confirmed_data", "confirmed_zero", "not_connected"}
    return {
        "rows": (
            [{
                "ad_account_id": "snap-1",
                "date": DAY.isoformat(),
                "spend_native": amount,
                "effective_spend_sar": amount,
                "purchases": 1,
            }]
            if amount_complete and amount is not None
            else []
        ),
        "daily_sar": {DAY.isoformat(): amount},
        "daily_state": {DAY.isoformat(): state},
        "total_sar": amount,
        "bank_commissions": ({"accounts": [], "total_fee_sar": 0} if amount_complete else None),
        "quality": {
            "status": "complete" if state != "unknown_incomplete" else "incomplete",
            "data_state": state,
            "coverage_complete": state != "unknown_incomplete",
            "amount_complete": amount_complete,
            "complete": amount_complete,
            "connected": state != "not_connected",
            "reason_codes": [],
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("state", "amount"),
    [
        ("confirmed_data", 10.0),
        ("confirmed_zero", 0.0),
        ("confirmed_no_data", None),
        ("unknown_incomplete", None),
    ],
)
async def test_chart_and_executive_share_the_same_snapchat_amount_and_state(
    monkeypatch, state, amount
):
    async def canonical(*_args, **_kwargs):
        return deepcopy(_canonical_snapshot(state, amount))

    monkeypatch.setattr(chart_module, "load_snapchat_dashboard_spend", canonical)
    monkeypatch.setattr(executive_module, "load_snapchat_dashboard_spend", canonical)
    db = _db(runs=[], facts=[])
    chart = await chart_module.build_dashboard_platform_spend(
        db,
        "owner-1",
        date_from=DAY.isoformat(),
        date_to=DAY.isoformat(),
    )
    executive = await executive_module.build_mezan_v2_ads(
        db,
        "owner-1",
        from_date=DAY.isoformat(),
        to_date=DAY.isoformat(),
    )

    assert chart["provider_totals_sar"]["snapchat"] == amount
    assert executive["breakdown"]["snapchat"] == amount
    assert chart["providers"]["snapchat"]["data_state"] == state
    assert executive["providers"]["snapchat"]["data_state"] == state
    assert chart["total_sar"] == executive["total"]
    if amount is None:
        assert chart["total_sar"] is None
        assert executive["total"] is None
        assert executive["known_subtotal_sar"] == 0.0
    else:
        assert executive["total"] == amount


def test_executive_provider_summary_preserves_confirmed_no_data():
    ads = {
        "breakdown": {
            "snapchat": None,
            "meta": 0,
            "tiktok": 0,
            "google_transitional": 0,
        },
        "providers": {
            "snapchat": {
                "orders": None,
                "data_state": "confirmed_no_data",
                "coverage_complete": True,
                "amount_complete": False,
            },
            "meta": {"orders": 0},
            "tiktok": {"orders": 0},
        },
    }
    result = executive_module.build_salla_ads_executive_breakdown([], ads)
    snap = result["providers"]["snapchat"]
    assert snap["spend_sar"] is None
    assert snap["data_state"] == "confirmed_no_data"
    assert result["total"]["spend_sar"] is None


@pytest.mark.asyncio
async def test_dashboard_v2_keeps_dependent_financial_totals_null_when_snapchat_is_unknown(
    monkeypatch,
):
    async def legacy_dashboard(**_kwargs):
        return {
            "totals": {
                "total_sales": 100,
                "total_orders": 1,
                "total_product_cost": 20,
                "total_ads_cost": 30,
                "operating_expenses_total": 5,
                "operating_salaries_total": 0,
                "net_profit": 45,
                "net_sales": 45,
            },
            "net_sales_config": {
                "deduct_product_costs": True,
                "deduct_ads": True,
                "deduct_operating_expenses": True,
                "deduct_payment_fees": True,
            },
            "payment_breakdown": [],
        }

    async def filtered_orders(*_args, **_kwargs):
        return []

    async def product_cost(*_args, **_kwargs):
        return {
            "total": 0.0,
            "missing_products_count": 0,
            "incomplete_orders_count": 0,
            "no_products_orders_count": 0,
            "source_contract": "test_product_cost",
        }

    async def ads(*_args, **_kwargs):
        return {
            "total": None,
            "known_subtotal_sar": 0.0,
            "breakdown": {
                "snapchat": None,
                "meta": 0.0,
                "tiktok": 0.0,
                "google_transitional": 0.0,
            },
            "providers": {
                "snapchat": {
                    "spend": None,
                    "orders": None,
                    "revenue": None,
                    "roas": None,
                    "data_state": "unknown_incomplete",
                    "coverage_complete": False,
                    "amount_complete": False,
                },
                "meta": {"spend": 0.0, "orders": 0, "revenue": 0.0, "roas": None},
                "tiktok": {"spend": 0.0, "orders": 0, "revenue": 0.0, "roas": None},
            },
            "spend_quality": {"status": "incomplete", "amount_complete": False},
            "bank_commissions": None,
            "source_contract": "test_ads",
        }

    async def recurring(*_args, **_kwargs):
        return {
            "total": 0.0,
            "rentals_total": 0.0,
            "utilities_total": 0.0,
            "renewals_total": 0.0,
            "by_type": {},
        }

    monkeypatch.setattr(executive_module, "_filtered_orders", filtered_orders)
    monkeypatch.setattr(executive_module, "build_mezan_v2_product_cost", product_cost)
    monkeypatch.setattr(executive_module, "build_mezan_v2_ads", ads)
    monkeypatch.setattr(executive_module, "compute_recurring_obligations_for_range", recurring)
    monkeypatch.setattr(
        executive_module,
        "merge_ad_bank_fees_into_dashboard",
        lambda *_args, **_kwargs: pytest.fail("incomplete spend must not merge ad fees"),
    )
    router = executive_module.make_dashboard_v2_router(
        object(),
        lambda: {"id": "owner-1"},
        legacy_dashboard,
        lambda _user: None,
    )
    endpoint = next(route.endpoint for route in router.routes if route.path == "/dashboard-v2")
    result = await endpoint(
        from_date=DAY.isoformat(),
        to_date=DAY.isoformat(),
        payment_methods=None,
        shipping_companies=None,
        user={"id": "owner-1"},
    )
    totals = result["totals"]
    assert totals["total_ads_cost"] is None
    assert totals["daily_ads_total"] is None
    assert totals["daily_costs_total"] is None
    assert totals["ads_spend_data_complete"] is False
    assert totals["ad_bank_commission_fees"] is None
    assert totals["total_payment_fees"] is None
    assert totals["net_profit"] is None
    assert totals["net_sales"] is None
