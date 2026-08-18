"""Secret-safe Meta asset subscription for Instagram webhooks.

The OAuth grant is stored by the integrations control plane. This module is
kept outside that package so the receive-only customer-intelligence worker does
not import the control plane's eager router composition at startup.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import re
from typing import Any, Awaitable

import httpx


META_CREDENTIALS_COLLECTION = "mezan_meta_oauth_credentials_v2"
META_DEFAULT_GRAPH_VERSION = "v25.0"
INSTAGRAM_WEBHOOK_FIELDS = ("messages", "comments")
# Facebook Login installs a messaging app on the linked Facebook Page. The Page
# subscribed_apps edge accepts Page webhook fields; Instagram comments remain an
# app-level Instagram webhook field. Keep the public capability contract at
# messages + comments while the Page installation itself verifies messages.
PAGE_WEBHOOK_INSTALL_FIELDS = ("messages",)
# The activation endpoint performs sequential Graph API calls. Without a
# per-call wall-clock deadline, one browser request could outlive the production
# origin request budget and surface as an unparseable Cloudflare response.
META_REQUEST_DEADLINE_SECONDS = 5.0

logger = logging.getLogger(__name__)


class MetaInstagramWebhookError(RuntimeError):
    """A stable, token-safe provider subscription failure."""

    def __init__(
        self,
        code: str,
        *,
        http_status: int | None = None,
        meta_error_code: int | None = None,
        error_subcode: int | None = None,
        trace_id: str | None = None,
    ):
        super().__init__(code)
        self.code = code
        self.http_status = http_status
        self.meta_error_code = meta_error_code
        self.error_subcode = error_subcode
        self.trace_id = trace_id


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


def _integer(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _trace_id(value: Any) -> str | None:
    rendered = _text(value)
    if not rendered or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", rendered):
        return None
    return rendered


def _provider_error(
    *,
    operation: str,
    response: httpx.Response,
    payload: dict[str, Any] | None,
) -> MetaInstagramWebhookError:
    error = (payload or {}).get("error")
    error = error if isinstance(error, dict) else {}
    meta_error_code = _integer(error.get("code"))
    error_subcode = _integer(error.get("error_subcode"))
    trace_id = _trace_id(error.get("fbtrace_id"))
    logger.warning(
        "instagram_webhook_meta_error operation=%s http_status=%s "
        "meta_error_code=%s error_subcode=%s trace_id=%s",
        operation,
        response.status_code,
        meta_error_code,
        error_subcode,
        trace_id,
    )
    return MetaInstagramWebhookError(
        "instagram_webhook_subscription_failed",
        http_status=response.status_code,
        meta_error_code=meta_error_code,
        error_subcode=error_subcode,
        trace_id=trace_id,
    )


async def _json(
    response: httpx.Response,
    *,
    operation: str,
) -> dict[str, Any]:
    payload: Any = None
    try:
        payload = response.json()
    except ValueError:
        if response.status_code >= 400:
            raise _provider_error(
                operation=operation,
                response=response,
                payload=None,
            ) from None
        raise MetaInstagramWebhookError(
            "instagram_webhook_subscription_failed",
            http_status=response.status_code,
        ) from None
    if response.status_code >= 400 or (
        isinstance(payload, dict) and isinstance(payload.get("error"), dict)
    ):
        raise _provider_error(
            operation=operation,
            response=response,
            payload=payload if isinstance(payload, dict) else None,
        )
    if not isinstance(payload, dict):
        raise MetaInstagramWebhookError(
            "instagram_webhook_subscription_failed",
            http_status=response.status_code,
        )
    return payload


async def _request_with_deadline(
    request: Awaitable[httpx.Response],
    *,
    operation: str,
) -> httpx.Response:
    """Bound one Graph request by wall-clock time, including connect + read."""
    try:
        return await asyncio.wait_for(
            request,
            timeout=META_REQUEST_DEADLINE_SECONDS,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "instagram_webhook_meta_timeout operation=%s timeout_seconds=%s",
            operation,
            META_REQUEST_DEADLINE_SECONDS,
        )
        raise MetaInstagramWebhookError(
            "instagram_webhook_subscription_failed"
        ) from None


def _actual_subscription_fields(
    payload: dict[str, Any],
    *,
    app_id: str,
) -> tuple[dict[str, Any] | None, set[str]]:
    app = next(
        (
            row
            for row in payload.get("data") or []
            if isinstance(row, dict) and _text(row.get("id")) == app_id
        ),
        None,
    )
    raw_fields = (app or {}).get("subscribed_fields") or []
    if isinstance(raw_fields, str):
        raw_fields = raw_fields.split(",")
    actual_fields = {_text(value) for value in raw_fields if _text(value)}
    return app, actual_fields


async def _install_linked_page_subscription(
    http: httpx.AsyncClient,
    *,
    app_id: str,
    page_id: str,
    page_token: str,
) -> None:
    """Install and read back the Facebook-Login messaging subscription."""
    edge = f"{_graph_base()}/{page_id}/subscribed_apps"
    subscribed_fields = ",".join(PAGE_WEBHOOK_INSTALL_FIELDS)
    install_response = await _request_with_deadline(
        http.post(
            edge,
            params={
                "subscribed_fields": subscribed_fields,
                "access_token": page_token,
                "appsecret_proof": _appsecret_proof(page_token),
            },
        ),
        operation="subscribe_linked_page_for_instagram",
    )
    installed = await _json(
        install_response,
        operation="subscribe_linked_page_for_instagram",
    )
    if installed.get("success") is not True:
        raise _provider_error(
            operation="subscribe_linked_page_for_instagram_unconfirmed",
            response=install_response,
            payload=installed,
        )

    verify_response = await _request_with_deadline(
        http.get(
            edge,
            params={
                "fields": "id,subscribed_fields",
                "access_token": page_token,
                "appsecret_proof": _appsecret_proof(page_token),
            },
        ),
        operation="verify_linked_page_instagram_subscription",
    )
    verified = await _json(
        verify_response,
        operation="verify_linked_page_instagram_subscription",
    )
    app, actual_fields = _actual_subscription_fields(verified, app_id=app_id)
    if not app or not set(PAGE_WEBHOOK_INSTALL_FIELDS).issubset(actual_fields):
        raise _provider_error(
            operation="verify_linked_page_instagram_subscription_unconfirmed",
            response=verify_response,
            payload=verified,
        )


async def subscribe_instagram_webhooks(
    db: Any,
    *,
    owner_user_id: str,
    instagram_account_id: str,
    page_id: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, ...]:
    """Install and verify receive-only Instagram webhook delivery.

    The selected Page-to-Instagram link is always verified first. Legacy Meta
    setups may accept the Instagram account edge directly. If that install is
    rejected, the current Facebook Login flow is installed on the linked Page's
    ``subscribed_apps`` edge with the transient Page token. No token is returned,
    persisted in the channel binding, or included in errors.
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
    http = client or httpx.AsyncClient(timeout=META_REQUEST_DEADLINE_SECONDS)
    try:
        pages_payload = await _json(
            await _request_with_deadline(
                http.get(
                    f"{_graph_base()}/me/accounts",
                    params={
                        "fields": "id,access_token,instagram_business_account{id}",
                        "limit": "100",
                        "access_token": user_token,
                        "appsecret_proof": _appsecret_proof(user_token),
                    },
                ),
                operation="resolve_linked_instagram_account",
            ),
            operation="resolve_linked_instagram_account",
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

        # Preserve the already-tested Instagram-account path for Meta setups
        # that accept it. The current Amasi Facebook Login grant rejects this
        # install, so only that installation failure falls back to the linked
        # Page edge. A successful install with an incomplete read-back still
        # fails closed and is never recorded as connected.
        instagram_edge = (
            f"{_graph_base()}/{instagram_account_id}/subscribed_apps"
        )
        subscribed_fields = ",".join(INSTAGRAM_WEBHOOK_FIELDS)
        try:
            install_response = await _request_with_deadline(
                http.post(
                    instagram_edge,
                    params={
                        "subscribed_fields": subscribed_fields,
                        "access_token": page_token,
                        "appsecret_proof": _appsecret_proof(page_token),
                    },
                ),
                operation="subscribe_instagram_account",
            )
            installed = await _json(
                install_response,
                operation="subscribe_instagram_account",
            )
            if installed.get("success") is not True:
                raise _provider_error(
                    operation="subscribe_instagram_account_unconfirmed",
                    response=install_response,
                    payload=installed,
                )
        except (MetaInstagramWebhookError, httpx.HTTPError) as primary_error:
            logger.warning(
                "instagram_webhook_subscription_fallback "
                "from_mode=instagram_account to_mode=linked_page "
                "http_status=%s meta_error_code=%s error_subcode=%s "
                "trace_id=%s exception_type=%s",
                getattr(primary_error, "http_status", None),
                getattr(primary_error, "meta_error_code", None),
                getattr(primary_error, "error_subcode", None),
                getattr(primary_error, "trace_id", None),
                type(primary_error).__name__,
            )
            await _install_linked_page_subscription(
                http,
                app_id=app_id,
                page_id=page_id,
                page_token=page_token,
            )
            return INSTAGRAM_WEBHOOK_FIELDS

        verify_response = await _request_with_deadline(
            http.get(
                instagram_edge,
                params={
                    "fields": "id,subscribed_fields",
                    "access_token": page_token,
                    "appsecret_proof": _appsecret_proof(page_token),
                },
            ),
            operation="verify_instagram_account_subscription",
        )
        verified = await _json(
            verify_response,
            operation="verify_instagram_account_subscription",
        )
        app, actual_fields = _actual_subscription_fields(verified, app_id=app_id)
        if not app or not set(INSTAGRAM_WEBHOOK_FIELDS).issubset(actual_fields):
            raise _provider_error(
                operation="verify_instagram_account_subscription_unconfirmed",
                response=verify_response,
                payload=verified,
            )
        return INSTAGRAM_WEBHOOK_FIELDS
    except httpx.HTTPError as exc:
        logger.warning(
            "instagram_webhook_meta_transport_error exception_type=%s",
            type(exc).__name__,
        )
        raise MetaInstagramWebhookError(
            "instagram_webhook_subscription_failed"
        ) from None
    finally:
        if owns_client:
            await http.aclose()


__all__ = [
    "INSTAGRAM_WEBHOOK_FIELDS",
    "META_REQUEST_DEADLINE_SECONDS",
    "PAGE_WEBHOOK_INSTALL_FIELDS",
    "MetaInstagramWebhookError",
    "subscribe_instagram_webhooks",
]
