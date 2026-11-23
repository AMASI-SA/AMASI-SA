"""Metadata-boundary contracts; no provider, environment or production database."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import patch

from operational_ad_funding import bindings as funding_bindings, party_id
from operational_source_freshness import read_source_freshness


NOW = datetime(2026, 11, 23, 14, 0, tzinfo=timezone.utc)
OWNER = "owner-a"


def at(minutes=0):
    return (NOW - timedelta(minutes=minutes)).isoformat()


def value(row, path, default=None):
    current = row
    for key in path.split("."):
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


def matches(row, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(row, part) for part in expected):
                return False
        elif key == "$and":
            if not all(matches(row, part) for part in expected):
                return False
        elif isinstance(expected, dict):
            actual = value(row, key)
            for op, operand in expected.items():
                if op == "$in" and actual not in operand:
                    return False
                if op == "$exists" and (actual is not None) != operand:
                    return False
                if op not in {"$in", "$exists"}:
                    raise AssertionError("Unexpected query operator " + op)
        elif value(row, key) != expected:
            return False
    return True


MISSING = object()


def expression(row, spec, variables=None):
    """Evaluate the bounded Mongo projection subset, not a financial state read."""
    variables = variables or {}
    if isinstance(spec, str) and spec.startswith("$$"):
        return value(variables, spec[2:], MISSING)
    if isinstance(spec, str) and spec.startswith("$"):
        return value(row, spec[1:], MISSING)
    if isinstance(spec, list):
        return [expression(row, item, variables) for item in spec]
    if not isinstance(spec, dict):
        return spec
    if "$type" in spec:
        item = expression(row, spec["$type"], variables)
        if item is MISSING:
            return "missing"
        return {dict: "object", list: "array", str: "string", bool: "bool", int: "int",
                float: "double", type(None): "null", datetime: "date"}[type(item)]
    if "$eq" in spec:
        left, right = expression(row, spec["$eq"], variables)
        return left == right
    if "$cond" in spec:
        condition, yes, no = spec["$cond"]
        return expression(row, yes if expression(row, condition, variables) else no, variables)
    if "$objectToArray" in spec:
        item = expression(row, spec["$objectToArray"], variables)
        assert type(item) is dict
        return [{"k": key, "v": entry} for key, entry in item.items()]
    if "$slice" in spec:
        items, count = expression(row, spec["$slice"], variables)
        return items[:count]
    if "$map" in spec:
        item = spec["$map"]
        return [expression(row, item["in"], {**variables, item["as"]: entry})
                for entry in expression(row, item["input"], variables)]
    assert not any(key.startswith("$") for key in spec), spec
    return {key: item for key, field in spec.items()
            if (item := expression(row, field, variables)) is not MISSING}


def projected(row, projection):
    result = {}
    for path, include in projection.items():
        if not include:
            continue
        item = expression(row, include) if isinstance(include, dict) else value(row, path, MISSING)
        if item is MISSING:
            continue
        target = result
        pieces = path.split(".")
        for key in pieces[:-1]:
            target = target.setdefault(key, {})
        target[pieces[-1]] = deepcopy(item)
    return result


class Cursor:
    def __init__(self, rows, projection, record, db):
        self.rows, self.projection, self.record, self.db = rows, projection, record, db
        self.bound = None

    def sort(self, fields, direction=None):
        fields = [(fields, direction)] if isinstance(fields, str) else fields
        for field, order in reversed(fields):
            self.rows.sort(key=lambda row: str(value(row, field) or ""), reverse=order == -1)
        return self

    def max_time_ms(self, milliseconds):
        self.record["max_time_ms"] = milliseconds
        return self

    def limit(self, count):
        self.bound = count
        self.record["limit"] = count
        return self

    async def to_list(self, length):
        assert self.bound is not None and 0 < length <= self.bound
        assert 0 < self.record["max_time_ms"] <= 2000
        if self.record["collection"] in self.db.delayed:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                self.db.cancelled.add(self.record["collection"])
                raise
        if self.record["collection"] in self.db.broken:
            raise RuntimeError("secret://unsafe-provider-error")
        return [projected(row, self.projection) for row in self.rows[:length]]


class Collection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def find(self, query, projection):
        assert projection and projection.get("_id") == 0
        record = {"collection": self.name, "query": deepcopy(query), "projection": projection}
        self.db.reads.append(record)
        rows = [deepcopy(row) for row in self.db.rows.get(self.name, []) if matches(row, query)]
        return Cursor(rows, projection, record, self.db)

    def __getattr__(self, name):
        raise AssertionError("Only bounded find is allowed, got " + name)


class DB:
    def __init__(self, rows=None):
        self.rows = rows or {}
        self.reads, self.broken, self.delayed, self.cancelled = [], set(), set(), set()

    def __getitem__(self, name):
        assert name in {
            "mz2_ad_account_bindings_v2", "mezan_integration_accounts_v2", "mezan_integrations_v2",
            "mezan_integration_sync_runs_v2", "mezan_integration_errors_v2",
            "mezan_ads_auto_sync_scheduler_v2", "salla_integrations", "salla_scheduled_reconciliation",
            "operational_balance_states_v1",
        }, name
        return Collection(self, name)


def configured(platform="meta", account_id="account-1", minutes=1):
    if platform == "google_ads" and account_id == "account-1":
        account_id = "1234567890"
    provider = {"meta": "meta_ads", "snapchat": "snapchat_ads", "tiktok": "tiktok_ads", "google_ads": "google_ads"}[platform]
    run_type = {"meta": "meta_reporting_async", "snapchat": "analytics_refresh", "tiktok": "tiktok_reporting_async", "google_ads": "google_ads_reporting_async"}[platform]
    source_mode = {"meta": "meta_marketing_reporting_v2", "snapchat": "snapchat_account_hourly_campaign_breakdown_riyadh_refresh_v3", "tiktok": "tiktok_marketing_reporting_v2", "google_ads": "google_ads_reporting_v1"}[platform]
    coverage = {"status": "complete", "data_state": "confirmed_zero", "expected_requests": 1, "completed_requests": 1}
    binding = {"user_id": OWNER, "platform": platform, "integration_account_id": "binding-" + account_id,
               "platform_account_id": account_id, "confirmed_by": "owner", "confirmed_at": at(100), "funding_mode": "prepaid"}
    account = {"user_id": OWNER, "provider": provider, "mezan_integration_account_id": "binding-" + account_id,
               "external_account_id": account_id, "ad_account_id": account_id, "connection_status": "connected",
               "connection_provenance": "api_connection", "mezan_selected": True, "last_sync_at": at(minutes),
               "last_observed_at": at(minutes), "source_mode": source_mode,
               "financial_last_sync_at": at(minutes), "financial_last_observed_at": at(minutes),
               "financial_data_quality": "complete", "financial_coverage": coverage}
    integration = {"user_id": OWNER, "provider": provider, "connection_status": "connected",
                   "financial_last_sync_at": at(minutes), "financial_data_quality": "complete",
                   "financial_coverage": coverage, "last_sync_at": at(minutes), "data_quality": "complete"}
    run = {"user_id": OWNER, "provider": provider, "run_type": run_type, "status": "complete",
           "started_at": at(minutes + 1), "finished_at": at(minutes), "source_mode": source_mode,
           "summary": {"accounts_attempted": 1, "accounts_complete": 1, "errors_count": 0,
                       "financial_proof": {"version": 1, "status": "complete", "accounts_complete": 1,
                                           "errors_count": 0, "coverage": coverage}}}
    return DB({"mz2_ad_account_bindings_v2": [binding], "mezan_integration_accounts_v2": [account],
               "mezan_integrations_v2": [integration], "mezan_integration_sync_runs_v2": [run],
               "mezan_ads_auto_sync_scheduler_v2": [{"_id": "ads-v2-server-scheduler", "enabled": True, "interval_seconds": 300}]})


def read(db, as_of=None, now=NOW):
    result = asyncio.run(read_source_freshness(db, OWNER, as_of=at(1) if as_of is None else as_of, now=now))
    return {row["key"]: row for row in result}


class SourceFreshnessTests(unittest.TestCase):
    def test_empty_metadata_never_invents_connector_success(self):
        result = read(DB())
        self.assertEqual(set(result), {"payroll", "recurring", "salla", "snapchat", "meta", "tiktok", "google_ads"})
        self.assertEqual(result["payroll"]["state"], "current")
        self.assertEqual(result["meta"]["state"], "inactive")
        self.assertIsNone(result["meta"]["last_success_at"])

    def test_calculation_age_and_riyadh_midnight_are_independent_of_contract_edits(self):
        self.assertEqual(read(DB(), as_of=at(120))["payroll"]["state"], "stale")
        midnight = datetime(2026, 11, 23, 21, 1, tzinfo=timezone.utc)
        result = read(DB(), as_of="2026-11-23T20:59:00+00:00", now=midnight)
        self.assertEqual(result["recurring"]["state"], "stale")
        self.assertEqual(result["recurring"]["data_through"], "2026-11-23")
        self.assertEqual(result["recurring"]["expected_through"], "2026-11-24")

    def test_invalid_future_and_unzoned_calculation_dates_are_unknown(self):
        for stamp in ["", "nonsense", "2026-11-23T15:00:00+00:00", "2026-11-23T14:00:00"]:
            with self.subTest(stamp=stamp):
                self.assertEqual(read(DB(), as_of=stamp)["payroll"]["state"], "unknown")

    def test_explicit_complete_zero_reporting_can_be_current(self):
        for platform in ["meta", "tiktok", "google_ads", "snapchat"]:
            with self.subTest(platform=platform):
                row = read(configured(platform))[platform]
                self.assertEqual(row["state"], "current")
                self.assertEqual(row["last_success_at"], at(1))
                self.assertIsNone(row["data_through"], "A sync timestamp is not certified financial coverage")

    def test_partial_integration_timestamp_does_not_replace_account_success(self):
        db = configured(minutes=180)
        db.rows["mezan_integrations_v2"][0].update(last_sync_at=at(1), data_quality="partial")
        db.rows["mezan_integration_accounts_v2"][0]["last_observed_at"] = at(1)
        result = read(db)["meta"]
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["last_success_at"], at(180))

    def test_fresh_engine_cannot_clear_upstream_failure(self):
        db = configured()
        db.rows["mezan_integration_errors_v2"] = [{"user_id": OWNER, "provider": "meta_ads", "occurred_at": at(0), "code": "meta_rate_limited", "message": "secret"}]
        row = read(db)["meta"]
        self.assertEqual(row["state"], "failed")
        self.assertNotIn("secret", str(row))

    def test_later_verified_success_clears_older_provider_error(self):
        db = configured()
        db.rows["mezan_integration_errors_v2"] = [{"user_id": OWNER, "provider": "meta_ads", "occurred_at": at(10), "code": "meta_rate_limited"}]
        self.assertEqual(read(db)["meta"]["state"], "current")

    def test_one_stale_bound_account_prevents_family_current(self):
        db = configured()
        second = configured(account_id="account-2", minutes=180)
        for collection in ["mz2_ad_account_bindings_v2", "mezan_integration_accounts_v2"]:
            db.rows[collection].extend(second.rows[collection])
        self.assertEqual(read(db)["meta"]["state"], "stale")

    def test_confirmed_binding_cannot_be_hidden_by_unselection_or_duplicate_mapping(self):
        for mutate in [lambda row: row.update(mezan_selected=False), lambda row: row.update(connection_status="disconnected")]:
            db = configured()
            mutate(db.rows["mezan_integration_accounts_v2"][0])
            self.assertNotEqual(read(db)["meta"]["state"], "current")
        db = configured()
        db.rows["mezan_integration_accounts_v2"].append(deepcopy(db.rows["mezan_integration_accounts_v2"][0]))
        self.assertEqual(read(db)["meta"]["state"], "unknown")

    def test_cross_owner_proof_never_satisfies_binding(self):
        db = configured()
        db.rows["mezan_integration_accounts_v2"][0]["user_id"] = "other-owner"
        self.assertEqual(read(db)["meta"]["state"], "unknown")

    def test_snapchat_incomplete_or_unflushed_batch_never_looks_current(self):
        for change in [lambda db: db.rows["mezan_integration_sync_runs_v2"][0]["summary"]["financial_proof"].update(status="partial"),
                       lambda db: db.rows["mezan_integrations_v2"][0].update(financial_last_sync_at=at(180)),
                       lambda db: db.rows["mezan_integration_accounts_v2"][0]["financial_coverage"].update(completed_requests=0)]:
            db = configured("snapchat")
            change(db)
            self.assertNotEqual(read(db)["snapchat"]["state"], "current")
        db = configured("snapchat")
        db.rows["mezan_integrations_v2"][0]["financial_last_sync_at"] = at(180)
        self.assertEqual(read(db)["snapchat"]["last_success_at"], at(180))

    def test_provider_cadences_are_not_all_two_hours(self):
        self.assertEqual(read(configured("meta", minutes=31))["meta"]["state"], "stale")
        self.assertEqual(read(configured("snapchat", minutes=31))["snapchat"]["state"], "current")
        self.assertEqual(read(configured("snapchat", minutes=146))["snapchat"]["state"], "stale")

    def test_completion_allowance_does_not_report_cadence_boundary_as_stall(self):
        for platform, age in [("meta", 5), ("meta", 30.25), ("snapchat", 120), ("snapchat", 145.25)]:
            with self.subTest(platform=platform, age=age):
                row = read(configured(platform, minutes=age))[platform]
                self.assertEqual(row["state"], "current")
                self.assertEqual(row["last_success_at"], at(age))
        self.assertEqual(read(configured("meta", minutes=30.26))["meta"]["state"], "stale")

    def test_explicit_disabled_scheduler_cannot_leave_any_ad_source_current(self):
        for platform in ["snapchat", "meta", "tiktok", "google_ads"]:
            with self.subTest(platform=platform):
                db = configured(platform)
                db.rows["mezan_ads_auto_sync_scheduler_v2"][0]["enabled"] = False
                row = read(db)[platform]
                self.assertEqual(row["state"], "failed")
                self.assertEqual(row["code"], "automatic_sync_disabled")
                self.assertEqual(row["last_success_at"], at(1))

    def test_missing_or_non_boolean_scheduler_metadata_is_unverified(self):
        for enabled in [None, 1, "true", {"enabled": True}]:
            with self.subTest(enabled=enabled):
                db = configured("snapchat")
                db.rows["mezan_ads_auto_sync_scheduler_v2"][0]["enabled"] = enabled
                self.assertEqual(read(db)["snapchat"]["state"], "unknown")
        db.rows["mezan_ads_auto_sync_scheduler_v2"] = []
        self.assertEqual(read(db)["snapchat"]["state"], "unknown")

    def test_running_is_not_success_and_stalled_run_is_failed(self):
        for minutes, state in [(1, "updating"), (26, "failed")]:
            db = configured(minutes=180)
            old = deepcopy(db.rows["mezan_integration_sync_runs_v2"][0])
            old.update(status="running", started_at=at(minutes), finished_at=None)
            db.rows["mezan_integration_sync_runs_v2"].append(old)
            self.assertEqual(read(db)["meta"]["state"], state)

    def test_current_running_attempt_does_not_clear_prior_failure(self):
        db = configured(minutes=180)
        db.rows["mezan_integration_errors_v2"] = [{"user_id": OWNER, "provider": "meta_ads", "occurred_at": at(10), "code": "failed"}]
        new = deepcopy(db.rows["mezan_integration_sync_runs_v2"][0])
        new.update(status="running", started_at=at(1), finished_at=None)
        db.rows["mezan_integration_sync_runs_v2"].append(new)
        self.assertEqual(read(db)["meta"]["state"], "failed")

    def test_salla_daily_success_does_not_require_any_new_order(self):
        db = self.salla()
        row = read(db)["salla"]
        self.assertEqual(row["state"], "current")
        self.assertEqual(row["data_through"], "2026-11-23T07:00:00+00:00")

    def test_salla_exhausted_or_partial_completed_slot_is_not_success(self):
        for status in ["failed", "partial", "incomplete", "interrupted"]:
            db = self.salla()
            db.rows["salla_scheduled_reconciliation"][0]["status"] = status
            self.assertEqual(read(db)["salla"]["state"], "failed")

    def test_salla_stale_schedule_and_missing_proof_are_visible(self):
        db = self.salla()
        row = db.rows["salla_scheduled_reconciliation"][0]
        row.update(completed_slot="2026-11-22", last_success_at="2026-11-22T07:05:00+00:00")
        self.assertEqual(read(db)["salla"]["state"], "stale")
        db.rows["salla_scheduled_reconciliation"] = []
        self.assertEqual(read(db)["salla"]["state"], "unknown")

    def test_salla_missing_result_fields_cannot_be_current(self):
        db = self.salla()
        db.rows["salla_scheduled_reconciliation"][0]["last_result"] = {}
        self.assertEqual(read(db)["salla"]["state"], "unknown")

    def test_salla_honors_its_existing_daily_continuation_window(self):
        db = self.salla()
        row = db.rows["salla_scheduled_reconciliation"][0]
        row.update(completed_slot="2026-11-22", last_success_at="2026-11-22T07:05:00+00:00")
        before = datetime(2026, 11, 23, 7, 59, tzinfo=timezone.utc)
        after = datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc)
        self.assertEqual(read(db, now=before)["salla"]["state"], "current")
        self.assertEqual(read(db, now=after)["salla"]["state"], "stale")

    def test_malformed_run_or_future_error_never_clears_warning(self):
        for change in [lambda row: row.update(status="invented"),
                       lambda row: row["summary"].update(accounts_complete=True),
                       lambda row: row.update(finished_at="2099-01-01T00:00:00Z")]:
            db = configured()
            change(db.rows["mezan_integration_sync_runs_v2"][0])
            self.assertEqual(read(db)["meta"]["state"], "unknown")

    def test_google_explicit_selection_overrides_old_account_flag(self):
        db = configured("google_ads")
        db.rows["mezan_integrations_v2"][0].update(account_selection_configured=True, selected_account_ids=["9999999999"])
        self.assertEqual(read(db)["google_ads"]["state"], "failed")

    def test_bson_naive_utc_dates_are_accepted_but_not_unzoned_strings(self):
        db = self.salla()
        db.rows["salla_scheduled_reconciliation"][0]["last_success_at"] = datetime(2026, 11, 23, 7, 5)
        self.assertEqual(read(db)["salla"]["state"], "current")

    def test_known_reason_is_safe_and_actionable(self):
        db = configured()
        db.rows["mezan_integration_errors_v2"] = [{"user_id": OWNER, "provider": "meta_ads", "occurred_at": at(0), "code": "meta_rate_limited"}]
        self.assertEqual(read(db)["meta"]["code"], "ad_source_rate_limited")

    def test_invalid_owner_does_not_query_a_broader_scope(self):
        db = configured()
        result = asyncio.run(read_source_freshness(db, {"$ne": None}, as_of=at(1), now=NOW))
        self.assertFalse(db.reads)
        self.assertTrue(all(row["state"] == "unknown" for row in result))

    def test_bounds_and_metadata_failure_never_fall_back_to_green(self):
        db = configured()
        db.rows["mz2_ad_account_bindings_v2"] *= 60
        self.assertEqual(read(db)["meta"]["state"], "unknown")
        db = configured()
        db.broken.add("mezan_integration_sync_runs_v2")
        output = read(db)
        self.assertEqual(output["meta"]["state"], "unknown")
        self.assertNotIn("secret", str(output))

    def test_deadline_cancels_owned_reads_and_returns_unknown(self):
        db = configured()
        db.delayed.add("mezan_integration_accounts_v2")
        with patch("operational_source_freshness.SOURCE_TIMEOUT_SECONDS", 0.01):
            result = read(db)
        self.assertEqual(result["meta"]["state"], "unknown")
        self.assertIn("mezan_integration_accounts_v2", db.cancelled)

    def test_projection_owner_bounds_and_public_shape(self):
        db = configured()
        result = read(db)
        allowed = {"key", "label", "state", "last_success_at", "data_through", "expected_through", "message", "code"}
        for row in result.values():
            self.assertEqual(set(row), allowed)
        for call in db.reads:
            self.assertLessEqual(call["limit"], 52)
            if call["collection"] != "mezan_ads_auto_sync_scheduler_v2":
                self.assertTrue(call["query"].get("user_id") == OWNER or call["query"].get("_id") == OWNER)
            for field in call["projection"]:
                self.assertNotIn(field, {"amount", "spend_native", "base_spend_native", "hours", "access_token", "lease_token", "message"})

    def test_unknown_or_unconfirmed_sources_are_not_silently_current(self):
        db = configured()
        db.rows["mz2_ad_account_bindings_v2"][0].pop("confirmed_by")
        self.assertEqual(read(db)["meta"]["state"], "inactive")
        db = configured()
        db.rows["mezan_integrations_v2"][0]["connection_status"] = "needs_reauth"
        self.assertEqual(read(db)["meta"]["state"], "failed")

    @staticmethod
    def salla():
        return DB({"salla_integrations": [{"user_id": OWNER, "status": "connected", "store_id": "1673234356"}],
                   "salla_scheduled_reconciliation": [{"_id": OWNER, "schedule_version": "daily_10_riyadh_update_v1",
                       "status": "completed", "completed_slot": "2026-11-23", "last_success_at": "2026-11-23T07:05:00+00:00",
                       "last_result": {"errors_count": 0, "incomplete_count": 0, "truncated": False, "coverage_complete": True}}]})


class OperationalAdFundingFreshnessTests(unittest.TestCase):
    @staticmethod
    def configured(platform="meta", minutes=1):
        db = configured(platform, minutes=minutes)
        binding = db.rows["mz2_ad_account_bindings_v2"].pop()
        setting = {key: binding[key] for key in ("platform", "integration_account_id", "platform_account_id")}
        setting.update(mode="wallet_first", confirmed_by=OWNER, confirmed_at=at(100),
                       currency="USD", timezone="Asia/Riyadh")
        identity = party_id(OWNER, setting["integration_account_id"])
        db.rows["operational_balance_states_v1"] = [{
            "_id": OWNER, "owner_id": OWNER, "status": "active", "revision": 5,
            "ad_funding": {identity: setting},
        }]
        db.rows["mezan_integration_accounts_v2"][0].update(currency="USD", timezone="Asia/Riyadh")
        return db, db.rows["operational_balance_states_v1"][0], identity

    def test_operational_only_approved_binding_is_configured_for_each_provider(self):
        for platform in ("snapchat", "meta", "tiktok", "google_ads"):
            with self.subTest(platform=platform):
                db, state, identity = self.configured(platform)
                canonical, conflicts = funding_bindings(state, OWNER, [])
                self.assertFalse(conflicts)
                self.assertEqual(canonical[0]["_id"], identity)
                result = read(db)
                self.assertEqual(result[platform]["state"], "current")
                self.assertEqual(result[platform]["last_success_at"], at(1))
                self.assertNotIn(identity, str(result))
                self.assertIsNone(result[platform]["data_through"])

    def test_operational_binding_preserves_stale_failed_disabled_and_missing_proof(self):
        for reason in ("stale", "provider_error", "disabled", "missing_account"):
            with self.subTest(reason=reason):
                db, _, _ = self.configured(minutes=180 if reason == "stale" else 1)
                if reason == "provider_error":
                    db.rows["mezan_integration_errors_v2"] = [{"user_id": OWNER, "provider": "meta_ads",
                        "occurred_at": at(0), "code": "meta_rate_limited"}]
                if reason == "disabled":
                    db.rows["mezan_ads_auto_sync_scheduler_v2"][0]["enabled"] = False
                if reason == "missing_account":
                    db.rows["mezan_integration_accounts_v2"] = []
                expected = {"stale": "ad_source_stale", "provider_error": "ad_source_rate_limited",
                            "disabled": "automatic_sync_disabled", "missing_account": "metadata_invalid"}
                self.assertEqual(read(db)["meta"]["code"], expected[reason])

    def test_native_only_behavior_survives_missing_or_empty_operational_settings(self):
        for state in (None, {"owner_id": OWNER}, {"owner_id": OWNER, "ad_funding": {}}):
            with self.subTest(state=state):
                db = configured()
                if state is not None:
                    db.rows["operational_balance_states_v1"] = [{"_id": OWNER, **state}]
                self.assertEqual(read(db)["meta"]["state"], "current")

    def test_foreign_unconfirmed_and_malformed_operational_authority_is_unknown(self):
        mutations = [lambda state, identity: state.update(owner_id="foreign-owner"),
                     lambda state, identity: state["ad_funding"][identity].update(confirmed_by="foreign-owner"),
                     lambda state, identity: state["ad_funding"][identity].pop("confirmed_at"),
                     lambda state, identity: state["ad_funding"][identity].update(confirmed_at={"time": at(100)}),
                     lambda state, identity: state["ad_funding"][identity].update(confirmed_at="invalid"),
                     lambda state, identity: state["ad_funding"][identity].update(confirmed_at="2099-01-01T00:00:00Z"),
                     lambda state, identity: state["ad_funding"][identity].update(archived="false"),
                     lambda state, identity: state["ad_funding"][identity].update(mode="unapproved"),
                     lambda state, identity: state["ad_funding"].update(wrong=state["ad_funding"].pop(identity)),
                     lambda state, identity: state["ad_funding"][identity].update(integration_account_id="x" * 201),
                     lambda state, identity: state["ad_funding"].update({identity: []}),
                     lambda state, identity: state.update(ad_funding=None),
                     lambda state, identity: state.update(ad_funding=[]),
                     lambda state, identity: state.update(ad_funding={"archive_marker": {"packed": "not-inline"}})]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                db, state, identity = self.configured()
                mutate(state, identity)
                self.assertEqual(read(db)["meta"]["state"], "unknown")

    def test_another_owners_document_cannot_configure_this_owner(self):
        db, state, _ = self.configured()
        state.update(_id="foreign-owner", owner_id="foreign-owner")
        self.assertEqual(read(db)["meta"]["state"], "inactive")

    def test_native_conflict_is_checked_before_health_inactive_flags(self):
        for flags in ({}, {"active": False}, {"is_active": False}, {"archived": True}):
            with self.subTest(flags=flags):
                db, state, _ = self.configured()
                native = configured().rows["mz2_ad_account_bindings_v2"][0]
                native.update(flags)
                db.rows["mz2_ad_account_bindings_v2"] = [native]
                self.assertTrue(funding_bindings(state, OWNER, [native])[1])
                self.assertEqual(read(db)["meta"]["state"], "unknown")

    def test_operational_reconnect_duplicate_is_not_silently_inactive(self):
        db, state, identity = self.configured()
        second = {**state["ad_funding"][identity], "integration_account_id": "reconnected-account"}
        state["ad_funding"][party_id(OWNER, second["integration_account_id"])] = second
        self.assertTrue(funding_bindings(state, OWNER, [])[1])
        self.assertEqual(read(db)["meta"]["state"], "unknown")

    def test_unconfirmed_or_inactive_native_binding_does_not_override_approved_operational(self):
        for change in ({"confirmed_by": None}, {"status": "inactive"}):
            with self.subTest(change=change):
                db, state, _ = self.configured()
                native = {**configured().rows["mz2_ad_account_bindings_v2"][0], **change}
                db.rows["mz2_ad_account_bindings_v2"] = [native]
                self.assertFalse(funding_bindings(state, OWNER, [native])[1])
                self.assertEqual(read(db)["meta"]["state"], "current")

    def test_inactive_operational_flags_remain_inactive(self):
        for key, flag in (("active", False), ("is_active", False), ("archived", True),
                          ("deleted", True), ("is_archived", True), ("is_deleted", True)):
            with self.subTest(key=key):
                db, state, identity = self.configured()
                state["ad_funding"][identity][key] = flag
                self.assertEqual(read(db)["meta"]["state"], "inactive")

    def test_wallet_first_account_currency_and_timezone_must_match(self):
        for field, wrong in (("currency", "SAR"), ("timezone", "UTC")):
            with self.subTest(field=field):
                db, _, _ = self.configured()
                db.rows["mezan_integration_accounts_v2"][0][field] = wrong
                self.assertEqual(read(db)["meta"]["code"], "metadata_invalid")

    def test_operational_and_combined_binding_limits_fail_closed(self):
        for count, native_count in ((80, 0), (26, 25)):
            with self.subTest(count=count, native_count=native_count):
                db, state, identity = self.configured()
                example = state["ad_funding"].pop(identity)
                for index in range(count):
                    identifier = "operational-" + str(index)
                    state["ad_funding"][party_id(OWNER, identifier)] = {
                        **example, "integration_account_id": identifier, "platform_account_id": identifier}
                db.rows["mz2_ad_account_bindings_v2"] = [
                    {**configured().rows["mz2_ad_account_bindings_v2"][0],
                     "integration_account_id": "native-" + str(i), "platform_account_id": "native-" + str(i)}
                    for i in range(native_count)]
                self.assertEqual(read(db)["meta"]["code"], "metadata_limit")
                call = next(call for call in db.reads if call["collection"] == "operational_balance_states_v1")
                bounded = projected(state, call["projection"])
                self.assertLessEqual(len(bounded["funding_entries"]), 51)

    def test_operational_projection_never_hydrates_financial_state_or_archive(self):
        db, state, identity = self.configured()
        secret = "financial-or-credential-content-must-not-be-read"
        state.update(engine={"packed_financial_snapshot_v1": {"data": secret}},
                     engine_audit_archive={"data": secret}, openings={"amount": secret},
                     movements=[{"amount": secret}], requests={"request": secret})
        state["ad_funding"][identity].update(evidence=secret, amount=secret, access_token=secret)
        with patch("operational_balance_store.read", side_effect=AssertionError("full state read")), \
             patch("operational_balance_archive.unpack", side_effect=AssertionError("archive hydration")), \
             patch("operational_ad_funding.catalog", side_effect=AssertionError("financial catalog")):
            self.assertEqual(read(db)["meta"]["state"], "current")
        call = next(call for call in db.reads if call["collection"] == "operational_balance_states_v1")
        self.assertEqual(call["query"], {"_id": OWNER})
        self.assertEqual(call["limit"], 2)
        metadata = projected(state, call["projection"])
        self.assertNotIn(secret, str(metadata))
        self.assertNotIn("engine", metadata)
        self.assertEqual(set(metadata), {"owner_id", "funding_type", "funding_entries"})

    def test_operational_metadata_failure_and_deadline_do_not_report_inactive(self):
        db, _, _ = self.configured()
        db.broken.add("operational_balance_states_v1")
        result = read(db)
        self.assertEqual(result["meta"]["code"], "metadata_unavailable")
        self.assertNotIn("secret", str(result))
        db, _, _ = self.configured()
        db.delayed.add("operational_balance_states_v1")
        with patch("operational_source_freshness.SOURCE_TIMEOUT_SECONDS", 0.01):
            self.assertEqual(read(db)["meta"]["state"], "unknown")
        self.assertIn("operational_balance_states_v1", db.cancelled)


if __name__ == "__main__":
    unittest.main()
