"""Tabby Merchant API async client.

Endpoints used (per Tabby OpenAPI — docs.tabby.ai):
  GET  /api/v2/payments               — list payments with date filter
  GET  /api/v2/payments/{id}          — single payment
  POST /api/v1/webhooks               — register a webhook

Auth: `Authorization: Bearer {secret_key}`.  `X-Merchant-Code` is
optional and only used by merchants Tabby has explicitly told to
include it (multi-store setups).  We pass it when the user fills it in.

Base URLs (region-specific):
  KSA          → https://api.tabby.sa
  UAE / Kuwait → https://api.tabby.ai
Tabby itself decides test-vs-live from the key prefix (sk_test_ vs sk_live_).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx


DEFAULT_TIMEOUT = 25.0


class TabbyError(Exception):
    """Raised on non-2xx responses from Tabby — message is human-readable."""

    def __init__(self, status: int, detail: str):
        super().__init__(f"Tabby HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class TabbyClient:
    def __init__(
        self,
        secret_key: str,
        *,
        merchant_code: str = "",
        base_url: str = "https://api.tabby.sa",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not secret_key:
            raise ValueError("Tabby secret_key is required")
        self.secret_key = secret_key
        self.merchant_code = merchant_code or ""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ── headers ────────────────────────────────────────────────
    def _headers(self) -> Dict[str, str]:
        h = {
            "Authorization": f"Bearer {self.secret_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.merchant_code:
            h["X-Merchant-Code"] = self.merchant_code
        return h

    # ── core HTTP ──────────────────────────────────────────────
    async def _get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            try:
                resp = await cli.get(url, headers=self._headers(), params=params)
            except httpx.HTTPError as exc:
                raise TabbyError(0, f"network error: {exc}") from exc
        if resp.status_code >= 400:
            raise TabbyError(resp.status_code, resp.text[:500])
        try:
            return resp.json()
        except ValueError:
            return {}

    # ── public — health check ──────────────────────────────────
    async def test_connection(self) -> Dict[str, Any]:
        """Issue the lightest read-only call we can to verify creds.

        Tabby exposes the live payments listing at GET /api/v2/payments
        (per official OpenAPI).  A 401/403 means the secret_key is
        wrong; a 2xx with an empty array means we're good and there
        just haven't been any payments yet.
        """
        data = await self._get("/api/v2/payments", params={"limit": 1})
        payments = (data or {}).get("payments") if isinstance(data, dict) else []
        return {"ok": True, "sample_count": len(payments or [])}

    # ── public — list payments ─────────────────────────────────
    async def list_payments(
        self,
        *,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Return a single page of payments from Tabby.

        Per OpenAPI:
          • endpoint: GET /api/v2/payments
          • filter params: `created_at__gte` (double underscore),
                           `created_at__lte`
          • format: ISO date `YYYY-MM-DD` (no time component required)
          • max `limit` accepted by Tabby: 20.
        """
        # Tabby caps limit at 20 — clamp to be safe.
        params: Dict[str, Any] = {
            "limit": max(1, min(int(limit), 20)),
            "offset": max(0, int(offset)),
        }
        if created_from:
            # accept full ISO datetime but trim to YYYY-MM-DD for Tabby
            params["created_at__gte"] = created_from[:10]
        if created_to:
            params["created_at__lte"] = created_to[:10]
        return await self._get("/api/v2/payments", params=params)

    async def list_payments_since(
        self, since_iso: str, *, page_size: int = 20, max_pages: int = 1000,
    ) -> List[Dict[str, Any]]:
        """Paginate from `since_iso` to now; cap at `max_pages` for safety.

        ⚠️ TABBY FILTER WORKAROUND (Iter-116 — Forensic Debug 2026-06-09):
        Tabby's `created_at__gte` query filter has been observed to
        return 0 payments for some merchant accounts even when the
        underlying data clearly contains payments newer than the
        provided date.  The OpenAPI advertises the filter, but in
        practice it sometimes rejects valid filter values silently.

        Instead of sending the broken server-side filter, we now:
          1. Page through ALL payments (newest first — Tabby's default).
          2. Apply the date filter **client-side** on `created_at`.
          3. Short-circuit when we reach an item OLDER than `since_iso`
             (Tabby sorts newest-first, so the remaining pages would
             all be older too).

        This costs more HTTP calls than the server filter would, but
        it's the only way to guarantee correctness across every
        merchant account configuration.
        """
        out: List[Dict[str, Any]] = []
        offset = 0
        page_size = max(1, min(int(page_size), 20))
        cutoff = (since_iso or "")[:10] if since_iso else ""

        for _ in range(max_pages):
            # NOTE: NO date filter passed to Tabby — only pagination.
            # We filter on `created_at` ourselves below.
            page = await self.list_payments(
                limit=page_size,
                offset=offset,
            )
            items = []
            if isinstance(page, dict):
                items = page.get("payments") or []
            if not items:
                break

            crossed_cutoff = False
            for it in items:
                created = (it.get("created_at") or "")[:10]
                if cutoff and created and created < cutoff:
                    # Tabby returned an item OLDER than our cutoff;
                    # everything beyond this point is older too.
                    crossed_cutoff = True
                    break
                if not cutoff or not created or created >= cutoff:
                    out.append(it)

            if crossed_cutoff:
                break
            if len(items) < page_size:
                break
            offset += page_size
        return out

    # ── public — single payment ────────────────────────────────
    async def get_payment(self, payment_id: str) -> Dict[str, Any]:
        return await self._get(f"/api/v2/payments/{payment_id}")
