"""Isolated ASGI regression checks for the MCP tool exception boundary."""
from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from mezan_mcp import security as mcp_security
from mezan_mcp.gateway import MCP_PROTOCOL_VERSION, make_mezan_mcp_router
from tests.test_mezan_mcp_gateway import AUTH_HEADERS, FakeDatabase, make_app


PUBLIC_TOOL_ERROR = {"error": "The read-only diagnostic tool failed"}
PRIVATE_DIAGNOSTIC = (
    "internal-host-a8 /srv/mezan/private/config.pem "
    "opaque-credential-a8 other-tenant-record-a8"
)


class InternalDriverFailure(RuntimeError):
    pass


class FailingDatabase(FakeDatabase):
    def __init__(self, error: Exception):
        super().__init__()
        self.error = error

    async def command(self, command: dict[str, Any]) -> dict[str, int]:
        assert command == {"ping": 1}
        self.command_calls += 1
        raise self.error


async def call_tool(
    app: FastAPI,
    *,
    name: str = "mezan_health",
    arguments: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="https://mcp.example",
    ) as client:
        return await client.post(
            "/api/ai/mcp",
            headers=AUTH_HEADERS if headers is None else headers,
            json={
                "jsonrpc": "2.0",
                "id": "security-a8",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            },
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_type", [ValueError, LookupError, KeyError, InternalDriverFailure]
)
async def test_tool_callee_exceptions_return_only_fixed_public_error(
    error_type: type[Exception],
) -> None:
    db = FailingDatabase(error_type(PRIVATE_DIAGNOSTIC))
    response = await call_tool(make_app(db))

    assert db.command_calls == 1
    assert response.status_code == 200
    assert response.headers["mcp-protocol-version"] == MCP_PROTOCOL_VERSION
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == "security-a8"
    assert set(body) == {"jsonrpc", "id", "result"}
    result = body["result"]
    assert result["isError"] is True
    assert result["structuredContent"] == PUBLIC_TOOL_ERROR
    assert result["content"] == [
        {"type": "text", "text": json.dumps(PUBLIC_TOOL_ERROR)}
    ]
    for canary in PRIVATE_DIAGNOSTIC.split():
        assert canary not in response.text
    assert error_type.__name__ not in response.text
    assert "error_type" not in response.text


@pytest.mark.asyncio
async def test_tool_http_exception_does_not_expose_internal_detail_or_headers() -> None:
    db = FailingDatabase(
        HTTPException(
            status_code=503,
            detail={"internal_detail": PRIVATE_DIAGNOSTIC},
            headers={"X-Internal-Reference": PRIVATE_DIAGNOSTIC},
        )
    )

    response = await call_tool(make_app(db))

    assert db.command_calls == 1
    assert response.status_code == 200
    result = response.json()["result"]
    assert result["isError"] is True
    assert result["structuredContent"] == PUBLIC_TOOL_ERROR
    assert json.loads(result["content"][0]["text"]) == PUBLIC_TOOL_ERROR
    assert "x-internal-reference" not in response.headers
    for canary in PRIVATE_DIAGNOSTIC.split():
        assert canary not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "arguments", "message"),
    [
        ("mezan_health", {"write": True}, "Unsupported tool argument: write"),
        ("mezan_get_order", {}, "order_number is required"),
        ("mezan_get_order", {"order_number": 1}, "order_number must be a string"),
    ],
)
async def test_local_argument_validation_keeps_public_messages(
    name: str, arguments: dict[str, Any], message: str,
) -> None:
    db = FailingDatabase(AssertionError("Invalid input must not reach a tool"))

    response = await call_tool(make_app(db), name=name, arguments=arguments)

    assert response.status_code == 200
    assert response.json() == {
        "jsonrpc": "2.0",
        "id": "security-a8",
        "error": {"code": -32602, "message": message},
    }
    assert db.command_calls == 0
    assert db.unified_orders.reads == 0


@pytest.fixture
def oauth_token(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Use real JWT verification with only the network JWKS lookup replaced."""
    issuer = "https://identity.example/"
    audience = "https://mcp.example/api/ai/mcp"
    monkeypatch.setenv("MEZAN_MCP_OAUTH_ISSUER", issuer)
    monkeypatch.setenv("MEZAN_MCP_OAUTH_AUDIENCE", audience)
    monkeypatch.setenv("MEZAN_MCP_OAUTH_JWKS_URL", "https://identity.example/jwks.json")
    monkeypatch.setenv("MEZAN_MCP_REQUIRED_SCOPE", "mezan:read")
    monkeypatch.setenv("MEZAN_MCP_TENANT_CLAIM", "mezan_tenant_id")
    monkeypatch.setenv("MEZAN_MCP_PUBLIC_BASE_URL", "https://mcp.example")
    monkeypatch.delenv("MEZAN_MCP_METADATA_URL", raising=False)
    key = ec.generate_private_key(ec.SECP256R1())
    jwks = SimpleNamespace(
        get_signing_key_from_jwt=lambda _token: SimpleNamespace(key=key.public_key())
    )
    monkeypatch.setattr(mcp_security, "_jwks_client", lambda _url: jwks)

    def issue(**overrides: Any) -> dict[str, str]:
        now = int(time.time())
        claims = {
            "iss": issuer,
            "aud": audience,
            "iat": now,
            "exp": now + 300,
            "sub": "synthetic-mcp-user",
            "scope": "mezan:read",
            "mezan_tenant_id": "tenant-1",
            **overrides,
        }
        token = jwt.encode(claims, key, algorithm="ES256")
        return {"Authorization": f"Bearer {token}"}

    return issue


@pytest.mark.asyncio
@pytest.mark.parametrize("credential", ["absent", "invalid", "missing_scope", "missing_tenant"])
async def test_tool_authentication_rejects_before_callee(
    oauth_token: Any, credential: str,
) -> None:
    db = FailingDatabase(AssertionError("Unauthenticated request reached a tool"))
    app = FastAPI()
    app.include_router(make_mezan_mcp_router(db))
    headers = {
        "absent": {},
        "invalid": {"Authorization": "Bearer invalid-synthetic-token"},
        "missing_scope": oauth_token(scope="unrelated:read"),
        "missing_tenant": oauth_token(mezan_tenant_id=""),
    }[credential]

    response = await call_tool(app, headers=headers)

    assert db.command_calls == 0
    if credential == "absent":
        assert response.status_code == 200
        result = response.json()["result"]
        assert result["isError"] is True
        assert "mezan:read" in result["_meta"]["mcp/www_authenticate"][0]
    else:
        assert response.status_code == 401
        assert 'error="invalid_token"' in response.headers["www-authenticate"]


@pytest.mark.asyncio
async def test_authorized_order_read_keeps_tenant_isolation_and_safe_output(
    oauth_token: Any,
) -> None:
    db = FakeDatabase()
    db.unified_orders.rows = [
        {
            "user_id": "tenant-2",
            "order_number": "shared-order",
            "total_amount": 999,
        },
        {
            "user_id": "tenant-1",
            "order_number": "shared-order",
            "total_amount": 25,
            "customer": {"name": "private-customer-a8"},
        },
        {
            "user_id": "tenant-2",
            "order_number": "other-order",
            "total_amount": 777,
        },
    ]
    app = FastAPI()
    app.include_router(make_mezan_mcp_router(db))

    allowed = await call_tool(
        app,
        name="mezan_get_order",
        arguments={"order_number": "shared-order"},
        headers=oauth_token(),
    )
    denied = await call_tool(
        app,
        name="mezan_get_order",
        arguments={"order_number": "other-order"},
        headers=oauth_token(),
    )

    assert allowed.status_code == denied.status_code == 200
    allowed_result = allowed.json()["result"]
    assert allowed_result["isError"] is False
    assert allowed_result["structuredContent"]["order_number"] == "shared-order"
    assert allowed_result["structuredContent"]["total_amount"] == 25
    assert "private-customer-a8" not in allowed.text
    assert "999" not in allowed.text
    assert denied.json()["result"]["isError"] is True
    assert denied.json()["result"]["structuredContent"] == PUBLIC_TOOL_ERROR
    assert "777" not in denied.text
    assert db.unified_orders.writes == db.integration_inbox.writes == 0
