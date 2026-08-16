"""Regression guards for freezing the legacy Snapchat data planes."""
from __future__ import annotations

import asyncio
from pathlib import Path

from ads_v2.data_layer import discovery
from ads_v2.sync.core import auto_reconcile_for_day, run_sync_for_account
from integrations_control_center.snapchat_native_guard import (
    assert_snapchat_v2_is_legacy_independent,
)


BACKEND = Path(__file__).resolve().parents[1]


def test_legacy_snapchat_routers_are_not_mounted() -> None:
    server_source = (BACKEND / "server.py").read_text(encoding="utf-8")
    ads_routes_source = (BACKEND / "ads_v2" / "routes.py").read_text(
        encoding="utf-8"
    )

    assert "attach_snapchat_routes" not in server_source
    assert "build_relink_router" not in ads_routes_source


def test_integrations_v2_has_no_legacy_sync_fallback() -> None:
    package_source = (
        BACKEND / "integrations_control_center" / "__init__.py"
    ).read_text(encoding="utf-8")

    assert "snapchat_oauth_configured():" not in package_source
    assert "attach_snapchat_native_data_routes" in package_source
    assert "route is not native_sync_route" in package_source
    assert_snapchat_v2_is_legacy_independent()


def test_ads_v2_discovery_never_reads_legacy_snapchat(monkeypatch) -> None:
    async def no_connection(_db, _user_id):
        return None

    monkeypatch.setattr(discovery, "read_v1_meta_connection", no_connection)
    monkeypatch.setattr(discovery, "read_v1_tiktok_connection", no_connection)

    class NoDatabaseAccess:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected database access: {name}")

    result = asyncio.run(
        discovery.discover_all_providers(NoDatabaseAccess(), "owner-1")
    )

    assert result["snapchat"]["connection_status"] == "legacy_frozen"
    assert result["snapchat"]["accounts"] == []
    assert result["snapchat"]["v1_token_ref"] is None


def test_ads_v2_sync_stops_before_reading_legacy_token() -> None:
    class Accounts:
        async def find_one(self, _query):
            return {
                "id": "legacy-snap-account",
                "user_id": "owner-1",
                "provider": "snapchat",
                "sync_enabled": True,
                "soft_deleted": False,
            }

    class Database:
        ads_accounts = Accounts()

        def __getitem__(self, name):
            raise AssertionError(f"legacy token collection was read: {name}")

        def __getattr__(self, name):
            raise AssertionError(f"unexpected database access: {name}")

    result = asyncio.run(
        run_sync_for_account(
            Database(), "owner-1", "legacy-snap-account", "2026-08-16"
        )
    )

    assert result["ok"] is False
    assert result["error"] == "snapchat_legacy_frozen"
    assert result["redirect_to"] == "/integrations-v2?provider=snapchat_ads"


def test_ads_v2_reconciliation_stops_before_reading_legacy_token() -> None:
    class Daily:
        async def find_one(self, _query, _projection=None):
            return {"spend_native": 10}

    class Accounts:
        async def find_one(self, _query):
            return {"provider": "snapchat"}

    class Database:
        ads_daily = Daily()
        ads_accounts = Accounts()

        def __getitem__(self, name):
            raise AssertionError(f"legacy token collection was read: {name}")

    result = asyncio.run(
        auto_reconcile_for_day(
            Database(), "owner-1", "legacy-snap-account", "2026-08-16"
        )
    )

    assert result["error"] == "snapchat_legacy_frozen"
