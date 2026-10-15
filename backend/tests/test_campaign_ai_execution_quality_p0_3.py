import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

import campaign_ai_execution_quality_gate as gate
import campaign_ai_monitor_legacy as legacy


NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
SNAP_SOURCE_MODE = (
    "snapchat_ads_manager_account_timezone_conversion_v8:"
    "ad_squad_active_campaign_account_day_bounded_v6"
)


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows[:length])


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    async def find_one(self, query, _projection=None, sort=None):
        rows = [row for row in self.rows if _matches(row, query)]
        for key, direction in reversed(sort or []):
            rows.sort(key=lambda row: str(row.get(key) or ""), reverse=direction < 0)
        return deepcopy(rows[0]) if rows else None

    def find(self, query, _projection=None):
        return FakeCursor(row for row in self.rows if _matches(row, query))


class FakeDB:
    def __init__(self, values=None):
        self.collections = {
            name: FakeCollection(rows) for name, rows in (values or {}).items()
        }

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def _iso(minutes=0):
    return (NOW + timedelta(minutes=minutes)).isoformat()


def _snap_target():
    return {
        "provider": "snapchat",
        "entity_level": "ad_group",
        "entity_id": "group-1",
        "account_id": "snap-1",
        "parent_id": "campaign-1",
        "status": "ACTIVE",
        "effective_status": "ACTIVE",
        "active": True,
        "current_daily_budget_native": 70.0,
        "spend_sar": 60.0,
        "revenue_sar": 0.0,
        "purchases": 0,
        "impressions": 300,
        "clicks": 12,
        "observed_days": 3,
        "data_complete": True,
        "currency_native": "SAR",
        "fx_rate_to_sar": 1.0,
        "fx_source": "provider_currency_identity",
        "provider_result_source": "snapchat_ads_manager_conversion_reporting",
        "result_source": "platform",
        "action_report_time": "conversion",
        "source_date_from": "2026-08-19",
        "source_date_to": "2026-08-21",
        "source_observed_at": _iso(-10),
        "account_timezone": "Asia/Riyadh",
        "pagination_complete": True,
        "source_mode": SNAP_SOURCE_MODE,
        "source_fact_collection": gate.SNAPCHAT_FACT_COLLECTION,
    }


def _meta_target():
    return {
        "provider": "meta",
        "entity_level": "campaign",
        "entity_id": "campaign-1",
        "account_id": "act-1",
        "status": "ACTIVE",
        "effective_status": "ACTIVE",
        "active": True,
        "current_daily_budget_native": 70.0,
        "spend_sar": 60.0,
        "revenue_sar": 0.0,
        "purchases": 0,
        "impressions": 300,
        "clicks": 12,
        "observed_days": 3,
        "data_complete": True,
        "currency_native": "SAR",
        "fx_rate_to_sar": 1.0,
        "fx_source": "implicit_sar",
        "provider_result_source": "meta_ads_manager_reporting",
        "result_source": "platform",
        "action_report_time": "conversion",
        "source_date_from": "2026-08-19",
        "source_date_to": "2026-08-21",
        "source_observed_at": _iso(-10),
        "account_timezone": "Asia/Riyadh",
        "pagination_complete": True,
        "source_mode": "meta_campaign_reporting_v2",
        "source_fact_collection": gate.META_CAMPAIGN_FACT_COLLECTION,
    }


def _snap_fact(day):
    window_start = datetime.fromisoformat(f"{day}T00:00:00+03:00")
    # Ad/Ad Squad TOTAL reporting requests the full account-local day even
    # while the current day remains open; refresh-state freshness proves when
    # that provider response was observed.
    window_end = window_start + timedelta(hours=24)
    return {
        "user_id": "owner",
        "provider": "snapchat_ads",
        "ad_account_id": "snap-1",
        "entity_type": "ad_squad",
        "external_id": "group-1",
        "date": day,
        "currency": "SAR",
        "spend_native": 20.0,
        "spend_sar": 20.0,
        "purchase_value_native": 0.0,
        "purchase_value_sar": 0.0,
        "purchases": 0,
        "metrics": {"spend": 20_000_000, "impressions": 100, "swipes": 4},
        "computed": {"ctr_pct": 4.0},
        "account_timezone": "Asia/Riyadh",
        "action_report_time": "conversion",
        "source_mode": SNAP_SOURCE_MODE,
        "conversion_reporting": {
            "metric": "conversion_purchases",
            "source_types": ["total"],
            "action_report_time": "conversion",
            "swipe_up_attribution_window": "28_DAY",
            "view_attribution_window": "7_DAY",
        },
        "provider_window_start": window_start.isoformat(),
        "provider_window_end": window_end.isoformat(),
        "updated_at": _iso(-10),
    }


def _meta_fact(day):
    return {
        "user_id": "owner",
        "provider": "meta_ads",
        "ad_account_id": "act-1",
        "campaign_id": "campaign-1",
        "date": day,
        "date_start": day,
        "date_stop": day,
        "currency_native": "SAR",
        "fx_rate_to_sar": 1.0,
        "fx_source": "implicit_sar",
        "spend_native": 20.0,
        "spend_sar": 20.0,
        "purchase_value_native": 0.0,
        "purchase_value_sar": 0.0,
        "purchases": 0,
        "purchase_action_type": None,
        "purchase_value_action_type": None,
        "impressions": 100,
        "clicks": 4,
        "source_mode": "meta_campaign_reporting_v2",
        "attribution_mode": "account_setting+unified",
        "observed_at": _iso(-10),
        "updated_at": _iso(-10),
    }


def _meta_child_target():
    return {
        **_meta_target(),
        "entity_level": "ad_group",
        "entity_id": "group-1",
        "parent_id": "campaign-1",
        "provider_result_source": "meta_ads_api_insights",
        "source_mode": "meta_ai_entity_reporting_v1",
        "source_fact_collection": gate.META_ENTITY_FACT_COLLECTION,
    }


def _meta_child_fact(day):
    return {
        "user_id": "owner",
        "provider": "meta",
        "ad_account_id": "act-1",
        "entity_level": "ad_group",
        "entity_id": "group-1",
        "campaign_id": "campaign-1",
        "date": day,
        "currency_native": "SAR",
        "fx_rate_to_sar": 1.0,
        "fx_source": "implicit_sar",
        "spend_native": 20.0,
        "spend_sar": 20.0,
        "revenue_native": 0.0,
        "revenue_sar": 0.0,
        "purchases": 0,
        "purchase_action_type": None,
        "revenue_action_type": None,
        "impressions": 100,
        "clicks": 4,
        "source_mode": "meta_ai_entity_reporting_v1",
        "action_report_time": "conversion",
        "account_timezone": "Asia/Riyadh",
        "observed_at": _iso(-10),
        "updated_at": _iso(-10),
    }


def _quality_fixture(provider="snapchat"):
    provider_id = gate.PROVIDER_IDS[provider]
    target = _snap_target() if provider == "snapchat" else _meta_target()
    account_id = target["account_id"]
    coverage = {
        "status": "complete",
        "data_state": "confirmed_data",
        "expected_requests": 6,
        "completed_requests": 6,
    }
    summary = {
        "date_from": "2026-08-19",
        "date_to": "2026-08-21",
        "accounts_attempted": 1,
        "accounts_complete": 1,
        "errors_count": 0,
        "error_samples": [],
    }
    if provider == "snapchat":
        summary.update({
            "coverage": deepcopy(coverage),
            "account_provider_calls": [
                {"ad_account_id": account_id, "provider_calls": 6}
            ],
        })
    account = {
        "user_id": "owner",
        "provider": provider_id,
        "external_account_id": account_id,
        "ad_account_id": account_id,
        "currency": "SAR",
        "timezone": "Asia/Riyadh",
        "source_mode": gate.RUN_SOURCE_MODES[provider],
        "last_sync_at": _iso(-10),
        "last_observed_at": _iso(-10),
        "performance_rows_saved": 3,
    }
    if provider == "snapchat":
        account.update({"data_quality": "complete", "coverage": deepcopy(coverage)})
    integration = {
        "user_id": "owner",
        "provider": provider_id,
        "data_quality": "complete",
        "last_sync_at": _iso(-10),
        "source_mode": gate.RUN_SOURCE_MODES[provider],
    }
    if provider == "snapchat":
        integration["coverage"] = deepcopy(coverage)
    run = {
        "run_id": f"{provider}-run-1",
        "user_id": "owner",
        "provider": provider_id,
        "run_type": gate.RUN_TYPES[provider],
        "source_mode": gate.RUN_SOURCE_MODES[provider],
        "status": "complete",
        "started_at": _iso(-20),
        "finished_at": _iso(-10),
        "summary": summary,
    }
    fact_collection = target["source_fact_collection"]
    facts = [
        (_snap_fact(day) if provider == "snapchat" else _meta_fact(day))
        for day in ("2026-08-19", "2026-08-20", "2026-08-21")
    ]
    db = FakeDB({
        gate.INTEGRATIONS_COLLECTION: [integration],
        gate.ACCOUNTS_COLLECTION: [account],
        gate.SYNC_RUNS_COLLECTION: [run],
        gate.ACCOUNT_COST_SETTINGS_COLLECTION: [],
        fact_collection: facts,
        gate.SNAPCHAT_ADSQUAD_REFRESH_STATE_COLLECTION: (
            [{
                "user_id": "owner",
                "ad_account_id": "snap-1",
                "source_mode": gate.SNAPCHAT_CHILD_REFRESH_CONTRACTS[
                    "ad_group"
                ][1],
                "last_success_at": _iso(-10),
                "errors_count": 0,
                "campaign_limit_reached": False,
                "coverage": deepcopy(coverage),
            }]
            if provider == "snapchat"
            else []
        ),
    })
    source_context = (
        {
            "monitor_errors": [],
            "meta_refresh": {
                "status": "complete",
                "date_from": "2026-08-19",
                "date_to": "2026-08-21",
                "observed_at": _iso(-10),
                "errors_count": 0,
                "errors": [],
                "pagination_complete": True,
            },
        }
        if provider == "meta"
        else {"monitor_errors": []}
    )
    return db, target, source_context


def _collect(db, target, source_context, baseline=None):
    return asyncio.run(gate.collect_execution_quality_evidence(
        db,
        "owner",
        target,
        snapshot_generated_at=NOW.isoformat(),
        snapshot_range={"from": "2026-08-19", "to": "2026-08-21"},
        now=lambda: NOW,
        source_context=source_context,
        baseline=baseline,
    ))


@pytest.mark.parametrize("provider", ["snapchat", "meta"])
def test_valid_complete_fresh_entity_bound_evidence_allows_both_providers(provider):
    db, target, context = _quality_fixture(provider)
    evidence = _collect(db, target, context)

    assert gate.evaluate_execution_quality(evidence, action="reduce")["allowed"] is True
    assert evidence["entity_facts"]["status"] == "complete"


@pytest.mark.parametrize("action", ["pause", "reduce"])
def test_incomplete_coverage_blocks_pause_and_decrease(action):
    db, target, context = _quality_fixture("snapchat")
    db[gate.ACCOUNTS_COLLECTION].rows[0]["coverage"].update({
        "status": "incomplete",
        "data_state": "unknown_incomplete",
        "completed_requests": 5,
    })

    decision = gate.evaluate_execution_quality(
        _collect(db, target, context), action=action
    )

    assert decision["allowed"] is False
    assert "execution_coverage_incomplete" in decision["blockers"]


@pytest.mark.parametrize("action", ["reduce", "scale"])
def test_stale_evidence_blocks_budget_changes(action):
    db, target, context = _quality_fixture("snapchat")
    stale = (NOW - timedelta(hours=4)).isoformat()
    db[gate.INTEGRATIONS_COLLECTION].rows[0]["last_sync_at"] = stale
    db[gate.ACCOUNTS_COLLECTION].rows[0]["last_sync_at"] = stale
    db[gate.SYNC_RUNS_COLLECTION].rows[0].update({
        "started_at": (NOW - timedelta(hours=4, minutes=10)).isoformat(),
        "finished_at": stale,
    })

    decision = gate.evaluate_execution_quality(
        _collect(db, target, context), action=action
    )

    assert "execution_data_stale" in decision["blockers"]


def test_partial_run_window_cannot_authorize_older_snapshot_day():
    db, target, context = _quality_fixture("meta")
    db[gate.SYNC_RUNS_COLLECTION].rows[0]["summary"]["date_from"] = "2026-08-20"
    db[gate.META_CAMPAIGN_FACT_COLLECTION].rows[0]["updated_at"] = (
        NOW - timedelta(hours=4)
    ).isoformat()

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert "execution_entity_facts_incomplete" in decision["blockers"]
    assert "provider_run_window_incomplete" in evidence["entity_facts"]["errors"]


def test_one_stale_day_cannot_hide_behind_two_fresh_days():
    db, target, context = _quality_fixture("snapchat")
    db[gate.SNAPCHAT_FACT_COLLECTION].rows[0]["updated_at"] = (
        NOW - timedelta(hours=4)
    ).isoformat()

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="pause")

    assert decision["allowed"] is False
    assert "execution_data_stale" in decision["blockers"]
    assert "entity_fact_not_bound_to_child_refresh" in evidence["entity_facts"]["errors"]


def test_unknown_usd_fx_blocks_without_explicit_account_setting():
    db, target, context = _quality_fixture("snapchat")
    target.update({
        "currency_native": "USD",
        "fx_rate_to_sar": 3.75,
        "fx_source": "account_cost_setting_required",
    })
    db[gate.ACCOUNTS_COLLECTION].rows[0]["currency"] = "USD"
    for row in db[gate.SNAPCHAT_FACT_COLLECTION].rows:
        row.update({
            "currency": "USD",
            "spend_sar": 75.0,
            "purchase_value_sar": 0.0,
        })

    decision = gate.evaluate_execution_quality(
        _collect(db, target, context), action="reduce"
    )

    assert "execution_fx_unknown" in decision["blockers"]


def _configure_complete_snapchat_usd(db, target):
    target.update({
        "currency_native": "USD",
        "fx_rate_to_sar": 3.75,
        "fx_source": "account_cost_setting_required",
        "spend_sar": 225.0,
    })
    db[gate.ACCOUNTS_COLLECTION].rows[0]["currency"] = "USD"
    db[gate.ACCOUNT_COST_SETTINGS_COLLECTION].rows = [{
        "user_id": "owner",
        "provider": "snapchat_ads",
        "external_account_id": "snap-1",
        "native_currency": "USD",
        "exchange_rate_to_sar": 3.75,
        "updated_at": _iso(-30),
    }]
    for row in db[gate.SNAPCHAT_FACT_COLLECTION].rows:
        row.update({
            "currency": "USD",
            "spend_native": 20.0,
            "spend_sar": 75.0,
            "purchase_value_native": 0.0,
            "purchase_value_sar": 0.0,
        })


def test_explicit_snapchat_usd_setting_and_fact_math_allow_execution():
    db, target, context = _quality_fixture("snapchat")
    _configure_complete_snapchat_usd(db, target)

    decision = gate.evaluate_execution_quality(
        _collect(db, target, context), action="reduce"
    )

    assert decision["allowed"] is True


def test_missing_native_amount_cannot_prove_fx_conversion():
    db, target, context = _quality_fixture("snapchat")
    _configure_complete_snapchat_usd(db, target)
    for row in db[gate.SNAPCHAT_FACT_COLLECTION].rows:
        del row["spend_native"]

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert "entity_fact_fx_amount_missing" in evidence["entity_facts"]["errors"]


def test_meta_fact_fx_rate_and_source_must_match_target_proof():
    db, target, context = _quality_fixture("meta")
    target.update({
        "currency_native": "USD",
        "fx_rate_to_sar": 3.75,
        "fx_source": "provider_row",
        "spend_sar": 225.0,
    })
    db[gate.ACCOUNTS_COLLECTION].rows[0]["currency"] = "USD"
    for row in db[gate.META_CAMPAIGN_FACT_COLLECTION].rows:
        row.update({
            "currency_native": "USD",
            "fx_rate_to_sar": 9.99,
            "fx_source": "bogus",
            "spend_native": 20.0,
            "spend_sar": 75.0,
        })

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert "entity_fact_fx_provenance_mismatch" in evidence["entity_facts"]["errors"]


def test_meta_target_fx_source_cannot_disagree_with_canonical_facts():
    db, target, context = _quality_fixture("meta")
    target.update({
        "currency_native": "USD",
        "fx_rate_to_sar": 3.75,
        "fx_source": "bogus",
        "spend_sar": 225.0,
    })
    db[gate.ACCOUNTS_COLLECTION].rows[0]["currency"] = "USD"
    for row in db[gate.META_CAMPAIGN_FACT_COLLECTION].rows:
        row.update({
            "currency_native": "USD",
            "fx_rate_to_sar": 3.75,
            "fx_source": "configured_usd_peg",
            "spend_native": 20.0,
            "spend_sar": 75.0,
        })

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert evidence["entity_facts"]["status"] == "complete"
    assert decision["allowed"] is False
    assert "execution_fx_unknown" in decision["blockers"]


def test_nested_provider_error_blocks_even_when_top_level_count_is_zero():
    db, target, context = _quality_fixture("snapchat")
    db[gate.SYNC_RUNS_COLLECTION].rows[0]["summary"]["ad_performance"] = {
        "errors_count": 1,
        "errors": [{"code": "snapchat_ad_payload_partial"}],
    }

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="pause")

    assert "execution_provider_sync_errors" in decision["blockers"]
    assert any("snapchat_ad_payload_partial" in code for code in evidence["provider_sync"]["error_codes"])


def test_malformed_provider_error_envelope_fails_closed():
    db, target, context = _quality_fixture("meta")
    run = db[gate.SYNC_RUNS_COLLECTION].rows[0]
    run["error"] = "provider timeout"
    run["summary"].update({
        "error_samples": {"code": "provider_partial"},
        "errors": {"code": "nested_malformed"},
    })

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="pause")

    assert decision["allowed"] is False
    assert "execution_provider_sync_errors" in decision["blockers"]
    assert any("malformed" in code for code in evidence["provider_sync"]["error_codes"])


def test_nonempty_unclassified_provider_error_sample_fails_closed():
    db, target, context = _quality_fixture("meta")
    db[gate.SYNC_RUNS_COLLECTION].rows[0]["summary"]["error_samples"] = [
        {"message": "provider failed"}
    ]

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="pause")

    assert decision["allowed"] is False
    assert "provider_error_unclassified" in evidence[
        "provider_sync"
    ]["error_codes"]


def test_missing_errors_count_fails_closed_without_value_error():
    db, target, context = _quality_fixture("meta")
    del db[gate.SYNC_RUNS_COLLECTION].rows[0]["summary"]["errors_count"]

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert "execution_provider_sync_errors" in decision["blockers"]


@pytest.mark.parametrize("state", ["confirmed_zero", "confirmed_no_data"])
def test_current_zero_or_no_data_cannot_authorize_stale_positive_target(state):
    db, target, context = _quality_fixture("snapchat")
    for collection in (gate.ACCOUNTS_COLLECTION, gate.INTEGRATIONS_COLLECTION):
        db[collection].rows[0]["coverage"]["data_state"] = state
    db[gate.SYNC_RUNS_COLLECTION].rows[0]["summary"]["coverage"]["data_state"] = state

    decision = gate.evaluate_execution_quality(
        _collect(db, target, context), action="pause"
    )

    assert decision["allowed"] is False
    assert "execution_data_state_unknown" in decision["blockers"]


def test_conflicting_latest_snapchat_run_data_state_blocks():
    db, target, context = _quality_fixture("snapchat")
    db[gate.SYNC_RUNS_COLLECTION].rows[0]["summary"]["coverage"]["data_state"] = (
        "confirmed_no_data"
    )

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="pause")

    assert decision["allowed"] is False
    assert evidence["coverage"]["consistent"] is False
    assert "execution_coverage_incomplete" in decision["blockers"]


def test_account_success_does_not_cover_missing_target_entity_facts():
    db, target, context = _quality_fixture("snapchat")
    db[gate.SNAPCHAT_FACT_COLLECTION].rows = []

    decision = gate.evaluate_execution_quality(
        _collect(db, target, context), action="pause"
    )

    assert "execution_entity_facts_incomplete" in decision["blockers"]


@pytest.mark.parametrize("provider", ["snapchat", "meta"])
def test_partial_pagination_blocks_both_providers(provider):
    db, target, context = _quality_fixture(provider)
    target["pagination_complete"] = False

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="pause")

    assert decision["allowed"] is False
    assert "execution_pagination_unresolved" in decision["blockers"]


def test_truncated_run_account_diagnostics_do_not_hide_canonical_account_proof():
    db, target, context = _quality_fixture("snapchat")
    db[gate.SYNC_RUNS_COLLECTION].rows[0]["summary"]["account_provider_calls"] = [
        {"ad_account_id": "snap-other", "provider_calls": 6}
    ]

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert evidence["provider_sync"]["summary_account_listed"] is False
    assert evidence["provider_sync"]["account_bound"] is True
    assert decision["allowed"] is True


def test_newer_noncanonical_analytics_run_blocks_stale_proof_mixing():
    db, target, context = _quality_fixture("snapchat")
    newer = deepcopy(db[gate.SYNC_RUNS_COLLECTION].rows[0])
    newer.update({
        "run_id": "newer-native-run",
        "source_mode": "snapchat_native_sync_v2",
        "started_at": _iso(-5),
        "finished_at": _iso(-1),
    })
    db[gate.SYNC_RUNS_COLLECTION].rows.append(newer)

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert evidence["provider_sync"]["run_id"] == "newer-native-run"
    assert decision["allowed"] is False
    assert "execution_provider_sync_errors" in decision["blockers"]


def test_provider_run_chronology_must_be_valid():
    db, target, context = _quality_fixture("meta")
    db[gate.SYNC_RUNS_COLLECTION].rows[0].update({
        "started_at": _iso(-5),
        "finished_at": _iso(-10),
    })

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert evidence["provider_sync"]["chronology_valid"] is False
    assert "execution_provider_sync_errors" in decision["blockers"]


def test_target_totals_must_reconcile_to_exact_entity_facts():
    db, target, context = _quality_fixture("meta")
    target["spend_sar"] = 9999.0

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert "entity_fact_spend_sar_mismatch" in evidence["entity_facts"]["errors"]


def test_negative_provider_metrics_are_malformed_not_complete():
    db, target, context = _quality_fixture("snapchat")
    target["spend_sar"] = -60.0
    for row in db[gate.SNAPCHAT_FACT_COLLECTION].rows:
        row.update({"spend_native": -20.0, "spend_sar": -20.0})
        row["metrics"]["spend"] = -20_000_000

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="pause")

    assert decision["allowed"] is False
    assert "entity_fact_metric_domain_invalid" in evidence["entity_facts"]["errors"]


def test_positive_meta_conversions_require_provider_action_provenance():
    db, target, context = _quality_fixture("meta")
    target.update({"purchases": 3, "revenue_sar": 30.0})
    for row in db[gate.META_CAMPAIGN_FACT_COLLECTION].rows:
        row.update({
            "purchases": 1,
            "purchase_value_native": 10.0,
            "purchase_value_sar": 10.0,
            "purchase_action_type": None,
            "purchase_value_action_type": None,
        })

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert "entity_fact_purchase_attribution_missing" in evidence["entity_facts"]["errors"]
    assert "entity_fact_value_attribution_missing" in evidence["entity_facts"]["errors"]


def test_meta_child_revenue_fx_conversion_must_be_proven():
    db, _target, context = _quality_fixture("meta")
    target = _meta_child_target()
    facts = [_meta_child_fact(day) for day in ("2026-08-19", "2026-08-20", "2026-08-21")]
    facts[0].update({"revenue_native": 100.0, "revenue_sar": 999.0})
    target["revenue_sar"] = 999.0
    db[gate.META_CAMPAIGN_FACT_COLLECTION].rows = []
    db[gate.META_ENTITY_FACT_COLLECTION].rows = facts

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert "entity_fact_fx_conversion_mismatch" in evidence["entity_facts"]["errors"]


def test_meta_campaign_rejects_noncanonical_revenue_alias():
    db, target, context = _quality_fixture("meta")
    target["revenue_sar"] = 300.0
    for row in db[gate.META_CAMPAIGN_FACT_COLLECTION].rows:
        row["revenue_sar"] = 100.0

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert "entity_fact_revenue_sar_mismatch" in evidence["entity_facts"]["errors"]


def test_partial_meta_child_refresh_window_blocks_execution():
    db, _target, context = _quality_fixture("meta")
    target = _meta_child_target()
    db[gate.META_CAMPAIGN_FACT_COLLECTION].rows = []
    db[gate.META_ENTITY_FACT_COLLECTION].rows = [
        _meta_child_fact(day)
        for day in ("2026-08-19", "2026-08-20", "2026-08-21")
    ]
    context["meta_refresh"].update({
        "date_from": "2026-08-20",
        "date_to": "2026-08-21",
    })

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert "meta_entity_refresh_window_mismatch" in evidence[
        "source_validation"
    ]["errors"]


def test_shifted_same_length_snapchat_source_window_is_untrusted():
    db, target, context = _quality_fixture("snapchat")
    target.update({"source_date_from": "2026-08-18", "source_date_to": "2026-08-20"})
    db[gate.SNAPCHAT_FACT_COLLECTION].rows = [
        _snap_fact(day) for day in ("2026-08-18", "2026-08-19", "2026-08-20")
    ]

    decision = gate.evaluate_execution_quality(
        _collect(db, target, context), action="reduce"
    )

    assert "execution_attribution_untrusted" in decision["blockers"]
    assert "execution_source_window_untrusted" in decision["blockers"]


def test_partial_historical_snapchat_provider_window_cannot_prove_day():
    db, target, context = _quality_fixture("snapchat")
    row = db[gate.SNAPCHAT_FACT_COLLECTION].rows[0]
    row["provider_window_end"] = (
        datetime.fromisoformat(row["provider_window_start"]) + timedelta(hours=1)
    ).isoformat()

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="pause")

    assert decision["allowed"] is False
    assert "entity_fact_source_contract_mismatch" in evidence["entity_facts"]["errors"]


def test_cached_snapchat_child_refresh_remains_valid_within_fifteen_minutes():
    db, target, context = _quality_fixture("snapchat")
    run = db[gate.SYNC_RUNS_COLLECTION].rows[0]
    run.update({"started_at": _iso(-5), "finished_at": _iso(-1)})
    db[gate.ACCOUNTS_COLLECTION].rows[0]["last_sync_at"] = _iso(-1)
    db[gate.INTEGRATIONS_COLLECTION].rows[0]["last_sync_at"] = _iso(-1)

    decision = gate.evaluate_execution_quality(
        _collect(db, target, context), action="reduce"
    )

    assert decision["allowed"] is True


def test_fact_written_after_provider_run_is_not_attributed_to_that_run():
    db, target, context = _quality_fixture("meta")
    for row in db[gate.META_CAMPAIGN_FACT_COLLECTION].rows:
        row.update({"observed_at": _iso(-1), "updated_at": _iso(-1)})

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert "entity_fact_not_refreshed_in_latest_run" in evidence["entity_facts"]["errors"]


def test_invalid_meta_account_timezone_is_untrusted():
    db, target, context = _quality_fixture("meta")
    target["account_timezone"] = "not/a-real-zone"

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert evidence["attribution"]["timezone_bound"] is False


def test_wrong_provider_fact_cannot_satisfy_entity_identity():
    db, target, context = _quality_fixture("meta")
    db[gate.META_CAMPAIGN_FACT_COLLECTION].rows[0]["provider"] = "snapchat_ads"

    evidence = _collect(db, target, context)
    decision = gate.evaluate_execution_quality(evidence, action="reduce")

    assert decision["allowed"] is False
    assert "execution_entity_facts_incomplete" in decision["blockers"]


def test_fact_fingerprint_drift_blocks_between_snapshot_and_execution():
    db, target, context = _quality_fixture("meta")
    baseline = _collect(db, target, context)
    db[gate.META_CAMPAIGN_FACT_COLLECTION].rows[0].update({
        "spend_native": 21.0,
        "spend_sar": 21.0,
    })

    current = _collect(db, target, context, baseline=baseline)
    decision = gate.evaluate_execution_quality(current, action="reduce")

    assert "execution_source_revision_changed" in decision["blockers"]
    assert "entity_fact_fingerprint_changed" in current["entity_facts"]["errors"]


@pytest.mark.parametrize("provider,current_state", [
    (
        "meta",
        {"id": "campaign-1", "status": "ACTIVE", "effective_status": "PAUSED", "daily_budget": "7000"},
    ),
    (
        "snapchat",
        {"id": "group-1", "status": "ACTIVE", "daily_budget_micro": 71_000_000},
    ),
])
def test_provider_status_or_budget_drift_blocks(provider, current_state):
    target = _meta_target() if provider == "meta" else _snap_target()
    recommendation = {"action": "reduce"}

    blockers = gate.provider_state_drift_blockers(
        provider, recommendation, target, current_state
    )

    assert blockers


@pytest.mark.parametrize("provider", ["snapchat", "meta"])
@pytest.mark.parametrize("action", ["reduce", "scale"])
def test_budget_change_requires_budget_bound_in_approved_target(provider, action):
    target = _meta_target() if provider == "meta" else _snap_target()
    target["current_daily_budget_native"] = None
    current_state = (
        {
            "id": target["entity_id"],
            "status": "ACTIVE",
            "effective_status": "ACTIVE",
            "daily_budget": "7000",
        }
        if provider == "meta"
        else {
            "id": target["entity_id"],
            "status": "ACTIVE",
            "effective_status": "ACTIVE",
            "daily_budget_micro": 70_000_000,
        }
    )

    blockers = gate.provider_state_drift_blockers(
        provider,
        {"action": action},
        target,
        current_state,
    )

    assert "execution_provider_budget_unproven" in blockers


def test_preflight_rejects_recommendation_target_identity_mismatch():
    db, target, context = _quality_fixture("meta")
    baseline = _collect(db, target, context)
    target = {**target, "execution_quality": baseline}
    recommendation = {
        "recommendation_id": "rec-1",
        "provider": "snapchat",
        "entity_level": "ad_group",
        "entity_id": "foreign-group",
        "account_id": "foreign-account",
        "action": "reduce",
        "change_percent": 15,
        "approval_available": True,
    }
    db["recs"].rows.append({
        "snapshot_id": "snapshot-1",
        "user_id": "owner",
        "generated_at": NOW.isoformat(),
        "range": {"from": "2026-08-19", "to": "2026-08-21"},
        "recommendations": [recommendation],
        "execution_targets": {"rec-1": target},
    })

    with pytest.raises(gate.ExecutionQualityBlocked) as captured:
        asyncio.run(gate.preflight_approved_execution(
            db,
            recommendation_collection="recs",
            user_id="owner",
            snapshot_id="snapshot-1",
            recommendation_id="rec-1",
            now=lambda: NOW,
        ))

    assert captured.value.blockers == ["execution_snapshot_identity_mismatch"]


def test_meta_child_execution_preflight_reuses_persisted_refresh_proof():
    db, _target, context = _quality_fixture("meta")
    target = _meta_child_target()
    db[gate.META_CAMPAIGN_FACT_COLLECTION].rows = []
    db[gate.META_ENTITY_FACT_COLLECTION].rows = [
        _meta_child_fact(day)
        for day in ("2026-08-19", "2026-08-20", "2026-08-21")
    ]
    baseline = _collect(db, target, context)
    assert gate.evaluate_execution_quality(baseline, action="reduce")["allowed"] is True
    target = {**target, "execution_quality": baseline}
    recommendation = {
        "recommendation_id": "rec-1",
        "provider": "meta",
        "entity_level": "ad_group",
        "entity_id": "group-1",
        "account_id": "act-1",
        "action": "reduce",
        "change_percent": 15,
        "approval_available": True,
    }
    snapshot = {
        "snapshot_id": "snapshot-1",
        "user_id": "owner",
        "generated_at": NOW.isoformat(),
        "range": {"from": "2026-08-19", "to": "2026-08-21"},
        "recommendations": [recommendation],
        "execution_targets": {"rec-1": target},
    }
    db["recs"].rows.append(snapshot)
    expected_digest = gate.execution_snapshot_digest(
        "snapshot-1", recommendation, target
    )

    result = asyncio.run(gate.preflight_approved_execution(
        db,
        recommendation_collection="recs",
        user_id="owner",
        snapshot_id="snapshot-1",
        recommendation_id="rec-1",
        expected_digest=expected_digest,
        now=lambda: NOW,
    ))

    assert result["execution_quality"]["status"] == "complete"
    assert result["snapshot_digest"] == expected_digest


def test_preflight_rejects_latest_snapshot_drift():
    db, target, context = _quality_fixture("meta")
    baseline = _collect(db, target, context)
    target = {**target, "execution_quality": baseline}
    recommendation = {
        "recommendation_id": "rec-1",
        "provider": "meta",
        "entity_level": "campaign",
        "entity_id": "campaign-1",
        "account_id": "act-1",
        "action": "reduce",
        "change_percent": 15,
        "approval_available": True,
    }
    db["recs"].rows.extend([
        {
            "snapshot_id": "approved-snapshot",
            "user_id": "owner",
            "generated_at": (NOW - timedelta(minutes=2)).isoformat(),
            "range": {"from": "2026-08-19", "to": "2026-08-21"},
            "recommendations": [recommendation],
            "execution_targets": {"rec-1": target},
        },
        {
            "snapshot_id": "newer-snapshot",
            "user_id": "owner",
            "generated_at": NOW.isoformat(),
            "range": {"from": "2026-08-19", "to": "2026-08-21"},
            "recommendations": [],
            "execution_targets": {},
        },
    ])

    with pytest.raises(gate.ExecutionQualityBlocked) as captured:
        asyncio.run(gate.preflight_approved_execution(
            db,
            recommendation_collection="recs",
            user_id="owner",
            snapshot_id="approved-snapshot",
            recommendation_id="rec-1",
            now=lambda: NOW,
        ))

    assert "execution_snapshot_drift" in captured.value.blockers


@pytest.mark.parametrize("provider", ["snapchat", "meta"])
def test_both_providers_dispatch_only_through_same_preflight(monkeypatch, provider):
    calls = {"preflight": 0, "snapchat": 0, "meta": 0}

    async def preflight(*_args, **_kwargs):
        calls["preflight"] += 1
        return {
            "recommendation": {
                "provider": provider,
                "entity_level": "campaign",
                "action": "pause",
            },
            "target": {"entity_id": "campaign-1", "account_id": "account-1"},
        }

    async def snap(*_args, **_kwargs):
        calls["snapchat"] += 1
        return {"status": "completed"}

    async def meta(*_args, **_kwargs):
        calls["meta"] += 1
        return {"status": "completed"}

    monkeypatch.setattr(legacy._execution_quality, "preflight_approved_execution", preflight)
    monkeypatch.setattr(legacy, "_execute_snapchat_approval", snap)
    monkeypatch.setattr(legacy, "_execute_meta_approval", meta)

    result = asyncio.run(legacy._execute_approved_recommendation(
        object(),
        "owner",
        snapshot_id="snapshot-1",
        recommendation_id="rec-1",
        expected_digest="digest-1",
        idempotency_key="execution-1",
    ))

    assert result["status"] == "completed"
    assert calls == {
        "preflight": 1,
        "snapchat": int(provider == "snapchat"),
        "meta": int(provider == "meta"),
    }


def test_dispatch_blocker_reaches_neither_provider(monkeypatch):
    calls = {"snapchat": 0, "meta": 0}

    async def blocked(*_args, **_kwargs):
        raise gate.ExecutionQualityBlocked(["execution_coverage_incomplete"])

    async def snap(*_args, **_kwargs):
        calls["snapchat"] += 1

    async def meta(*_args, **_kwargs):
        calls["meta"] += 1

    monkeypatch.setattr(legacy._execution_quality, "preflight_approved_execution", blocked)
    monkeypatch.setattr(legacy, "_execute_snapchat_approval", snap)
    monkeypatch.setattr(legacy, "_execute_meta_approval", meta)

    with pytest.raises(gate.ExecutionQualityBlocked):
        asyncio.run(legacy._execute_approved_recommendation(
            object(),
            "owner",
            snapshot_id="snapshot-1",
            recommendation_id="rec-1",
            expected_digest="digest-1",
            idempotency_key="execution-1",
        ))

    assert calls == {"snapchat": 0, "meta": 0}
