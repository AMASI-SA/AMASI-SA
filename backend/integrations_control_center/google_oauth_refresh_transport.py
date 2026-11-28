"""Unwired no-retry Google OAuth transport; never called by current writers.

This module alone is not authorization to refresh. The caller must only dispatch
through the fenced journal/CAS contract after the shared grant migration is
complete. Inputs and exceptions must never be logged because they can contain
credential material. OAuth/provider response does not establish account access.
"""
from datetime import datetime, timedelta
import httpx

from integrations_control_center.google_oauth_refresh_contract import EncryptedRefresh

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"


class RefreshProviderError(Exception):
    """Sanitized failure: unknown outcomes must not trigger automatic retry."""


async def send_google_refresh_once(*, client_id, client_secret, refresh_token,
                                   now, encrypt):
    """One request, no redirects and transport connection retries set to zero.

    The application must not wrap this call in retries; a timeout may follow a
    provider-side success. Only ciphertext leaves this function on success.
    """
    if (not all(isinstance(x, str) and x for x in (client_id, client_secret, refresh_token))
        or not isinstance(now, datetime) or now.utcoffset() is None or not callable(encrypt)):
        raise ValueError("invalid refresh inputs")
    try:
        transport = httpx.AsyncHTTPTransport(retries=0)
        async with httpx.AsyncClient(transport=transport, follow_redirects=False,
                                     timeout=25.0, trust_env=False) as client:
            response = await client.post(GOOGLE_TOKEN_URL, data={
                "client_id": client_id, "client_secret": client_secret,
                "refresh_token": refresh_token, "grant_type": "refresh_token"})
    except httpx.HTTPError:
        raise RefreshProviderError("Google refresh transport outcome unknown") from None
    if response.status_code != 200:
        raise RefreshProviderError("Google refresh did not return success")
    try:
        payload = response.json()
        token = payload.get("access_token")
        expires = payload.get("expires_in")
        if (not isinstance(token, str) or not token or type(expires) is not int
            or not 60 <= expires <= 86400):
            raise ValueError
        ciphertext = encrypt(token)
        if not isinstance(ciphertext, str) or not ciphertext:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise RefreshProviderError("Google refresh response was invalid") from None
    return EncryptedRefresh(ciphertext, now + timedelta(seconds=expires))
