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
        request_body_json: Any = None,
    ):
        self.status_code = status_code
        self.code = code
        self.message = message
        self.response_excerpt = response_excerpt[:500]
        self.endpoint = endpoint
        # The EXACT JSON body httpx serialized and sent. Persisted so
        # the operator can post-mortem "did we send the right field?"
        # questions directly from MongoDB instead of guessing.
        self.request_body_json = request_body_json
        super().__init__(f"Qoyod API {status_code} on {endpoint}: {code}")

    def to_log_dict(self) -> dict[str, Any]:
        # NEVER include the api_key. Safe to persist into qoyod_invoices.last_error.
        return {
            "code":                  self.code,
            "message":               self.message,
            "status_code":           self.status_code,
            "endpoint":              self.endpoint,
            "qoyod_response_excerpt": self.response_excerpt,
            "request_body_json":     self.request_body_json,
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
        # `follow_redirects=True` is a safety belt against Qoyod's
        # domain migration (www.qoyod.com → legacy.qoyod.com). Without
        # it, a 307 surfaces to the operator as an unhelpful "HTTP error".
        async with httpx.AsyncClient(timeout=self._timeout,
                                     follow_redirects=True) as http:
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
                request_body_json=json_body,
            )
        return body

    # ── Public surface — only what the MVP pipeline needs ───────────
    async def me(self) -> dict:
        """Used by /test-connection to verify the API key.

        Qoyod retired `/me` in the legacy.qoyod.com migration so we
        probe a cheap protected list endpoint instead — valid key →
        200 with paging metadata; invalid key → 401 → classified as
        `qoyod_unauthorized` upstream. We never actually use the
        returned products list."""
        return await self._request(
            "GET", "/products", params={"limit": 1, "page": 1})

    async def list_branches(self) -> Any:
        return await self._request("GET", "/branches")

    async def list_accounts(self, *, kind: Optional[str] = None) -> Any:
        params = {"kind": kind} if kind else None
        return await self._request("GET", "/accounts", params=params)

    async def list_inventories(self) -> Any:
        """Iter-290 — Qoyod warehouses (`/inventories`).

        Used by the Settings UI to populate `default_inventory_id`.
        Qoyod's invoice validator requires `inventory_id` on every
        line item even for service/non-stock products, so the operator
        must pick (or create) at least one warehouse and bind it here.

        Read-only — Mezan NEVER posts to /inventories.
        """
        return await self._request("GET", "/inventories")

    async def list_taxes(self) -> Any:
        return await self._request("GET", "/taxes")

    async def list_products(self, *, page: int = 1, limit: int = 50) -> Any:
        """GET /products — used by Go-Live Readiness to estimate how many
        SKUs already exist in Qoyod before we begin creating new ones."""
        return await self._request(
            "GET", "/products", params={"page": page, "limit": limit})

    async def find_product_by_sku(self, sku: str) -> Optional[dict]:
        """Look up a single Qoyod product by SKU (returns the first match).

        Kept for backwards-compatibility with the legacy Trust Gate path.
        New callers should prefer `find_all_products_by_sku` so they can
        detect duplicate-SKU collisions in Qoyod.
        """
        rows = await self.find_all_products_by_sku(sku, limit=1)
        return rows[0] if rows else None

    async def find_all_products_by_sku(
        self, sku: str, *, limit: int = 10,
    ) -> list[dict]:
        """Iter-288 — return ALL Qoyod products whose SKU matches.

        Why a list, not a single row?
        ─────────────────────────────
        For Auto-Adopt (Iter-288) we MUST distinguish:
          0 matches → safe to create
          1 match   → adopt that single match
          ≥2 matches → REFUSE (ambiguous binding) and surface the
                       duplicates so the operator can clean up.
        `find_product_by_sku` returned only the first hit and hid the
        duplicate-SKU case; this method surfaces it explicitly.
        """
        if not sku or not isinstance(sku, str):
            return []
        sku = sku.strip()
        if not sku:
            return []

        candidates: list[dict[str, Any]] = [
            {"q[sku_eq]": sku, "limit": limit},   # Ransack (most common)
            {"sku":       sku, "limit": limit},   # legacy flat filter
        ]
        for params in candidates:
            try:
                body = await self._request(
                    "GET", "/products", params=params)
            except QoyodAPIError:
                continue
            rows = []
            if isinstance(body, dict):
                rows = body.get("products") or body.get("data") or []
            elif isinstance(body, list):
                rows = body
            # Defensive: Qoyod may ignore the filter and return ALL
            # products — verify the SKU actually matches.
            matches: list[dict] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                row_sku = (r.get("sku") or r.get("reference") or "")
                if isinstance(row_sku, str) and row_sku.strip() == sku:
                    matches.append(r)
                    if len(matches) >= limit:
                        break
            if matches:
                return matches
            # Filter honoured (empty) → done; no need to try the legacy
            # candidate since Qoyod definitively has no row with this SKU.
            if rows == []:
                return []
        return []

    async def list_contacts(self, *, page: int = 1, limit: int = 50) -> Any:
        """GET /customers — same purpose as `list_products` but for customers.

        Note (2026-06-26 connectivity blocker diagnosis): Qoyod's legacy
        domain uses `/customers` for the list endpoint and reserves
        `/contacts/{id}` for single-resource GETs. Using `/contacts`
        without an id returns 404 "Invalid ID". We keep the Python
        method name `list_contacts` for consistency with `create_contact`."""
        return await self._request(
            "GET", "/customers", params={"page": page, "limit": limit})

    async def create_contact(self, payload: dict, *, idem: str) -> Any:
        """POST /customers — creates a customer record in Qoyod.

        Note (2026-06-26 endpoint audit): the legacy domain serves both
        `/contacts` and `/customers` for POST, but only `/customers`
        honors auth-first ordering (POST /contacts returns 422 even
        with an invalid key, which breaks our error classifier).
        We standardise on `/customers` for safety + consistency with
        `list_contacts`. The Python method name is kept to preserve
        call sites — the resource is logically the same entity."""
        return await self._request(
            "POST", "/customers", json_body=payload, idempotency_key=idem)

    async def create_product(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/products", json_body=payload, idempotency_key=idem)

    async def create_invoice(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/invoices", json_body=payload, idempotency_key=idem)

    async def create_receipt(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/receipts", json_body=payload, idempotency_key=idem)

    async def create_invoice_payment(self, payload: dict, *, idem: str) -> Any:
        """Iter-290h — `POST /invoice_payments`. Registers a payment ON
        an invoice (vs. `/receipts` which creates a STANDALONE receipt
        that does NOT close the invoice balance — operators kept seeing
        "غير مستعمل" receipts in Qoyod).

        Canonical body shape (per Qoyod docs + LIVE evidence 2026-02-28
        / 2026-06-28 — order 269048975 confirmed):

            {"invoice_payment": {
                "invoice_id":   <int>,
                "amount":       <decimal>,
                "date":         "YYYY-MM-DD",
                "account_id":   <int>,        # Qoyod Chart-of-Accounts id
                "reference":    "<order #>",
                "description":  "<optional>"
            }}
        """
        return await self._request(
            "POST", "/invoice_payments",
            json_body=payload, idempotency_key=idem)

    # ── Read-only list endpoints for Fresh-Start Audit ──────────────
    # Strictly READ; never call DELETE/PUT from this client.
    async def list_invoices(self, *, page: int = 1, limit: int = 50) -> Any:
        """GET /invoices — paginated. Used by Fresh-Start Audit ONLY."""
        return await self._request(
            "GET", "/invoices", params={"page": page, "limit": limit})

    async def list_receipts(self, *, page: int = 1, limit: int = 50) -> Any:
        """GET /receipts — paginated. Used by Fresh-Start Audit ONLY."""
        return await self._request(
            "GET", "/receipts", params={"page": page, "limit": limit})

    # ── DELETE endpoints — Fresh-Start Cleanup ONLY ─────────────────
    # Strictly gated by `qoyod_fresh_start_cleanup.execute_cleanup`.
    # Never call these from any other code path.
    async def delete_invoice(self, invoice_id: str) -> Any:
        return await self._request("DELETE", f"/invoices/{invoice_id}")

    async def delete_receipt(self, receipt_id: str) -> Any:
        return await self._request("DELETE", f"/receipts/{receipt_id}")

    async def delete_product(self, product_id: str) -> Any:
        return await self._request("DELETE", f"/products/{product_id}")

    async def delete_customer(self, customer_id: str) -> Any:
        # Qoyod uses /customers (legacy) — same path as list_contacts.
        return await self._request("DELETE", f"/customers/{customer_id}")
