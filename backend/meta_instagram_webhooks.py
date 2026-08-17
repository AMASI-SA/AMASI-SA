"""Secret-safe Meta asset subscription for Instagram webhooks.

The OAuth grant is stored by the integrations control plane.  This module is
kept outside that package so the receive-only customer-intelligence worker does
not import the control plane's eager router composition at startup.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from typing import Any

import httpx


META_CREDENTIALS_COLLECTION = "mezan_meta_oauth_credentials_v2"
META_DEFAULT_GRAPH_VERSION = "v25.0"
INSTAGRAM_WEBHOOK_FIELDS = ("messages", "comments")

log = logging.getLogger("mezan.meta_instagram_webhooks")


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _safe_trace_id(value: Any) -> str | None:
    rendered = "" if value is None else str(value).strip()
    if not rendered:
        return None
    return re.sub(r"[^A-Za-z0-9_-]", "", rendered)[:160] or None


class MetaInstagramWebhookError(RuntimeError):
    """A stable provider failure containing only token-safe diagnostics."""

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
        self.http_status = _optional_int(http_status)
        self.meta_error_code = _optional_int(meta_error_code)
        self.error_subcode = _optional_int(error_subcode)
        self.trace_id = _safe_trace_id(trace_id)

    @property
    def safe_diagnostics(self) -> dict[str, int | str]:
        values: dict[str, int | str | None] = {
            "http_status": self.http_status,
            "meta_error_code": self.meta_error_code,
            "error_subcode": self.error_subcode,
            "trace_id": self.trace_id,
        }
        return {key: value for key, value in values.items() if value is not None}


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


def _log_provider_failure(
    *,
    operation: str,
    error: MetaInstagramWebhookError,
) -> None:
    # Never log the request URL, query parameters, response body, token,
    # appsecret_proof, account identifier, or provider error message.
    log.warning(
        "meta_instagram_webhook_request_failed "
        "operation=%s http_status=%s meta_error_code=%s "
        "error_subcode=%s trace_id=%s",
        operation,
        error.http_status,
        error.meta_error_code,
        error.error_subcode,
        error.trace_id,
    )


def _response_failure(
    response: httpx.Response,
    *,
    payload: dict[str, Any] | None = None,
) -> MetaInstagramWebhookError:
    provider_error = (payload or {}).get("error")
    error = provider_error if isinstance(provider_error, dict) else {}
    return MetaInstagramWebhookError(
        "instagram_webhook_subscription_failed",
        http_status=response.status_code,
        meta_error_code=error.get("code"),
        error_subcode=error.get("error_subcode"),
        trace_id=error.get("fbtrace_id"),
    )


async def _json(
    response: httpx.Response,
    *,
    operation: str,
) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        error = _response_failure(response)
        _log_provider_failure(operation=operation, error=error)
        raise error from None
    if not isinstance(payload, dict):
        error = _response_failure(response)
        _log_provider_failure(operation=operation, error=error)
        raise error
    if response.status_code >= 400 or isinstance(payload.get("error"), dict):
        error = _response_failure(response, payload=payload)
        _log_provider_failure(operation=operation, error=error)
        raise error
    return payload


def _raise_subscription_failure(
    *,
    operation: str,
    http_status: int | None,
) -> None:
    error = MetaInstagramWebhookError(
        "instagram_webhook_subscription_failed",
        http_status=http_status,
    )
    _log_provider_failure(operation=operation, error=error)
    raise error


async def subscribe_instagram_webhooks(
    db: Any,
    *,
    owner_user_id: str,
    instagram_account_id: str,
    page_id: str,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, ...]:
    """Install and verify the app on a linked Instagram professional account.

    A Page Access Token is derived transiently from the encrypted user grant,
    then used on the linked Instagram account's ``subscribed_apps`` edge.
    Neither token is returned, persisted in the channel binding, nor included
    in logs or exceptions.  The operation is idempotent at Meta.
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
        pages_response = await http.get(
            f"{_graph_base()}/me/accounts",
            params={
                "fields": "id,access_token,instagram_business_account{id}",
                "limit": "100",
                "access_token": user_token,
                "appsecret_proof": _appsecret_proof(user_token),
            },
        )
        pages_payload = await _json(
            pages_response,
            operation="resolve_linked_page",
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

        # The selected Page and professional Instagram account were matched
        # above. Meta accepts the Page Access Token for the linked Instagram
        # account's subscribed_apps edge.
        edge = f"{_graph_base()}/{instagram_account_id}/subscribed_apps"
        subscribed_fields = ",".join(INSTAGRAM_WEBHOOK_FIELDS)
        installed_response = await http.post(
            edge,
            params={
                "subscribed_fields": subscribed_fields,
                "access_token": page_token,
                "appsecret_proof": _appsecret_proof(page_token),
            },
        )
        installed = await _json(
            installed_response,
            operation="subscribe_instagram_account",
        )
        if installed.get("success") is not True:
            _raise_subscription_failure(
                operation="subscribe_instagram_account",
                http_status=installed_response.status_code,
            )

        verified_response = await http.get(
            edge,
            params={
                "access_token": page_token,
                "appsecret_proof": _appsecret_proof(page_token),
            },
        )
        verified = await _json(
            verified_response,
            operation="verify_instagram_subscription",
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
            _raise_subscription_failure(
                operation="verify_instagram_subscription",
                http_status=verified_response.status_code,
            )
        return INSTAGRAM_WEBHOOK_FIELDS
    except MetaInstagramWebhookError:
        raise
    except httpx.HTTPError as exc:
        response = getattr(exc, "response", None)
        error = MetaInstagramWebhookError(
            "instagram_webhook_subscription_failed",
            http_status=getattr(response, "status_code", None),
        )
        _log_provider_failure(operation="transport", error=error)
        raise error from None
    finally:
        if owns_client:
            await http.aclose()


__all__ = [
    "INSTAGRAM_WEBHOOK_FIELDS",
    "MetaInstagramWebhookError",
    "subscribe_instagram_webhooks",
]
