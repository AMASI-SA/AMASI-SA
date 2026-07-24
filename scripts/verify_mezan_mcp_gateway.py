#!/usr/bin/env python3
"""Verify a deployed Mezan MCP gateway without printing tokens or tool data."""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any


EXPECTED_TOOLS = {
    "mezan_health",
    "mezan_get_system_status",
    "mezan_get_order",
    "mezan_compare_order_with_salla",
    "mezan_get_error_trace",
    "mezan_list_recent_failures",
    "mezan_qoyod_reconciliation",
    "mezan_get_database_schema",
}


class VerificationError(RuntimeError):
    pass


def _canonical_endpoint(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/api/ai/mcp"
    ):
        raise VerificationError(
            "The endpoint must be the canonical HTTPS /api/ai/mcp URL"
        )
    return value.rstrip("/")


def _request_json(
    url: str,
    *,
    method: str = "GET",
    token: str = "",
    payload: dict[str, Any] | None = None,
    timeout: float = 20,
) -> tuple[int, dict[str, str], dict[str, Any]]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {
        "Accept": "application/json, text/event-stream",
        "User-Agent": "mezan-mcp-deployment-verifier/1.0",
        "X-Request-ID": f"deployment-check-{uuid.uuid4()}",
    }
    if payload is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        response = urllib.request.urlopen(
            request,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    except urllib.error.HTTPError as exc:
        response = exc
    status = int(response.status)
    response_headers = {key.lower(): value for key, value in response.headers.items()}
    raw = response.read(1024 * 1024)
    if not raw:
        parsed_body: dict[str, Any] = {}
    else:
        try:
            parsed_body = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise VerificationError(
                f"{method} {urllib.parse.urlparse(url).path} returned non-JSON HTTP {status}"
            ) from exc
    return status, response_headers, parsed_body


def _validated_https_url(value: Any, label: str) -> str:
    url = str(value or "").strip()
    parsed = urllib.parse.urlparse(url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise VerificationError(f"{label} must be a canonical HTTPS URL")
    return url


def _authorization_server_metadata(issuer: str) -> dict[str, Any]:
    issuer = _validated_https_url(issuer, "Authorization server issuer")
    parsed = urllib.parse.urlparse(issuer)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    issuer_path = parsed.path.rstrip("/")
    candidates = [
        f"{issuer.rstrip('/')}/.well-known/openid-configuration",
        f"{origin}/.well-known/oauth-authorization-server{issuer_path}",
        f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server",
    ]
    attempted: set[str] = set()
    for discovery_url in candidates:
        if discovery_url in attempted:
            continue
        attempted.add(discovery_url)
        try:
            status, _headers, document = _request_json(discovery_url)
        except VerificationError:
            continue
        if status == 200 and document:
            return document
    raise VerificationError(
        "Authorization server did not publish OIDC or OAuth discovery metadata"
    )


def _verify_authorization_server_contract(issuer: str) -> None:
    metadata = _authorization_server_metadata(issuer)
    if metadata.get("issuer") != issuer:
        raise VerificationError(
            "Authorization-server discovery issuer does not exactly match the resource metadata"
        )
    for key, label in (
        ("authorization_endpoint", "Authorization endpoint"),
        ("token_endpoint", "Token endpoint"),
        ("registration_endpoint", "DCR registration endpoint"),
    ):
        _validated_https_url(metadata.get(key), label)
    if "code" not in (metadata.get("response_types_supported") or []):
        raise VerificationError("Authorization server does not advertise code responses")
    if "authorization_code" not in (metadata.get("grant_types_supported") or []):
        raise VerificationError(
            "Authorization server does not advertise the authorization_code grant"
        )
    if "S256" not in (metadata.get("code_challenge_methods_supported") or []):
        raise VerificationError("Authorization server does not require/support PKCE S256")
    if "none" not in (metadata.get("token_endpoint_auth_methods_supported") or []):
        raise VerificationError(
            "Authorization server does not advertise public OAuth clients"
        )


def _rpc(
    endpoint: str,
    token: str,
    request_id: int,
    method: str,
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    status, _headers, body = _request_json(
        endpoint,
        method="POST",
        token=token,
        payload={
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params or {},
        },
    )
    if status != 200:
        raise VerificationError(f"MCP {method} returned HTTP {status}")
    if body.get("error"):
        code = body["error"].get("code", "unknown")
        raise VerificationError(f"MCP {method} returned JSON-RPC error {code}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise VerificationError(f"MCP {method} returned an invalid result")
    return result


def _call_tool(
    endpoint: str,
    token: str,
    request_id: int,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = _rpc(
        endpoint,
        token,
        request_id,
        "tools/call",
        {"name": name, "arguments": arguments or {}},
    )
    if result.get("isError") is not False:
        raise VerificationError(f"Read-only tool {name} reported an error")
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        raise VerificationError(f"Read-only tool {name} omitted structuredContent")
    return structured


def verify(endpoint: str, token: str, order_number: str = "") -> None:
    endpoint = _canonical_endpoint(endpoint)
    parsed = urllib.parse.urlparse(endpoint)

    initialized = _rpc(endpoint, "", 1, "initialize")
    if initialized.get("serverInfo", {}).get("name") != "Mezan MCP Gateway":
        raise VerificationError("Unexpected MCP server identity")
    print("PASS public MCP initialize")

    discovered = _rpc(endpoint, "", 2, "tools/list")
    tools = discovered.get("tools") or []
    names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
    if names != EXPECTED_TOOLS or len(tools) != len(EXPECTED_TOOLS):
        raise VerificationError("MCP tool discovery does not match the phase-one allowlist")
    for tool in tools:
        annotations = tool.get("annotations") or {}
        if annotations.get("readOnlyHint") is not True or annotations.get("destructiveHint") is not False:
            raise VerificationError(f"Tool {tool.get('name')} is not marked read-only")
        security = tool.get("securitySchemes") or []
        if security != [{"type": "oauth2", "scopes": ["mezan:read"]}]:
            raise VerificationError(f"Tool {tool.get('name')} has an unsafe OAuth policy")
        if (tool.get("_meta") or {}).get("securitySchemes") != security:
            raise VerificationError(
                f"Tool {tool.get('name')} omitted compatible OAuth metadata"
            )
        if tool.get("outputSchema") != {
            "type": "object",
            "additionalProperties": True,
        }:
            raise VerificationError(f"Tool {tool.get('name')} omitted outputSchema")
    print("PASS exact eight-tool public read-only discovery")

    unauthenticated_call = _rpc(
        endpoint,
        "",
        3,
        "tools/call",
        {"name": "mezan_health", "arguments": {}},
    )
    challenges = (
        (unauthenticated_call.get("_meta") or {}).get("mcp/www_authenticate")
        or []
    )
    if unauthenticated_call.get("isError") is not True or len(challenges) != 1:
        raise VerificationError(
            "Unauthenticated tools/call did not return one MCP OAuth challenge"
        )
    challenge = challenges[0]
    if not isinstance(challenge, str) or any(
        part not in challenge
        for part in (
            "resource_metadata=",
            'scope="mezan:read"',
            "error=",
            "error_description=",
        )
    ):
        raise VerificationError("MCP OAuth challenge is incomplete")
    print("PASS tool-level OAuth challenge")

    match = re.search(r'resource_metadata="([^"]+)"', challenge)
    if not match:
        raise VerificationError("OAuth challenge omitted the quoted metadata URL")
    metadata_url = match.group(1)
    metadata_parsed = urllib.parse.urlparse(metadata_url)
    if (
        metadata_parsed.scheme != "https"
        or metadata_parsed.netloc != parsed.netloc
        or metadata_parsed.username
        or metadata_parsed.password
        or metadata_parsed.query
        or metadata_parsed.fragment
    ):
        raise VerificationError("OAuth challenge advertised an unsafe metadata URL")

    status, _headers, metadata = _request_json(metadata_url)
    if status != 200:
        raise VerificationError(f"Protected-resource metadata returned HTTP {status}")
    if metadata.get("resource") != endpoint:
        raise VerificationError("Protected-resource metadata advertises the wrong resource")
    authorization_servers = metadata.get("authorization_servers") or []
    if len(authorization_servers) != 1:
        raise VerificationError("Protected-resource metadata has no authorization server")
    if metadata.get("scopes_supported") != ["mezan:read"]:
        raise VerificationError("Protected-resource metadata must request only mezan:read")
    print("PASS protected-resource metadata")

    api_metadata_url = (
        f"{parsed.scheme}://{parsed.netloc}/api/.well-known/oauth-protected-resource"
    )
    api_status, api_headers, api_metadata = _request_json(api_metadata_url)
    if api_status != 200:
        raise VerificationError(
            f"API protected-resource metadata returned HTTP {api_status}"
        )
    if "application/json" not in api_headers.get("content-type", "").lower():
        raise VerificationError(
            "API protected-resource metadata did not return JSON content"
        )
    if api_metadata != metadata:
        raise VerificationError(
            "API and advertised protected-resource metadata differ"
        )
    print("PASS API protected-resource metadata alias")

    canonical_metadata_url = (
        f"{parsed.scheme}://{parsed.netloc}/.well-known/oauth-protected-resource"
    )
    root_status, root_headers, root_metadata = _request_json(canonical_metadata_url)
    if root_status != 200:
        raise VerificationError(
            f"Canonical protected-resource metadata returned HTTP {root_status}"
        )
    if "application/json" not in root_headers.get("content-type", "").lower():
        raise VerificationError(
            "Canonical protected-resource metadata did not return JSON content"
        )
    if root_metadata != metadata:
        raise VerificationError(
            "Canonical and advertised protected-resource metadata differ"
        )
    print("PASS canonical root protected-resource metadata")

    _verify_authorization_server_contract(str(authorization_servers[0]))
    print("PASS OAuth DCR, authorization code, and PKCE S256 discovery")

    if not token:
        raise VerificationError(
            "Set MEZAN_MCP_BEARER_TOKEN in the secure deployment runner; never pass it on the command line"
        )

    health = _call_tool(endpoint, token, 4, "mezan_health")
    if health.get("access") != "read-only" or health.get("qoyod_network_access") is not False:
        raise VerificationError("Health response does not confirm the read-only boundary")
    print("PASS mezan_health read-only boundary")

    if order_number:
        arguments = {"order_number": order_number}
        _call_tool(endpoint, token, 5, "mezan_get_order", arguments)
        _call_tool(endpoint, token, 6, "mezan_compare_order_with_salla", arguments)
        _call_tool(endpoint, token, 7, "mezan_get_error_trace", arguments)
        reconciliation = _call_tool(
            endpoint, token, 8, "mezan_qoyod_reconciliation", arguments
        )
        if reconciliation.get("qoyod_access") != (
            "local read-only reconciliation; no Qoyod network call"
        ):
            raise VerificationError("Qoyod reconciliation did not confirm local-only access")
        print("PASS order, Salla comparison, trace, and local-only Qoyod reconciliation")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify a deployed read-only Mezan MCP gateway."
    )
    parser.add_argument("endpoint", help="Deployment HTTPS URL ending in /api/ai/mcp")
    parser.add_argument(
        "--order-number",
        default="",
        help="Optional non-sensitive order number for the full acceptance gate",
    )
    args = parser.parse_args()
    try:
        verify(
            args.endpoint,
            os.environ.get("MEZAN_MCP_BEARER_TOKEN", "").strip(),
            args.order_number.strip(),
        )
    except VerificationError as exc:
        print(f"FAIL {exc}", file=sys.stderr)
        return 1
    print("PASS Mezan MCP deployment acceptance checks completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
