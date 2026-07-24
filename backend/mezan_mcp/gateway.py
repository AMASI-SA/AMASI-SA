"""Streamable HTTP transport for the read-only Mezan MCP gateway."""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Awaitable, Callable, Mapping

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from .security import (
    Principal,
    SubjectRateLimiter,
    audit_tool_call,
    authenticate_request,
    oauth_challenge,
    protected_resource_metadata,
    sanitize_output,
)
from .services import MezanReadOnlyTools, invoke_tool


MCP_PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "Mezan MCP Gateway"
SERVER_VERSION = "0.1.0"
MAX_REQUEST_BYTES = 64 * 1024
AuthDependency = Callable[[Request], Awaitable[Principal]]
MCP_RESPONSE_HEADERS = {
    "MCP-Protocol-Version": MCP_PROTOCOL_VERSION,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}


def _oauth_security() -> list[dict[str, Any]]:
    scope = os.environ.get("MEZAN_MCP_REQUIRED_SCOPE", "mezan:read")
    return [{"type": "oauth2", "scopes": [scope]}]


def _tool(
    name: str,
    title: str,
    description: str,
    properties: Mapping[str, Any] | None = None,
    required: list[str] | None = None,
    *,
    open_world: bool = False,
) -> dict[str, Any]:
    security = _oauth_security()
    return {
        "name": name,
        "title": title,
        "description": description,
        "inputSchema": {
            "type": "object",
            "properties": dict(properties or {}),
            "required": required or [],
            "additionalProperties": False,
        },
        "outputSchema": {
            "type": "object",
            "additionalProperties": True,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": open_world,
        },
        "securitySchemes": security,
        "_meta": {"securitySchemes": security},
    }


ORDER_NUMBER = {
    "order_number": {
        "type": "string",
        "minLength": 1,
        "maxLength": 64,
        "description": "Salla/Mezan order number; never a customer phone number.",
    }
}


TOOL_DESCRIPTORS: list[dict[str, Any]] = [
    _tool("mezan_health", "Mezan health", "Check the gateway and database with read-only operations."),
    _tool(
        "mezan_get_system_status",
        "Mezan system status",
        "Summarize database reachability and recent failures without personal data.",
        {"hours": {"type": "integer", "minimum": 1, "maximum": 168, "default": 24}},
    ),
    _tool(
        "mezan_get_order",
        "Get a Mezan order",
        "Read the operational and accounting-safe view of one order. Customer PII is omitted.",
        ORDER_NUMBER,
        ["order_number"],
    ),
    _tool(
        "mezan_compare_order_with_salla",
        "Compare order with Salla",
        "Compare one order with Salla using a direct GET-only request without token refresh or writes.",
        ORDER_NUMBER,
        ["order_number"],
        open_world=True,
    ),
    _tool(
        "mezan_get_error_trace",
        "Get error trace",
        "Read a sanitized integration trace by order number or error reference.",
        {
            **ORDER_NUMBER,
            "error_reference": {"type": "string", "minLength": 1, "maxLength": 128},
        },
    ),
    _tool(
        "mezan_list_recent_failures",
        "List recent failures",
        "List recent sanitized integration failures for the authenticated tenant.",
        {
            "hours": {"type": "integer", "minimum": 1, "maximum": 720, "default": 24},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
        },
    ),
    _tool(
        "mezan_qoyod_reconciliation",
        "Qoyod reconciliation",
        "Read local Mezan reconciliation records for Qoyod. Never call the Qoyod API.",
        ORDER_NUMBER,
        ["order_number"],
    ),
    _tool(
        "mezan_get_database_schema",
        "Get safe database schema",
        "Return the static allowlisted diagnostic schema; arbitrary queries are not accepted.",
    ),
]


def _jsonrpc_result(request_id: Any, result: Any) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "result": result},
        headers=MCP_RESPONSE_HEADERS,
    )


def _jsonrpc_error(
    request_id: Any,
    code: int,
    message: str,
    *,
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}},
        status_code=status_code,
        headers=MCP_RESPONSE_HEADERS,
    )


def _tool_result(payload: Mapping[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    safe = sanitize_output(payload)
    return {
        "content": [{"type": "text", "text": json.dumps(safe, ensure_ascii=False, default=str)}],
        "structuredContent": safe,
        "isError": is_error,
    }


def _authentication_tool_result() -> dict[str, Any]:
    """Prompt ChatGPT to start OAuth without exposing protected tool data."""
    return {
        "content": [
            {
                "type": "text",
                "text": "Authentication is required to call this read-only Mezan tool.",
            }
        ],
        "isError": True,
        "_meta": {
            "mcp/www_authenticate": [
                oauth_challenge(
                    error="invalid_token",
                    error_description=(
                        "Authenticate with OAuth and grant the mezan:read scope."
                    ),
                )
            ]
        },
    }


def _validate_tool_arguments(name: str, arguments: Mapping[str, Any]) -> None:
    descriptor = next((tool for tool in TOOL_DESCRIPTORS if tool["name"] == name), None)
    if descriptor is None:
        raise KeyError(f"Unknown MCP tool: {name}")
    schema = descriptor["inputSchema"]
    properties = schema.get("properties") or {}
    unknown = sorted(set(arguments) - set(properties))
    if unknown:
        raise ValueError(f"Unsupported tool argument: {unknown[0]}")
    for required in schema.get("required") or []:
        if arguments.get(required) in (None, ""):
            raise ValueError(f"{required} is required")
    for key, value in arguments.items():
        rule = properties[key]
        expected = rule.get("type")
        if expected == "string":
            if not isinstance(value, str):
                raise ValueError(f"{key} must be a string")
            if len(value) < int(rule.get("minLength", 0)):
                raise ValueError(f"{key} is too short")
            if len(value) > int(rule.get("maxLength", MAX_REQUEST_BYTES)):
                raise ValueError(f"{key} is too long")
        elif expected == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{key} must be an integer")
            if "minimum" in rule and value < int(rule["minimum"]):
                raise ValueError(f"{key} is below the allowed minimum")
            if "maximum" in rule and value > int(rule["maximum"]):
                raise ValueError(f"{key} exceeds the allowed maximum")


def make_mezan_mcp_router(
    raw_db: Any,
    *,
    auth_dependency: AuthDependency = authenticate_request,
    rate_limiter: SubjectRateLimiter | None = None,
) -> APIRouter:
    """Create the MCP router without importing or mutating existing routes."""
    router = APIRouter(tags=["Mezan MCP Gateway"])
    tools = MezanReadOnlyTools(raw_db)
    limiter = rate_limiter or SubjectRateLimiter(
        limit=int(os.environ.get("MEZAN_MCP_RATE_LIMIT_PER_MINUTE", "60")),
        window_seconds=60,
    )

    async def resolve_principal(request: Request) -> Principal:
        """Keep the injected authenticator interface stable."""
        return await auth_dependency(request)

    @router.get("/.well-known/oauth-protected-resource", include_in_schema=False)
    @router.get(
        "/.well-known/oauth-protected-resource/api/ai/mcp",
        include_in_schema=False,
    )
    @router.get("/api/.well-known/oauth-protected-resource", include_in_schema=False)
    @router.get(
        "/api/.well-known/oauth-protected-resource/api/ai/mcp",
        include_in_schema=False,
    )
    async def oauth_resource_metadata() -> JSONResponse:
        return JSONResponse(
            protected_resource_metadata(),
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.options("/api/ai/mcp", include_in_schema=False)
    async def mcp_options() -> Response:
        return Response(
            status_code=204,
            headers={**MCP_RESPONSE_HEADERS, "Allow": "POST, OPTIONS"},
        )

    @router.get("/api/ai/mcp", include_in_schema=False)
    async def mcp_get(_principal: Principal = Depends(resolve_principal)) -> Response:
        return Response(
            status_code=405,
            headers={**MCP_RESPONSE_HEADERS, "Allow": "POST, OPTIONS"},
        )

    @router.delete("/api/ai/mcp", include_in_schema=False)
    async def mcp_delete(_principal: Principal = Depends(resolve_principal)) -> Response:
        return Response(
            status_code=405,
            headers={**MCP_RESPONSE_HEADERS, "Allow": "POST, OPTIONS"},
        )

    @router.post("/api/ai/mcp", include_in_schema=False)
    async def mcp_post(request: Request) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    return _jsonrpc_error(
                        None,
                        -32600,
                        "Request body is too large",
                        status_code=413,
                    )
            except ValueError:
                return _jsonrpc_error(None, -32600, "Invalid Content-Length", status_code=400)
        try:
            raw_body = await request.body()
            if len(raw_body) > MAX_REQUEST_BYTES:
                return _jsonrpc_error(
                    None,
                    -32600,
                    "Request body is too large",
                    status_code=413,
                )
            body = json.loads(raw_body)
        except Exception:
            return _jsonrpc_error(None, -32700, "Parse error")
        if not isinstance(body, Mapping):
            return _jsonrpc_error(None, -32600, "Invalid Request")

        method = str(body.get("method") or "")
        request_id = body.get("id")
        params = body.get("params") or {}
        if not isinstance(params, Mapping):
            return _jsonrpc_error(request_id, -32602, "Invalid params")

        if method == "notifications/initialized":
            return Response(status_code=202, headers=MCP_RESPONSE_HEADERS)
        if method == "initialize":
            return _jsonrpc_result(
                request_id,
                {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                    "instructions": (
                        "Production, Salla and Qoyod access is diagnostic and read-only. "
                        "Customer PII and secrets are omitted."
                    ),
                },
            )
        if method == "ping":
            return _jsonrpc_result(request_id, {})
        if method == "tools/list":
            return _jsonrpc_result(request_id, {"tools": TOOL_DESCRIPTORS})
        if method != "tools/call":
            return _jsonrpc_error(request_id, -32601, "Method not found")

        authorization = request.headers.get("authorization", "")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            return _jsonrpc_result(request_id, _authentication_tool_result())
        principal = await resolve_principal(request)
        await limiter.check(principal.subject)

        tool_name = str(params.get("name") or "")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, Mapping):
            return _jsonrpc_error(request_id, -32602, "Tool arguments must be an object")
        request_ref = str(request.headers.get("x-request-id") or uuid.uuid4())
        started = time.monotonic()
        try:
            _validate_tool_arguments(tool_name, arguments)
        except (KeyError, ValueError) as exc:
            audit_tool_call(
                request_id=request_ref,
                subject=principal.subject,
                tenant_id=principal.tenant_id,
                tool=tool_name or "unknown",
                outcome="rejected",
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            return _jsonrpc_error(request_id, -32602, str(exc))

        outcome = "ok"
        try:
            payload = await invoke_tool(tools, tool_name, principal.tenant_id, arguments)
            result = _tool_result(payload)
        except (KeyError, ValueError, LookupError) as exc:
            outcome = "rejected"
            result = _tool_result({"error": str(exc)}, is_error=True)
        except HTTPException:
            outcome = "rejected"
            raise
        except Exception as exc:
            outcome = "failed"
            result = _tool_result(
                {"error": "The read-only diagnostic tool failed", "error_type": type(exc).__name__},
                is_error=True,
            )
        finally:
            audit_tool_call(
                request_id=request_ref,
                subject=principal.subject,
                tenant_id=principal.tenant_id,
                tool=tool_name or "unknown",
                outcome=outcome,
                duration_ms=int((time.monotonic() - started) * 1000),
            )
        return _jsonrpc_result(request_id, result)

    return router
