from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

import pytest

from integrations_control_center import snapchat_account_selection as selection


class FakeResult:
    matched_count = 1
    modified_count = 1


class FakeCursor:
    def __init__(self, rows):
        self.rows = deepcopy(list(rows))

    def sort(self, key_or_list, direction=None):
        specs = key_or_list if isinstance(key_or_list, list) else [(key_or_list, direction)]
        for key, order in reversed(specs):
            self.rows.sort(
                key=lambda row: str(row.get(key) or ""),
                reverse=(order or 1) < 0,
            )
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length):
        return deepcopy(self.rows[:length])

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return deepcopy(next(self._iterator))
        except StopIteration as exc:
            raise StopAsyncIteration from exc


def _matches(row, query):
    for key, expected in query.items():
        actual = row.get(key)
        if isinstance(expected, dict):
            for operator, value in expected.items():
                if operator == "$in" and actual not in value:
                    return False
                if operator == "$gte" and not (actual is not None and actual >= value):
                    return False
        elif actual != expected:
            return False
    return True


class FakeCollection:
    def __init__(self, name, db):
        self.name = name
        self.db = db

    @property
    def rows(self):
        return self.db.rows.setdefault(self.name, [])

    def find(self, query, projection=None):
        return FakeCursor(row for row in self.rows if _matches(row, query))

    async def find_one(self, query, projection=None, sort=None):
        rows = [row for row in self.rows if _matches(row, query)]
        if sort:
            rows = FakeCursor(rows).sort(sort).rows
        return deepcopy(rows[0]) if rows else None

    async def count_documents(self, query):
        return sum(_matches(row, query) for row in self.rows)

    async def update_one(self, query, update, upsert=False):
        target = next((row for row in self.rows if _matches(row, query)), None)
        if target is None and upsert:
            target = {
                key: deepcopy(value)
                for key, value in query.items()
                if not isinstance(value, dict)
            }
            self.rows.append(target)
        if target is not None:
            target.update(deepcopy(update.get("$setOnInsert") or {}))
            target.update(deepcopy(update.get("$set") or {}))
        self.db.writes.append((self.name, "update_one", deepcopy(update)))
        return FakeResult()

    async def insert_one(self, document):
        self.rows.append(deepcopy(document))
        self.db.writes.append((self.name, "insert_one", deepcopy(document)))
        return object()


class FakeDB:
    def __init__(self, rows=None):
        self.rows = deepcopy(rows or {})
        self.writes = []

    def __getitem__(self, name):
        return FakeCollection(name, self)

    def __getattr__(self, name):
        return self[name]


def _account(account_id, *, selected=False):
    return {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "mezan_integration_account_id": f"mezan-{account_id}",
        "external_account_id": account_id,
        "ad_account_id": account_id,
        "display_name": f"Snap {account_id}",
        "currency": "USD",
        "timezone": "Asia/Riyadh",
        "organization_id": "org-1",
        "organization_name": "Amasi",
        "account_status": "ACTIVE",
        "connection_status": "connected",
        "connection_provenance": "api_connection",
        "mezan_selected": selected,
        "selection_status": "selected" if selected else "discovered",
        "selected_at": "2026-07-30T10:00:00+00:00" if selected else None,
    }


def _db():
    return FakeDB(
        {
            "mezan_integration_accounts_v2": [
                _account("account-1"),
                _account("account-2"),
                _account("account-3"),
            ],
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "snapchat_ads",
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                }
            ],
        }
    )


@pytest.mark.asyncio
async def test_selection_defaults_to_none_and_owner_can_select_exact_accounts(monkeypatch):
    db = _db()
    before = await selection.get_snapchat_account_selection(db, "owner-1")
    assert before["discovered_count"] == 3
    assert before["selected_count"] == 0
    assert before["selection_required"] is True

    monkeypatch.setattr(
        selection,
        "_now_iso",
        lambda: "2026-07-30T16:30:00+00:00",
    )
    result = await selection.save_snapchat_account_selection(
        db,
        "owner-1",
        selection.SnapchatAccountSelectionInput(
            account_ids=["account-3", "account-1", "account-1"]
        ),
    )

    assert result["selected_count"] == 2
    assert result["selection_required"] is False
    assert {
        item["account_id"]
        for item in result["accounts"]
        if item["selected"]
    } == {"account-1", "account-3"}
    rows = db.rows["mezan_integration_accounts_v2"]
    assert next(row for row in rows if row["external_account_id"] == "account-2")[
        "mezan_selected"
    ] is False
    assert db.rows["mezan_integrations_v2"][0]["selected_account_count"] == 2
    audit = db.rows["mezan_integration_sync_runs_v2"][0]
    assert audit["run_type"] == "snapchat_account_selection"
    assert audit["summary"]["provider_write_reached"] is False
    assert audit["summary"]["accounting_write_reached"] is False


@pytest.mark.asyncio
async def test_selection_rejects_unknown_account_numbers():
    db = _db()
    with pytest.raises(Exception) as exc:
        await selection.save_snapchat_account_selection(
            db,
            "owner-1",
            selection.SnapchatAccountSelectionInput(
                account_ids=["account-1", "not-discovered"]
            ),
        )
    assert getattr(exc.value, "status_code", None) == 422
    assert exc.value.detail["code"] == "snapchat_account_selection_unknown_ids"
    assert db.rows.get("mezan_integration_sync_runs_v2", []) == []


@pytest.mark.asyncio
async def test_native_operations_read_only_selected_accounts():
    db = _db()
    db.rows["mezan_integration_accounts_v2"][1]["mezan_selected"] = True
    db.rows["mezan_integration_accounts_v2"][1]["selection_status"] = "selected"

    rows = await selection._load_selected_accounts(db, "owner-1")
    assert [row["ad_account_id"] for row in rows] == ["account-2"]

    db.rows["mezan_integration_accounts_v2"][1]["mezan_selected"] = False
    with pytest.raises(selection.SnapchatNativeSyncError) as exc:
        await selection._load_selected_accounts(db, "owner-1")
    assert exc.value.code == "snapchat_accounts_not_selected"
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_canonical_scheduler_includes_migrated_account_with_tenant_credentials(
    monkeypatch,
):
    db = _db()
    migrated = db.rows["mezan_integration_accounts_v2"][1]
    migrated["mezan_selected"] = True
    migrated["selection_status"] = "selected"
    migrated.pop("connection_provenance")
    db.rows[selection.SNAPCHAT_CREDENTIALS_COLLECTION] = [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "refresh_token_ciphertext": b"tenant-refresh",
            "organization_ids": ["org-1"],
        }
    ]
    monkeypatch.setattr(
        selection,
        "decrypt_snapchat_token",
        lambda value: "refresh-token" if value == b"tenant-refresh" else "",
    )

    failure_stages = []
    rows = await selection._load_canonical_scheduler_accounts(
        db,
        "owner-1",
        failure_stage_observer=failure_stages.append,
    )

    assert [row["ad_account_id"] for row in rows] == ["account-2"]
    assert rows[0].get("connection_provenance") is None
    assert failure_stages == [
        "integration_account_credential_proof",
        "selected_accounts_load",
        "integration_account_credential_proof",
    ]


@pytest.mark.asyncio
async def test_canonical_scheduler_recovers_missing_provider_projection_after_proof(
    monkeypatch,
):
    db = _db()
    db.rows["mezan_integrations_v2"] = []
    account = db.rows["mezan_integration_accounts_v2"][1]
    account["mezan_selected"] = True
    account["selection_status"] = "selected"
    account.pop("connection_provenance")
    db.rows[selection.SNAPCHAT_CREDENTIALS_COLLECTION] = [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "refresh_token_ciphertext": b"tenant-refresh",
            "organization_ids": ["org-1"],
        }
    ]
    monkeypatch.setattr(
        selection,
        "decrypt_snapchat_token",
        lambda value: "refresh-token" if value == b"tenant-refresh" else "",
    )
    monkeypatch.setattr(
        selection,
        "_now_iso",
        lambda: "2026-08-23T07:15:00+00:00",
    )

    rows = await selection._load_canonical_scheduler_accounts(db, "owner-1")

    assert [row["ad_account_id"] for row in rows] == ["account-2"]
    assert db.rows["mezan_integrations_v2"] == [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "connection_status": "connected",
            "connection_provenance": "api_connection",
            "source_mode": selection.SCHEDULER_PROJECTION_RECOVERY_SOURCE_MODE,
            "data_quality": "incomplete",
            "data_delay_minutes": None,
            "health_score": 70,
            "checked_at": "2026-08-23T07:15:00+00:00",
            "created_at": "2026-08-23T07:15:00+00:00",
            "updated_at": "2026-08-23T07:15:00+00:00",
            "projection_recovered_at": "2026-08-23T07:15:00+00:00",
        }
    ]


@pytest.mark.asyncio
async def test_canonical_scheduler_never_overwrites_disconnected_projection(
    monkeypatch,
):
    db = _db()
    db.rows["mezan_integrations_v2"][0]["connection_status"] = "needs_reauth"
    account = db.rows["mezan_integration_accounts_v2"][1]
    account["mezan_selected"] = True
    db.rows[selection.SNAPCHAT_CREDENTIALS_COLLECTION] = [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "refresh_token_ciphertext": b"tenant-refresh",
            "organization_ids": ["org-1"],
        }
    ]
    monkeypatch.setattr(
        selection,
        "decrypt_snapchat_token",
        lambda _value: "refresh-token",
    )

    with pytest.raises(selection.SnapchatNativeSyncError) as exc:
        await selection._load_canonical_scheduler_accounts(db, "owner-1")

    assert exc.value.code == "snapchat_integration_not_connected"
    assert db.rows["mezan_integrations_v2"][0]["connection_status"] == "needs_reauth"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("foreign_tenant", "snapchat_accounts_not_selected"),
        ("disconnected", "snapchat_accounts_not_selected"),
        ("unselected", "snapchat_accounts_not_selected"),
        ("missing_identity", "snapchat_selected_account_identity_invalid"),
        ("ambiguous_identity", "snapchat_selected_account_identity_invalid"),
        ("numeric_identity", "snapchat_selected_account_identity_invalid"),
        ("foreign_organization", "snapchat_selected_account_identity_invalid"),
    ],
)
async def test_canonical_scheduler_rejects_unowned_or_ineligible_accounts(
    monkeypatch,
    mutation,
    expected_code,
):
    db = _db()
    account = db.rows["mezan_integration_accounts_v2"][1]
    account["mezan_selected"] = True
    account["selection_status"] = "selected"
    account.pop("connection_provenance")
    if mutation == "foreign_tenant":
        account["user_id"] = "owner-2"
    elif mutation == "disconnected":
        account["connection_status"] = "needs_reauth"
    elif mutation == "unselected":
        account["mezan_selected"] = False
    elif mutation == "missing_identity":
        account.pop("ad_account_id")
        account.pop("external_account_id")
    elif mutation == "ambiguous_identity":
        account["external_account_id"] = "different-account"
    elif mutation == "numeric_identity":
        account["ad_account_id"] = 123
        account["external_account_id"] = 123
    elif mutation == "foreign_organization":
        account["organization_id"] = "foreign-org"
    db.rows[selection.SNAPCHAT_CREDENTIALS_COLLECTION] = [
        {
            "user_id": "owner-1",
            "provider": "snapchat_ads",
            "refresh_token_ciphertext": b"tenant-refresh",
            "organization_ids": ["org-1"],
        }
    ]
    monkeypatch.setattr(
        selection,
        "decrypt_snapchat_token",
        lambda _value: "refresh-token",
    )

    with pytest.raises(selection.SnapchatNativeSyncError) as exc:
        await selection._load_canonical_scheduler_accounts(db, "owner-1")

    assert exc.value.code == expected_code


@pytest.mark.asyncio
@pytest.mark.parametrize("credentials", [None, b"undecryptable"])
async def test_canonical_scheduler_excludes_credential_unproven_account(
    monkeypatch,
    credentials,
):
    db = _db()
    account = db.rows["mezan_integration_accounts_v2"][1]
    account["mezan_selected"] = True
    account.pop("connection_provenance")
    if credentials is not None:
        db.rows[selection.SNAPCHAT_CREDENTIALS_COLLECTION] = [
            {
                "user_id": "owner-1",
                "provider": "snapchat_ads",
                "refresh_token_ciphertext": credentials,
            }
        ]

    def decrypt(_value):
        raise ValueError("invalid credential")

    monkeypatch.setattr(selection, "decrypt_snapchat_token", decrypt)

    with pytest.raises(selection.SnapchatNativeSyncError) as exc:
        await selection._load_canonical_scheduler_accounts(db, "owner-1")

    assert exc.value.code == "snapchat_needs_reauth"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("credential_mutation", "expected_code"),
    [
        ("wrong_user", "snapchat_needs_reauth"),
        ("wrong_provider", "snapchat_needs_reauth"),
        ("duplicate", "snapchat_credential_identity_ambiguous"),
        ("empty_organizations", "snapchat_selected_account_identity_invalid"),
        ("null_organizations", "snapchat_credential_metadata_invalid"),
        ("scalar_organizations", "snapchat_credential_metadata_invalid"),
        ("malformed_organization_member", "snapchat_credential_metadata_invalid"),
    ],
)
async def test_canonical_scheduler_rejects_ambiguous_or_malformed_credentials(
    monkeypatch,
    credential_mutation,
    expected_code,
):
    db = _db()
    account = db.rows["mezan_integration_accounts_v2"][1]
    account["mezan_selected"] = True
    account.pop("connection_provenance")
    credential = {
        "user_id": "owner-1",
        "provider": "snapchat_ads",
        "refresh_token_ciphertext": b"tenant-refresh",
        "organization_ids": ["org-1"],
    }
    if credential_mutation == "wrong_user":
        credential["user_id"] = "owner-2"
    elif credential_mutation == "wrong_provider":
        credential["provider"] = "meta_ads"
    elif credential_mutation == "scalar_organizations":
        credential["organization_ids"] = 123
    elif credential_mutation == "empty_organizations":
        credential["organization_ids"] = []
    elif credential_mutation == "null_organizations":
        credential["organization_ids"] = None
    elif credential_mutation == "malformed_organization_member":
        credential["organization_ids"] = ["org-1", 123]
    rows = [credential]
    if credential_mutation == "duplicate":
        rows.append(dict(credential))
    db.rows[selection.SNAPCHAT_CREDENTIALS_COLLECTION] = rows
    monkeypatch.setattr(
        selection,
        "decrypt_snapchat_token",
        lambda _value: "refresh-token",
    )

    with pytest.raises(selection.SnapchatNativeSyncError) as exc:
        await selection._load_canonical_scheduler_accounts(db, "owner-1")

    assert exc.value.code == expected_code


@pytest.mark.asyncio
async def test_oauth_rediscovery_preserves_only_explicit_selection(monkeypatch):
    from integrations_control_center import snapchat_connections

    db = _db()
    db.rows["mezan_integration_accounts_v2"][0]["mezan_selected"] = True
    db.rows["mezan_integration_accounts_v2"][0]["selection_status"] = "selected"
    db.rows["mezan_integration_accounts_v2"][0]["selected_at"] = (
        "2026-07-30T10:00:00+00:00"
    )

    async def fake_projection(
        target_db,
        *,
        user_id,
        token_payload,
        discovery,
        provider_error=None,
    ):
        target_db.rows["mezan_integration_accounts_v2"] = [
            _account("account-1"),
            _account("account-2"),
            _account("account-4"),
        ]

    monkeypatch.setattr(snapchat_connections, "persist_snapchat_projection", fake_projection)
    selection.install_snapchat_selection_projection_preservation()

    await snapchat_connections.persist_snapchat_projection(
        db,
        user_id="owner-1",
        token_payload={},
        discovery={},
    )

    rows = db.rows["mezan_integration_accounts_v2"]
    account_1 = next(row for row in rows if row["external_account_id"] == "account-1")
    account_4 = next(row for row in rows if row["external_account_id"] == "account-4")
    assert account_1["mezan_selected"] is True
    assert account_1["selected_at"] == "2026-07-30T10:00:00+00:00"
    assert account_4["mezan_selected"] is False
    assert db.rows["mezan_integrations_v2"][0]["selected_account_count"] == 1


def test_input_deduplicates_account_numbers_and_blocks_empty_selection():
    payload = selection.SnapchatAccountSelectionInput(
        account_ids=[" account-1 ", "account-1", "account-2"]
    )
    assert payload.account_ids == ["account-1", "account-2"]
    with pytest.raises(ValueError):
        selection.SnapchatAccountSelectionInput(account_ids=[])
