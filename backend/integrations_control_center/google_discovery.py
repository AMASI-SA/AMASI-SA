"""Bounded read-only discovery for Google integration accounts."""
from __future__ import annotations

import os
from typing import Any

import httpx

from .google_oauth_security import (
    GOOGLE_PROVIDER_IDS,
    GOOGLE_SCOPE_BY_PROVIDER,
    GOOGLE_GA4_SUMMARIES_URL,
    GOOGLE_SEARCH_CONSOLE_SITES_URL,
    GOOGLE_MERCHANT_ACCOUNTS_URL,
    GOOGLE_USERINFO_URL,
)


async def _get_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    headers: dict[str, str],
    params: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        response = await client.get(url, headers=headers, params=params)
    except httpx.HTTPError:
        return None, "network_error"
    if response.status_code >= 400:
        return None, f"http_{response.status_code}"
    try:
        value = response.json()
    except ValueError:
        return None, "invalid_json"
    return value if isinstance(value, dict) else {}, None


async def _discover_google_accounts(
    access_token: str,
    granted_scopes: set[str],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    results: dict[str, list[dict[str, Any]]] = {
        provider: [] for provider in GOOGLE_PROVIDER_IDS
    }
    errors: dict[str, str] = {}
    headers = {"Authorization": f"Bearer {access_token}"}
    async with httpx.AsyncClient(timeout=25.0) as client:
        if GOOGLE_SCOPE_BY_PROVIDER["google_analytics_4"] in granted_scopes:
            data, error = await _get_json(
                client,
                GOOGLE_GA4_SUMMARIES_URL,
                headers=headers,
                params={"pageSize": 200},
            )
            if error:
                errors["google_analytics_4"] = error
            else:
                for account in (data or {}).get("accountSummaries") or []:
                    account_name = account.get("displayName") or account.get("name")
                    for prop in account.get("propertySummaries") or []:
                        resource = str(prop.get("property") or "")
                        property_id = resource.rsplit("/", 1)[-1] if resource else ""
                        if property_id:
                            results["google_analytics_4"].append(
                                {
                                    "external_account_id": property_id,
                                    "display_name": prop.get("displayName")
                                    or account_name
                                    or property_id,
                                    "timezone": None,
                                    "currency": None,
                                }
                            )

        if GOOGLE_SCOPE_BY_PROVIDER["google_search_console"] in granted_scopes:
            data, error = await _get_json(
                client, GOOGLE_SEARCH_CONSOLE_SITES_URL, headers=headers
            )
            if error:
                errors["google_search_console"] = error
            else:
                for site in (data or {}).get("siteEntry") or []:
                    site_url = str(site.get("siteUrl") or "").strip()
                    if site_url:
                        results["google_search_console"].append(
                            {
                                "external_account_id": site_url,
                                "display_name": site_url,
                                "timezone": None,
                                "currency": None,
                            }
                        )

        if GOOGLE_SCOPE_BY_PROVIDER["google_merchant_center"] in granted_scopes:
            data, error = await _get_json(
                client,
                GOOGLE_MERCHANT_ACCOUNTS_URL,
                headers=headers,
                params={"pageSize": 100},
            )
            if error:
                errors["google_merchant_center"] = error
            else:
                for account in (data or {}).get("accounts") or []:
                    resource = str(account.get("name") or "")
                    account_id = resource.rsplit("/", 1)[-1] if resource else ""
                    if account_id:
                        results["google_merchant_center"].append(
                            {
                                "external_account_id": account_id,
                                "display_name": account.get("accountName")
                                or account.get("displayName")
                                or account_id,
                                "timezone": None,
                                "currency": None,
                            }
                        )

        if GOOGLE_SCOPE_BY_PROVIDER["google_ads"] in granted_scopes:
            developer_token = os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip()
            if not developer_token:
                errors["google_ads"] = "developer_token_missing"
            else:
                version = os.environ.get("GOOGLE_ADS_API_VERSION", "v25").strip()
                data, error = await _get_json(
                    client,
                    f"https://googleads.googleapis.com/{version}/customers:listAccessibleCustomers",
                    headers={**headers, "developer-token": developer_token},
                )
                if error:
                    errors["google_ads"] = error
                else:
                    for resource in (data or {}).get("resourceNames") or []:
                        account_id = str(resource).rsplit("/", 1)[-1]
                        if account_id:
                            results["google_ads"].append(
                                {
                                    "external_account_id": account_id,
                                    "ad_account_id": account_id,
                                    "display_name": f"Google Ads {account_id}",
                                    "timezone": None,
                                    "currency": None,
                                }
                            )
    return results, errors


async def _userinfo(access_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        data, _ = await _get_json(
            client,
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
    return data or {}
