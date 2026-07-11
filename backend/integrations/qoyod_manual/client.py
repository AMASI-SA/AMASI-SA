"""Minimal Qoyod HTTP client for Plan-B Manual Send.

Why not reuse `integrations.qoyod.api_client.QoyodAPIClient`?
────────────────────────────────────────────────────────────
That client is fenced by Rev32.1 pre-flight guards and the Iter-294
Global Write Lock which are FROZEN under Plan B (user directive
2026-02: "لا تعديل فوق الحراس الحالية"). The manual path must be
completely isolated — one HTTP client, one code path, zero guards
other than the 4 explicit ones enforced in `send.py`.

This client is intentionally minimal:
    • Direct httpx wrapper — no rev32/rev48 hooks.
    • No hidden idempotency injection: the caller always supplies
      the Idempotency-Key header explicitly.
    • Raises `ManualQoyodError` with the raw status + body excerpt so
      the operator sees the real قيود response.
"""
from __future__ import annotations

import os
from typing import Any, Optional

import httpx


MEZAN_MANUAL_VERSION = "plan-b-manual-1.0"


class ManualQoyodError(Exception):
    """Raised for any non-2xx response from Qoyod."""

    def __init__(
        self,
        *,
        status_code: int,
        endpoint: str,
        response_excerpt: str,
        request_body: Any = None,
    ):
        self.status_code = status_code
        self.endpoint = endpoint
        self.response_excerpt = response_excerpt[:800]
        self.request_body = request_body
        super().__init__(
            f"Qoyod {status_code} on {endpoint}: {response_excerpt[:200]}")

    def to_dict(self) -> dict:
        return {
            "status_code":      self.status_code,
            "endpoint":         self.endpoint,
            "response_excerpt": self.response_excerpt,
            "request_body":     self.request_body,
        }


class ManualQoyodClient:
    """Thin httpx wrapper. Stateless — one instance per manual send."""

    def __init__(self, *, api_key: str, base_url: Optional[str] = None,
                 timeout: float = 25.0):
        if not api_key:
            raise ValueError("Qoyod API key is required")
        self._api_key = api_key
        self._base_url = (base_url
                          or os.environ.get("QOYOD_API_BASE", "")).rstrip("/")
        if not self._base_url:
            raise RuntimeError("QOYOD_API_BASE not set")
        self._timeout = timeout

    def __repr__(self) -> str:  # pragma: no cover
        return f"ManualQoyodClient(base={self._base_url!r}, key=***)"

    def _headers(self, *, idem: Optional[str] = None) -> dict:
        h = {
            "API-KEY":       self._api_key,
            "Accept":        "application/json",
            "Content-Type":  "application/json",
            "User-Agent":    f"mezan-manual/{MEZAN_MANUAL_VERSION}",
            "X-Mezan-Module": "qoyod-manual",
        }
        if idem:
            h["Idempotency-Key"] = idem
        return h

    async def _request(
        self, method: str, path: str, *,
        json_body: Optional[dict] = None,
        params: Optional[dict] = None,
        idem: Optional[str] = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        async with httpx.AsyncClient(timeout=self._timeout,
                                     follow_redirects=True) as http:
            resp = await http.request(
                method, url,
                headers=self._headers(idem=idem),
                json=json_body, params=params,
            )
        try:
            body = resp.json()
        except Exception:
            body = resp.text
        if not (200 <= resp.status_code < 300):
            raise ManualQoyodError(
                status_code=resp.status_code,
                endpoint=f"{method} {path}",
                response_excerpt=str(body),
                request_body=json_body,
            )
        return body

    # ── Read endpoints ──────────────────────────────────────────────
    async def find_customers_by_phone(self, phone: str,
                                       *, limit: int = 5) -> list[dict]:
        """Best-effort search by phone. Qoyod honours Ransack-style
        `q[phone_eq]` on the legacy domain. Returns the list of matches
        or `[]`."""
        if not phone:
            return []
        for params in (
            {"q[phone_eq]": phone, "limit": limit},
            {"q[mobile_eq]": phone, "limit": limit},
            {"phone": phone, "limit": limit},
        ):
            try:
                body = await self._request("GET", "/customers", params=params)
            except ManualQoyodError:
                continue
            rows = []
            if isinstance(body, dict):
                rows = body.get("customers") or body.get("data") or []
            elif isinstance(body, list):
                rows = body
            matches = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                rphone = str(r.get("phone") or r.get("mobile") or "").strip()
                if rphone and rphone == phone:
                    matches.append(r)
            if matches:
                return matches
        return []

    async def find_customers_by_email(self, email: str,
                                       *, limit: int = 5) -> list[dict]:
        if not email:
            return []
        try:
            body = await self._request(
                "GET", "/customers",
                params={"q[email_eq]": email, "limit": limit})
        except ManualQoyodError:
            return []
        rows = []
        if isinstance(body, dict):
            rows = body.get("customers") or body.get("data") or []
        elif isinstance(body, list):
            rows = body
        return [r for r in rows if isinstance(r, dict)
                and str(r.get("email") or "").lower() == email.lower()]

    async def find_product_by_sku(self, sku: str) -> Optional[dict]:
        """Return the first product whose SKU exactly matches. Returns
        `None` if not found. Does NOT raise on duplicates — the caller
        just uses the first match (Plan B rule)."""
        if not sku:
            return None
        for params in (
            {"q[sku_eq]": sku, "limit": 5},
            {"sku": sku, "limit": 5},
        ):
            try:
                body = await self._request(
                    "GET", "/products", params=params)
            except ManualQoyodError:
                continue
            rows = []
            if isinstance(body, dict):
                rows = body.get("products") or body.get("data") or []
            elif isinstance(body, list):
                rows = body
            for r in rows:
                if not isinstance(r, dict):
                    continue
                rsku = str(r.get("sku") or r.get("reference") or "").strip()
                if rsku == sku:
                    return r
        return None

    async def get_invoice(self, invoice_id: int) -> dict:
        """Read one invoice from Qoyod after creation.

        This is the accounting source of truth for the amount that Qoyod
        actually persisted after applying its own line/tax rounding.
        """
        body = await self._request("GET", f"/invoices/{int(invoice_id)}")
        if isinstance(body, dict):
            node = body.get("invoice") or body.get("data") or body
            return node if isinstance(node, dict) else {}
        return {}


    async def find_invoice_by_reference(self, reference: str
                                         ) -> Optional[dict]:
        """Return the first invoice whose reference matches — used by
        the duplicate-check safety net (guard #1 supplement)."""
        if not reference:
            return None
        for params in (
            {"q[reference_eq]": reference, "limit": 3},
            {"reference": reference, "limit": 3},
        ):
            try:
                body = await self._request(
                    "GET", "/invoices", params=params)
            except ManualQoyodError:
                continue
            rows = []
            if isinstance(body, dict):
                rows = body.get("invoices") or body.get("data") or []
            elif isinstance(body, list):
                rows = body
            for r in rows:
                if not isinstance(r, dict):
                    continue
                if str(r.get("reference") or "") == str(reference):
                    return r
        return None

    # ── Write endpoints ─────────────────────────────────────────────
    async def create_customer(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/customers", json_body=payload, idem=idem)

    async def create_product(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/products", json_body=payload, idem=idem)

    async def create_invoice(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/invoices", json_body=payload, idem=idem)

    async def create_invoice_payment(self, payload: dict, *,
                                     idem: str) -> Any:
        return await self._request(
            "POST", "/invoice_payments", json_body=payload, idem=idem)
