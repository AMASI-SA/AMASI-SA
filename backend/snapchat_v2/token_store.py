"""Encrypted Snapchat OAuth grant access with a distributed refresh lease.

The V2 reporting plane reuses the platform-owned OAuth grant written by the
existing callback. Tokens are decrypted only inside this module and are never
returned by status/report APIs or written to logs.
"""
from __future__ import annotations

import asyncio
import math
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

import httpx

from integrations_control_center.snapchat_oauth_security import (
    SNAPCHAT_CREDENTIALS_COLLECTION,
    SNAPCHAT_PROVIDER_ID,
    SNAPCHAT_REQUESTED_SCOPES,
    SNAPCHAT_TOKEN_URL,
    decrypt_snapchat_token,
    encrypt_snapchat_token,
)

REFRESH_LEASE_TTL = timedelta(seconds=45)
ACCESS_TOKEN_SKEW = timedelta(seconds=120)
REFRESH_WAIT_SECONDS = 8.0
REFRESH_RETRIES = 2


class SnapchatTokenStoreError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        needs_reauth: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.needs_reauth = needs_reauth


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        current = value
    else:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            current = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc)


def _credentials_query(user_id: str) -> dict[str, Any]:
    return {
        "user_id": str(user_id),
        "$or": [
            {"provider": SNAPCHAT_PROVIDER_ID},
            {"provider": {"$exists": False}},
        ],
    }


def _safe_error_code(value: Any) -> str:
    text = str(value or "unknown").strip().lower().replace(" ", "_")
    return "".join(ch for ch in text if ch.isalnum() or ch in {"_", "-"})[:96]


class SnapchatTokenStore:
    def __init__(
        self,
        db: Any,
        *,
        now: Callable[[], datetime] = _utcnow,
        client_factory: Callable[..., Any] = httpx.AsyncClient,
    ) -> None:
        self.db = db
        self.now = now
        self.client_factory = client_factory

    @property
    def collection(self) -> Any:
        return self.db[SNAPCHAT_CREDENTIALS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self.collection.create_index(
            [("user_id", 1), ("provider", 1)],
            name="snapchat_v2_credentials_lookup",
        )
        await self.collection.create_index(
            [("refresh_lease_expires_at", 1)],
            name="snapchat_v2_refresh_lease_expiry",
        )

    async def _read(self, user_id: str, *, include_secrets: bool) -> dict[str, Any] | None:
        projection: dict[str, int] = {
            "_id": 0,
            "user_id": 1,
            "provider": 1,
            "access_token_expires_at": 1,
            "scope": 1,
            "last_refresh_success_at": 1,
            "last_refresh_error_code": 1,
            "refresh_lease_owner_id": 1,
            "refresh_lease_expires_at": 1,
            "credential_revision": 1,
            "updated_at": 1,
        }
        if include_secrets:
            projection["access_token_ciphertext"] = 1
            projection["refresh_token_ciphertext"] = 1
        return await self.collection.find_one(_credentials_query(user_id), projection)

    async def snapshot(self, user_id: str) -> dict[str, Any]:
        row = await self._read(user_id, include_secrets=False)
        if not row:
            return {
                "connected": False,
                "provider": SNAPCHAT_PROVIDER_ID,
                "needs_reauth": False,
            }
        expires_at = _as_utc(row.get("access_token_expires_at"))
        now = self.now().astimezone(timezone.utc)
        return {
            "connected": True,
            "provider": SNAPCHAT_PROVIDER_ID,
            "access_token_expires_at": expires_at,
            "access_token_fresh": bool(expires_at and expires_at > now + ACCESS_TOKEN_SKEW),
            "scope": list(row.get("scope") or []),
            "last_refresh_success_at": _as_utc(row.get("last_refresh_success_at")),
            "last_refresh_error_code": row.get("last_refresh_error_code"),
            "refresh_in_progress": bool(
                row.get("refresh_lease_owner_id")
                and (_as_utc(row.get("refresh_lease_expires_at")) or now) > now
            ),
            "credential_revision": int(row.get("credential_revision") or 0),
            "needs_reauth": row.get("last_refresh_error_code") == "snapchat_needs_reauth",
        }

    async def _acquire_refresh_lease(self, user_id: str, owner_id: str) -> bool:
        now = self.now().astimezone(timezone.utc)
        lease_gate = {
            "$or": [
                {"refresh_lease_expires_at": {"$exists": False}},
                {"refresh_lease_expires_at": {"$lte": now}},
                {"refresh_lease_owner_id": owner_id},
            ]
        }
        result = await self.collection.update_one(
            {"$and": [_credentials_query(user_id), lease_gate]},
            {
                "$set": {
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "refresh_lease_owner_id": owner_id,
                    "refresh_lease_acquired_at": now,
                    "refresh_lease_heartbeat_at": now,
                    "refresh_lease_expires_at": now + REFRESH_LEASE_TTL,
                    "updated_at": now,
                }
            },
        )
        return int(getattr(result, "modified_count", 0) or 0) == 1

    async def _release_refresh_lease(self, user_id: str, owner_id: str) -> None:
        now = self.now().astimezone(timezone.utc)
        await self.collection.update_one(
            {"$and": [_credentials_query(user_id), {"refresh_lease_owner_id": owner_id}]},
            {
                "$set": {"updated_at": now},
                "$unset": {
                    "refresh_lease_owner_id": "",
                    "refresh_lease_acquired_at": "",
                    "refresh_lease_heartbeat_at": "",
                    "refresh_lease_expires_at": "",
                },
            },
        )

    async def _wait_for_peer_refresh(self, user_id: str) -> str | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + REFRESH_WAIT_SECONDS
        while loop.time() < deadline:
            await asyncio.sleep(0.25)
            row = await self._read(user_id, include_secrets=True)
            if not row:
                return None
            expires_at = _as_utc(row.get("access_token_expires_at"))
            if expires_at and expires_at > self.now().astimezone(timezone.utc) + ACCESS_TOKEN_SKEW:
                try:
                    token = decrypt_snapchat_token(row.get("access_token_ciphertext"))
                except ValueError as exc:
                    raise SnapchatTokenStoreError(
                        "snapchat_credential_decryption_failed",
                        "Snapchat credentials could not be decrypted.",
                    ) from exc
                return token or None
            lease_expiry = _as_utc(row.get("refresh_lease_expires_at"))
            if not row.get("refresh_lease_owner_id") or not lease_expiry or lease_expiry <= self.now().astimezone(timezone.utc):
                return None
        return None

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        raw = str(response.headers.get("Retry-After") or "").strip()
        try:
            seconds = float(raw)
        except (TypeError, ValueError, OverflowError):
            seconds = float(2**attempt)
        if not math.isfinite(seconds):
            seconds = float(2**attempt)
        return min(max(seconds, 0.25), 5.0)

    async def _provider_refresh(self, refresh_token: str) -> dict[str, Any]:
        client_id = os.environ.get("SNAPCHAT_MARKETING_CLIENT_ID", "").strip()
        client_secret = os.environ.get("SNAPCHAT_MARKETING_CLIENT_SECRET", "").strip()
        if not client_id or not client_secret:
            raise SnapchatTokenStoreError(
                "snapchat_oauth_not_configured",
                "Snapchat platform OAuth settings are incomplete.",
            )

        data = {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
        }
        response: httpx.Response | None = None
        try:
            async with self.client_factory(timeout=25.0) as client:
                for attempt in range(REFRESH_RETRIES + 1):
                    response = await client.post(SNAPCHAT_TOKEN_URL, data=data)
                    if response.status_code == 400 and "invalid_client" in (response.text or "").lower():
                        response = await client.post(
                            SNAPCHAT_TOKEN_URL,
                            data={
                                "refresh_token": refresh_token,
                                "grant_type": "refresh_token",
                            },
                            auth=(client_id, client_secret),
                        )
                    if response.status_code not in {429} and response.status_code < 500:
                        break
                    if attempt < REFRESH_RETRIES:
                        await asyncio.sleep(self._retry_delay(response, attempt))
        except httpx.HTTPError as exc:
            raise SnapchatTokenStoreError(
                "snapchat_token_refresh_network_error",
                "Snapchat token refresh failed.",
                retryable=True,
            ) from exc

        if response is None:
            raise SnapchatTokenStoreError(
                "snapchat_token_refresh_missing_response",
                "Snapchat token refresh failed.",
                retryable=True,
            )
        try:
            payload = response.json() or {}
        except (TypeError, ValueError):
            payload = {}
        if response.status_code >= 400:
            combined = " ".join(
                [
                    str(payload.get("error") or ""),
                    str(payload.get("error_description") or ""),
                    str(response.text or ""),
                ]
            ).lower()
            if "invalid_grant" in combined:
                raise SnapchatTokenStoreError(
                    "snapchat_needs_reauth",
                    "Snapchat authorization must be renewed.",
                    needs_reauth=True,
                )
            if "invalid_client" in combined:
                raise SnapchatTokenStoreError(
                    "snapchat_oauth_not_configured",
                    "Snapchat OAuth client credentials were rejected.",
                )
            raise SnapchatTokenStoreError(
                f"snapchat_token_refresh_http_{response.status_code}",
                "Snapchat temporarily rejected the token refresh.",
                retryable=response.status_code == 429 or response.status_code >= 500,
            )
        if not isinstance(payload, dict):
            raise SnapchatTokenStoreError(
                "snapchat_token_refresh_invalid_payload",
                "Snapchat token response was invalid.",
                retryable=True,
            )
        return payload

    async def _mark_refresh_error(self, user_id: str, owner_id: str, code: str) -> None:
        now = self.now().astimezone(timezone.utc)
        await self.collection.update_one(
            {"$and": [_credentials_query(user_id), {"refresh_lease_owner_id": owner_id}]},
            {
                "$set": {
                    "last_refresh_error_code": _safe_error_code(code),
                    "last_refresh_error_at": now,
                    "updated_at": now,
                }
            },
        )

    async def get_access_token(self, user_id: str, *, force_refresh: bool = False) -> str:
        row = await self._read(user_id, include_secrets=True)
        if not row:
            raise SnapchatTokenStoreError(
                "snapchat_not_connected",
                "Snapchat is not connected.",
            )
        try:
            access = decrypt_snapchat_token(row.get("access_token_ciphertext"))
            refresh = decrypt_snapchat_token(row.get("refresh_token_ciphertext"))
        except ValueError as exc:
            raise SnapchatTokenStoreError(
                "snapchat_credential_decryption_failed",
                "Snapchat credentials could not be decrypted.",
            ) from exc
        if not refresh:
            raise SnapchatTokenStoreError(
                "snapchat_needs_reauth",
                "Snapchat authorization must be renewed.",
                needs_reauth=True,
            )
        expires_at = _as_utc(row.get("access_token_expires_at"))
        now = self.now().astimezone(timezone.utc)
        if not force_refresh and access and expires_at and expires_at > now + ACCESS_TOKEN_SKEW:
            return access

        owner_id = f"refresh:{uuid.uuid4()}"
        if not await self._acquire_refresh_lease(user_id, owner_id):
            peer_token = await self._wait_for_peer_refresh(user_id)
            if peer_token:
                return peer_token
            if not await self._acquire_refresh_lease(user_id, owner_id):
                raise SnapchatTokenStoreError(
                    "snapchat_token_refresh_busy",
                    "Another worker is refreshing Snapchat credentials.",
                    retryable=True,
                )

        try:
            locked = await self._read(user_id, include_secrets=True)
            if not locked:
                raise SnapchatTokenStoreError(
                    "snapchat_not_connected",
                    "Snapchat is not connected.",
                )
            locked_expiry = _as_utc(locked.get("access_token_expires_at"))
            if not force_refresh and locked_expiry and locked_expiry > self.now().astimezone(timezone.utc) + ACCESS_TOKEN_SKEW:
                current = decrypt_snapchat_token(locked.get("access_token_ciphertext"))
                if current:
                    return current
            original_refresh_ciphertext = locked.get("refresh_token_ciphertext")
            locked_refresh = decrypt_snapchat_token(original_refresh_ciphertext)
            if not locked_refresh:
                raise SnapchatTokenStoreError(
                    "snapchat_needs_reauth",
                    "Snapchat authorization must be renewed.",
                    needs_reauth=True,
                )
            try:
                payload = await self._provider_refresh(locked_refresh)
            except SnapchatTokenStoreError as exc:
                if exc.code == "snapchat_needs_reauth":
                    latest = await self._read(user_id, include_secrets=True)
                    latest_ciphertext = (latest or {}).get("refresh_token_ciphertext")
                    if latest_ciphertext and latest_ciphertext != original_refresh_ciphertext:
                        latest_refresh = decrypt_snapchat_token(latest_ciphertext)
                        payload = await self._provider_refresh(latest_refresh)
                        original_refresh_ciphertext = latest_ciphertext
                    else:
                        await self._mark_refresh_error(user_id, owner_id, exc.code)
                        raise
                else:
                    await self._mark_refresh_error(user_id, owner_id, exc.code)
                    raise

            new_access = str(payload.get("access_token") or "").strip()
            if not new_access:
                raise SnapchatTokenStoreError(
                    "snapchat_token_missing",
                    "Snapchat token response was incomplete.",
                    retryable=True,
                )
            new_refresh = str(payload.get("refresh_token") or locked_refresh).strip()
            try:
                expires_in = max(int(payload.get("expires_in") or 3600), 60)
            except (TypeError, ValueError, OverflowError):
                expires_in = 3600
            saved_at = self.now().astimezone(timezone.utc)
            save_query = {
                "$and": [
                    _credentials_query(user_id),
                    {"refresh_lease_owner_id": owner_id},
                    {"refresh_token_ciphertext": original_refresh_ciphertext},
                ]
            }
            result = await self.collection.update_one(
                save_query,
                {
                    "$set": {
                        "provider": SNAPCHAT_PROVIDER_ID,
                        "access_token_ciphertext": encrypt_snapchat_token(new_access),
                        "refresh_token_ciphertext": encrypt_snapchat_token(new_refresh),
                        "access_token_expires_at": saved_at + timedelta(seconds=expires_in),
                        "scope": payload.get("scope")
                        or locked.get("scope")
                        or list(SNAPCHAT_REQUESTED_SCOPES),
                        "last_refresh_success_at": saved_at,
                        "last_refresh_error_code": None,
                        "updated_at": saved_at,
                    },
                    "$inc": {"credential_revision": 1},
                },
            )
            if int(getattr(result, "modified_count", 0) or 0) != 1:
                latest = await self._read(user_id, include_secrets=True)
                latest_expiry = _as_utc((latest or {}).get("access_token_expires_at"))
                if latest_expiry and latest_expiry > saved_at + ACCESS_TOKEN_SKEW:
                    latest_access = decrypt_snapchat_token(
                        (latest or {}).get("access_token_ciphertext")
                    )
                    if latest_access:
                        return latest_access
                raise SnapchatTokenStoreError(
                    "snapchat_refresh_rotation_race",
                    "Snapchat credentials changed during refresh; retry safely.",
                    retryable=True,
                )
            return new_access
        finally:
            await self._release_refresh_lease(user_id, owner_id)
