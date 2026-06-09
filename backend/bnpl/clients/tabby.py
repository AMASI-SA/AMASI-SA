"""Tabby Merchant API async client.

Endpoints used:
  GET  /api/v1/payments               — list/search payments (date range)
  GET  /v2/payments/{id}              — single payment with refund detail
  POST /api/v1/webhooks               — register a webhook

Auth: `Authorization: Bearer {secret_key}` + optional `X-Merchant-Code`.

Base URL is KSA by default (https://api.tabby.sa).  Test vs live is
chosen entirely by which secret_key the user pastes in Settings —
exactly as Tabby documents.
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

        Tabby doesn't publish a dedicated /ping endpoint; we call the
        payments list endpoint with a tiny page size — a 401/403 means
        creds are wrong, a 2xx means we're good.
        """
        data = await self._get("/api/v1/payments", params={"limit": 1})
        return {
            "ok": True,
            "sample_count": len(data.get("data") or []) if isinstance(data, dict) else 0,
        }

    # ── public — list payments ─────────────────────────────────
    async def list_payments(
        self,
        *,
        created_from: Optional[str] = None,
        created_to: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Return a single page of payments from Tabby.

        `created_from` / `created_to` should be ISO-8601 UTC strings.
        The exact filter parameter names follow Tabby's API reference.
        """
        params: Dict[str, Any] = {"limit": limit, "offset": offset}
        if created_from:
            params["created_at[gte]"] = created_from
        if created_to:
            params["created_at[lte]"] = created_to
        return await self._get("/api/v1/payments", params=params)

    async def list_payments_since(
        self, since_iso: str, *, page_size: int = 50, max_pages: int = 200,
    ) -> List[Dict[str, Any]]:
        """Paginate from `since_iso` to now; cap at `max_pages` for safety."""
        out: List[Dict[str, Any]] = []
        offset = 0
        for _ in range(max_pages):
            page = await self.list_payments(
                created_from=since_iso,
                limit=page_size,
                offset=offset,
            )
            items = page.get("data") if isinstance(page, dict) else []
            items = items or []
            out.extend(items)
            if len(items) < page_size:
                break
            offset += page_size
        return out

    # ── public — single payment ────────────────────────────────
    async def get_payment(self, payment_id: str) -> Dict[str, Any]:
        return await self._get(f"/v2/payments/{payment_id}")
