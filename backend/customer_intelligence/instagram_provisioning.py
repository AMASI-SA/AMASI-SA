"""Owner-confirmed provisioning for a receive-only Instagram channel binding.

The Meta OAuth connection already discovers Instagram professional accounts.
This service exposes only opaque candidate references to the browser, resolves
the selected candidate server-side, and stores a non-reversible channel binding.
It never returns or stores a raw provider account ID or access token here.
"""
from __future__ import annotations

import hmac
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field
from pymongo.errors import DuplicateKeyError

from meta_instagram_webhooks import (
    MetaInstagramWebhookError,
    subscribe_instagram_webhooks,
)

from .channel_gateway import build_channel_account_key
from .foundation import CHANNELS_COLLECTION, ChannelRecord


# Stable read-only projection contracts owned by the Meta control plane. Keep
# this receive-only module independent of the control plane's eager router
# package so channel ingress can start in lightweight worker/test processes.
META_CREDENTIALS_COLLECTION = "mezan_meta_oauth_credentials_v2"
META_ASSETS_COLLECTION = "mezan_meta_assets_v2"

INSTAGRAM_REQUIRED_PERMISSIONS = frozenset(
    {
        "instagram_basic",
        "instagram_manage_comments",
        "instagram_manage_messages",
        "pages_manage_metadata",
    }
)
# This is deliberately separate from INSTAGRAM_REQUIRED_PERMISSIONS. Instagram
# receive-only setup must not be blocked by the Facebook Page messaging scope,
# but Meta's linked-Page subscribed_apps fallback requires both permissions.
PAGE_SUBSCRIPTION_PERMISSIONS = frozenset(
    {
        "pages_manage_metadata",
        "pages_messaging",
    }
)
INSTAGRAM_PROVISION_CONFIRMATION = "CONNECT_RECEIVE_ONLY_INSTAGRAM"


class InstagramProvisioningError(RuntimeError):
    def __init__(
        self,
        code: str,
        *,
        operation: str | None = None,
        http_status: int | None = None,
        meta_error_code: int | None = None,
        error_subcode: int | None = None,
        trace_id: str | None = None,
        page_subscription_permission_ready: bool | None = None,
        missing_page_permissions: tuple[str, ...] = (),
    ):
        super().__init__(code)
        self.code = code
        self.operation = operation
        self.http_status = http_status
        self.meta_error_code = meta_error_code
        self.error_subcode = error_subcode
        self.trace_id = trace_id
        self.page_subscription_permission_ready = page_subscription_permission_ready
        self.missing_page_permissions = tuple(missing_page_permissions)


class InstagramSetupModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InstagramCandidatePublic(InstagramSetupModel):
    candidate_ref: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


class InstagramSetupPublic(InstagramSetupModel):
    schema_version: Literal[1] = 1
    state: Literal[
        "ready",
        "connected",
        "meta_reauthorization_required",
        "no_instagram_account",
        "store_not_ready",
    ]
    candidates: list[InstagramCandidatePublic] = Field(default_factory=list)
    required_permissions_ready: bool
    receive_only: Literal[True] = True
    send_allowed: Literal[False] = False
    comment_reply_allowed: Literal[False] = False
    ai_auto_reply_allowed: Literal[False] = False


class InstagramProvisionIn(InstagramSetupModel):
    candidate_ref: str = Field(min_length=1, max_length=96)
    confirmation: Literal["CONNECT_RECEIVE_ONLY_INSTAGRAM"]


class InstagramProvisionResult(InstagramSetupModel):
    schema_version: Literal[1] = 1
    status: Literal["connected", "no_change"]
    provider: Literal["instagram"] = "instagram"
    channel_ref: str = Field(min_length=1)
    receive_only: Literal[True] = True
    send_allowed: Literal[False] = False
    comment_reply_allowed: Literal[False] = False
    ai_auto_reply_allowed: Literal[False] = False
    plaintext_credentials_stored: Literal[False] = False


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _candidate_ref(external_account_id: str) -> str:
    digest = build_channel_account_key("instagram", external_account_id).rsplit(":", 1)[-1]
    return f"instagram_candidate_{digest}"


def _channel_ref(channel_id: str) -> str:
    return f"instagram_channel_{build_channel_account_key('instagram', channel_id).rsplit(':', 1)[-1][:16]}"


async def _connected_store_id(db: Any, *, owner_user_id: str) -> str | None:
    cursor = db.salla_integrations.find(
        {"user_id": owner_user_id, "status": "connected"},
        {"_id": 0, "store_id": 1},
    ).limit(2)
    rows = await cursor.to_list(length=2)
    store_ids = {_text(row.get("store_id")) for row in rows if _text(row.get("store_id"))}
    return next(iter(store_ids)) if len(store_ids) == 1 else None


async def _assets(db: Any, *, owner_user_id: str) -> list[dict[str, Any]]:
    cursor = getattr(db, META_ASSETS_COLLECTION).find(
        {
            "user_id": owner_user_id,
            "provider": "meta_ads",
            "asset_type": "instagram_account",
            "connection_status": "connected",
        },
        {"_id": 0, "external_asset_id": 1, "display_name": 1, "page_id": 1},
    ).limit(100)
    return await cursor.to_list(length=100)


async def _granted_permissions(db: Any, *, owner_user_id: str) -> set[str]:
    credential = await getattr(db, META_CREDENTIALS_COLLECTION).find_one(
        {"user_id": owner_user_id, "provider": "meta_ads"},
        {"_id": 0, "scope": 1},
    )
    return {_text(value) for value in (credential or {}).get("scope") or [] if _text(value)}


async def _permissions_ready(db: Any, *, owner_user_id: str) -> bool:
    scopes = await _granted_permissions(db, owner_user_id=owner_user_id)
    return INSTAGRAM_REQUIRED_PERMISSIONS.issubset(scopes)


def _safe_binding(document: dict[str, Any], *, owner_user_id: str) -> bool:
    return bool(
        document.get("user_id") == owner_user_id
        and document.get("provider") == "instagram"
        and document.get("status") == "connected"
        and document.get("ingress_enabled") is True
        and document.get("egress_mode") == "disabled"
        and document.get("send_allowed") is False
        and document.get("ai_auto_reply_allowed") is False
        and document.get("plaintext_credentials_stored") is False
    )


def _subscription_confirmed(document: dict[str, Any]) -> bool:
    return document.get("webhook_subscription_status") == "confirmed"


WebhookSubscriber = Callable[..., Awaitable[tuple[str, ...]]]


class InstagramProvisioningService:
    def __init__(
        self,
        db: Any,
        *,
        now: Any | None = None,
        webhook_subscriber: WebhookSubscriber | None = None,
    ):
        self._db = db
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._webhook_subscriber = webhook_subscriber or subscribe_instagram_webhooks

    async def setup(self, *, owner_user_id: str) -> InstagramSetupPublic:
        owner_id = _text(owner_user_id)
        permissions_ready = await _permissions_ready(self._db, owner_user_id=owner_id)
        assets = await _assets(self._db, owner_user_id=owner_id)
        channels = getattr(self._db, CHANNELS_COLLECTION)
        connected = await channels.find_one(
            {"user_id": owner_id, "provider": "instagram", "status": "connected"},
            {"_id": 0},
        )
        if connected:
            if not _safe_binding(connected, owner_user_id=owner_id):
                raise InstagramProvisioningError("instagram_channel_policy_invalid")
            if _subscription_confirmed(connected):
                state = "connected"
            elif not permissions_ready:
                state = "meta_reauthorization_required"
            elif not assets:
                state = "no_instagram_account"
            else:
                state = "ready"
        elif not permissions_ready:
            state = "meta_reauthorization_required"
        elif not await _connected_store_id(self._db, owner_user_id=owner_id):
            state = "store_not_ready"
        elif not assets:
            state = "no_instagram_account"
        else:
            state = "ready"
        return InstagramSetupPublic(
            state=state,
            candidates=[
                InstagramCandidatePublic(
                    candidate_ref=_candidate_ref(_text(row.get("external_asset_id"))),
                    display_name=_text(row.get("display_name")) or "حساب إنستغرام",
                )
                for row in assets
                if _text(row.get("external_asset_id"))
            ],
            required_permissions_ready=permissions_ready,
        )

    async def provision(
        self,
        *,
        owner_user_id: str,
        request: InstagramProvisionIn,
    ) -> InstagramProvisionResult:
        owner_id = _text(owner_user_id)
        granted_permissions = await _granted_permissions(
            self._db,
            owner_user_id=owner_id,
        )
        if not INSTAGRAM_REQUIRED_PERMISSIONS.issubset(granted_permissions):
            raise InstagramProvisioningError("meta_reauthorization_required")
        merchant_id = await _connected_store_id(self._db, owner_user_id=owner_id)
        if not merchant_id:
            raise InstagramProvisioningError("connected_store_required")
        candidates = await _assets(self._db, owner_user_id=owner_id)
        selected = next(
            (
                row
                for row in candidates
                if hmac.compare_digest(
                    _candidate_ref(_text(row.get("external_asset_id"))),
                    request.candidate_ref,
                )
            ),
            None,
        )
        if not selected:
            raise InstagramProvisioningError("instagram_candidate_not_found")
        external_id = _text(selected.get("external_asset_id"))
        page_id = _text(selected.get("page_id"))
        if not page_id:
            raise InstagramProvisioningError("instagram_page_link_required")
        account_key = build_channel_account_key("instagram", external_id)
        digest = account_key.rsplit(":", 1)[-1]
        channel_id = f"instagram-{digest[:24]}"
        channels = getattr(self._db, CHANNELS_COLLECTION)
        existing = await channels.find_one(
            {"provider": "instagram", "external_account_key": account_key},
            {"_id": 0},
        )
        if existing:
            if (
                existing.get("merchant_id") != merchant_id
                or not _safe_binding(existing, owner_user_id=owner_id)
            ):
                raise InstagramProvisioningError("instagram_binding_conflict")
            channel_id = _text(existing.get("channel_id"))

        try:
            await self._webhook_subscriber(
                self._db,
                owner_user_id=owner_id,
                instagram_account_id=external_id,
                page_id=page_id,
            )
        except MetaInstagramWebhookError as exc:
            missing_page_permissions = tuple(
                sorted(PAGE_SUBSCRIPTION_PERMISSIONS - granted_permissions)
            )
            raise InstagramProvisioningError(
                exc.code,
                operation=exc.operation,
                http_status=exc.http_status,
                meta_error_code=exc.meta_error_code,
                error_subcode=exc.error_subcode,
                trace_id=exc.trace_id,
                page_subscription_permission_ready=not missing_page_permissions,
                missing_page_permissions=missing_page_permissions,
            ) from exc

        now = self._now()
        if existing:
            await channels.update_one(
                {"provider": "instagram", "external_account_key": account_key},
                {
                    "$set": {
                        "webhook_subscription_status": "confirmed",
                        "webhook_subscription_checked_at": now,
                        "updated_at": now,
                    }
                },
            )
            return InstagramProvisionResult(
                status="connected",
                channel_ref=_channel_ref(channel_id),
            )

        document = ChannelRecord(
            user_id=owner_id,
            merchant_id=merchant_id,
            channel_id=channel_id,
            provider="instagram",
            external_account_key=account_key,
            status="connected",
            ingress_enabled=True,
            egress_mode="disabled",
            send_allowed=False,
            ai_auto_reply_allowed=False,
            webhook_subscription_status="confirmed",
            webhook_subscription_checked_at=now,
            created_at=now,
            updated_at=now,
            plaintext_credentials_stored=False,
        ).model_dump(mode="python")
        try:
            await channels.insert_one(document)
        except DuplicateKeyError as exc:
            raise InstagramProvisioningError("instagram_binding_conflict") from exc
        return InstagramProvisionResult(
            status="connected",
            channel_ref=_channel_ref(channel_id),
        )


__all__ = [
    "INSTAGRAM_PROVISION_CONFIRMATION",
    "INSTAGRAM_REQUIRED_PERMISSIONS",
    "META_ASSETS_COLLECTION",
    "META_CREDENTIALS_COLLECTION",
    "PAGE_SUBSCRIPTION_PERMISSIONS",
    "InstagramCandidatePublic",
    "InstagramProvisionIn",
    "InstagramProvisionResult",
    "InstagramProvisioningError",
    "InstagramProvisioningService",
    "InstagramSetupPublic",
]
