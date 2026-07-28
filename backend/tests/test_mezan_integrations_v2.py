"""Deterministic tests for the isolated integrations-v2 control plane."""
from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from integrations_control_center.catalog import (
    AD_CAPABILITY_KEYS,
    AD_MUTATION_CAPABILITIES,
    ADVERTISING_PROVIDERS,
    PROVIDERS,
    PROVIDER_BY_ID,
    build_capability_matrix,
)
from integrations_control_center.legacy_readers import (
    LEGACY_PROJECTIONS,
    sanitize_for_output,
)
from integrations_control_center.models import (
    ConnectionTestResponse,
    OverviewResponse,
    ensure_integrations_control_center_indexes,
)
from integrations_control_center.routes import _require_owner
from integrations_control_center.service import IntegrationsControlCenterService


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=timezone.utc)
SALLA_DEFAULT_SCOPES = (
    "offline_access",
    "settings.read",
    "orders.read_write",
    "shipping.read_write",
    "webhooks.read_write",
)


def _matches(document: dict, query: dict) -> bool:
    def condition_matches(value: Any, condition: Any, *, exists: bool) -> bool:
        if not isinstance(condition, dict):
            return value == condition
        for operator, expected in condition.items():
            if operator == "$exists" and bool(expected) != exists:
                return False
            if operator == "$ne" and value == expected:
                return False
            if operator == "$in" and value not in expected:
                return False
            if operator == "$nin" and value in expected:
                return False
            if operator == "$gte" and (value is None or value < expected):
                return False
            if operator == "$lte" and (value is None or value > expected):
                return False
        return True

    for key, condition in query.items():
        if key == "$or":
            if not any(_matches(document, branch) for branch in condition):
                return False
            continue
        exists = key in document
        if not condition_matches(document.get(key), condition, exists=exists):
            return False
    return True


def _project(document: dict, projection: dict | None) -> dict:
    if not projection:
        return deepcopy(document)
    include = {key for key, flag in projection.items() if flag and key != "_id"}
    if include:
        return {key: deepcopy(document[key]) for key in include if key in document}
    excluded = {key for key, flag in projection.items() if not flag}
    return {
        key: deepcopy(value)
        for key, value in document.items()
        if key not in excluded
    }


def _sort_rows(rows: list[dict], sort_spec: Any) -> list[dict]:
    specs = sort_spec if isinstance(sort_spec, list) else [sort_spec]
    output = list(rows)
    for field, direction in reversed(specs):
        output.sort(
            key=lambda row: (
                row.get(field) is not None,
                str(row.get(field) or ""),
            ),
            reverse=direction < 0,
        )
    return output


class FakeCursor:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def sort(self, key_or_list, direction=None):
        spec = key_or_list if direction is None else [(key_or_list, direction)]
        self.rows = _sort_rows(self.rows, spec)
        return self

    def limit(self, value: int):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length: int):
        return deepcopy(self.rows[:length])

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return deepcopy(next(self._iterator))
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCollection:
    def __init__(self, name: str, db: "FakeDB"):
        self.name = name
        self.db = db

    @property
    def rows(self) -> list[dict]:
        return self.db.rows.setdefault(self.name, [])

    async def find_one(self, query, projection=None, sort=None):
        self.db.reads.append((self.name, deepcopy(query), deepcopy(projection)))
        rows = [row for row in self.rows if _matches(row, query)]
        if sort:
            rows = _sort_rows(rows, sort)
        return _project(rows[0], projection) if rows else None

    def find(self, query, projection=None):
        self.db.reads.append((self.name, deepcopy(query), deepcopy(projection)))
        return FakeCursor(
            [_project(row, projection) for row in self.rows if _matches(row, query)]
        )

    async def count_documents(self, query):
        return sum(_matches(row, query) for row in self.rows)

    async def insert_one(self, document):
        safe = deepcopy(document)
        self.rows.append(safe)
        self.db.writes.append((self.name, "insert_one", safe))
        return object()

    async def update_one(self, query, update, upsert=False):
        target = next((row for row in self.rows if _matches(row, query)), None)
        inserted = False
        if target is None and upsert:
            target = {
                key: deepcopy(value)
                for key, value in query.items()
                if not key.startswith("$") and not isinstance(value, dict)
            }
            self.rows.append(target)
            inserted = True
        if target is not None:
            if inserted:
                target.update(deepcopy(update.get("$setOnInsert") or {}))
            target.update(deepcopy(update.get("$set") or {}))
        self.db.writes.append(
            (self.name, "update_one", {"query": deepcopy(query), "update": deepcopy(update)})
        )
        return object()

    async def create_index(self, keys, **kwargs):
        self.db.indexes.append((self.name, deepcopy(keys), deepcopy(kwargs)))
        return kwargs.get("name")


class FakeDB:
    def __init__(self, rows: dict[str, list[dict]] | None = None):
        self.rows = deepcopy(rows or {})
        self.reads: list[tuple] = []
        self.writes: list[tuple] = []
        self.indexes: list[tuple] = []

    def __getitem__(self, name: str):
        return FakeCollection(name, self)

    def __getattr__(self, name: str):
        return FakeCollection(name, self)


class FailingCollection:
    async def find_one(self, *args, **kwargs):
        raise RuntimeError("mongo unavailable")

    def find(self, *args, **kwargs):
        raise RuntimeError("mongo unavailable")


class FailingDB:
    def __getitem__(self, name: str):
        return FailingCollection()

    def __getattr__(self, name: str):
        return FailingCollection()


def _service(db: FakeDB) -> IntegrationsControlCenterService:
    return IntegrationsControlCenterService(db, now=lambda: NOW)


def test_catalog_has_exact_provider_and_ad_capability_contract():
    assert [item.provider for item in PROVIDERS] == [
        "salla",
        "snapchat_ads",
        "tiktok_ads",
        "meta_ads",
        "google_analytics_4",
        "google_search_console",
        "google_merchant_center",
        "google_ads",
        "qoyod",
        "shipping_companies",
    ]
    assert AD_CAPABILITY_KEYS == (
        "campaigns.read",
        "campaigns.create",
        "campaigns.update",
        "campaigns.pause",
        "campaigns.resume",
        "budgets.read",
        "budgets.update",
        "ads.read",
        "ads.create",
        "ads.update",
        "creatives.read",
        "creatives.create",
        "audiences.read",
        "insights.read",
        "conversions.read",
    )
    # Keep this isolated contract aligned with
    # salla_integration.service._DEFAULT_SCOPES_FALLBACK without importing
    # the operational connector (which has provider/network dependencies).
    assert PROVIDER_BY_ID["salla"].required_permissions == SALLA_DEFAULT_SCOPES


@pytest.mark.parametrize("provider", sorted(ADVERTISING_PROVIDERS))
def test_every_advertising_mutation_is_blocked_by_policy(provider):
    matrix = build_capability_matrix(
        PROVIDER_BY_ID[provider],
        connection_status="connected",
        has_data=True,
        current_permissions=["ads_management", "adwords"],
    )
    assert set(matrix) == set(AD_CAPABILITY_KEYS)
    for capability in AD_MUTATION_CAPABILITIES:
        entry = matrix[capability]
        assert entry["available"] is False
        assert entry["state"] == "approval_required"
        assert entry["approval_required"] is True
        assert entry["blocked_by_policy"] is True


def test_advertising_reads_require_field_level_local_evidence():
    definition = PROVIDER_BY_ID["meta_ads"]
    without_evidence = build_capability_matrix(
        definition,
        connection_status="data_available",
        has_data=True,
    )
    for capability in {
        "campaigns.read",
        "ads.read",
        "insights.read",
        "conversions.read",
    }:
        assert without_evidence[capability]["state"] == "blocked_missing_data"
        assert without_evidence[capability]["available"] is False

    proven = build_capability_matrix(
        definition,
        connection_status="data_available",
        has_data=True,
        evidence_capabilities={
            "campaigns.read",
            "ads.read",
            "insights.read",
            "conversions.read",
        },
    )
    for capability in {
        "campaigns.read",
        "ads.read",
        "insights.read",
        "conversions.read",
    }:
        assert proven[capability]["state"] == "available"
        assert proven[capability]["available"] is True


def test_advertising_mutations_need_a_management_connection_before_approval():
    matrix = build_capability_matrix(
        PROVIDER_BY_ID["tiktok_ads"],
        connection_status="data_available",
        has_data=True,
        evidence_capabilities={"campaigns.read", "insights.read"},
    )
    for capability in AD_MUTATION_CAPABILITIES:
        assert matrix[capability]["state"] == "not_connected"
        assert matrix[capability]["approval_required"] is False
        assert matrix[capability]["blocked_by_policy"] is True


def test_recursive_sanitizer_removes_nested_secrets_and_token_text():
    dirty = {
        "access_token": "top-secret",
        "safe": {
            "client_secret": "nested-secret",
            "apiKey": "camel-case-secret",
            "message": "request failed: Authorization=Bearer abcdefghijklmnop",
            "query": "https://provider.example/callback?token=top-secret-query",
            "app": "app_secret=top-secret-app",
            "spaced": "refresh token: top-secret-spaced",
            "camel": "clientSecret=top-secret-camel",
            "items": [
                {"refresh-token": "rotate-me", "code": "safe_code"},
                b"ciphertext",
            ],
        },
    }
    safe = sanitize_for_output(dirty)
    rendered = json.dumps(safe, ensure_ascii=False)
    assert "top-secret" not in rendered
    assert "nested-secret" not in rendered
    assert "camel-case-secret" not in rendered
    assert "rotate-me" not in rendered
    assert "abcdefghijklmnop" not in rendered
    assert "top-secret-query" not in rendered
    assert "top-secret-app" not in rendered
    assert "top-secret-spaced" not in rendered
    assert "top-secret-camel" not in rendered
    assert safe["safe"]["items"][0] == {"code": "safe_code"}


def test_legacy_projections_are_inclusion_allowlists_without_secret_values():
    required_sources = {
        source
        for provider in PROVIDERS
        for source in provider.legacy_sources
    }
    assert required_sources <= set(LEGACY_PROJECTIONS)
    for source, projection in LEGACY_PROJECTIONS.items():
        assert projection.get("_id") == 0, source
        assert all(flag in {0, 1} for flag in projection.values())
        forbidden = {
            "access_token",
            "refresh_token",
            "client_secret",
            "app_secret",
            "api_key",
            "api_key_enc",
            "token",
        }
        assert forbidden.isdisjoint(projection), source
        assert "last_error" not in projection, source
        assert "last_error_message" not in projection, source


def test_owner_gate_accepts_owner_and_rejects_other_roles():
    owner = {"id": "owner-1", "role": "owner"}
    assert _require_owner(owner) is owner
    assert _require_owner({"id": "owner-2", "is_owner": True})["id"] == "owner-2"
    with pytest.raises(HTTPException) as exc:
        _require_owner({"id": "employee-1", "role": "admin"})
    assert getattr(exc.value, "status_code", None) == 403
    assert exc.value.detail["code"] == "owner_only"


@pytest.mark.asyncio
async def test_indexes_cover_all_seven_v2_collections_and_unique_identities():
    db = FakeDB()
    await ensure_integrations_control_center_indexes(db)
    indexed_collections = {name for name, _, _ in db.indexes}
    assert indexed_collections == {
        "mezan_integrations_v2",
        "mezan_integration_accounts_v2",
        "mezan_integration_permissions_v2",
        "mezan_integration_health_v2",
        "mezan_integration_sync_runs_v2",
        "mezan_integration_errors_v2",
        "mezan_campaign_product_links_v2",
    }
    unique_names = {
        options["name"]
        for _, _, options in db.indexes
        if options.get("unique")
    }
    assert unique_names == {
        "mezan_integrations_v2_user_provider_unique",
        "mezan_integration_accounts_v2_identity_unique",
        "mezan_integration_permissions_v2_key_unique",
        "mezan_integration_sync_runs_v2_run_unique",
        "mezan_integration_errors_v2_error_unique",
        "mezan_campaign_product_links_v2_idempotency_unique",
    }


@pytest.mark.asyncio
async def test_overview_has_ten_cards_and_distinguishes_tiktok_data_feed():
    db = FakeDB(
        {
            "salla_integrations": [
                {
                    "user_id": "owner-1",
                    "status": "connected",
                    "store_id": "store-7",
                    "store_name": "AMASI",
                    "scope": " ".join(SALLA_DEFAULT_SCOPES),
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "access_token_encrypted": b"must-never-leak",
                }
            ],
            "salla_sync_logs": [
                {
                    "id": "sync-1",
                    "user_id": "owner-1",
                    "kind": "orders",
                    "status": "success",
                    "started_at": "2026-07-28T10:00:00+00:00",
                    "ended_at": "2026-07-28T10:05:00+00:00",
                }
            ],
            "tiktok_ads_daily": [
                {
                    "user_id": "owner-1",
                    "advertiser_id": "adv-1",
                    "date": "2026-07-28",
                    "updated_at": "2026-07-28T11:00:00+00:00",
                    "campaign_id": "campaign-1",
                }
            ],
        }
    )
    overview = await _service(db).overview("owner-1")
    OverviewResponse.model_validate(overview)
    assert overview["summary"]["total"] == 10
    assert len(overview["providers"]) == 10
    by_provider = {card["provider"]: card for card in overview["providers"]}
    assert by_provider["salla"]["connection_status"] == "connected"
    assert by_provider["salla"]["connection_provenance"] == "api_connection"
    assert by_provider["salla"]["permissions"]["missing"] == []
    assert by_provider["tiktok_ads"]["connection_status"] == "data_available"
    assert by_provider["tiktok_ads"]["connection_provenance"] == "data_feed"
    assert by_provider["tiktok_ads"]["source_mode"] == "data_feed"
    assert overview["summary"]["connected"] == 1
    assert overview["summary"]["api_connections"] == 1
    assert overview["summary"]["data_feeds"] == 1
    assert by_provider["shipping_companies"]["connection_status"] == "planned"
    rendered = json.dumps(overview, ensure_ascii=False)
    assert "must-never-leak" not in rendered


@pytest.mark.asyncio
async def test_salla_permission_evidence_distinguishes_unknown_from_missing():
    base_connection = {
        "user_id": "owner-1",
        "status": "connected",
        "store_id": "store-7",
        "store_name": "AMASI",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "access_token_encrypted": b"must-never-leak",
    }
    unknown_db = FakeDB({"salla_integrations": [base_connection]})
    unknown_overview = await _service(unknown_db).overview("owner-1")
    unknown_salla = next(
        card
        for card in unknown_overview["providers"]
        if card["provider"] == "salla"
    )
    assert unknown_salla["permissions"] == {
        "current": [],
        "missing": [],
        "unknown": True,
    }
    assert {
        entry["state"] for entry in unknown_salla["capabilities"].values()
    } == {"unknown"}

    await _service(unknown_db).test_connection("owner-1", "salla")
    stored_account = unknown_db.rows["mezan_integration_accounts_v2"][0]
    assert {
        entry["state"] for entry in stored_account["capabilities"].values()
    } == {"unknown"}

    incomplete_db = FakeDB(
        {
            "salla_integrations": [
                {
                    **base_connection,
                    "scope": (
                        "offline_access settings.read orders.read_write "
                        "webhooks.read_write"
                    ),
                }
            ]
        }
    )
    incomplete_overview = await _service(incomplete_db).overview("owner-1")
    incomplete_salla = next(
        card
        for card in incomplete_overview["providers"]
        if card["provider"] == "salla"
    )
    assert incomplete_salla["permissions"] == {
        "current": [
            "offline_access",
            "orders.read_write",
            "settings.read",
            "webhooks.read_write",
        ],
        "missing": ["shipping.read_write"],
        "unknown": False,
    }
    assert {
        entry["state"] for entry in incomplete_salla["capabilities"].values()
    } == {"blocked_missing_permission"}


@pytest.mark.asyncio
async def test_production_shape_separates_api_legacy_feed_and_disconnected():
    db = FakeDB(
        {
            "salla_integrations": [
                {
                    "user_id": "owner-1",
                    "status": "connected",
                    "store_id": "store-amasi",
                    "store_name": "متجر أماسي",
                    "scope": " ".join(SALLA_DEFAULT_SCOPES),
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "refresh_token_encrypted": b"encrypted-salla-refresh",
                }
            ],
            "meta_connections": [
                {
                    "user_id": "owner-1",
                    "access_token": "meta-token-never-project",
                    "ad_account_id": "act_amasi",
                    "ad_account_name": "اماسي",
                    "connection_status": "connected",
                }
            ],
            "meta_ads_daily": [
                {
                    "user_id": "owner-1",
                    "account_id": "act_amasi",
                    "date": "2026-07-28",
                    "updated_at": "2026-07-28T11:30:00+00:00",
                    "campaign_id": "meta-campaign",
                    "spend": 10,
                }
            ],
            "snapchat_connections": [
                {
                    "user_id": "owner-1",
                    "refresh_token": "snap-token-never-project",
                    "ad_account_id": "snap-amasi",
                    "ad_account_name": "متجر أماسي سعودي",
                }
            ],
            "snapchat_account_daily": [
                {
                    "user_id": "owner-1",
                    "ad_account_id": "snap-amasi",
                    "date": "2026-07-28",
                    "updated_at": "2026-07-28T11:30:00+00:00",
                    "spend": 12,
                }
            ],
            "tiktok_ads_daily": [
                {
                    "user_id": "owner-1",
                    "date": "2026-07-06",
                    "updated_at": "2026-07-06T00:00:00+00:00",
                    "campaign_id": "tiktok-feed-campaign",
                    "spend": 8,
                }
            ],
            "qoyod_credentials": [
                {
                    "user_id": "main",
                    "api_key_enc": "qoyod-key-never-project",
                    "last_verified_at": "2026-07-27T20:00:00+00:00",
                }
            ],
        }
    )

    overview = await _service(db).overview("owner-1")
    by_provider = {card["provider"]: card for card in overview["providers"]}

    assert by_provider["salla"]["connection_provenance"] == "api_connection"
    assert by_provider["meta_ads"]["connection_provenance"] == "api_connection"
    assert by_provider["snapchat_ads"]["connection_provenance"] == "legacy_integration"
    assert by_provider["qoyod"]["connection_provenance"] == "legacy_integration"
    assert by_provider["tiktok_ads"]["connection_provenance"] == "data_feed"
    assert by_provider["salla"]["permissions"]["missing"] == []
    assert by_provider["meta_ads"]["permissions"] == {
        "current": [],
        "missing": [],
        "unknown": True,
    }
    assert by_provider["snapchat_ads"]["permissions"] == {
        "current": [],
        "missing": [],
        "unknown": True,
    }
    for provider in {
        "google_analytics_4",
        "google_search_console",
        "google_merchant_center",
        "google_ads",
    }:
        assert by_provider[provider]["connection_provenance"] == "disconnected"
    assert by_provider["shipping_companies"]["connection_provenance"] == "planned"

    expected_summary = {
        "total": 10,
        "connected": 4,
        "api_connections": 2,
        "legacy_integrations": 2,
        "data_feeds": 1,
        "disconnected": 4,
        "planned": 1,
        "unknown": 0,
        "missing_permissions": 0,
    }
    assert {
        key: overview["summary"][key] for key in expected_summary
    } == expected_summary
    assert sum(
        overview["summary"][key]
        for key in (
            "api_connections",
            "legacy_integrations",
            "data_feeds",
            "disconnected",
            "planned",
            "unknown",
        )
    ) == overview["summary"]["total"]
    rendered = json.dumps(overview, ensure_ascii=False)
    assert "meta-token-never-project" not in rendered
    assert "snap-token-never-project" not in rendered
    assert "qoyod-key-never-project" not in rendered


@pytest.mark.asyncio
async def test_stored_or_stale_credentials_do_not_claim_verified_connection():
    db = FakeDB(
        {
            "salla_integrations": [
                {
                    "user_id": "owner-1",
                    "status": "connected",
                    "store_id": "stale-store",
                    # No encrypted access or refresh credential.
                }
            ],
            "meta_connections": [
                {
                    "user_id": "owner-1",
                    "access_token": "stored-but-unverified",
                    "ad_account_id": "act_123",
                    # Saving config alone does not set a verified status.
                }
            ],
            "snapchat_connections": [
                {
                    "user_id": "owner-1",
                    "access_token": "access-only-is-not-oauth-complete",
                }
            ],
            "tiktok_connections": [
                {
                    "user_id": "owner-1",
                    "access_token": "placeholder-token",
                }
            ],
            "tiktok_ads_daily": [
                {
                    "user_id": "owner-1",
                    "date": "2026-07-28",
                    "updated_at": "2026-07-28T11:00:00+00:00",
                    "spend": 1,
                }
            ],
            "qoyod_credentials": [
                {
                    "user_id": "main",
                    "api_key_enc": "rotated-key",
                    "last_verified_at": "2026-07-20T00:00:00+00:00",
                    "rotated_at": "2026-07-21T00:00:00+00:00",
                }
            ],
        }
    )

    overview = await _service(db).overview("owner-1")
    by_provider = {card["provider"]: card for card in overview["providers"]}

    assert by_provider["salla"]["connection_status"] == "unknown"
    assert by_provider["salla"]["connection_provenance"] == "disconnected"
    assert by_provider["meta_ads"]["connection_status"] == "unknown"
    assert by_provider["meta_ads"]["connection_provenance"] == "api_connection"
    assert by_provider["meta_ads"]["permissions"] == {
        "current": [],
        "missing": [],
        "unknown": True,
    }
    assert by_provider["snapchat_ads"]["connection_status"] == "not_connected"
    assert by_provider["snapchat_ads"]["connection_provenance"] == "disconnected"
    assert by_provider["tiktok_ads"]["connection_status"] == "data_available"
    assert by_provider["tiktok_ads"]["connection_provenance"] == "data_feed"
    assert by_provider["qoyod"]["connection_status"] == "unknown"
    assert by_provider["qoyod"]["connection_provenance"] == "legacy_integration"


@pytest.mark.asyncio
async def test_database_failure_is_not_misreported_as_disconnected():
    with pytest.raises(RuntimeError, match="mongo unavailable"):
        await IntegrationsControlCenterService(FailingDB(), now=lambda: NOW).overview(
            "owner-1"
        )


@pytest.mark.asyncio
async def test_local_connection_test_only_writes_mezan_v2_and_sanitizes_errors():
    db = FakeDB(
        {
            "meta_connections": [
                {
                    "user_id": "owner-1",
                    "access_token": "plaintext-meta-token",
                    "app_secret": "plaintext-meta-secret",
                    "ad_account_id": "act_123",
                    "connection_status": "error",
                    "last_error_message": (
                        "Authorization=Bearer plaintext-meta-token failed"
                    ),
                    "last_error_at": "2026-07-28T11:00:00+00:00",
                }
            ],
            "meta_ads_daily": [
                {
                    "user_id": "owner-1",
                    "account_id": "act_123",
                    "date": "2026-07-28",
                    "updated_at": "2026-07-28T11:00:00+00:00",
                    "campaign_id": "campaign-123",
                    "ad_id": "ad-123",
                    "spend": 42.5,
                    "purchases": 1,
                }
            ],
        }
    )
    result = await _service(db).test_connection("owner-1", "meta_ads")
    ConnectionTestResponse.model_validate(result)
    assert result["provider"] == "meta_ads"
    assert db.writes
    assert all(name.startswith("mezan_") for name, _, _ in db.writes)
    assert not any(
        name in {"meta_connections", "meta_ads_daily"}
        for name, _, _ in db.writes
    )
    rendered = json.dumps(db.writes, ensure_ascii=False, default=str)
    assert "plaintext-meta-token" not in rendered
    assert "plaintext-meta-secret" not in rendered
    assert len(db.rows["mezan_integration_health_v2"]) == 1
    assert len(db.rows["mezan_integration_sync_runs_v2"]) == 1
    assert len(db.rows["mezan_integration_errors_v2"]) == 1
    integration = db.rows["mezan_integrations_v2"][0]
    assert integration["connection_provenance"] == "api_connection"
    account = db.rows["mezan_integration_accounts_v2"][0]
    assert account["connection_provenance"] == "api_connection"
    assert account["capabilities"]["campaigns.read"]["available"] is True
    assert account["capabilities"]["ads.read"]["available"] is True
    assert account["capabilities"]["insights.read"]["available"] is True
    assert account["capabilities"]["conversions.read"]["available"] is True
    assert account["capabilities"]["budgets.update"]["available"] is False
    health = db.rows["mezan_integration_health_v2"][0]
    assert health["connection_provenance"] == "api_connection"
    run = db.rows["mezan_integration_sync_runs_v2"][0]
    assert run["summary"]["connection_provenance"] == "api_connection"


@pytest.mark.asyncio
async def test_legacy_state_stays_live_after_a_v2_health_snapshot():
    db = FakeDB(
        {
            "salla_integrations": [
                {
                    "user_id": "owner-1",
                    "status": "connected",
                    "store_id": "current-store",
                    "store_name": "Current Store",
                    "scope": " ".join(SALLA_DEFAULT_SCOPES),
                    "expires_at": "2099-01-01T00:00:00+00:00",
                    "refresh_token_encrypted": b"encrypted-refresh",
                }
            ],
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "salla",
                    "connection_status": "not_connected",
                    "source_mode": "stale_v2_snapshot",
                    "has_data": False,
                }
            ],
            "mezan_integration_health_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "salla",
                    "health_status": "degraded",
                    "health_score": 70,
                    "data_quality": "delayed",
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                    "checked_at": "2026-07-28T09:00:00+00:00",
                }
            ],
        }
    )
    overview = await _service(db).overview("owner-1")
    salla = next(card for card in overview["providers"] if card["provider"] == "salla")
    assert salla["connection_status"] == "connected"
    assert salla["connection_provenance"] == "api_connection"
    assert salla["source_mode"] == "legacy_connection"
    assert salla["accounts"][0]["store_id"] == "current-store"
    assert salla["health"]["score"] == 70


@pytest.mark.asyncio
async def test_stale_health_cannot_mask_a_changed_legacy_connection_status():
    db = FakeDB(
        {
            "mezan_integration_health_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "salla",
                    "health_status": "healthy",
                    "health_score": 100,
                    "data_quality": "good",
                    "connection_status": "connected",
                    "checked_at": "2026-07-28T09:00:00+00:00",
                }
            ],
        }
    )
    overview = await _service(db).overview("owner-1")
    salla = next(card for card in overview["providers"] if card["provider"] == "salla")
    assert salla["connection_status"] == "not_connected"
    assert salla["health"]["status"] == "not_available"
    assert salla["health"]["score"] is None


@pytest.mark.asyncio
async def test_safe_settings_and_reconnect_deep_links_never_enable_disconnect():
    overview = await _service(FakeDB()).overview("owner-1")
    by_provider = {card["provider"]: card for card in overview["providers"]}
    expected = {
        "salla": "/settings/salla",
        "snapchat_ads": "/snapchat-accounts",
        "meta_ads": "/settings",
        "qoyod": "/integrations/qoyod/settings",
    }
    for provider, href in expected.items():
        assert by_provider[provider]["actions"]["settings"] == {
            "enabled": True,
            "reason": None,
            "href": href,
        }
        assert by_provider[provider]["actions"]["reconnect"] == {
            "enabled": True,
            "reason": None,
            "href": href,
        }
    for card in overview["providers"]:
        assert card["actions"]["disconnect"]["enabled"] is False
        assert card["actions"]["disconnect"]["href"] is None


@pytest.mark.asyncio
async def test_v2_snapshot_is_preferred_but_cannot_grant_ad_writes():
    db = FakeDB(
        {
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "google_ads",
                    "connection_status": "connected",
                    "source_mode": "v2_snapshot",
                    "has_data": True,
                    "data_quality": "good",
                    "last_sync_at": "2026-07-28T11:30:00+00:00",
                    # Deliberately malicious/untrusted field. The V2 projection
                    # excludes it and the service always rebuilds the policy.
                    "capabilities": {
                        "budgets.update": {"state": "available"}
                    },
                    "access_token": "must-not-leak",
                }
            ]
        }
    )
    overview = await _service(db).overview("owner-1")
    google = next(
        card for card in overview["providers"] if card["provider"] == "google_ads"
    )
    assert google["connection_status"] == "data_available"
    assert google["connection_provenance"] == "unknown"
    assert overview["summary"]["unknown"] == 1
    assert sum(
        overview["summary"][key]
        for key in (
            "api_connections",
            "legacy_integrations",
            "data_feeds",
            "disconnected",
            "planned",
            "unknown",
        )
    ) == overview["summary"]["total"]
    budget_write = google["capabilities"]["budgets.update"]
    assert budget_write["state"] == "not_connected"
    assert budget_write["blocked_by_policy"] is True
    assert "must-not-leak" not in json.dumps(overview, ensure_ascii=False)


@pytest.mark.asyncio
async def test_v2_permission_observations_ignore_stale_rows():
    db = FakeDB(
        {
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "google_analytics_4",
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                    "source_mode": "v2_snapshot",
                    "has_data": True,
                    "permissions_observed": False,
                    "permission_observation_id": "observation-new",
                }
            ],
            "mezan_integration_permissions_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "google_analytics_4",
                    "permission_key": "analytics.readonly",
                    "permission_status": "missing",
                    "permission_observation_id": "observation-old",
                }
            ],
        }
    )

    overview = await _service(db).overview("owner-1")
    ga4 = next(
        card
        for card in overview["providers"]
        if card["provider"] == "google_analytics_4"
    )
    assert ga4["permissions"] == {
        "current": [],
        "missing": [],
        "unknown": True,
    }
    assert {
        entry["state"] for entry in ga4["capabilities"].values()
    } == {"unknown"}

    db.rows["mezan_integrations_v2"][0].update(
        {
            "permissions_observed": True,
            "permission_observation_id": "observation-current",
        }
    )
    db.rows["mezan_integration_permissions_v2"].append(
        {
            "user_id": "owner-1",
            "provider": "google_analytics_4",
            "permission_key": "analytics.readonly",
            "permission_status": "current",
            "permission_observation_id": "observation-current",
        }
    )
    refreshed = await _service(db).overview("owner-1")
    refreshed_ga4 = next(
        card
        for card in refreshed["providers"]
        if card["provider"] == "google_analytics_4"
    )
    assert refreshed_ga4["permissions"] == {
        "current": ["analytics.readonly"],
        "missing": [],
        "unknown": False,
    }
    assert {
        entry["state"] for entry in refreshed_ga4["capabilities"].values()
    } == {"available"}


@pytest.mark.asyncio
async def test_v2_accounts_keep_individual_state_and_fail_closed_on_bad_provenance():
    db = FakeDB(
        {
            "mezan_integrations_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "google_ads",
                    "connection_status": "connected",
                    "connection_provenance": "api_connection",
                    "source_mode": "v2_snapshot",
                    "has_data": True,
                    "data_quality": "good",
                }
            ],
            "mezan_integration_accounts_v2": [
                {
                    "user_id": "owner-1",
                    "provider": "google_ads",
                    "mezan_integration_account_id": "google-account-1",
                    "ad_account_id": "act-1",
                    "connection_status": "needs_reauth",
                    "connection_provenance": "api_connection",
                    "source_mode": "v2_account",
                },
                {
                    "user_id": "owner-1",
                    "provider": "google_ads",
                    "mezan_integration_account_id": "google-account-2",
                    "ad_account_id": "act-2",
                    "connection_status": "connected",
                    "connection_provenance": "untrusted-value",
                    "source_mode": "v2_account",
                    "last_sync_at": "2026-07-28T11:30:00+00:00",
                },
            ],
        }
    )

    overview = await _service(db).overview("owner-1")
    google = next(
        card for card in overview["providers"] if card["provider"] == "google_ads"
    )
    accounts = {
        account["mezan_integration_account_id"]: account
        for account in google["accounts"]
    }
    assert accounts["google-account-1"]["connection_status"] == "needs_reauth"
    assert (
        accounts["google-account-1"]["connection_provenance"]
        == "api_connection"
    )
    assert accounts["google-account-2"]["connection_status"] == "data_available"
    assert accounts["google-account-2"]["connection_provenance"] == "unknown"
    assert google["capabilities"]["budgets.update"]["state"] == "approval_required"
    assert (
        accounts["google-account-1"]["capabilities"]["budgets.update"]["state"]
        == "not_connected"
    )
    assert (
        accounts["google-account-2"]["capabilities"]["budgets.update"]["state"]
        == "not_connected"
    )


@pytest.mark.asyncio
async def test_activity_lists_are_tenant_scoped_bounded_and_sanitized():
    db = FakeDB(
        {
            "mezan_integration_sync_runs_v2": [
                {
                    "user_id": "owner-1",
                    "run_id": "r1",
                    "provider": "salla",
                    "run_type": "local_connection_test",
                    "status": "passed",
                    "started_at": "2026-07-28T10:00:00+00:00",
                    "summary": {"message": "ok"},
                    "access_token": "hidden",
                },
                {
                    "user_id": "someone-else",
                    "run_id": "r2",
                    "provider": "salla",
                    "status": "passed",
                    "started_at": "2026-07-28T11:00:00+00:00",
                },
            ]
        }
    )
    result = await _service(db).list_sync_runs(
        "owner-1",
        provider="salla",
        limit=999,
    )
    assert result["limit"] == 100
    assert result["total"] == 1
    assert [item["run_id"] for item in result["items"]] == ["r1"]
    assert "hidden" not in json.dumps(result)
