"""Fail-closed tests for the production WhatsApp channel provisioner."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pymongo.errors import DuplicateKeyError

SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "provision_whatsapp_channel.py"
)
SPEC = importlib.util.spec_from_file_location("provision_whatsapp_channel", SCRIPT_PATH)
assert SPEC and SPEC.loader
provision = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = provision
SPEC.loader.exec_module(provision)

NOW = datetime(2026, 8, 13, 1, 0, tzinfo=timezone.utc)
PHONE_NUMBER_ID = "123456789012345"


def _matches(document, query):
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in expected):
                return False
            continue
        actual = document.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif isinstance(expected, dict) and "$lte" in expected:
            if actual is None or actual > expected["$lte"]:
                return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.maximum = len(rows)

    def limit(self, value):
        self.maximum = value
        return self

    async def to_list(self, *, length):
        return deepcopy(self.rows[: min(length, self.maximum)])


class FakeCollection:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or [])
        self.insert_calls = []
        self.insert_error = None
        self.insert_started = None
        self.continue_insert = None

    def find(self, query, _projection=None):
        return FakeCursor([row for row in self.rows if _matches(row, query)])

    async def insert_one(self, document):
        if self.insert_error:
            raise self.insert_error
        if self.insert_started is not None and self.continue_insert is not None:
            self.insert_started.set()
            await self.continue_insert.wait()
        self.insert_calls.append(deepcopy(document))
        self.rows.append(deepcopy(document))
        return SimpleNamespace(inserted_id="new")

    async def find_one_and_update(
        self,
        query,
        update,
        *,
        upsert=False,
        return_document=None,
    ):
        del return_document
        matching = next((row for row in self.rows if _matches(row, query)), None)
        if matching is None:
            requested_id = query.get("_id")
            if any(row.get("_id") == requested_id for row in self.rows):
                raise DuplicateKeyError("lease already held")
            if not upsert:
                return None
            matching = deepcopy(update.get("$setOnInsert", {}))
            self.rows.append(matching)
        matching.update(deepcopy(update.get("$set", {})))
        return deepcopy(matching)

    async def delete_one(self, query):
        for index, row in enumerate(self.rows):
            if _matches(row, query):
                self.rows.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)


class FakeDB:
    def __init__(self, *, owners=None, stores=None, channels=None):
        self.users = FakeCollection(owners or [])
        self.salla_integrations = FakeCollection(stores or [])
        self.mezan_customer_channels_v1 = FakeCollection(channels or [])
        self.mezan_customer_channel_provision_locks_v1 = FakeCollection()


def _db(**overrides):
    defaults = {
        "owners": [{"_id": "owner-object", "id": "owner-1", "role": "owner"}],
        "stores": [
            {
                "_id": "store-object",
                "user_id": "owner-1",
                "store_id": 945168084,
                "status": "connected",
            }
        ],
    }
    defaults.update(overrides)
    return FakeDB(**defaults)


def _safe_channel(
    *,
    account_key=f"account:v1:{'a' * 64}",
    channel_id="whatsapp-amasi",
    owner_id="owner-1",
    merchant_id="945168084",
):
    return {
        "_id": f"object-{channel_id}",
        "schema_version": 1,
        "user_id": owner_id,
        "merchant_id": merchant_id,
        "channel_id": channel_id,
        "provider": "whatsapp",
        "external_account_key": account_key,
        "status": "connected",
        "ingress_enabled": True,
        "egress_mode": "disabled",
        "send_allowed": False,
        "ai_auto_reply_allowed": False,
        "created_at": NOW,
        "updated_at": NOW,
        "plaintext_credentials_stored": False,
    }


@pytest.fixture(autouse=True)
def dedicated_binding_key(monkeypatch):
    monkeypatch.setenv(
        provision.BINDING_HMAC_ENV,
        "test-only-dedicated-binding-key-0123456789",
    )


@pytest.mark.asyncio
async def test_dry_run_plans_receive_only_insert_without_writing_or_leaking_ids():
    db = _db()

    plan = await provision.build_plan(db, phone_number_id=PHONE_NUMBER_ID, now=NOW)
    public = plan.public()
    rendered = json.dumps(public, default=str)

    assert plan.action == "insert"
    assert plan.document["merchant_id"] == "945168084"
    assert plan.document["status"] == "connected"
    assert plan.document["ingress_enabled"] is True
    assert plan.document["egress_mode"] == "disabled"
    assert plan.document["send_allowed"] is False
    assert plan.document["ai_auto_reply_allowed"] is False
    assert plan.document["plaintext_credentials_stored"] is False
    assert db.mezan_customer_channels_v1.insert_calls == []
    assert PHONE_NUMBER_ID not in rendered
    assert "owner-1" not in rendered
    assert "945168084" not in rendered
    assert PHONE_NUMBER_ID not in json.dumps(plan.document, default=str)


@pytest.mark.asyncio
async def test_apply_inserts_only_the_non_reversible_binding():
    db = _db()
    plan = await provision.build_plan(db, phone_number_id=PHONE_NUMBER_ID, now=NOW)

    result = await provision.apply_plan(db, plan)

    assert result["mode"] == "applied"
    inserted = db.mezan_customer_channels_v1.insert_calls[0]
    assert inserted["external_account_key"].startswith("account:v1:")
    assert inserted["channel_id"].startswith("whatsapp-")
    assert PHONE_NUMBER_ID not in json.dumps(inserted, default=str)


@pytest.mark.asyncio
async def test_existing_channel_is_preserved_and_requires_an_explicit_add_gate():
    placeholder = _safe_channel()
    db = _db(channels=[placeholder])

    with pytest.raises(provision.ProvisioningError, match="allow-additional-channel"):
        await provision.build_plan(db, phone_number_id=PHONE_NUMBER_ID, now=NOW)

    plan = await provision.build_plan(
        db,
        phone_number_id=PHONE_NUMBER_ID,
        allow_additional_channel=True,
        now=NOW,
    )
    assert plan.action == "insert_additional_channel"
    assert plan.document["channel_id"] != placeholder["channel_id"]

    with pytest.raises(provision.ProvisioningError, match="allow-additional-channel"):
        await provision.apply_plan(db, plan)

    result = await provision.apply_plan(
        db,
        plan,
        allow_additional_channel=True,
    )
    assert result["mode"] == "applied"
    assert db.mezan_customer_channels_v1.rows[0] == placeholder
    assert len(db.mezan_customer_channels_v1.rows) == 2


@pytest.mark.asyncio
async def test_scope_change_after_planning_blocks_insert():
    db = _db()
    plan = await provision.build_plan(db, phone_number_id=PHONE_NUMBER_ID, now=NOW)
    db.mezan_customer_channels_v1.rows.append(_safe_channel())

    with pytest.raises(provision.ProvisioningError, match="changed after planning"):
        await provision.apply_plan(db, plan)

    assert db.mezan_customer_channels_v1.insert_calls == []


@pytest.mark.asyncio
async def test_scope_lease_blocks_two_interleaved_first_inserts():
    db = _db()
    second_phone_number_id = "987654321098765"
    first_plan = await provision.build_plan(
        db,
        phone_number_id=PHONE_NUMBER_ID,
        now=NOW,
    )
    second_plan = await provision.build_plan(
        db,
        phone_number_id=second_phone_number_id,
        now=NOW,
    )
    started = asyncio.Event()
    continue_insert = asyncio.Event()
    db.mezan_customer_channels_v1.insert_started = started
    db.mezan_customer_channels_v1.continue_insert = continue_insert

    first_apply = asyncio.create_task(
        provision.apply_plan(
            db,
            first_plan,
            lease_owner="first-lease",
            now=NOW,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)

    with pytest.raises(provision.ProvisioningError, match="holds the store scope"):
        await provision.apply_plan(
            db,
            second_plan,
            lease_owner="second-lease",
            now=NOW,
        )

    continue_insert.set()
    await first_apply
    assert len(db.mezan_customer_channels_v1.rows) == 1
    assert db.mezan_customer_channel_provision_locks_v1.rows == []


@pytest.mark.asyncio
async def test_exact_canonical_binding_is_idempotent_noop():
    account_key = provision.build_channel_account_key("whatsapp", PHONE_NUMBER_ID)
    db = _db(channels=[_safe_channel(account_key=account_key)])

    plan = await provision.build_plan(db, phone_number_id=PHONE_NUMBER_ID, now=NOW)
    result = await provision.apply_plan(db, plan)

    assert plan.action == "noop"
    assert result["mode"] == "no_change"
    assert result["write_required"] is False
    assert db.mezan_customer_channels_v1.insert_calls == []


@pytest.mark.asyncio
async def test_noop_rechecks_binding_state_under_the_scope_lease():
    account_key = provision.build_channel_account_key("whatsapp", PHONE_NUMBER_ID)
    db = _db(channels=[_safe_channel(account_key=account_key)])
    plan = await provision.build_plan(db, phone_number_id=PHONE_NUMBER_ID, now=NOW)
    db.mezan_customer_channels_v1.rows[0]["status"] = "paused"

    with pytest.raises(provision.ProvisioningError, match="changed after planning"):
        await provision.apply_plan(db, plan, lease_owner="noop-lease", now=NOW)

    assert db.mezan_customer_channel_provision_locks_v1.rows == []


@pytest.mark.asyncio
async def test_exact_binding_owned_by_another_tenant_is_rejected():
    account_key = provision.build_channel_account_key("whatsapp", PHONE_NUMBER_ID)
    foreign = _safe_channel(
        account_key=account_key,
        owner_id="owner-2",
        merchant_id="store-2",
    )
    db = _db(channels=[foreign])

    with pytest.raises(provision.ProvisioningError, match="another tenant"):
        await provision.build_plan(db, phone_number_id=PHONE_NUMBER_ID, now=NOW)


@pytest.mark.asyncio
async def test_noncanonical_exact_binding_is_not_silently_repaired():
    account_key = provision.build_channel_account_key("whatsapp", PHONE_NUMBER_ID)
    unsafe = _safe_channel(account_key=account_key)
    unsafe["ingress_enabled"] = False
    db = _db(channels=[unsafe])

    with pytest.raises(provision.ProvisioningError, match="canonical receive-only"):
        await provision.build_plan(db, phone_number_id=PHONE_NUMBER_ID, now=NOW)


@pytest.mark.asyncio
async def test_unsafe_or_multiple_existing_channels_fail_closed():
    unsafe = _safe_channel()
    unsafe["external_account_key"] = "raw-provider-value"
    unsafe_db = _db(channels=[unsafe])
    with pytest.raises(provision.ProvisioningError, match="not a safe receive-only"):
        await provision.build_plan(
            unsafe_db,
            phone_number_id=PHONE_NUMBER_ID,
            allow_additional_channel=True,
            now=NOW,
        )

    multiple_db = _db(
        channels=[
            _safe_channel(),
            _safe_channel(
                account_key=f"account:v1:{'b' * 64}",
                channel_id="whatsapp-other",
            ),
        ]
    )
    with pytest.raises(provision.ProvisioningError, match="multiple different"):
        await provision.build_plan(
            multiple_db,
            phone_number_id=PHONE_NUMBER_ID,
            allow_additional_channel=True,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_explicit_owner_and_store_selectors_resolve_one_scope():
    db = _db(
        owners=[
            {"id": "owner-1", "role": "owner"},
            {"id": "owner-2", "role": "owner"},
        ],
        stores=[
            {"user_id": "owner-1", "store_id": 945168084, "status": "connected"},
            {"user_id": "owner-1", "store_id": 111, "status": "connected"},
        ],
    )

    plan = await provision.build_plan(
        db,
        phone_number_id=PHONE_NUMBER_ID,
        owner_id="owner-1",
        merchant_id="945168084",
        now=NOW,
    )

    assert plan.document["user_id"] == "owner-1"
    assert plan.document["merchant_id"] == "945168084"


@pytest.mark.asyncio
async def test_ambiguous_owner_or_store_fails_closed():
    duplicate_owner_db = _db(
        owners=[
            {"id": "owner-1", "role": "owner"},
            {"id": "owner-2", "role": "owner"},
        ]
    )
    with pytest.raises(provision.ProvisioningError, match="exactly one Owner"):
        await provision.build_plan(
            duplicate_owner_db,
            phone_number_id=PHONE_NUMBER_ID,
            now=NOW,
        )

    duplicate_store_db = _db(
        stores=[
            {"user_id": "owner-1", "store_id": "store-1", "status": "connected"},
            {"user_id": "owner-1", "store_id": "store-2", "status": "connected"},
        ]
    )
    with pytest.raises(
        provision.ProvisioningError, match="exactly one connected Salla"
    ):
        await provision.build_plan(
            duplicate_store_db,
            phone_number_id=PHONE_NUMBER_ID,
            now=NOW,
        )


@pytest.mark.asyncio
async def test_missing_or_weak_dedicated_hmac_key_is_rejected(monkeypatch):
    monkeypatch.delenv(provision.BINDING_HMAC_ENV, raising=False)
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", "legacy-fallback-must-not-be-used")

    with pytest.raises(provision.ProvisioningError, match=provision.BINDING_HMAC_ENV):
        await provision.build_plan(_db(), phone_number_id=PHONE_NUMBER_ID, now=NOW)

    monkeypatch.setenv(provision.BINDING_HMAC_ENV, "too-short")
    with pytest.raises(provision.ProvisioningError, match="at least 32"):
        await provision.build_plan(_db(), phone_number_id=PHONE_NUMBER_ID, now=NOW)


@pytest.mark.asyncio
async def test_invalid_phone_number_id_is_rejected_without_echoing_it():
    invalid = "not-a-meta-id-secret"
    with pytest.raises(provision.ProvisioningError) as error:
        await provision.build_plan(_db(), phone_number_id=invalid, now=NOW)

    assert invalid not in str(error.value)


@pytest.mark.asyncio
async def test_duplicate_key_during_insert_fails_without_claiming_success():
    db = _db()
    plan = await provision.build_plan(db, phone_number_id=PHONE_NUMBER_ID, now=NOW)
    db.mezan_customer_channels_v1.insert_error = DuplicateKeyError("duplicate")

    with pytest.raises(provision.ProvisioningError, match="conflicting"):
        await provision.apply_plan(db, plan)

    assert db.mezan_customer_channel_provision_locks_v1.rows == []


def test_free_form_channel_id_option_does_not_exist():
    with pytest.raises(SystemExit):
        provision._parser().parse_args(["--channel-id", PHONE_NUMBER_ID])
