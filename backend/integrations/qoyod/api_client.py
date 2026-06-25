"""Qoyod REST API client — thin httpx wrapper.

Design rules (ADR-001):
    #10 Idempotency  — every POST may pass an Idempotency-Key header
                       so retries don't double-create invoices.
    #14 Secrets       — the API key is held only during the call; never
                       logged. `__repr__` and any exception messages
                       redact it.
    #13 Versioning    — the base URL & version come from env (`QOYOD_API_BASE`)
                       so we can target Qoyod v2 today, v3 later, without
                       code edits.

The client is intentionally minimal — no retry policy here. Retry is
the orchestrator's responsibility (so the orchestrator can decide
whether to retry the WHOLE pipeline stage vs. only the HTTP call).
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx


# Version identifier sent with every Qoyod request (Day-1 review).
# Bump whenever the Qoyod payload contract changes — helps trace the
# exact Mezan build that produced a payload in case of incident.
MEZAN_VERSION = os.environ.get("MEZAN_VERSION", "1.0.0-qoyod-mvp")


class QoyodAPIError(Exception):
    """Raised for any non-2xx response. Carries the parsed body so the
    orchestrator can classify it (transient vs. permanent vs. config)."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        response_excerpt: str = "",
        endpoint: str = "",
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.response_excerpt = response_excerpt[:500]
        self.endpoint = endpoint
        super().__init__(f"Qoyod API {status_code} on {endpoint}: {code}")

    def to_log_dict(self) -> dict[str, Any]:
        # NEVER include the api_key. Safe to persist into qoyod_invoices.last_error.
        return {
            "code":                  self.code,
            "message":               self.message,
            "status_code":           self.status_code,
            "endpoint":              self.endpoint,
            "qoyod_response_excerpt": self.response_excerpt,
        }


def _classify(status_code: int, body: Any) -> tuple[str, str]:
    """Map HTTP status → (code, message) used by the rest of the system."""
    if status_code == 401:
        return ("qoyod_unauthorized", "مفتاح API غير صالح أو منتهي")
    if status_code == 403:
        return ("qoyod_forbidden", "ليس لديك صلاحية لهذه العملية في قيود")
    if status_code == 404:
        return ("qoyod_not_found", "المورد غير موجود في قيود")
    if status_code == 422:
        # Qoyod returns validation details in body
        return ("qoyod_validation_error",
                _short(body, fallback="فشل التحقق من البيانات في قيود"))
    if status_code == 429:
        return ("qoyod_rate_limited", "تجاوزنا حد الطلبات المسموح من قيود")
    if 500 <= status_code < 600:
        return ("qoyod_server_error", "قيود ترجع خطأ مؤقت — سيُعاد المحاولة")
    return ("qoyod_http_error", f"رمز HTTP غير متوقع: {status_code}")


def _short(body: Any, fallback: str = "") -> str:
    if isinstance(body, dict):
        for k in ("message", "error", "errors", "detail"):
            v = body.get(k)
            if v:
                return str(v)[:200]
    if isinstance(body, str):
        return body[:200]
    return fallback


class QoyodAPIClient:
    """Per-call construction is cheap; kept stateless so the
    orchestrator can swap api_keys (testing / retry) without leaking
    state between requests."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        timeout: float = 15.0,
    ):
        if not api_key:
            raise ValueError("Qoyod API key is required")
        self._api_key = api_key
        self._base_url = (base_url or os.environ.get("QOYOD_API_BASE", "")).rstrip("/")
        if not self._base_url:
            raise RuntimeError(
                "QOYOD_API_BASE is not set in backend/.env")
        self._timeout = timeout

    # Redact secrets in any debug print.
    def __repr__(self) -> str:  # pragma: no cover
        return f"QoyodAPIClient(base_url={self._base_url!r}, api_key=***)"

    def _headers(self, idempotency_key: Optional[str] = None) -> dict:
        # `X-Mezan-Version` is a diagnostic header per Day-1 review.
        # Lets Qoyod-side support match a request to a Mezan release
        # without exposing internal details.
        h = {
            "API-KEY":          self._api_key,
            "Accept":           "application/json",
            "Content-Type":     "application/json",
            "User-Agent":       f"mezan-qoyod/{MEZAN_VERSION}",
            "X-Mezan-Version":  MEZAN_VERSION,
            "X-Mezan-Module":   "qoyod-mvp",
        }
        if idempotency_key:
            h["Idempotency-Key"] = idempotency_key
        return h

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout) as http:
            try:
                resp = await http.request(
                    method, url,
                    headers=self._headers(idempotency_key),
                    json=json_body, params=params,
                )
            except httpx.TimeoutException as exc:
                raise QoyodAPIError(
                    status_code=0, code="qoyod_timeout",
                    message="انتهت مهلة الاتصال بقيود",
                    endpoint=f"{method} {path}",
                ) from exc
            except httpx.RequestError as exc:
                raise QoyodAPIError(
                    status_code=0, code="qoyod_network_error",
                    message=f"تعذّر الاتصال بقيود: {type(exc).__name__}",
                    endpoint=f"{method} {path}",
                ) from exc

        # Try to parse JSON either way for richer error info.
        try:
            body = resp.json()
        except Exception:
            body = resp.text

        if not 200 <= resp.status_code < 300:
            code, message = _classify(resp.status_code, body)
            raise QoyodAPIError(
                status_code=resp.status_code,
                code=code, message=message,
                response_excerpt=str(body)[:500],
                endpoint=f"{method} {path}",
            )
        return body

    # ── Public surface — only what the MVP pipeline needs ───────────
    async def me(self) -> dict:
        """Used by /test-connection to verify the API key."""
        return await self._request("GET", "/me")

    async def list_branches(self) -> Any:
        return await self._request("GET", "/branches")

    async def list_accounts(self, *, kind: Optional[str] = None) -> Any:
        params = {"kind": kind} if kind else None
        return await self._request("GET", "/accounts", params=params)

    async def list_taxes(self) -> Any:
        return await self._request("GET", "/taxes")

    async def create_contact(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/contacts", json_body=payload, idempotency_key=idem)

    async def create_product(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/products", json_body=payload, idempotency_key=idem)

    async def create_invoice(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/invoices", json_body=payload, idempotency_key=idem)

    async def create_receipt(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/receipts", json_body=payload, idempotency_key=idem)
