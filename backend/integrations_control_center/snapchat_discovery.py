"""Read-only Snapchat organization and ad-account discovery for Mezan V2."""
from __future__ import annotations

from typing import Any

import httpx

SNAPCHAT_API_BASE = "https://adsapi.snapchat.com/v1"
MAX_ORGANIZATIONS = 50
MAX_AD_ACCOUNTS = 200


def _unwrap(value: Any, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    nested = value.get(key)
    return nested if isinstance(nested, dict) else value


async def discover_snapchat_accounts(access_token: str) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
    }
    identity: dict[str, Any] = {}
    organizations: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=25.0, headers=headers) as client:
        me_response = await client.get(f"{SNAPCHAT_API_BASE}/me")
        if me_response.status_code >= 400:
            raise RuntimeError(f"snapchat_me_http_{me_response.status_code}")
        identity = _unwrap(me_response.json().get("me") or {}, "me")

        org_response = await client.get(
            f"{SNAPCHAT_API_BASE}/me/organizations",
            params={"with_ad_accounts": "true"},
        )
        if org_response.status_code >= 400:
            raise RuntimeError(
                f"snapchat_organizations_http_{org_response.status_code}"
            )
        payload = org_response.json()
        wrappers = payload.get("organizations") or []
        if not isinstance(wrappers, list):
            raise RuntimeError("snapchat_organizations_invalid_payload")

        for wrapped in wrappers[:MAX_ORGANIZATIONS]:
            org = _unwrap(wrapped, "organization")
            org_id = str(org.get("id") or "").strip()
            if not org_id:
                continue
            organization = {
                "organization_id": org_id,
                "organization_name": org.get("name") or org_id,
                "organization_type": org.get("type"),
                "my_member_id": org.get("my_member_id"),
            }
            organizations.append(organization)

            embedded = org.get("ad_accounts") or org.get("adaccounts") or []
            if not isinstance(embedded, list) or not embedded:
                response = await client.get(
                    f"{SNAPCHAT_API_BASE}/organizations/{org_id}/adaccounts"
                )
                if response.status_code >= 400:
                    continue
                embedded = response.json().get("adaccounts") or []

            for account_wrapper in embedded:
                if len(accounts) >= MAX_AD_ACCOUNTS:
                    break
                account = _unwrap(account_wrapper, "adaccount")
                account_id = str(account.get("id") or "").strip()
                if not account_id:
                    continue
                accounts.append(
                    {
                        "external_account_id": account_id,
                        "ad_account_id": account_id,
                        "display_name": account.get("name") or account_id,
                        "currency": account.get("currency"),
                        "timezone": account.get("timezone"),
                        "account_status": account.get("status"),
                        "organization_id": org_id,
                        "organization_name": organization["organization_name"],
                    }
                )

    return {
        "identity": {
            "external_user_id": identity.get("id"),
            "display_name": identity.get("display_name") or identity.get("email"),
            "email": identity.get("email"),
        },
        "organizations": organizations,
        "accounts": accounts,
    }
