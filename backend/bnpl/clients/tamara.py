"""Tamara Merchant API async client.

Tamara does NOT publish a list-orders endpoint, so this client supports
only per-order retrieval — designed for the webhook-first flow:

  GET  /merchants/orders/{order_id}                  — by Tamara order_id
  GET  /merchants/orders/reference-id/{reference_id} — by merchant ref
  POST /orders/{order_id}/authorise                  — flow control
  POST /payments/simplified-refund/{order_id}        — refund initiation

Auth: `Authorization: Bearer {api_token}`.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import httpx


DEFAULT_TIMEOUT = 25.0


class TamaraError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(f"Tamara HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class TamaraClient:
    def __init__(
        self,
        api_token: str,
        *,
        base_url: str = "https://api.tamara.co",
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not api_token:
            raise ValueError("Tamara api_token is required")
        self.api_token = api_token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _request(
        self, method: str, path: str,
        *, params: Optional[Dict[str, Any]] = None,
        json: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            try:
                resp = await cli.request(
                    method, url, headers=self._headers(),
                    params=params, json=json,
                )
            except httpx.HTTPError as exc:
                raise TamaraError(0, f"network error: {exc}") from exc
        if resp.status_code >= 400:
            raise TamaraError(resp.status_code, resp.text[:500])
        try:
            return resp.json()
        except ValueError:
            return {}

    # ── public — health check ──────────────────────────────────
    async def test_connection(self) -> Dict[str, Any]:
        """Tamara doesn't expose /ping; we hit a non-existent reference-id
        which still proves auth: 401/403 = bad token, 404 = good token
        (we accept 404 as a valid auth proof)."""
        url = f"{self.base_url}/merchants/orders/reference-id/__bnpl_ping__"
        async with httpx.AsyncClient(timeout=self.timeout) as cli:
            try:
                resp = await cli.get(url, headers=self._headers())
            except httpx.HTTPError as exc:
                raise TamaraError(0, f"network error: {exc}") from exc
        if resp.status_code in (401, 403):
            raise TamaraError(resp.status_code, "Invalid API token")
        # 404 (or 200) → token is good
        return {"ok": True, "probe_status": resp.status_code}

    # ── orders ─────────────────────────────────────────────────
    async def get_order_by_id(self, order_id: str) -> Dict[str, Any]:
        return await self._request("GET", f"/merchants/orders/{order_id}")

    async def get_order_by_reference(self, reference_id: str) -> Dict[str, Any]:
        return await self._request(
            "GET", f"/merchants/orders/reference-id/{reference_id}",
        )
