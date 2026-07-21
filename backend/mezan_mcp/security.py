"""Security boundary for the Mezan MCP resource server.

This module deliberately implements only the OAuth protected-resource side.
Authorization, login, consent and token issuance belong to an external OAuth
2.1 identity provider.  The gateway never accepts Mezan's browser session JWT
and never receives a static API key from ChatGPT.
"""
from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import os
import re
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Iterable, Mapping
from urllib.parse import urlparse

import jwt
from fastapi import HTTPException, Request


log = logging.getLogger("mezan.mcp.audit")

REQUIRED_SCOPE_DEFAULT = "mezan:read"
TENANT_CLAIM_DEFAULT = "mezan_tenant_id"
ALLOWED_JWT_ALGORITHMS = ("RS256", "ES256")
SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "database_url",
    "mongo_url",
    "client_secret",
    "password",
    "secret",
    "token",
    "phone",
    "mobile",
    "email",
    "address",
    "shipping_address",
    "billing_address",
    "latitude",
    "longitude",
}

_TEXT_REDACTIONS = (
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"), "[jwt redacted]"),
    (re.compile(r"(?i)\b(?:mongodb(?:\+srv)?|postgres(?:ql)?|mysql|redis)://[^\s]+"), "[database url redacted]"),
    (re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b"), "[email redacted]"),
    # Match Saudi mobile numbers only, so order references are not hidden.
    (re.compile(r"(?<!\d)(?:\+?966|00966)?0?5\d{8}(?!\d)"), "[phone redacted]"),
    (
        re.compile(
            r"(?i)(access[_-]?token|refresh[_-]?token|client[_-]?secret|authorization|password)"
            r"\s*[:=]\s*[\"']?[^\s,;\"'}]+"
        ),
        r"\1=[redacted]",
    ),
)


def _redact_string(value: str) -> str:
    clean = value
    for pattern, replacement in _TEXT_REDACTIONS:
        clean = pattern.sub(replacement, clean)
    return clean


def _audit_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


class WriteBlockedError(RuntimeError):
    """Raised when code crosses the production read-only boundary."""


class ReadOnlyCollection:
    """Allow only Mongo read operations.

    The explicit mutation methods are present only so an accidental future
    call fails loudly in tests and at runtime instead of reaching Motor.
    """

    def __init__(self, collection: Any):
        self._collection = collection

    def find(self, *args: Any, **kwargs: Any) -> Any:
        return self._collection.find(*args, **kwargs)

    async def find_one(self, *args: Any, **kwargs: Any) -> Any:
        return await self._collection.find_one(*args, **kwargs)

    async def count_documents(self, *args: Any, **kwargs: Any) -> int:
        return await self._collection.count_documents(*args, **kwargs)

    def aggregate(self, *args: Any, **kwargs: Any) -> Any:
        return self._collection.aggregate(*args, **kwargs)

    async def distinct(self, *args: Any, **kwargs: Any) -> list[Any]:
        return await self._collection.distinct(*args, **kwargs)

    @staticmethod
    def _blocked(*_args: Any, **_kwargs: Any) -> None:
        raise WriteBlockedError("Production MCP database access is read-only")

    insert_one = _blocked
    insert_many = _blocked
    update_one = _blocked
    update_many = _blocked
    replace_one = _blocked
    delete_one = _blocked
    delete_many = _blocked
    find_one_and_update = _blocked
    find_one_and_delete = _blocked
    bulk_write = _blocked
    create_index = _blocked
    drop = _blocked


class ReadOnlyDatabase:
    """Small database facade exposing allowlisted collections only."""

    ALLOWED_COLLECTIONS = frozenset({
        "unified_orders",
        "salla_integrations",
        "salla_sync_logs",
        "integration_inbox",
        "qoyod_invoices",
        "import_jobs",
        "webhook_parse_failures",
    })

    def __init__(self, db: Any):
        self._db = db

    def __getattr__(self, name: str) -> ReadOnlyCollection:
        if name not in self.ALLOWED_COLLECTIONS:
            raise AttributeError(f"MCP collection is not allowlisted: {name}")
        return ReadOnlyCollection(getattr(self._db, name))

    async def ping(self) -> bool:
        await self._db.command({"ping": 1})
        return True


class ReadOnlyHttpClient:
    """HTTP client guard that can only perform GET requests."""

    def __init__(self, client: Any, *, allowed_hosts: Iterable[str] = ("api.salla.dev",)):
        self._client = client
        self._allowed_hosts = frozenset(
            str(host).strip().lower() for host in allowed_hosts if str(host).strip()
        )

    def _validated_url(self, url: Any) -> str:
        validated = validate_public_https_url(str(url))
        host = (urlparse(validated).hostname or "").lower()
        if host not in self._allowed_hosts:
            raise WriteBlockedError("Outbound MCP host is not allowlisted")
        return validated

    async def get(self, url: Any, *args: Any, **kwargs: Any) -> Any:
        return await self._client.get(self._validated_url(url), *args, **kwargs)

    async def request(self, method: str, url: Any, *args: Any, **kwargs: Any) -> Any:
        if str(method).upper() != "GET":
            raise WriteBlockedError("Salla MCP access permits GET only")
        return await self._client.request(
            "GET", self._validated_url(url), *args, **kwargs
        )

    async def post(self, *_args: Any, **_kwargs: Any) -> None:
        raise WriteBlockedError("Salla MCP access permits GET only")

    put = post
    patch = post
    delete = post


def _clean_value(value: Any, *, depth: int = 0) -> Any:
    if depth > 7:
        return "[truncated]"
    if isinstance(value, Mapping):
        clean: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            normalized = key.lower().replace("-", "_")
            if normalized in SENSITIVE_KEYS or any(
                marker in normalized
                for marker in ("secret", "password", "access_token", "refresh_token")
            ):
                continue
            clean[key] = _clean_value(raw_value, depth=depth + 1)
        return clean
    if isinstance(value, (list, tuple, set)):
        return [_clean_value(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, bytes):
        return "[binary redacted]"
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except Exception:
            return str(value)
    if isinstance(value, str):
        return _redact_string(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)


def sanitize_output(value: Any) -> Any:
    """Recursively remove secrets and unnecessary personal information."""
    return _clean_value(value)


def audit_tool_call(
    *,
    request_id: str,
    subject: str,
    tenant_id: str,
    tool: str,
    outcome: str,
    duration_ms: int,
) -> None:
    """Emit a structured audit event without arguments or personal data."""
    log.info(
        "mcp_tool_call %s",
        json.dumps(
            {
                "request_id": _redact_string(str(request_id))[:128],
                "subject_hash": _audit_digest(subject),
                "tenant_hash": _audit_digest(tenant_id),
                "tool": _redact_string(str(tool))[:128],
                "outcome": _redact_string(str(outcome))[:64],
                "duration_ms": int(duration_ms),
            },
            ensure_ascii=True,
            sort_keys=True,
        ),
    )


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str
    scopes: frozenset[str]


class OAuthConfigError(RuntimeError):
    pass


def public_base_url() -> str:
    return (
        os.environ.get("MEZAN_MCP_PUBLIC_BASE_URL")
        or os.environ.get("PUBLIC_BASE_URL")
        or "https://mezansalla.com"
    ).rstrip("/")


def resource_url() -> str:
    return (
        os.environ.get("MEZAN_MCP_RESOURCE_URL")
        or f"{public_base_url()}/api/ai/mcp"
    ).rstrip("/")


def resource_metadata_url() -> str:
    return f"{public_base_url()}/.well-known/oauth-protected-resource"


def protected_resource_metadata() -> dict[str, Any]:
    issuer = (os.environ.get("MEZAN_MCP_OAUTH_ISSUER") or "").rstrip("/")
    resource = _oauth_https_url("resource", resource_url())
    data: dict[str, Any] = {
        "resource": resource,
        "scopes_supported": [
            os.environ.get("MEZAN_MCP_REQUIRED_SCOPE", REQUIRED_SCOPE_DEFAULT)
        ],
        "bearer_methods_supported": ["header"],
    }
    if issuer:
        data["authorization_servers"] = [_oauth_https_url("issuer", issuer)]
    return data


def _oauth_config() -> tuple[str, str, str, str, str]:
    issuer = (os.environ.get("MEZAN_MCP_OAUTH_ISSUER") or "").rstrip("/")
    audience = os.environ.get("MEZAN_MCP_OAUTH_AUDIENCE") or resource_url()
    jwks_url = os.environ.get("MEZAN_MCP_OAUTH_JWKS_URL") or (
        f"{issuer}/.well-known/jwks.json" if issuer else ""
    )
    required_scope = os.environ.get("MEZAN_MCP_REQUIRED_SCOPE", REQUIRED_SCOPE_DEFAULT)
    tenant_claim = os.environ.get("MEZAN_MCP_TENANT_CLAIM", TENANT_CLAIM_DEFAULT)
    if not issuer or not audience or not jwks_url:
        raise OAuthConfigError(
            "MCP OAuth resource server is not configured; set issuer, audience and JWKS URL"
        )
    issuer = _oauth_https_url("issuer", issuer)
    audience = _oauth_https_url("audience", audience)
    jwks_url = _oauth_https_url("JWKS URL", jwks_url)
    return issuer, audience, jwks_url, required_scope, tenant_claim


def _oauth_https_url(label: str, value: str) -> str:
    """Fail closed when OAuth metadata points outside a canonical HTTPS URL."""
    try:
        validated = validate_public_https_url(value)
    except ValueError as exc:
        raise OAuthConfigError(f"MCP OAuth {label} must be a public HTTPS URL") from exc
    parsed = urlparse(validated)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise OAuthConfigError(
            f"MCP OAuth {label} must not contain credentials, a query, or a fragment"
        )
    return validated.rstrip("/")


def _scopes(claims: Mapping[str, Any]) -> frozenset[str]:
    value = claims.get("scope", claims.get("scp", ""))
    if isinstance(value, str):
        return frozenset(part for part in value.split() if part)
    if isinstance(value, Iterable):
        return frozenset(str(part) for part in value if str(part))
    return frozenset()


@lru_cache(maxsize=8)
def _jwks_client(jwks_url: str) -> Any:
    """Reuse the issuer's JWKS cache instead of fetching it for every call."""
    return jwt.PyJWKClient(jwks_url, cache_keys=True)


def _decode_token_sync(token: str) -> Principal:
    issuer, audience, jwks_url, required_scope, tenant_claim = _oauth_config()
    client = _jwks_client(jwks_url)
    signing_key = client.get_signing_key_from_jwt(token)
    claims = jwt.decode(
        token,
        signing_key.key,
        algorithms=list(ALLOWED_JWT_ALGORITHMS),
        audience=audience,
        issuer=issuer,
        options={"require": ["exp", "iat", "sub"]},
    )
    scopes = _scopes(claims)
    if required_scope not in scopes:
        raise jwt.InvalidTokenError("required MCP scope is missing")
    tenant_id = str(claims.get(tenant_claim) or "").strip()
    if not tenant_id:
        raise jwt.InvalidTokenError("tenant claim is missing")
    return Principal(
        subject=str(claims["sub"]),
        tenant_id=tenant_id,
        scopes=scopes,
    )


def _unauthorized(detail: str = "OAuth bearer token required") -> HTTPException:
    return HTTPException(
        status_code=401,
        detail=detail,
        headers={
            "WWW-Authenticate": (
                f'Bearer resource_metadata="{resource_metadata_url()}", '
                f'scope="{os.environ.get("MEZAN_MCP_REQUIRED_SCOPE", REQUIRED_SCOPE_DEFAULT)}"'
            ),
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
        },
    )


async def authenticate_request(request: Request) -> Principal:
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise _unauthorized()
    try:
        return await asyncio.to_thread(_decode_token_sync, token.strip())
    except OAuthConfigError as exc:
        raise HTTPException(
            status_code=503,
            detail=str(exc),
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        ) from exc
    except Exception as exc:
        log.warning("mcp_oauth_rejected type=%s", type(exc).__name__)
        raise _unauthorized("Invalid or expired OAuth bearer token") from exc


class SubjectRateLimiter:
    """Per-process sliding-window limit keyed by OAuth subject.

    Deployments with several workers should additionally apply a gateway/WAF
    rate limit. This local guard is intentionally dependency-free.
    """

    def __init__(self, limit: int = 60, window_seconds: int = 60):
        self.limit = max(1, limit)
        self.window_seconds = max(1, window_seconds)
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def check(self, subject: str) -> None:
        now = time.monotonic()
        async with self._lock:
            hits = self._hits[subject]
            while hits and hits[0] <= now - self.window_seconds:
                hits.popleft()
            if len(hits) >= self.limit:
                raise HTTPException(
                    status_code=429,
                    detail="MCP rate limit exceeded",
                    headers={
                        "Retry-After": str(self.window_seconds),
                        "Cache-Control": "no-store",
                    },
                )
            hits.append(now)


def validate_public_https_url(url: str) -> str:
    """Reject non-HTTPS/private destinations before an outbound Salla GET."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise ValueError("Outbound MCP URL must use HTTPS")
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return url
    if not address.is_global:
        raise ValueError("Outbound MCP URL must not target a private address")
    return url
