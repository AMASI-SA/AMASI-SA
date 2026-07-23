from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from mezan_mcp.gateway import TOOL_DESCRIPTORS, make_mezan_mcp_router
from mezan_mcp.security import (
    OAuthConfigError,
    Principal,
    ReadOnlyDatabase,
    ReadOnlyHttpClient,
    SubjectRateLimiter,
    WriteBlockedError,
    audit_tool_call,
    sanitize_output,
)
from mezan_mcp import security as mcp_security
from mezan_mcp.services import MezanReadOnlyTools, safe_order
from scripts import verify_mezan_mcp_gateway as deployment_verifier


AUTH_HEADERS = {"Authorization": "Bearer test-token"}


class FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]):
        self.rows = rows

    def limit(self, value: int) -> "FakeCursor":
        self.rows = self.rows[:value]
        return self

    async def to_list(self, value: int) -> list[dict[str, Any]]:
        return self.rows[:value]


class FakeCollection:
    def __init__(self, rows: list[dict[str, Any]] | None = None):
        self.rows = rows or []
        self.reads = 0
        self.writes = 0

    def find(self, *_args: Any, **_kwargs: Any) -> FakeCursor:
        self.reads += 1
        return FakeCursor([dict(row) for row in self.rows])

    async def find_one(self, query: dict[str, Any], *_args: Any, **_kwargs: Any) -> dict[str, Any] | None:
        self.reads += 1
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items() if not key.startswith("$")):
                return dict(row)
        return None

    async def update_one(self, *_args: Any, **_kwargs: Any) -> None:
        self.writes += 1


class FakeDatabase:
    def __init__(self):
        self.command_calls = 0
        self.unified_orders = FakeCollection()
        self.salla_integrations = FakeCollection()
        self.salla_sync_logs = FakeCollection()
        self.integration_inbox = FakeCollection()
        self.qoyod_invoices = FakeCollection()
        self.import_jobs = FakeCollection()
        self.webhook_parse_failures = FakeCollection()

    async def command(self, command: dict[str, Any]) -> dict[str, int]:
        assert command == {"ping": 1}
        self.command_calls += 1
        return {"ok": 1}


async def authenticated(_request: Any) -> Principal:
    return Principal(
        subject="chatgpt-test-user",
        tenant_id="tenant-1",
        scopes=frozenset({"mezan:read"}),
    )


def make_app(db: FakeDatabase, *, limit: int = 60) -> FastAPI:
    app = FastAPI()
    app.include_router(
        make_mezan_mcp_router(
            db,
            auth_dependency=authenticated,
            rate_limiter=SubjectRateLimiter(limit=limit, window_seconds=60),
        )
    )
    return app


def test_phase_one_exposes_exactly_eight_read_only_tools() -> None:
    expected = {
        "mezan_health",
        "mezan_get_system_status",
        "mezan_get_order",
        "mezan_compare_order_with_salla",
        "mezan_get_error_trace",
        "mezan_list_recent_failures",
        "mezan_qoyod_reconciliation",
        "mezan_get_database_schema",
    }
    assert {tool["name"] for tool in TOOL_DESCRIPTORS} == expected
    assert len(TOOL_DESCRIPTORS) == 8
    for tool in TOOL_DESCRIPTORS:
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
        assert tool["annotations"]["idempotentHint"] is True
        assert tool["securitySchemes"][0]["type"] == "oauth2"
        assert tool["securitySchemes"][0]["scopes"] == ["mezan:read"]
        assert tool["_meta"]["securitySchemes"] == tool["securitySchemes"]
        assert tool["outputSchema"] == {
            "type": "object",
            "additionalProperties": True,
        }
        assert not any(
            forbidden in tool["name"]
            for forbidden in ("create", "send", "retry", "replay", "update", "delete")
        )


def test_oauth_configuration_rejects_non_https_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEZAN_MCP_OAUTH_ISSUER", "http://identity.example")
    monkeypatch.setenv("MEZAN_MCP_OAUTH_AUDIENCE", "https://preview.example/api/ai/mcp")
    monkeypatch.setenv("MEZAN_MCP_OAUTH_JWKS_URL", "https://identity.example/jwks.json")
    with pytest.raises(OAuthConfigError):
        mcp_security._oauth_config()

    monkeypatch.setenv("MEZAN_MCP_OAUTH_ISSUER", "https://identity.example")
    monkeypatch.setenv(
        "MEZAN_MCP_OAUTH_JWKS_URL",
        "https://identity.example/jwks.json?token=bad",
    )
    with pytest.raises(OAuthConfigError):
        mcp_security._oauth_config()


def test_oauth_configuration_preserves_exact_auth0_issuer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuer = "https://mezan.eu.auth0.com/"
    monkeypatch.setenv("MEZAN_MCP_OAUTH_ISSUER", issuer)
    monkeypatch.setenv(
        "MEZAN_MCP_OAUTH_AUDIENCE", "https://mezansalla.com/api/ai/mcp"
    )
    monkeypatch.delenv("MEZAN_MCP_OAUTH_JWKS_URL", raising=False)

    configured_issuer, _audience, jwks_url, _scope, _tenant_claim = (
        mcp_security._oauth_config()
    )

    assert configured_issuer == issuer
    assert jwks_url == "https://mezan.eu.auth0.com/.well-known/jwks.json"
    assert mcp_security.protected_resource_metadata()["authorization_servers"] == [
        issuer
    ]


def test_production_database_facade_blocks_mutations_and_unknown_collections() -> None:
    raw = FakeDatabase()
    db = ReadOnlyDatabase(raw)
    with pytest.raises(WriteBlockedError):
        db.qoyod_invoices.update_one({}, {"$set": {"status": "sent"}})
    with pytest.raises(WriteBlockedError):
        db.integration_inbox.delete_one({})
    with pytest.raises(AttributeError):
        _ = db.users
    assert raw.qoyod_invoices.writes == 0
    assert raw.integration_inbox.writes == 0


@pytest.mark.asyncio
async def test_salla_http_guard_allows_get_and_blocks_every_write_method() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"method": request.method})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as raw:
        client = ReadOnlyHttpClient(raw)
        response = await client.get("https://api.salla.dev/admin/v2/orders/1")
        assert response.json() == {"method": "GET"}
        for method in (client.post, client.put, client.patch, client.delete):
            with pytest.raises(WriteBlockedError):
                await method("https://api.salla.dev/admin/v2/orders/1")
        with pytest.raises(WriteBlockedError):
            await client.request("POST", "https://api.salla.dev/admin/v2/orders/1")
        with pytest.raises(WriteBlockedError):
            await client.get("https://example.com/private")


def test_output_redaction_removes_secrets_and_customer_pii() -> None:
    output = sanitize_output(
        {
            "order_number": "123",
            "phone": "+966500000000",
            "email": "customer@example.com",
            "address": "private",
            "access_token": "secret-token",
            "nested": {
                "refresh_token": "secret-refresh",
                "status": "Bearer abc.def.ghi customer@example.com +966500000000",
            },
        }
    )
    assert output == {
        "order_number": "123",
        "nested": {
            "status": "Bearer [redacted] [email redacted] [phone redacted]"
        },
    }
    rendered = str(output)
    assert "+966" not in rendered
    assert "secret-token" not in rendered


def test_safe_order_understands_nested_salla_items_without_exposing_pii() -> None:
    result = safe_order(
        {
            "order_number": "123",
            "customer_mobile": "+966500000000",
            "amounts": {"total": {"amount": 115}},
            "total_amount": {"amount": 115},
            "items": [
                {
                    "product": {"name": "سلسال", "sku": "SKU-1"},
                    "quantity": 2,
                    "amounts": {
                        "price_without_tax": {"amount": 50},
                        "total": {"amount": 100},
                    },
                    "options": [
                        {"name": "اللون", "value": {"name": "فضي"}},
                        {"name": "اللون", "value": {"name": "فضي"}},
                    ],
                }
            ],
        }
    )
    assert result["total_amount"] == 115
    assert result["items"] == [
        {
            "name": "سلسال",
            "sku": "SKU-1",
            "quantity": 2,
            "unit_price": 50,
            "total": 100,
            "options": [{"name": "اللون", "value": "فضي"}],
        }
    ]
    assert "customer_mobile" not in result


def test_audit_log_hashes_subject_and_tenant(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="mezan.mcp.audit"):
        audit_tool_call(
            request_id="request-1",
            subject="operator@example.com",
            tenant_id="tenant-sensitive",
            tool="mezan_health",
            outcome="ok",
            duration_ms=3,
        )
    payload = caplog.records[-1].getMessage().split("mcp_tool_call ", 1)[1]
    event = json.loads(payload)
    assert event["tool"] == "mezan_health"
    assert "subject_hash" in event and "tenant_hash" in event
    assert "subject" not in event and "tenant_id" not in event
    assert "operator@example.com" not in payload
    assert "tenant-sensitive" not in payload


@pytest.mark.asyncio
async def test_qoyod_reconciliation_reads_local_data_and_never_calls_qoyod(monkeypatch: pytest.MonkeyPatch) -> None:
    db = FakeDatabase()
    db.unified_orders.rows = [
        {"user_id": "tenant-1", "order_number": "9001", "total_amount": 134.00}
    ]
    db.integration_inbox.rows = [
        {"user_id": "tenant-1", "salla_order_number": "9001", "status": "sent"}
    ]
    db.qoyod_invoices.rows = [
        {
            "user_id": "tenant-1",
            "salla_order_number": "9001",
            "qoyod_invoice_id": "q-1",
            "total": 134.00,
        }
    ]

    class NetworkMustNotBeUsed:
        def __init__(self, *_args: Any, **_kwargs: Any):
            raise AssertionError("Qoyod network access is forbidden")

    monkeypatch.setattr(httpx, "AsyncClient", NetworkMustNotBeUsed)
    result = await MezanReadOnlyTools(db).mezan_qoyod_reconciliation(
        "tenant-1", {"order_number": "9001"}
    )
    assert result["qoyod_access"] == "local read-only reconciliation; no Qoyod network call"
    assert result["local_qoyod_invoices"][0]["difference_from_mezan"] == 0
    assert db.qoyod_invoices.writes == 0
    assert db.integration_inbox.writes == 0


@pytest.mark.asyncio
async def test_mcp_initialize_discovery_and_health() -> None:
    db = FakeDatabase()
    app = make_app(db)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://preview.example") as client:
        initialize = await client.post(
            "/api/ai/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        assert initialize.status_code == 200
        assert initialize.headers["cache-control"] == "no-store"
        assert initialize.headers["x-content-type-options"] == "nosniff"
        assert initialize.json()["result"]["serverInfo"]["name"] == "Mezan MCP Gateway"

        discovered = await client.post(
            "/api/ai/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        assert len(discovered.json()["result"]["tools"]) == 8

        health = await client.post(
            "/api/ai/mcp",
            headers=AUTH_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "mezan_health", "arguments": {}},
            },
        )
        payload = health.json()["result"]
        assert payload["isError"] is False
        assert payload["structuredContent"]["access"] == "read-only"
        assert payload["structuredContent"]["qoyod_network_access"] is False
        assert db.command_calls == 1


@pytest.mark.asyncio
async def test_public_discovery_never_authenticates_or_reads_the_database() -> None:
    db = FakeDatabase()
    auth_calls = 0

    async def authentication_must_not_run(_request: Any) -> Principal:
        nonlocal auth_calls
        auth_calls += 1
        raise AssertionError("Public MCP discovery must not authenticate")

    app = FastAPI()
    app.include_router(
        make_mezan_mcp_router(db, auth_dependency=authentication_must_not_run)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://preview.example"
    ) as client:
        initialize = await client.post(
            "/api/ai/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        )
        discovered = await client.post(
            "/api/ai/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        )
        ping = await client.post(
            "/api/ai/mcp",
            json={"jsonrpc": "2.0", "id": 3, "method": "ping", "params": {}},
        )
        initialized = await client.post(
            "/api/ai/mcp",
            json={
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            },
        )

    assert initialize.status_code == 200
    assert len(discovered.json()["result"]["tools"]) == 8
    assert ping.status_code == 200
    assert initialized.status_code == 202
    assert auth_calls == 0
    assert db.command_calls == 0
    assert sum(
        collection.reads
        for collection in (
            db.unified_orders,
            db.salla_integrations,
            db.salla_sync_logs,
            db.integration_inbox,
            db.qoyod_invoices,
            db.import_jobs,
            db.webhook_parse_failures,
        )
    ) == 0


@pytest.mark.asyncio
async def test_mcp_transport_is_post_only_and_metadata_is_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEZAN_MCP_PUBLIC_BASE_URL", "https://preview.mezansalla.com")
    monkeypatch.setenv(
        "MEZAN_MCP_METADATA_URL",
        "https://preview.mezansalla.com/api/.well-known/oauth-protected-resource",
    )
    monkeypatch.setenv("MEZAN_MCP_OAUTH_ISSUER", "https://identity.example/")
    app = make_app(FakeDatabase())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://preview.mezansalla.com"
    ) as client:
        metadata = await client.get("/.well-known/oauth-protected-resource")
        path_metadata = await client.get(
            "/.well-known/oauth-protected-resource/api/ai/mcp"
        )
        api_metadata = await client.get(
            "/api/.well-known/oauth-protected-resource"
        )
        api_path_metadata = await client.get(
            "/api/.well-known/oauth-protected-resource/api/ai/mcp"
        )
        get_response = await client.get("/api/ai/mcp")
        delete_response = await client.delete("/api/ai/mcp")
        options_response = await client.options("/api/ai/mcp")

    assert metadata.status_code == 200
    assert metadata.headers["content-type"].startswith("application/json")
    assert metadata.headers["cache-control"] == "no-store"
    assert metadata.headers["x-content-type-options"] == "nosniff"
    assert metadata.json()["resource"] == "https://preview.mezansalla.com/api/ai/mcp"
    assert metadata.json()["authorization_servers"] == ["https://identity.example/"]
    assert path_metadata.status_code == 200
    assert path_metadata.json() == metadata.json()
    assert api_metadata.status_code == 200
    assert api_metadata.json() == metadata.json()
    assert api_path_metadata.status_code == 200
    assert api_path_metadata.json() == metadata.json()
    assert get_response.status_code == 405
    assert delete_response.status_code == 405
    assert options_response.status_code == 204
    assert options_response.headers["allow"] == "POST, OPTIONS"


@pytest.mark.asyncio
async def test_mcp_rejects_oversized_requests_and_invalid_tool_arguments() -> None:
    app = make_app(FakeDatabase())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://preview.example"
    ) as client:
        oversized = await client.post(
            "/api/ai/mcp",
            content=b"{" + (b"x" * (64 * 1024)),
            headers={"content-type": "application/json"},
        )
        invalid = await client.post(
            "/api/ai/mcp",
            headers=AUTH_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "mezan_get_order",
                    "arguments": {"order_number": "1", "write": True},
                },
            },
        )

    assert oversized.status_code == 413
    assert invalid.status_code == 200
    assert invalid.json()["error"]["code"] == -32602
    assert "Unsupported tool argument" in invalid.json()["error"]["message"]


@pytest.mark.asyncio
async def test_order_read_is_tenant_isolated() -> None:
    db = FakeDatabase()
    db.unified_orders.rows = [
        {"user_id": "tenant-2", "order_number": "9001", "total_amount": 10}
    ]
    tools = MezanReadOnlyTools(db)
    with pytest.raises(LookupError):
        await tools.mezan_get_order("tenant-1", {"order_number": "9001"})


@pytest.mark.asyncio
async def test_tools_call_returns_chatgpt_oauth_challenge_before_any_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata_url = (
        "https://preview.example/api/.well-known/oauth-protected-resource"
    )
    monkeypatch.setenv("MEZAN_MCP_METADATA_URL", metadata_url)
    db = FakeDatabase()
    app = FastAPI()
    app.include_router(make_mezan_mcp_router(db))
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://preview.example") as client:
        discovered = await client.post(
            "/api/ai/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        )
        response = await client.post(
            "/api/ai/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "mezan_health", "arguments": {}},
            },
        )
        protected_order = await client.post(
            "/api/ai/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "mezan_get_order",
                    "arguments": {"order_number": "safe-test-order"},
                },
            },
        )

    assert discovered.status_code == 200
    assert len(discovered.json()["result"]["tools"]) == 8
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert "structuredContent" not in result
    challenges = result["_meta"]["mcp/www_authenticate"]
    assert len(challenges) == 1
    assert f'resource_metadata="{metadata_url}"' in challenges[0]
    assert 'scope="mezan:read"' in challenges[0]
    assert 'error="invalid_token"' in challenges[0]
    assert 'error_description="' in challenges[0]
    assert protected_order.status_code == 200
    assert protected_order.json()["result"]["isError"] is True
    assert db.command_calls == 0
    collections = (
        db.unified_orders,
        db.salla_integrations,
        db.salla_sync_logs,
        db.integration_inbox,
        db.qoyod_invoices,
        db.import_jobs,
        db.webhook_parse_failures,
    )
    assert sum(collection.reads for collection in collections) == 0


@pytest.mark.asyncio
async def test_supplied_invalid_token_remains_http_401() -> None:
    db = FakeDatabase()

    async def reject_invalid_token(_request: Any) -> Principal:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired OAuth bearer token",
            headers={
                "WWW-Authenticate": (
                    'Bearer resource_metadata="https://preview.example/'
                    '.well-known/oauth-protected-resource", '
                    'scope="mezan:read"'
                )
            },
        )

    app = FastAPI()
    app.include_router(
        make_mezan_mcp_router(db, auth_dependency=reject_invalid_token)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://preview.example"
    ) as client:
        response = await client.post(
            "/api/ai/mcp",
            headers={"Authorization": "Bearer expired-token"},
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "mezan_health", "arguments": {}},
            },
        )

    assert response.status_code == 401
    assert "resource_metadata=" in response.headers["www-authenticate"]
    assert db.command_calls == 0


@pytest.mark.asyncio
async def test_oauth_configuration_failure_is_not_disguised_as_login_prompt() -> None:
    db = FakeDatabase()

    async def oauth_unavailable(_request: Any) -> Principal:
        raise HTTPException(status_code=503, detail="OAuth configuration unavailable")

    app = FastAPI()
    app.include_router(
        make_mezan_mcp_router(db, auth_dependency=oauth_unavailable)
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="https://preview.example"
    ) as client:
        response = await client.post(
            "/api/ai/mcp",
            headers=AUTH_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "mezan_health", "arguments": {}},
            },
        )

    assert response.status_code == 503
    assert "mcp/www_authenticate" not in response.text
    assert db.command_calls == 0


@pytest.mark.asyncio
async def test_mcp_rate_limit_is_enforced() -> None:
    db = FakeDatabase()
    app = make_app(db, limit=1)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://preview.example") as client:
        first = await client.post(
            "/api/ai/mcp",
            headers=AUTH_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "mezan_health", "arguments": {}},
            },
        )
        second = await client.post(
            "/api/ai/mcp",
            headers=AUTH_HEADERS,
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "mezan_health", "arguments": {}},
            },
        )
    assert first.status_code == 200
    assert second.status_code == 429
    assert db.command_calls == 1


def test_deployment_verifier_requires_dcr_public_clients_and_pkce_s256(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    issuer = "https://identity.example/"
    discovery = {
        "issuer": issuer,
        "authorization_endpoint": "https://identity.example/authorize",
        "token_endpoint": "https://identity.example/oauth/token",
        "registration_endpoint": "https://identity.example/oidc/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    }

    def fake_request(
        url: str,
        **_kwargs: Any,
    ) -> tuple[int, dict[str, str], dict[str, Any]]:
        assert url == "https://identity.example/.well-known/openid-configuration"
        return 200, {"content-type": "application/json"}, discovery

    monkeypatch.setattr(deployment_verifier, "_request_json", fake_request)
    deployment_verifier._verify_authorization_server_contract(issuer)


def test_deployment_verifier_rejects_incomplete_pkce_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        deployment_verifier,
        "_authorization_server_metadata",
        lambda _issuer: {
            "issuer": "https://identity.example/",
            "authorization_endpoint": "https://identity.example/authorize",
            "token_endpoint": "https://identity.example/oauth/token",
            "registration_endpoint": "https://identity.example/oidc/register",
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code"],
            "code_challenge_methods_supported": ["plain"],
            "token_endpoint_auth_methods_supported": ["none"],
        },
    )

    with pytest.raises(
        deployment_verifier.VerificationError,
        match="PKCE S256",
    ):
        deployment_verifier._verify_authorization_server_contract(
            "https://identity.example/"
        )
