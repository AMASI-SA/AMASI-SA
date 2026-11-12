"""One-time Google selection against local fixtures; no live account access.

The transaction double stages writes and commits/aborts at the driver seam.
It exercises application failure paths, not Mongo server transaction support.
"""
from contextlib import asynccontextmanager
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

from mongomock_motor import AsyncMongoMockClient
import pytest

from integrations_control_center.ad_daily_close_proof import close_proof
from integrations_control_center.google_ads_reporting import _accounts
from integrations_control_center.google_dashboard_spend import load_google_dashboard_spend


SPEC = importlib.util.spec_from_file_location(
    "set_google_reporting_accounts", Path(__file__).resolve().parents[2] / "scripts/set_google_reporting_accounts.py",
)
selection = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(selection)

OWNER = "selection-owner-fixture"
IDS = ("1111111101", "1111111102", "1111111103")
DAY = "2026-07-04"


class Collection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def find(self, query, projection=None, *, session=None):
        self.db.reads.append((self.name, deepcopy(query), deepcopy(projection), session is not None))
        target = session.snapshot if session else self.db.raw
        return target[self.name].find(query, projection)

    async def update_one(self, query, update, *, upsert, session):
        assert session is not None and upsert is False
        self.db.update_attempts += 1
        if self.db.update_attempts == self.db.fail_update_number:
            return SimpleNamespace(matched_count=0, modified_count=0)
        result = await session.snapshot[self.name].update_one(query, update, upsert=False)
        session.writes.append((self.name, "update_one", (query, update), {"upsert": False}))
        return result

    async def insert_one(self, document, *, session):
        assert session is not None and self.name == selection.AUDIT
        if self.db.audit_failure:
            raise RuntimeError("PRIVATE_AUDIT_SENTINEL")
        result = await session.snapshot[self.name].insert_one(deepcopy(document))
        session.writes.append((self.name, "insert_one", (deepcopy(document),), {}))
        return result


class Session:
    def __init__(self, db):
        self.db, self.writes = db, []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        self.db.sessions_closed += 1

    @asynccontextmanager
    async def start_transaction(self, **options):
        assert options["read_concern"].level == "snapshot"
        assert options["read_preference"] == selection.ReadPreference.PRIMARY
        assert options["write_concern"].document == {"w": "majority", "wtimeout": 5000}
        assert options["max_commit_time_ms"] == 5000
        self.db.transactions += 1
        if self.db.before_transaction:
            await self.db.before_transaction(self.db.raw)
        self.snapshot = AsyncMongoMockClient().selection_transaction
        for name in await self.db.raw.list_collection_names():
            rows = await self.db.raw[name].find({}).to_list(length=None)
            if rows:
                await self.snapshot[name].insert_many(deepcopy(rows))
        try:
            yield
        except Exception:
            self.db.aborts += 1
            raise
        else:
            for name, operation, args, kwargs in self.writes:
                await getattr(self.db.raw[name], operation)(*args, **kwargs)
            self.db.commits += 1
            if self.db.after_commit:
                await self.db.after_commit(self.db.raw)


class DB:
    def __init__(self):
        self.raw = AsyncMongoMockClient().google_selection
        self.client = self
        self.reads = []
        self.transactions = self.commits = self.aborts = self.sessions_closed = self.update_attempts = 0
        self.before_transaction = self.after_commit = None
        self.fail_update_number = None
        self.audit_failure = self.session_failure = False

    def __getitem__(self, name):
        return Collection(self, name)

    async def start_session(self):
        if self.session_failure:
            raise RuntimeError("PRIVATE_TRANSACTION_TOPOLOGY_SENTINEL")
        return Session(self)


def account(account_id, **overrides):
    return {
        "_id": "account-" + account_id, "user_id": OWNER, "provider": "google_ads",
        "ad_account_id": account_id, "external_account_id": account_id,
        "connection_status": "connected", "connection_provenance": "api_connection",
        "currency": "SAR", "timezone": "Asia/Riyadh",
        "updated_at": "unchanged-account-time", "last_observed_at": "unchanged-observation-time",
        "last_sync_at": "unchanged-sync-time", "opaque_payload": {"keep": [1, 2, 3]},
        **overrides,
    }


async def seed(db):
    await db.raw.users.insert_one({"_id": "owner-row", "id": OWNER, "role": "owner", "is_active": True})
    await db.raw[selection.ACCOUNTS].insert_many([account(value) for value in IDS] + [
        account(IDS[0], _id="foreign-owner", user_id="another-owner", mezan_selected=True),
        account("2222222201", connection_status="disconnected", mezan_selected=True),
        account("2222222202", connection_provenance="legacy_import", mezan_selected=True),
        account("2222222203", provider="meta_ads", mezan_selected=True),
    ])
    await db.raw.mezan_integrations_v2.insert_one({
        "user_id": OWNER, "provider": "google_ads", "connection_status": "connected",
        "connection_provenance": "api_connection", "updated_at": "preserved-parent-time",
    })
    await db.raw.synthetic_credentials.insert_one({"user_id": OWNER, "ciphertext": "PRIVATE_CREDENTIAL_SENTINEL"})
    await db.raw.synthetic_campaigns.insert_one({"user_id": OWNER, "budget": 123, "status": "unchanged"})
    await db.raw.daily_costs.insert_one({"user_id": OWNER, "date": "2026-07-03", "google_ads": 17})


async def snapshot(db):
    return {name: await db[name].find({}).sort("_id", 1).to_list(length=None)
            for name in await db.list_collection_names()}


async def run(db, **overrides):
    return await selection.set_reporting_accounts(db, **{
        "owner_id": OWNER, "selected_account_ids": [IDS[0]], "expected_account_ids": list(IDS),
        **overrides,
    })


@pytest.mark.asyncio
async def test_dry_run_is_default_reports_previous_missing_selection_and_reads_no_secrets():
    db = DB()
    await seed(db)
    before = await snapshot(db.raw)
    result = await run(db)
    assert result["mode"] == "dry_run" and result["changed"] is False
    assert result["planned_change_count"] == 3 and result["accounts_to_change"] == list(IDS)
    assert result["previous_selected_account_ids"] == []
    assert result["previous_selection"] == [
        {"account_id": value, "mezan_selected_present": False, "mezan_selected": None} for value in IDS
    ]
    assert await snapshot(db.raw) == before and db.transactions == db.update_attempts == 0
    assert {name for name, *_ in db.reads} == {"users", selection.ACCOUNTS}
    assert all("opaque_payload" not in (projection or {}) for _, _, projection, _ in db.reads)
    assert "PRIVATE_CREDENTIAL_SENTINEL" not in json.dumps(result)


@pytest.mark.asyncio
async def test_apply_sets_exact_subset_preserves_owner_isolation_and_every_other_field():
    db = DB()
    await seed(db)
    before = await snapshot(db.raw)
    result = await run(db, apply=True)
    after = await snapshot(db.raw)
    assert result["status"] == "applied" and result["changed"] is True
    assert db.commits == 1 and db.aborts == 0 and db.sessions_closed == 1
    assert len(after[selection.ACCOUNTS]) == len(before[selection.ACCOUNTS])
    by_id = {row["_id"]: row for row in after[selection.ACCOUNTS]}
    for row in before[selection.ACCOUNTS]:
        updated = by_id[row["_id"]]
        if row["_id"] in {"account-" + value for value in IDS}:
            assert updated["mezan_selected"] is (row["ad_account_id"] == IDS[0])
            assert {key: value for key, value in updated.items() if key not in selection.SELECTION_FIELDS} == row
        else:
            assert updated == row
    for name in before.keys() - {selection.ACCOUNTS}:
        assert before[name] == after[name]
    audit = after[selection.AUDIT]
    assert len(audit) == 1 and audit[0]["run_id"] == result["audit_run_id"]
    assert audit[0]["summary"]["selected_account_ids"] == [IDS[0]]
    assert audit[0]["summary"]["previous_selection"] == result["previous_selection"]
    assert all(audit[0]["summary"][key] is False for key in selection.NO_EXTERNAL_WRITES)
    assert sum(in_session for name, _, _, in_session in db.reads if name == selection.ACCOUNTS) == 1
    assert sum(not in_session for name, _, _, in_session in db.reads if name == selection.ACCOUNTS) == 2


@pytest.mark.asyncio
async def test_repeat_is_idempotent_and_does_not_touch_selection_times_or_duplicate_audit():
    db = DB()
    await seed(db)
    await run(db, apply=True)
    before = await snapshot(db.raw)
    result = await run(db, apply=True)
    assert result["status"] == "unchanged" and result["changed"] is False
    assert result["accounts_to_change"] == [] and "audit_run_id" not in result
    assert await snapshot(db.raw) == before and db.update_attempts == 3


@pytest.mark.asyncio
async def test_hyphenated_ids_and_previous_true_false_null_flags_are_reviewable_and_replaced_exactly():
    db = DB()
    await seed(db)
    for account_id, flag in zip(IDS, (False, True, None)):
        await db.raw[selection.ACCOUNTS].update_one(
            {"_id": "account-" + account_id}, {"$set": {"mezan_selected": flag}},
        )
    await db.raw[selection.ACCOUNTS].update_one(
        {"_id": "account-" + IDS[0]}, {"$set": {"external_account_id": "111-111-1101"}},
    )
    result = await run(db, selected_account_ids=["111-111-1101"], apply=True)
    assert result["previous_selection"] == [
        {"account_id": account_id, "mezan_selected_present": True, "mezan_selected": flag}
        for account_id, flag in zip(IDS, (False, True, None))
    ]
    assert result["previous_selected_account_ids"] == [IDS[1]]
    assert [row["ad_account_id"] for row in await _accounts(db.raw, OWNER)] == [IDS[0]]
    row = await db.raw[selection.ACCOUNTS].find_one({"_id": "account-" + IDS[0]})
    assert row["external_account_id"] == "111-111-1101"  # Identity storage is untouched.


@pytest.mark.asyncio
@pytest.mark.parametrize("overrides,code", [
    ({"owner_id": ""}, "explicit_owner_id_required"),
    ({"owner_id": "absent-owner"}, "unique_active_owner_required"),
    ({"selected_account_ids": []}, "selected_account_ids_required_and_bounded"),
    ({"expected_account_ids": []}, "expected_account_ids_required_and_bounded"),
    ({"selected_account_ids": [IDS[0], "111-111-1101"]}, "duplicate_selected_account_ids"),
    ({"expected_account_ids": [*IDS, IDS[0]]}, "duplicate_expected_account_ids"),
    ({"selected_account_ids": ["9999999901"]}, "selected_accounts_outside_expected_scope"),
    ({"expected_account_ids": [IDS[0]]}, "expected_google_account_scope_mismatch"),
    ({"expected_account_ids": [*IDS, "9999999901"]}, "expected_google_account_scope_mismatch"),
    ({"selected_account_ids": [True]}, "invalid_google_account_id"),
    ({"expected_account_ids": [str(5555555500 + i) for i in range(51)]}, "expected_account_ids_required_and_bounded"),
])
async def test_invalid_arguments_refuse_before_writes(overrides, code):
    db = DB()
    await seed(db)
    before = await snapshot(db.raw)
    with pytest.raises(selection.SelectionError, match=code):
        await run(db, apply=True, **overrides)
    assert await snapshot(db.raw) == before and db.update_attempts == db.transactions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fields", [
    {"role": "staff"}, {"is_active": False}, {"disabled": True}, {"deleted_at": "deleted"},
])
async def test_owner_must_remain_active(fields):
    db = DB()
    await seed(db)
    await db.raw.users.update_one({"id": OWNER}, {"$set": fields})
    with pytest.raises(selection.SelectionError, match="unique_active_owner_required"):
        await run(db, apply=True)
    assert db.update_attempts == db.transactions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind,code", [
    ("duplicate-owner", "unique_active_owner_required"),
    ("duplicate-account", "duplicate_connected_google_account"),
    ("conflicting-identity", "google_account_identity_unproved"),
    ("nonboolean-selection", "invalid_existing_google_selection"),
    ("oversized-scope", "connected_google_account_limit_exceeded"),
])
async def test_ambiguous_or_unbounded_existing_scope_is_refused(kind, code):
    db = DB()
    await seed(db)
    if kind == "duplicate-owner":
        await db.raw.users.insert_one({"id": OWNER, "role": "owner"})
    elif kind == "duplicate-account":
        await db.raw[selection.ACCOUNTS].insert_one(account("111-111-1101"))
    elif kind == "conflicting-identity":
        await db.raw[selection.ACCOUNTS].update_one({"_id": "account-" + IDS[0]}, {"$set": {"external_account_id": IDS[1]}})
    elif kind == "nonboolean-selection":
        await db.raw[selection.ACCOUNTS].update_one({"_id": "account-" + IDS[0]}, {"$set": {"mezan_selected": "true"}})
    else:
        await db.raw[selection.ACCOUNTS].insert_many([account(str(3333333300 + i)) for i in range(48)])
    before = await snapshot(db.raw)
    with pytest.raises(selection.SelectionError, match=code):
        await run(db, apply=True)
    assert await snapshot(db.raw) == before and db.update_attempts == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["expanded-scope", "selection-changed", "owner-disabled"])
async def test_fresh_transaction_read_refuses_concurrent_changes(kind):
    db = DB()
    await seed(db)
    async def concurrent_change(raw):
        if kind == "expanded-scope":
            await raw[selection.ACCOUNTS].insert_one(account("4444444401"))
        elif kind == "selection-changed":
            await raw[selection.ACCOUNTS].update_one({"_id": "account-" + IDS[1]}, {"$set": {"mezan_selected": True}})
        else:
            await raw.users.update_one({"id": OWNER}, {"$set": {"disabled": True}})
    db.before_transaction = concurrent_change
    with pytest.raises(selection.SelectionError):
        await run(db, apply=True)
    assert db.aborts == 1 and db.commits == db.update_attempts == 0
    assert await db.raw[selection.AUDIT].count_documents({}) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["second-cas", "audit", "unsupported-transactions"])
async def test_any_apply_failure_has_no_partial_write_or_fallback(failure):
    db = DB()
    await seed(db)
    before = await snapshot(db.raw)
    db.fail_update_number = 2 if failure == "second-cas" else None
    db.audit_failure = failure == "audit"
    db.session_failure = failure == "unsupported-transactions"
    with pytest.raises((selection.SelectionError, RuntimeError)):
        await run(db, apply=True)
    assert await snapshot(db.raw) == before and db.commits == 0


@pytest.mark.asyncio
async def test_postcommit_scope_change_reports_unknown_outcome_instead_of_false_rollback():
    db = DB()
    await seed(db)
    async def concurrent_change(raw):
        await raw[selection.ACCOUNTS].insert_one(account("4444444401"))
    db.after_commit = concurrent_change
    with pytest.raises(selection.SelectionError, match="google_selection_readback_unverified") as error:
        await run(db, apply=True)
    assert error.value.outcome_unknown is True and db.commits == 1
    assert await db.raw[selection.AUDIT].count_documents({}) == 1
    selected = await db.raw[selection.ACCOUNTS].find_one({"_id": "account-" + IDS[0]})
    assert selected["mezan_selected"] is True


def native_fact(account_id, amount):
    observed = "2026-07-05T00:00:00+00:00"
    return {
        "user_id": OWNER, "provider": "google_ads", "ad_account_id": account_id, "date": DAY,
        "source_mode": "google_ads_reporting_v1", "account_timezone": "Asia/Riyadh",
        "currency_native": "SAR", "spend_native": amount, "spend_sar": amount,
        "fx_rate_to_sar": 1, "observed_at": observed,
        "source_close_proof": close_proof(
            account_id=account_id, business_date=DAY, timezone="Asia/Riyadh", currency="SAR",
            spend=amount, provider_row_count=1, complete_response=True, explicit_spend_present=True,
            identity_proven=True, source_mode="google_ads_reporting_v1", observed_at=observed,
        ),
    }


async def spend(db):
    return await load_google_dashboard_spend(
        db.raw, OWNER, date_from=DAY, date_to=DAY, now=datetime(2026, 7, 6, tzinfo=timezone.utc),
    )


@pytest.mark.asyncio
async def test_existing_google_consumers_use_exact_selection_without_erasing_saved_reports():
    db = DB()
    await seed(db)
    reports = db.raw.mezan_google_ads_performance_daily_v2
    await reports.insert_many([native_fact(IDS[0], 25.4), native_fact(IDS[1], 800)])
    saved = await reports.find({}).to_list(length=None)
    assert {row["ad_account_id"] for row in await _accounts(db.raw, OWNER)} == set(IDS)
    before = await spend(db)
    assert before["account_ids"] == list(IDS) and before["total_sar"] is None
    assert "google_native_account_day_missing" in before["quality"]["reason_codes"]
    await run(db, apply=True)
    assert [row["ad_account_id"] for row in await _accounts(db.raw, OWNER)] == [IDS[0]]
    after = await spend(db)
    assert after["account_ids"] == [IDS[0]]
    assert after["total_sar"] == after["known_total_sar"] == 25.4
    assert after["quality"]["amount_complete"] is True
    assert await reports.find({}).to_list(length=None) == saved


@pytest.mark.asyncio
async def test_selected_account_with_truly_missing_spend_stays_unknown():
    db = DB()
    await seed(db)
    await run(db, apply=True)
    result = await spend(db)
    assert result["account_ids"] == [IDS[0]]
    assert result["total_sar"] is None and result["daily_sar"][DAY] is None
    assert result["quality"]["amount_available"] is False


def test_cli_is_dry_run_by_default_and_requires_both_explicit_lists():
    args = selection._parser().parse_args([
        "--owner-id", OWNER, "--selected-account-ids", IDS[0], "--expected-account-ids", *IDS,
    ])
    assert args.apply is False
    with pytest.raises(SystemExit):
        selection._parser().parse_args(["--owner-id", OWNER, "--selected-account-ids", IDS[0]])


@pytest.mark.parametrize("apply", [False, True])
def test_cli_sanitizes_database_errors_and_does_not_claim_failed_apply_was_rolled_back(monkeypatch, capsys, apply):
    async def fail(_args):
        raise RuntimeError("mongodb://PRIVATE_URI:SECRET@PRIVATE_HOST")
    monkeypatch.setattr(selection, "_main_async", fail)
    args = ["--owner-id", OWNER, "--selected-account-ids", IDS[0], "--expected-account-ids", *IDS]
    assert selection.main(args + (["--apply"] if apply else [])) == 2
    output = capsys.readouterr()
    result = json.loads(output.err)
    assert result == {"ok": False, "error": "google_selection_database_unavailable", "apply_outcome_unknown": apply}
    assert output.out == "" and "PRIVATE" not in output.err and "SECRET" not in output.err
