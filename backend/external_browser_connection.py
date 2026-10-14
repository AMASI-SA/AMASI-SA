"""Operator-only Browserless connectivity probe; not a storefront collector.

No production route imports this module. A successful blank-page round trip
does not establish public-only egress, recording policy, or research readiness.
Credentials must come from the deployment secret store, never request/model data.
"""
from __future__ import annotations

import asyncio
import json
import secrets

import httpx


ENDPOINT = 'https://production-sfo.browserless.io/function'
MAX_RESPONSE_BYTES = 8192
# Static reviewed code only. No navigation, external imports, or model code.
PROBE_CODE = '''export default async ({ page, context }) => ({
  data: {
    protocol: "mezan-browser-probe-v1",
    nonce: context.nonce,
    blank_page: await page.evaluate(() => location.href === "about:blank")
  },
  type: "application/json"
});'''


async def probe_browser_connection(
    token: str, *, transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    """One bounded request, no retries. Never return exception/provider bodies."""
    def outcome(status: str) -> dict:
        return {'status': status, 'storefront_checked': False}

    if not token or not token.strip():
        return outcome('not_configured')
    if token != token.strip() or any(ord(c) < 33 or ord(c) > 126 for c in token):
        return outcome('invalid_configuration')
    nonce = secrets.token_hex(16)
    expected = {'protocol': 'mezan-browser-probe-v1', 'nonce': nonce, 'blank_page': True}
    try:
        async with asyncio.timeout(35):
            async with httpx.AsyncClient(
                transport=transport, timeout=httpx.Timeout(30, connect=5),
                follow_redirects=False, trust_env=False,
            ) as client:
                async with client.stream(
                    'POST', ENDPOINT, params={'timeout': '25000'},
                    headers={'Authorization': f'Bearer {token}',
                             'Accept': 'application/json', 'Accept-Encoding': 'identity'},
                    json={'code': PROBE_CODE, 'context': {'nonce': nonce}},
                ) as response:
                    status = response.status_code
                    if status in (401, 403):
                        return outcome('access_denied')
                    if status == 429:
                        return outcome('capacity_unavailable')
                    if status == 408:
                        return outcome('timeout')
                    if status >= 500:
                        return outcome('service_unavailable')
                    if status != 200:
                        return outcome('unexpected_response')
                    if response.headers.get('content-type', '').split(';')[0].strip().lower() != 'application/json':
                        return outcome('unexpected_response')
                    if response.headers.get('content-encoding', 'identity') != 'identity':
                        return outcome('unexpected_response')
                    body = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=1024):
                        if len(body) + len(chunk) > MAX_RESPONSE_BYTES:
                            return outcome('unexpected_response')
                        body.extend(chunk)
                    payload = json.loads(body)
                    # The docs illustrate the data/type envelope; REST may emit
                    # its data directly. Accept only these two exact contracts.
                    if isinstance(payload, dict) and set(payload) == {'data', 'type'}:
                        if payload['type'] != 'application/json':
                            return outcome('unexpected_response')
                        payload = payload['data']
                    if payload != expected or not isinstance(payload.get('blank_page'), bool):
                        return outcome('unexpected_response')
                    return outcome('connection_verified')
    except (TimeoutError, httpx.TimeoutException):
        return outcome('timeout')
    except httpx.HTTPError:
        return outcome('service_unavailable')
    except (ValueError, UnicodeError):
        return outcome('unexpected_response')
