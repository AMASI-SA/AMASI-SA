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

from integrations.qoyod.write_lock import (
    WRITE_METHODS,
    QoyodWriteLockedError,
    classify_action,
    emit_blocked_log,
    extract_payload_hints,
    record_blocked_attempt,
)


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
    state between requests.

    Iter-294 — `write_lock_enabled=True` activates the Global Qoyod
    Production Write Lock. When the lock is on, every POST/PUT/PATCH/
    DELETE to api.qoyod.com is intercepted at `_request` BEFORE the
    HTTP call. The outbound payload is persisted to
    `qoyod_write_lock_attempts` for audit, then `QoyodWriteLockedError`
    is raised. Read endpoints (GET) pass through untouched.

    The lock requires `db` + `user_id` for the audit record. If either
    is missing AND write_lock_enabled=True, the client still refuses
    the write (defense-in-depth) but cannot persist the audit row.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: Optional[str] = None,
        timeout: float = 15.0,
        # ── Iter-294: Global Write Lock ────────────────────────────────
        db: Any = None,
        user_id: Optional[str] = None,
        write_lock_enabled: bool = False,
        # ── Iter-2026-02.rev32.1 — Dead-letter hardening ───────────────
        # `row_id` + `trace_id` let the write methods invoke the
        # rev32.1 pre-flight guard against a FRESH read of the
        # integration_inbox row. Every caller that legitimately writes
        # (pipeline, retry, reprocess, manual send, approve_locked_
        # payment, go_live, …) MUST pass these. Legacy admin probes
        # that write without a row context must set
        # `allow_writes_without_row=True` (audited via kill_switch
        # collection). Everything else is fail-CLOSED.
        row_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        allow_writes_without_row: bool = False,
    ):
        if not api_key:
            raise ValueError("Qoyod API key is required")
        self._api_key = api_key
        self._base_url = (base_url or os.environ.get("QOYOD_API_BASE", "")).rstrip("/")
        if not self._base_url:
            raise RuntimeError(
                "QOYOD_API_BASE is not set in backend/.env")
        self._timeout = timeout
        # Lock state — snapshot at construction time. Callers MUST
        # rebuild the client between iterations if they need to honour
        # a live flag change. The pipeline does this naturally because
        # `_get_api_client` runs once per row.
        self._db = db
        self._user_id = user_id
        self._write_lock_enabled = bool(write_lock_enabled)
        # rev32.1 — Row-scoped fencing context.
        self._row_id = row_id
        self._trace_id = trace_id
        self._allow_writes_without_row = bool(allow_writes_without_row)

    # Iter-293.4-rev5 — Public read-only view of the lock state so the
    # pipeline can honour the per-order approval bypass. Callers MUST
    # NOT mutate this; rebuild a fresh QoyodAPIClient if the desired
    # state changes (e.g. between rows / between runs).
    @property
    def write_lock_enabled(self) -> bool:
        return self._write_lock_enabled

    # Redact secrets in any debug print.
    def __repr__(self) -> str:  # pragma: no cover
        return (f"QoyodAPIClient(base_url={self._base_url!r}, "
                f"api_key=***, write_lock={self._write_lock_enabled})")

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
        # ── Iter-293.4: Global Write Lock guard ─────────────────────
        # Defense-in-depth. Fires for ANY POST/PUT/PATCH/DELETE when
        # `production_writes_locked=True` was snapshotted at client
        # construction. Records the attempt to `qoyod_write_lock_attempts`
        # for audit, emits a `BLOCKED_QOYOD_WRITE` log line to stdout,
        # then raises QoyodWriteLockedError so the caller surfaces a
        # clean "BLOCKED" outcome instead of a silent skip.
        if self._write_lock_enabled and method.upper() in WRITE_METHODS:
            action = classify_action(method, path)
            attempt_id: Optional[str] = None
            if self._db is not None and self._user_id:
                attempt_id = await record_blocked_attempt(
                    self._db,
                    user_id=self._user_id,
                    action=action,
                    method=method,
                    path=path,
                    payload=json_body,
                    idempotency_key=idempotency_key,
                )
            else:
                # No DB available — still emit the stdout log so the
                # operator sees the block in journalctl.
                emit_blocked_log(
                    action=action, method=method, path=path,
                    hints=extract_payload_hints(action, json_body),
                )
            raise QoyodWriteLockedError(
                action=action,
                attempt_id=attempt_id,
                method=method.upper(),
                path=path,
            )

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

    async def list_product_categories(self) -> Any:
        """Iter-290i — Qoyod product categories (`/product_categories`).

        Read-only — used by the Reference-Lists picker so operators
        can choose categories by name instead of typing numeric ids.
        """
        return await self._request("GET", "/product_categories")

    async def list_product_units(self) -> Any:
        """Iter-290i — Qoyod product units (`/product_units`).

        Read-only — same purpose as `list_product_categories`.
        """
        return await self._request("GET", "/product_units")

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

    async def _rev32_1_preflight(
        self, *, action: str, payload: Any,
    ) -> None:
        """rev32.1 — Called at the top of every write method.
        Delegates to `assert_client_write_permitted` which reads
        FRESH settings + row from DB and enforces all rev32/32.1
        conditions (BLOCKED_FOR_WRITE_STAGES, dead_lettered_at,
        worker_pipeline_sha match, sas_gate eligible, allow-list,
        live-write settings). ANY violation raises Rev32Violation
        BEFORE the HTTP call is made.

        Backward-compat: probes/admin paths that construct the
        client without db/row_id but need to write MUST set
        `allow_writes_without_row=True` at construction — otherwise
        the write is refused.
        """
        # Local import to avoid a circular dependency at module load.
        from integrations.qoyod.rev32_hardening import (
            assert_client_write_permitted, payment_method_from_payload,
        )
        pm = payment_method_from_payload(action, payload)
        await assert_client_write_permitted(
            db=self._db,
            row_id=self._row_id,
            trace_id=self._trace_id,
            user_id=self._user_id or "main",
            action=action,
            payment_method=pm,
            allow_writes_without_row=self._allow_writes_without_row,
            client_repr=repr(self),
        )

    async def create_contact(self, payload: dict, *, idem: str) -> Any:
        """POST /customers — creates a customer record in Qoyod.

        Note (2026-06-26 endpoint audit): the legacy domain serves both
        `/contacts` and `/customers` for POST, but only `/customers`
        honors auth-first ordering (POST /contacts returns 422 even
        with an invalid key, which breaks our error classifier).
        We standardise on `/customers` for safety + consistency with
        `list_contacts`. The Python method name is kept to preserve
        call sites — the resource is logically the same entity."""
        await self._rev32_1_preflight(action="create_customer", payload=payload)
        return await self._request(
            "POST", "/customers", json_body=payload, idempotency_key=idem)

    async def create_product(self, payload: dict, *, idem: str) -> Any:
        await self._rev32_1_preflight(action="create_product", payload=payload)
        return await self._request(
            "POST", "/products", json_body=payload, idempotency_key=idem)

    async def create_invoice(self, payload: dict, *, idem: str) -> Any:
        await self._rev32_1_preflight(action="create_invoice", payload=payload)
        return await self._request(
            "POST", "/invoices", json_body=payload, idempotency_key=idem)

    async def create_receipt(self, payload: dict, *, idem: str) -> Any:
        # /receipts is legacy — not part of rev32/32.1 guarded actions
        # (create_invoice_payment supersedes it). Left unguarded because
        # GUARDED_WRITE_ACTIONS does not include it; if a future rev
        # decides to fence receipts too, add it to
        # rev32_hardening.GUARDED_WRITE_ACTIONS and here.
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
        await self._rev32_1_preflight(
            action="create_invoice_payment", payload=payload)
        return await self._request(
            "POST", "/invoice_payments",
            json_body=payload, idempotency_key=idem)

    # ── Read-only list endpoints for Fresh-Start Audit ──────────────
    # Strictly READ; never call DELETE/PUT from this client.
    async def list_invoices(self, *, page: int = 1, limit: int = 50) -> Any:
        """GET /invoices — paginated. Used by Fresh-Start Audit ONLY."""
        return await self._request(
            "GET", "/invoices", params={"page": page, "limit": limit})

    async def get_invoice(self, invoice_id: str) -> Any:
        """GET /invoices/{id} — fetch a single invoice as قيود sees it.

        Iter-290h.7 — Used by the payment-method-field probe to diff
        the structure of an empty-payment-method invoice against a
        reference invoice that DOES show the payment method, so we
        can identify the canonical wire field name (if any) without
        guessing. Strictly READ-ONLY.
        """
        return await self._request(
            "GET", f"/invoices/{invoice_id}")

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
