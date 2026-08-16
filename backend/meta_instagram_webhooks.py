"""Secret-safe Meta asset subscription for Instagram webhooks.

The OAuth grant is stored by the integrations control plane.  This module is
kept outside that package so the receive-only customer-intelligence worker does
not import the control plane's eager router composition at startup.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
from typing import Any

import httpx


META_CREDENTIALS_COLLECTION = "mezan_meta_oauth_credentials_v2"
META_DEFAULT_GRAPH_VERSION = "v25.0"
INSTAGRAM_WEBHOOK_FIELDS = ("comments", "messages")


class MetaInstagramWebhookError(RuntimeError):
    """A stable, token-safe provider subscription failure."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _graph_base() -> str:
    version = os.environ.get(
        "META_GRAPH_API_VERSION", META_DEFAULT_GRAPH_VERSION
    ).strip()
    if not re.fullmatch(r"v\d{1,2}\.\d", version):
        raise MetaInstagramWebhookError("meta_configuration_invalid")
    return f"https://graph.facebook.com/{version}"


def _decrypt_token(value: bytes | None) -> str:
    if not value:
        raise MetaInstagramWebhookError("meta_reauthorization_required")
    try:
        from cryptography.fernet import Fernet, MultiFernet

        primary = os.environ.get("META_TOKEN_ENC_KEY", "").strip()
        if not primary:
            raise MetaInstagramWebhookError("meta_configuration_invalid")
        keys = [Fernet(primary.encode("utf-8"))]
        previous = os.environ.get("META_TOKEN_ENC_KEY_OLD", "").strip()
        if previous:
            keys.append(Fernet(previous.encode("utf-8")))
        return MultiFernet(keys).decrypt(value).decode("utf-8")
    except MetaInstagramWebhookError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise MetaInstagramWebhookError("meta_reauthorization_required") from exc


def _appsecret_proof(access_token: str) -> str:
    secret = os.environ.get("META_BUSINESS_APP_SECRET", "").strip()
    if not secret:
        raise MetaInstagramWebhookError("meta_configuration_invalid")
    return hmac.new(
        secret.encode("utf-8"), access_token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


async def _json(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        raise MetaInstagramWebhookError("instagram_webhook_subscription_failed")
    try:
        payload = response.json()
    except ValueError as exc:
        raise MetaInstagramWebhookError(
            "instagram_webhook_subscription_failed"
        ) from exc
    if not isinstance(payload, dict):
        raise MetaInstagramWebhookError("instagram_webhook_subscription_failed")
    return payload


async def subscribe_instagram_webhooks(
    db: Any,
    *,
    owner_user_id: str,
    instagram_account_id: str,
    page_id: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, ...]:
    """Install and verify the app on a linked Instagram professional account.

    A Page access token is derived transiently from the encrypted user grant.
    Neither token is returned, persisted in the channel binding, or included in
    errors.  The operation is idempotent at Meta's ``subscribed_apps`` edge.
    """

    credential = await getattr(db, META_CREDENTIALS_COLLECTION).find_one(
        {"user_id": owner_user_id, "provider": "meta_ads"},
        {"_id": 0, "access_token_ciphertext": 1},
    )
    user_token = _decrypt_token((credential or {}).get("access_token_ciphertext"))
    app_id = os.environ.get("META_BUSINESS_APP_ID", "").strip()
    if not app_id:
        raise MetaInstagramWebhookError("meta_configuration_invalid")

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=25.0)
    try:
        pages_payload = await _json(
            await http.get(
                f"{_graph_base()}/me/accounts",
                params={
                    "fields": "id,access_token,instagram_business_account{id}",
                    "limit": "100",
                    "access_token": user_token,
                    "appsecret_proof": _appsecret_proof(user_token),
                },
            )
        )
        page = next(
            (
                row
                for row in pages_payload.get("data") or []
                if isinstance(row, dict) and _text(row.get("id")) == page_id
            ),
            None,
        )
        if not page:
            raise MetaInstagramWebhookError("instagram_page_access_required")
        linked_instagram_id = _text(
            (page.get("instagram_business_account") or {}).get("id")
        )
        if linked_instagram_id != instagram_account_id:
            raise MetaInstagramWebhookError("instagram_asset_link_mismatch")
        page_token = _text(page.get("access_token"))
        if not page_token:
            raise MetaInstagramWebhookError("instagram_page_access_required")

        edge = f"{_graph_base()}/{instagram_account_id}/subscribed_apps"
        subscribed_fields = ",".join(INSTAGRAM_WEBHOOK_FIELDS)
        installed = await _json(
            await http.post(
                edge,
                params={
                    "subscribed_fields": subscribed_fields,
                    "access_token": page_token,
                    "appsecret_proof": _appsecret_proof(page_token),
                },
            )
        )
        if installed.get("success") is not True:
            raise MetaInstagramWebhookError("instagram_webhook_subscription_failed")

        verified = await _json(
            await http.get(
                edge,
                params={
                    "access_token": page_token,
                    "appsecret_proof": _appsecret_proof(page_token),
                },
            )
        )
        app = next(
            (
                row
                for row in verified.get("data") or []
                if isinstance(row, dict) and _text(row.get("id")) == app_id
            ),
            None,
        )
        actual_fields = {
            _text(value) for value in (app or {}).get("subscribed_fields") or []
        }
        if not app or not set(INSTAGRAM_WEBHOOK_FIELDS).issubset(actual_fields):
            raise MetaInstagramWebhookError("instagram_webhook_subscription_failed")
        return INSTAGRAM_WEBHOOK_FIELDS
    except httpx.HTTPError as exc:
        raise MetaInstagramWebhookError(
            "instagram_webhook_subscription_failed"
        ) from exc
    finally:
        if owns_client:
            await http.aclose()


__all__ = [
    "INSTAGRAM_WEBHOOK_FIELDS",
    "MetaInstagramWebhookError",
    "subscribe_instagram_webhooks",
]
