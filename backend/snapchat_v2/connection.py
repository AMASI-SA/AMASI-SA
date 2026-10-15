"""Connection facade for the isolated Snapchat Integration V2 shadow plane."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from integrations_control_center.snapchat_discovery import discover_snapchat_accounts
from integrations_control_center.snapchat_oauth_security import (
    SNAPCHAT_PROVIDER_ID,
    start_snapchat_connection,
)

from .accounts import (
    ensure_account_indexes,
    get_selected_account,
    import_existing_accounts,
    list_accounts,
    select_account,
    sync_accounts,
)
from .token_store import SnapchatTokenStore, SnapchatTokenStoreError

SNAPCHAT_CONNECTIONS_COLLECTION = "mezan_snapchat_connections_v2"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_code(value: Any) -> str:
    text = str(value or "unknown").strip().lower().replace(" ", "_")
    return "".join(ch for ch in text if ch.isalnum() or ch in {"_", "-"})[:96]


class SnapchatConnectionError(RuntimeError):
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


class SnapchatConnectionManager:
    def __init__(
        self,
        db: Any,
        *,
        now: Callable[[], datetime] = _utcnow,
        token_store: SnapchatTokenStore | None = None,
        discover: Callable[[str], Any] = discover_snapchat_accounts,
    ) -> None:
        self.db = db
        self.now = now
        self.tokens = token_store or SnapchatTokenStore(db, now=now)
        self.discover = discover

    @property
    def collection(self) -> Any:
        return self.db[SNAPCHAT_CONNECTIONS_COLLECTION]

    async def ensure_indexes(self) -> None:
        await self.collection.create_index(
            [("user_id", 1), ("provider", 1)],
            unique=True,
            name="snapchat_v2_connection_user_unique",
        )
        await self.collection.create_index(
            [("connection_status", 1), ("updated_at", -1)],
            name="snapchat_v2_connection_status_latest",
        )
        await self.tokens.ensure_indexes()
        await ensure_account_indexes(self.db)

    async def start_oauth(self, user_id: str) -> dict[str, Any]:
        return await start_snapchat_connection(self.db, str(user_id))

    async def _write_connection(
        self,
        user_id: str,
        *,
        status: str,
        account_count: int,
        selected_account_id: str | None,
        permissions: list[str],
        error_code: str | None = None,
        needs_reauth: bool = False,
        discovery_source: str,
    ) -> None:
        now = self.now().astimezone(timezone.utc)
        await self.collection.update_one(
            {"user_id": str(user_id), "provider": SNAPCHAT_PROVIDER_ID},
            {
                "$set": {
                    "user_id": str(user_id),
                    "provider": SNAPCHAT_PROVIDER_ID,
                    "connection_status": status,
                    "account_count": max(int(account_count), 0),
                    "selected_account_id": selected_account_id,
                    "permissions": sorted(set(permissions)),
                    "needs_reauth": bool(needs_reauth),
                    "last_error_code": _safe_code(error_code) if error_code else None,
                    "discovery_source": discovery_source,
                    "checked_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )

    async def bootstrap_shadow(self, user_id: str) -> dict[str, Any]:
        await self.ensure_indexes()
        user_id = str(user_id)
        token = await self.tokens.snapshot(user_id)
        accounts = await import_existing_accounts(self.db, user_id)
        selected = await get_selected_account(self.db, user_id)
        connected = bool(token.get("connected"))
        status = (
            "connected"
            if connected and selected
            else "degraded"
            if connected
            else "not_connected"
        )
        await self._write_connection(
            user_id,
            status=status,
            account_count=sum(1 for row in accounts if row.get("active") is True),
            selected_account_id=(selected or {}).get("ad_account_id"),
            permissions=list(token.get("scope") or []),
            error_code=token.get("last_refresh_error_code"),
            needs_reauth=bool(token.get("needs_reauth")),
            discovery_source="existing_integrations_v2",
        )
        return await self.status(user_id)

    async def discover_accounts(self, user_id: str, *, force_refresh: bool = False) -> dict[str, Any]:
        await self.ensure_indexes()
        user_id = str(user_id)
        try:
            access_token = await self.tokens.get_access_token(
                user_id,
                force_refresh=force_refresh,
            )
            payload = await self.discover(access_token)
            accounts = await sync_accounts(
                self.db,
                user_id,
                list(payload.get("accounts") or []),
            )
            selected = await get_selected_account(self.db, user_id)
            token_snapshot = await self.tokens.snapshot(user_id)
            await self._write_connection(
                user_id,
                status="connected" if selected else "degraded",
                account_count=sum(1 for row in accounts if row.get("active") is True),
                selected_account_id=(selected or {}).get("ad_account_id"),
                permissions=list(token_snapshot.get("scope") or []),
                discovery_source="snapchat_api",
            )
            return {
                "status": "complete",
                "identity": dict(payload.get("identity") or {}),
                "organizations": list(payload.get("organizations") or []),
                "accounts": accounts,
                "selected_account": selected,
            }
        except SnapchatTokenStoreError as exc:
            await self._write_connection(
                user_id,
                status="needs_reauth" if exc.needs_reauth else "error",
                account_count=0,
                selected_account_id=None,
                permissions=[],
                error_code=exc.code,
                needs_reauth=exc.needs_reauth,
                discovery_source="snapchat_api",
            )
            raise SnapchatConnectionError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                needs_reauth=exc.needs_reauth,
            ) from exc
        except Exception as exc:  # noqa: BLE001
            code = _safe_code(exc) or "snapchat_account_discovery_failed"
            await self._write_connection(
                user_id,
                status="error",
                account_count=0,
                selected_account_id=None,
                permissions=[],
                error_code=code,
                discovery_source="snapchat_api",
            )
            raise SnapchatConnectionError(
                "snapchat_account_discovery_failed",
                "Snapchat account discovery failed.",
                retryable=True,
            ) from exc

    async def select_account(self, user_id: str, ad_account_id: str) -> dict[str, Any]:
        selected = await select_account(self.db, str(user_id), ad_account_id)
        token_snapshot = await self.tokens.snapshot(str(user_id))
        accounts = await list_accounts(self.db, str(user_id))
        await self._write_connection(
            str(user_id),
            status="connected" if token_snapshot.get("connected") else "not_connected",
            account_count=len(accounts),
            selected_account_id=selected.get("ad_account_id"),
            permissions=list(token_snapshot.get("scope") or []),
            error_code=token_snapshot.get("last_refresh_error_code"),
            needs_reauth=bool(token_snapshot.get("needs_reauth")),
            discovery_source="manual_selection",
        )
        return selected

    async def validate_ready(
        self,
        user_id: str,
        *,
        ad_account_id: str | None = None,
    ) -> tuple[str, dict[str, Any]]:
        user_id = str(user_id)
        if ad_account_id:
            await self.select_account(user_id, ad_account_id)
        selected = await get_selected_account(self.db, user_id)
        if not selected:
            await self.bootstrap_shadow(user_id)
            selected = await get_selected_account(self.db, user_id)
        if not selected:
            raise SnapchatConnectionError(
                "snapchat_selected_account_missing",
                "No primary Snapchat ad account is selected.",
            )
        try:
            access_token = await self.tokens.get_access_token(user_id)
        except SnapchatTokenStoreError as exc:
            raise SnapchatConnectionError(
                exc.code,
                exc.message,
                retryable=exc.retryable,
                needs_reauth=exc.needs_reauth,
            ) from exc
        return access_token, selected

    async def status(self, user_id: str) -> dict[str, Any]:
        user_id = str(user_id)
        row = await self.collection.find_one(
            {"user_id": user_id, "provider": SNAPCHAT_PROVIDER_ID},
            {"_id": 0},
        )
        token = await self.tokens.snapshot(user_id)
        accounts = await list_accounts(self.db, user_id, active_only=False)
        selected = next(
            (row for row in accounts if row.get("selected") is True and row.get("active") is True),
            None,
        )
        status = dict(row or {})
        status.update(
            {
                "provider": SNAPCHAT_PROVIDER_ID,
                "connected": bool(token.get("connected")),
                "token": token,
                "accounts": accounts,
                "selected_account": selected,
                "checked_at": self.now().astimezone(timezone.utc),
            }
        )
        return status
