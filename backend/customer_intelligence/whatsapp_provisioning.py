"""Governed provisioning for receive-only Meta WhatsApp channel bindings.

This module contains the reusable planning and apply operations.  Callers must
provide the Meta Phone Number ID directly; the value is validated, converted
to a non-reversible account key, and is never returned or persisted in raw
form.  Planning is read-only and applying is insert-only.  Existing bindings
are never rebound or silently repaired.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from .channel_gateway import build_channel_account_key
from .foundation import CHANNELS_COLLECTION, ChannelRecord

PHONE_NUMBER_ID_ENV = "MEZAN_WHATSAPP_PHONE_NUMBER_ID"
BINDING_HMAC_ENV = "MEZAN_CHANNEL_BINDING_HMAC_KEY"
PROVIDER = "whatsapp"
MIN_HMAC_KEY_LENGTH = 32
MAX_SCOPED_CHANNELS = 20
PROVISION_LOCKS_COLLECTION = "mezan_customer_channel_provision_locks_v1"
PROVISION_LEASE_SECONDS = 120


class ProvisioningError(RuntimeError):
    """Fail-closed operator error containing no provider secret values."""


@dataclass(frozen=True)
class ProvisioningPlan:
    """An immutable, tenant-scoped insert or idempotent no-op plan."""

    action: str
    document: dict[str, Any]
    binding_fingerprint: str
    owner_ref: str
    merchant_ref: str
    scope_filter: dict[str, Any]
    expected_scope_tokens: tuple[str, ...]
    requires_additional_channel_gate: bool = False

    def public(self, *, applied: bool = False) -> dict[str, Any]:
        """Return the provider-safe operator representation of this plan."""

        mode = (
            "no_change"
            if self.action == "noop"
            else ("applied" if applied else "dry_run")
        )
        return {
            "ok": True,
            "mode": mode,
            "action": self.action,
            "provider": PROVIDER,
            "channel_ref": _opaque_ref("channel", self.document["channel_id"]),
            "owner_ref": self.owner_ref,
            "merchant_ref": self.merchant_ref,
            "binding_fingerprint": self.binding_fingerprint,
            "existing_channel_count": len(self.expected_scope_tokens),
            "write_required": self.action != "noop",
            "additional_channel_gate_required": (self.requires_additional_channel_gate),
            "receive_only": True,
            "send_allowed": False,
            "ai_auto_reply_allowed": False,
            "plaintext_credentials_stored": False,
        }


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _opaque_ref(namespace: str, value: str) -> str:
    digest = hashlib.sha256(f"{namespace}\x1f{value}".encode("utf-8")).hexdigest()
    return digest[:12]


def _binding_digest(account_key: str) -> str:
    return account_key.rsplit(":", 1)[-1]


def _binding_fingerprint(account_key: str) -> str:
    return _binding_digest(account_key)[:12]


def _scope_token(document: dict[str, Any]) -> str:
    """Hash relevant state for the pre-insert concurrency check."""

    fields = (
        "_id",
        "schema_version",
        "user_id",
        "merchant_id",
        "channel_id",
        "provider",
        "external_account_key",
        "status",
        "ingress_enabled",
        "egress_mode",
        "send_allowed",
        "ai_auto_reply_allowed",
        "updated_at",
        "plaintext_credentials_stored",
    )
    state = {field: str(document.get(field)) for field in fields}
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scope_tokens(rows: list[dict[str, Any]]) -> tuple[str, ...]:
    return tuple(sorted(_scope_token(row) for row in rows))


def _scope_lock_id(scope_filter: dict[str, Any]) -> str:
    encoded = json.dumps(scope_filter, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(
        f"mezan-channel-provision-v1\x1f{encoded}".encode("utf-8")
    ).hexdigest()
    return f"scope:v1:{digest}"


async def _rows(
    collection: Any,
    query: dict[str, Any],
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    cursor = collection.find(
        query,
        {
            "_id": 1,
            "schema_version": 1,
            "id": 1,
            "role": 1,
            "user_id": 1,
            "store_id": 1,
            "status": 1,
            "merchant_id": 1,
            "channel_id": 1,
            "provider": 1,
            "external_account_key": 1,
            "ingress_enabled": 1,
            "egress_mode": 1,
            "send_allowed": 1,
            "ai_auto_reply_allowed": 1,
            "created_at": 1,
            "updated_at": 1,
            "plaintext_credentials_stored": 1,
        },
    )
    cursor = cursor.limit(limit)
    return await cursor.to_list(length=limit)


def _store_values(value: str) -> list[Any]:
    values: list[Any] = [value]
    if value.isdigit():
        try:
            values.append(int(value))
        except (TypeError, ValueError, OverflowError):
            pass
    return values


async def _resolve_owner(db: Any, owner_id: str | None) -> dict[str, Any]:
    if owner_id:
        rows = await _rows(db.users, {"id": owner_id, "role": "owner"}, limit=2)
    else:
        rows = await _rows(db.users, {"role": "owner"}, limit=2)
    if len(rows) != 1 or not _text(rows[0].get("id")):
        raise ProvisioningError("exactly one Owner must match the provisioning scope")
    return rows[0]


async def _resolve_store(
    db: Any,
    *,
    owner_id: str,
    merchant_id: str | None,
) -> dict[str, Any]:
    query: dict[str, Any] = {"user_id": owner_id, "status": "connected"}
    if merchant_id:
        query["store_id"] = {"$in": _store_values(merchant_id)}
    rows = await _rows(db.salla_integrations, query, limit=3)
    rows = [row for row in rows if _text(row.get("store_id"))]
    if len(rows) != 1:
        raise ProvisioningError(
            "exactly one connected Salla store must match the Owner scope"
        )
    return rows[0]


def _assert_safe_existing_channel(document: dict[str, Any]) -> None:
    account_key = _text(document.get("external_account_key"))
    safe = (
        bool(_text(document.get("channel_id")))
        and bool(re.fullmatch(r"account:v1:[0-9a-f]{64}", account_key))
        and document.get("egress_mode") == "disabled"
        and document.get("send_allowed") is False
        and document.get("ai_auto_reply_allowed") is False
        and document.get("plaintext_credentials_stored") is False
    )
    if not safe:
        raise ProvisioningError(
            "the existing store channel is not a safe receive-only binding"
        )


def _assert_exact_binding_is_canonical(
    document: dict[str, Any],
    *,
    owner_id: str,
    merchant_id: str,
    account_key: str,
) -> None:
    expected = {
        "schema_version": 1,
        "user_id": owner_id,
        "merchant_id": merchant_id,
        "provider": PROVIDER,
        "external_account_key": account_key,
        "status": "connected",
        "ingress_enabled": True,
        "egress_mode": "disabled",
        "send_allowed": False,
        "ai_auto_reply_allowed": False,
        "plaintext_credentials_stored": False,
    }
    if not _text(document.get("channel_id")) or any(
        document.get(key) != value for key, value in expected.items()
    ):
        raise ProvisioningError(
            "the matching binding is not in the canonical receive-only state"
        )


async def build_plan(
    db: Any,
    *,
    phone_number_id: str,
    owner_id: str | None = None,
    merchant_id: str | None = None,
    allow_additional_channel: bool = False,
    now: datetime | None = None,
) -> ProvisioningPlan:
    """Build a read-only, fail-closed provisioning plan."""

    binding_secret = _text(os.environ.get(BINDING_HMAC_ENV))
    if len(binding_secret) < MIN_HMAC_KEY_LENGTH:
        raise ProvisioningError(
            f"{BINDING_HMAC_ENV} must be configured explicitly with at least "
            f"{MIN_HMAC_KEY_LENGTH} characters"
        )
    normalized_phone_number_id = _text(phone_number_id)
    if not re.fullmatch(r"[0-9]{5,30}", normalized_phone_number_id):
        raise ProvisioningError(
            f"{PHONE_NUMBER_ID_ENV} must contain a valid Meta Phone Number ID"
        )

    owner = await _resolve_owner(db, _text(owner_id) or None)
    resolved_owner_id = _text(owner["id"])
    store = await _resolve_store(
        db,
        owner_id=resolved_owner_id,
        merchant_id=_text(merchant_id) or None,
    )
    resolved_merchant_id = _text(store["store_id"])
    account_key = build_channel_account_key(PROVIDER, normalized_phone_number_id)
    channels = getattr(db, CHANNELS_COLLECTION)

    globally_bound = await _rows(
        channels,
        {"provider": PROVIDER, "external_account_key": account_key},
        limit=2,
    )
    if len(globally_bound) > 1:
        raise ProvisioningError("the provider binding is duplicated")

    scope_filter = {
        "user_id": resolved_owner_id,
        "merchant_id": resolved_merchant_id,
        "provider": PROVIDER,
    }
    scoped = await _rows(channels, scope_filter, limit=MAX_SCOPED_CHANNELS + 1)
    if len(scoped) > MAX_SCOPED_CHANNELS:
        raise ProvisioningError("too many WhatsApp channels exist in this store scope")

    timestamp = now or datetime.now(timezone.utc)
    owner_ref = _opaque_ref("owner", resolved_owner_id)
    merchant_ref = _opaque_ref("merchant", resolved_merchant_id)
    fingerprint = _binding_fingerprint(account_key)

    if globally_bound:
        bound = globally_bound[0]
        if (
            _text(bound.get("user_id")) != resolved_owner_id
            or _text(bound.get("merchant_id")) != resolved_merchant_id
        ):
            raise ProvisioningError("the provider binding belongs to another tenant")
        _assert_exact_binding_is_canonical(
            bound,
            owner_id=resolved_owner_id,
            merchant_id=resolved_merchant_id,
            account_key=account_key,
        )
        if len(scoped) > 2:
            raise ProvisioningError(
                "too many different WhatsApp channels exist in this store scope"
            )
        for sibling in scoped:
            _assert_safe_existing_channel(sibling)
        return ProvisioningPlan(
            action="noop",
            document=bound,
            binding_fingerprint=fingerprint,
            owner_ref=owner_ref,
            merchant_ref=merchant_ref,
            scope_filter=scope_filter,
            expected_scope_tokens=_scope_tokens(scoped),
        )

    if len(scoped) > 1:
        raise ProvisioningError(
            "multiple different WhatsApp channels already exist in this store scope"
        )
    if scoped:
        _assert_safe_existing_channel(scoped[0])
        if not allow_additional_channel:
            raise ProvisioningError(
                "--allow-additional-channel is required to preserve the existing "
                "channel and insert a separate Meta binding"
            )

    resolved_channel_id = f"whatsapp-{_binding_digest(account_key)[:24]}"
    if any(
        _text(channel.get("channel_id")) == resolved_channel_id for channel in scoped
    ):
        raise ProvisioningError("the derived channel identity conflicts in this scope")

    document = ChannelRecord(
        user_id=resolved_owner_id,
        merchant_id=resolved_merchant_id,
        channel_id=resolved_channel_id,
        provider=PROVIDER,
        external_account_key=account_key,
        status="connected",
        ingress_enabled=True,
        egress_mode="disabled",
        send_allowed=False,
        ai_auto_reply_allowed=False,
        created_at=timestamp,
        updated_at=timestamp,
        plaintext_credentials_stored=False,
    ).model_dump(mode="python")

    return ProvisioningPlan(
        action="insert_additional_channel" if scoped else "insert",
        document=document,
        binding_fingerprint=fingerprint,
        owner_ref=owner_ref,
        merchant_ref=merchant_ref,
        scope_filter=scope_filter,
        expected_scope_tokens=_scope_tokens(scoped),
        requires_additional_channel_gate=bool(scoped),
    )


async def _acquire_scope_lease(
    db: Any,
    *,
    plan: ProvisioningPlan,
    lease_owner: str,
    now: datetime,
) -> None:
    lock_id = _scope_lock_id(plan.scope_filter)
    expires_at = now + timedelta(seconds=PROVISION_LEASE_SECONDS)
    locks = getattr(db, PROVISION_LOCKS_COLLECTION)
    try:
        acquired = await locks.find_one_and_update(
            {
                "_id": lock_id,
                "$or": [
                    {"lease_expires_at": {"$lte": now}},
                    {"lease_owner": lease_owner},
                ],
            },
            {
                "$set": {
                    "schema_version": 1,
                    "scope_ref": _opaque_ref(
                        "provision-scope",
                        json.dumps(
                            plan.scope_filter,
                            sort_keys=True,
                            separators=(",", ":"),
                        ),
                    ),
                    "lease_owner": lease_owner,
                    "acquired_at": now,
                    "lease_expires_at": expires_at,
                },
                "$setOnInsert": {"_id": lock_id},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise ProvisioningError(
            "another provisioning operation holds the store scope lease"
        ) from exc
    if not acquired or acquired.get("lease_owner") != lease_owner:
        raise ProvisioningError(
            "another provisioning operation holds the store scope lease"
        )


async def _release_scope_lease(
    db: Any,
    *,
    plan: ProvisioningPlan,
    lease_owner: str,
) -> None:
    locks = getattr(db, PROVISION_LOCKS_COLLECTION)
    await locks.delete_one(
        {
            "_id": _scope_lock_id(plan.scope_filter),
            "lease_owner": lease_owner,
        }
    )


async def apply_plan(
    db: Any,
    plan: ProvisioningPlan,
    *,
    allow_additional_channel: bool = False,
    lease_owner: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Apply a previously built plan after leasing and rechecking its scope."""

    if plan.requires_additional_channel_gate and not allow_additional_channel:
        raise ProvisioningError(
            "--allow-additional-channel is required for this insert"
        )

    resolved_lease_owner = lease_owner or secrets.token_hex(24)
    lease_now = now or datetime.now(timezone.utc)
    await _acquire_scope_lease(
        db,
        plan=plan,
        lease_owner=resolved_lease_owner,
        now=lease_now,
    )

    collection = getattr(db, CHANNELS_COLLECTION)
    try:
        current_scope = await _rows(
            collection,
            plan.scope_filter,
            limit=MAX_SCOPED_CHANNELS + 1,
        )
        if _scope_tokens(current_scope) != plan.expected_scope_tokens:
            raise ProvisioningError(
                "the store channel scope changed after planning; no insert was applied"
            )
        if plan.action == "noop":
            return plan.public(applied=True)
        try:
            await collection.insert_one(plan.document)
        except DuplicateKeyError as exc:
            raise ProvisioningError(
                "a conflicting channel binding already exists"
            ) from exc
        return plan.public(applied=True)
    finally:
        await _release_scope_lease(
            db,
            plan=plan,
            lease_owner=resolved_lease_owner,
        )


__all__ = [
    "BINDING_HMAC_ENV",
    "PHONE_NUMBER_ID_ENV",
    "ProvisioningError",
    "ProvisioningPlan",
    "apply_plan",
    "build_channel_account_key",
    "build_plan",
]
