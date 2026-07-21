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

    unauth_status, unauth_headers, _body = _request_json(
        endpoint,
        method="POST",
        payload={"jsonrpc": "2.0", "id": 0, "method": "tools/list", "params": {}},
    )
    challenge = unauth_headers.get("www-authenticate", "")
    if unauth_status != 401 or "resource_metadata=" not in challenge:
        raise VerificationError("Unauthenticated MCP request did not return OAuth discovery challenge")
    print("PASS OAuth challenge")

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
    if not metadata.get("authorization_servers"):
        raise VerificationError("Protected-resource metadata has no authorization server")
    if metadata.get("scopes_supported") != ["mezan:read"]:
        raise VerificationError("Protected-resource metadata must request only mezan:read")
    print("PASS protected-resource metadata")

    if not token:
        raise VerificationError(
            "Set MEZAN_MCP_BEARER_TOKEN in the secure deployment runner; never pass it on the command line"
        )

    initialized = _rpc(endpoint, token, 1, "initialize")
    if initialized.get("serverInfo", {}).get("name") != "Mezan MCP Gateway":
        raise VerificationError("Unexpected MCP server identity")
    print("PASS MCP initialize")

    discovered = _rpc(endpoint, token, 2, "tools/list")
    tools = discovered.get("tools") or []
    names = {tool.get("name") for tool in tools if isinstance(tool, dict)}
    if names != EXPECTED_TOOLS or len(tools) != len(EXPECTED_TOOLS):
        raise VerificationError("MCP tool discovery does not match the phase-one allowlist")
    for tool in tools:
        annotations = tool.get("annotations") or {}
        if annotations.get("readOnlyHint") is not True or annotations.get("destructiveHint") is not False:
            raise VerificationError(f"Tool {tool.get('name')} is not marked read-only")
    print("PASS exact eight-tool read-only discovery")

    health = _call_tool(endpoint, token, 3, "mezan_health")
    if health.get("access") != "read-only" or health.get("qoyod_network_access") is not False:
        raise VerificationError("Health response does not confirm the read-only boundary")
    print("PASS mezan_health read-only boundary")

    if order_number:
        arguments = {"order_number": order_number}
        _call_tool(endpoint, token, 4, "mezan_get_order", arguments)
        _call_tool(endpoint, token, 5, "mezan_compare_order_with_salla", arguments)
        _call_tool(endpoint, token, 6, "mezan_get_error_trace", arguments)
        reconciliation = _call_tool(
            endpoint, token, 7, "mezan_qoyod_reconciliation", arguments
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
