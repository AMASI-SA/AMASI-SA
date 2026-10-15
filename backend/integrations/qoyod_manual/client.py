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

import ast
import asyncio
import os
from typing import Any, Optional

import httpx

from integrations.qoyod.base_url import normalize_qoyod_api_base


MEZAN_MANUAL_VERSION = "plan-b-manual-1.0"

# Qoyod's supported invoice-list endpoint is the fail-closed fallback when
# the optional exact-reference filter responds with 404.  The same complete
# snapshot is reused by one client instance, so an automatic batch scans the
# provider once rather than once per order.
_INVOICE_SCAN_PAGE_SIZE = 50
_INVOICE_SCAN_MAX_PAGES = 200
_INVOICE_SCAN_DELAY_SECONDS = 0.1

_PRODUCT_SCAN_PAGE_SIZE = 50
_PRODUCT_SCAN_MAX_PAGES = 200
_PRODUCT_SCAN_DELAY_SECONDS = 0.1

def _is_confirmed_empty_list(exc: "ManualQoyodError") -> bool:
    """Recognize Qoyod's documented-by-production empty-list sentinel.

    Qoyod returns HTTP 404 with ``We found nothing`` for an empty list on
    some tenants.  Keep this deliberately narrower than a generic 404: route
    failures such as ``URL not found`` must never authorize a later create.
    """
    if exc.status_code != 404:
        return False
    message = exc.response_excerpt.strip()
    # _request preserves plain-text responses and stringifies JSON bodies.
    # Only the exact provider message is evidence of an empty page; a proxy
    # page or a different error mentioning these words is not sufficient.
    if message.startswith("{"):
        try:
            body = ast.literal_eval(message)
        except (ValueError, SyntaxError):
            return False
        if not isinstance(body, dict) or set(body) != {"error"}:
            return False
        message = body["error"]
    return isinstance(message, str) and message.strip().casefold() == "we found nothing"


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
        configured_base = base_url or os.environ.get("QOYOD_API_BASE", "")
        self._base_url = normalize_qoyod_api_base(configured_base)
        if not self._base_url:
            raise RuntimeError("QOYOD_API_BASE not set")
        self._timeout = timeout
        self._invoice_reference_snapshot: Optional[dict[str, dict]] = None
        self._product_sku_snapshot: Optional[dict[str, dict]] = None

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
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
            ) as http:
                resp = await http.request(
                    method,
                    url,
                    headers=self._headers(idem=idem),
                    json=json_body,
                    params=params,
                )
        except httpx.RequestError as exc:
            raise ManualQoyodError(
                status_code=0,
                endpoint=f"{method} {path}",
                response_excerpt=(
                    f"{type(exc).__name__}: {str(exc)[:500]}"
                ),
                request_body=json_body,
            ) from exc
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
    async def _read_with_query_fallbacks(
        self,
        path: str,
        query_options: tuple[dict, ...],
        *,
        stop_when=None,
    ) -> list[Any]:
        """Run lookup variants without converting unknown state to absence.

        Only a query-shape rejection (400/422) may try the next documented
        filter. Authentication, throttling, network, timeout, and provider
        failures propagate immediately so callers cannot perform a write
        after an unconfirmed duplicate lookup. Once an exact match is found,
        later fallback queries are skipped so a subsequent throttle cannot
        obscure an already-confirmed provider result.
        """
        bodies: list[Any] = []
        last_shape_error: Optional[ManualQoyodError] = None
        for params in query_options:
            try:
                body = await self._request(
                    "GET",
                    path,
                    params=params,
                )
            except ManualQoyodError as exc:
                if exc.status_code in {400, 422}:
                    last_shape_error = exc
                    continue
                raise
            bodies.append(body)
            if stop_when is not None and stop_when(body):
                break
        if not bodies and last_shape_error is not None:
            raise last_shape_error
        return bodies

    async def find_customers_by_phone(self, phone: str,
                                       *, limit: int = 5) -> list[dict]:
        """Find an exact phone match while keeping provider failures visible."""
        if not phone:
            return []

        def _phone_matches(body: Any) -> list[dict]:
            rows = []
            if isinstance(body, dict):
                rows = body.get("customers") or body.get("data") or []
            elif isinstance(body, list):
                rows = body
            matches = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                value = str(
                    row.get("phone") or row.get("mobile") or ""
                ).strip()
                if value and value == phone:
                    matches.append(row)
            return matches

        bodies = await self._read_with_query_fallbacks(
            "/customers",
            (
                {"q[phone_eq]": phone, "limit": limit},
                {"q[mobile_eq]": phone, "limit": limit},
                {"phone": phone, "limit": limit},
            ),
            stop_when=lambda body: bool(_phone_matches(body)),
        )
        for body in bodies:
            matches = _phone_matches(body)
            if matches:
                return matches
        return []

    async def find_customers_by_email(self, email: str,
                                       *, limit: int = 5) -> list[dict]:
        if not email:
            return []
        body = await self._request(
            "GET", "/customers",
            params={"q[email_eq]": email, "limit": limit})
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

        if self._product_sku_snapshot is not None:
            return self._product_sku_snapshot.get(sku)

        def _exact_product(body: Any) -> Optional[dict]:
            for row in self._product_rows(body):
                value = str(
                    row.get("sku") or row.get("reference") or ""
                ).strip()
                if value == sku:
                    return row
            return None

        try:
            bodies = await self._read_with_query_fallbacks(
                "/products",
                (
                    {"q[sku_eq]": sku, "limit": 5},
                    {"sku": sku, "limit": 5},
                ),
                stop_when=lambda body: _exact_product(body) is not None,
            )
        except ManualQoyodError as exc:
            if exc.status_code != 404:
                raise
            # A filtered 404 is not evidence that a product is absent.
            # Independently read the unfiltered catalog; only a complete,
            # valid successful list may authorize the caller's create path.
            self._product_sku_snapshot = await self._load_product_sku_snapshot()
            return self._product_sku_snapshot.get(sku)
        for body in bodies:
            match = _exact_product(body)
            if match is not None:
                return match
        if any(self._product_rows(body) for body in bodies):
            # A nonempty list without an exact match may mean the provider
            # ignored the filter. A limited first page cannot prove absence.
            self._product_sku_snapshot = await self._load_product_sku_snapshot()
            return self._product_sku_snapshot.get(sku)
        return None

    @staticmethod
    def _product_rows(body: Any) -> list[dict]:
        node = body
        if isinstance(node, dict) and "data" in node:
            node = node["data"]
        if isinstance(node, dict):
            node = node.get("products")
        if not isinstance(node, list) or any(
            not isinstance(row, dict) for row in node
        ):
            raise ManualQoyodError(
                status_code=0, endpoint="GET /products",
                response_excerpt="product lookup response shape unknown",
            )
        return node

    async def _load_product_sku_snapshot(self) -> dict[str, dict]:
        """Bounded catalog scan with an exact provider end-of-list sentinel.

        A short successful page completes the existing page/limit contract.
        Repeated pages, unknown bodies, a full final page at the cap, and
        unexpected HTTP/network failures leave absence unconfirmed. An exact
        empty-page sentinel may end the scan only when no declared total is
        still outstanding. The snapshot is published only after completion
        and lives for this client only.
        """
        by_sku: dict[str, dict] = {}
        seen_ids: set[str] = set()
        expected_count: Optional[int] = None
        for page in range(1, _PRODUCT_SCAN_MAX_PAGES + 1):
            try:
                body = await self._request(
                    "GET", "/products",
                    params={"page": page, "limit": _PRODUCT_SCAN_PAGE_SIZE},
                )
            except ManualQoyodError as exc:
                # Qoyod uses the same sentinel for an empty catalog and
                # the page after its last full page. Keep the accumulated
                # products: returning {} here would lose existing SKUs and
                # permit duplicate creation. A declared but unobserved row
                # count always overrides this sentinel and remains unknown.
                if _is_confirmed_empty_list(exc) and (
                    expected_count is None or len(seen_ids) == expected_count
                ):
                    return by_sku
                raise
            rows = self._product_rows(body)
            meta = body.get("meta") if isinstance(body, dict) else None
            if isinstance(meta, dict) and "total" in meta:
                total = meta["total"]
                if isinstance(total, str) and total.isdigit():
                    total = int(total)
                if type(total) is not int or total < 0 or (
                    expected_count is not None and total != expected_count
                ):
                    raise ManualQoyodError(
                        status_code=0, endpoint="GET /products",
                        response_excerpt="product catalog total unconfirmed",
                    )
                expected_count = total
            for row in rows:
                product_id = str(row.get("id") or "").strip()
                if not product_id or product_id in seen_ids:
                    raise ManualQoyodError(
                        status_code=0, endpoint="GET /products",
                        response_excerpt="product catalog identity/pagination unconfirmed",
                    )
                seen_ids.add(product_id)
                sku = str(row.get("sku") or row.get("reference") or "").strip()
                if sku:
                    by_sku.setdefault(sku, row)
            if expected_count is not None:
                if len(seen_ids) > expected_count or (
                    not rows and len(seen_ids) != expected_count
                ):
                    raise ManualQoyodError(
                        status_code=0, endpoint="GET /products",
                        response_excerpt="product catalog incomplete or changed during scan",
                    )
                if len(seen_ids) == expected_count:
                    return by_sku
            elif len(rows) < _PRODUCT_SCAN_PAGE_SIZE:
                return by_sku
            if page < _PRODUCT_SCAN_MAX_PAGES and _PRODUCT_SCAN_DELAY_SECONDS > 0:
                await asyncio.sleep(_PRODUCT_SCAN_DELAY_SECONDS)
        raise ManualQoyodError(
            status_code=0, endpoint="GET /products",
            response_excerpt="product catalog incomplete at pagination cap",
        )

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

        normalized_reference = str(reference)
        if self._invoice_reference_snapshot is not None:
            return self._invoice_reference_snapshot.get(normalized_reference)

        def _exact_invoice(body: Any) -> Optional[dict]:
            rows = self._invoice_rows(body) or []
            for row in rows:
                if str(row.get("reference") or "") == str(reference):
                    return row
            return None

        try:
            bodies = await self._read_with_query_fallbacks(
                "/invoices",
                (
                    {"q[reference_eq]": reference, "limit": 3},
                    {"reference": reference, "limit": 3},
                ),
                stop_when=lambda body: _exact_invoice(body) is not None,
            )
        except ManualQoyodError as exc:
            if exc.status_code != 404:
                raise
            # The live Qoyod tenant responds with 404 for the optional
            # reference-filter shape even though the documented unfiltered
            # paginated list remains available.  A 404 is not absence: scan
            # the complete list and only then decide whether the exact
            # reference exists. Any incomplete/failed scan raises and keeps
            # the caller's write path closed.
            self._invoice_reference_snapshot = (
                await self._load_complete_invoice_reference_snapshot()
            )
            return self._invoice_reference_snapshot.get(
                normalized_reference
            )
        for body in bodies:
            if self._invoice_rows(body) is None:
                raise ManualQoyodError(
                    status_code=0,
                    endpoint="GET /invoices",
                    response_excerpt=(
                        "invoice reference lookup response shape unknown"
                    ),
                )
            match = _exact_invoice(body)
            if match is not None:
                return match
        return None

    @staticmethod
    def _invoice_rows(body: Any) -> Optional[list[dict]]:
        """Extract one valid invoice-list page without guessing absence."""
        node = body
        if isinstance(node, dict) and "data" in node:
            data = node.get("data")
            if isinstance(data, dict):
                node = data
            elif isinstance(data, list):
                node = data
        if isinstance(node, dict):
            for key in ("invoices", "items"):
                rows = node.get(key)
                if isinstance(rows, list):
                    if any(not isinstance(row, dict) for row in rows):
                        return None
                    return rows
            return None
        if isinstance(node, list):
            if any(not isinstance(row, dict) for row in node):
                return None
            return node
        return None

    async def _load_complete_invoice_reference_snapshot(
        self,
    ) -> dict[str, dict]:
        """Read all Qoyod invoice pages or fail before any later write.

        Qoyod uses 404 on list endpoints for an empty collection or a page
        beyond the end. Other HTTP/network failures, unknown response shapes,
        and a full final page at the safety cap remain UNKNOWN and propagate.
        """
        by_reference: dict[str, dict] = {}
        for page in range(1, _INVOICE_SCAN_MAX_PAGES + 1):
            try:
                body = await self._request(
                    "GET",
                    "/invoices",
                    params={
                        "page": page,
                        "limit": _INVOICE_SCAN_PAGE_SIZE,
                    },
                )
            except ManualQoyodError as exc:
                if exc.status_code == 404 and page > 1:
                    return by_reference
                raise

            rows = self._invoice_rows(body)
            if rows is None:
                raise ManualQoyodError(
                    status_code=0,
                    endpoint="GET /invoices",
                    response_excerpt=(
                        "invoice reference snapshot response shape unknown"
                    ),
                )
            for row in rows:
                value = str(row.get("reference") or "").strip()
                if value:
                    by_reference.setdefault(value, row)

            if len(rows) < _INVOICE_SCAN_PAGE_SIZE:
                return by_reference
            if page < _INVOICE_SCAN_MAX_PAGES \
                    and _INVOICE_SCAN_DELAY_SECONDS > 0:
                await asyncio.sleep(_INVOICE_SCAN_DELAY_SECONDS)

        raise ManualQoyodError(
            status_code=0,
            endpoint="GET /invoices",
            response_excerpt=(
                "invoice reference snapshot incomplete at pagination cap"
            ),
        )

    # ── Write endpoints ─────────────────────────────────────────────
    async def create_customer(self, payload: dict, *, idem: str) -> Any:
        return await self._request(
            "POST", "/customers", json_body=payload, idem=idem)

    async def create_product(self, payload: dict, *, idem: str) -> Any:
        # Invalidate before the write: even a timeout can mean the provider
        # persisted the product. Never reuse a cached absence after a POST.
        self._product_sku_snapshot = None
        return await self._request(
            "POST", "/products", json_body=payload, idem=idem)

    async def create_invoice(self, payload: dict, *, idem: str) -> Any:
        body = await self._request(
            "POST", "/invoices", json_body=payload, idem=idem)
        if self._invoice_reference_snapshot is not None:
            invoice_payload = (
                payload.get("invoice") if isinstance(payload, dict) else None
            )
            reference = str(
                (invoice_payload or {}).get("reference") or ""
            ).strip()
            if reference:
                invoice = (
                    body.get("invoice") if isinstance(body, dict) else None
                )
                snapshot_row = dict(invoice or {})
                snapshot_row.setdefault("reference", reference)
                self._invoice_reference_snapshot[reference] = snapshot_row
        return body

    async def create_invoice_payment(self, payload: dict, *,
                                     idem: str) -> Any:
        return await self._request(
            "POST", "/invoice_payments", json_body=payload, idem=idem)
