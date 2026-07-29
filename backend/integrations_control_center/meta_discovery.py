"""Read-only Meta Business/Marketing API discovery for Mezan V2."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from .meta_oauth_security import meta_appsecret_proof, meta_graph_base

MAX_PAGES = 5
MAX_BUSINESSES = 100
MAX_AD_ACCOUNTS = 200
MAX_ASSETS_PER_TYPE = 500


def _safe_next_url(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = urlsplit(text)
    except Exception:  # noqa: BLE001
        return None
    if parsed.scheme != "https" or parsed.hostname != "graph.facebook.com":
        return None
    return text


async def _get_all(
    client: httpx.AsyncClient,
    path: str,
    *,
    access_token: str,
    fields: str,
    limit: int,
) -> list[dict[str, Any]]:
    url = f"{meta_graph_base()}/{path.lstrip('/')}"
    params: dict[str, Any] | None = {
        "access_token": access_token,
        "appsecret_proof": meta_appsecret_proof(access_token),
        "fields": fields,
        "limit": min(limit, 100),
    }
    rows: list[dict[str, Any]] = []
    for _ in range(MAX_PAGES):
        response = await client.get(url, params=params)
        if response.status_code >= 400:
            raise RuntimeError(f"meta_graph_http_{response.status_code}")
        payload = response.json() or {}
        data = payload.get("data") or []
        if not isinstance(data, list):
            raise RuntimeError("meta_graph_invalid_list_payload")
        rows.extend(item for item in data if isinstance(item, dict))
        if len(rows) >= limit:
            return rows[:limit]
        next_url = _safe_next_url((payload.get("paging") or {}).get("next"))
        if not next_url:
            break
        url = next_url
        params = None
    return rows[:limit]


async def _get_one(
    client: httpx.AsyncClient,
    path: str,
    *,
    access_token: str,
    fields: str,
) -> dict[str, Any]:
    response = await client.get(
        f"{meta_graph_base()}/{path.lstrip('/')}",
        params={
            "access_token": access_token,
            "appsecret_proof": meta_appsecret_proof(access_token),
            "fields": fields,
        },
    )
    if response.status_code >= 400:
        raise RuntimeError(f"meta_graph_http_{response.status_code}")
    payload = response.json() or {}
    if not isinstance(payload, dict):
        raise RuntimeError("meta_graph_invalid_object_payload")
    return payload


async def discover_meta_assets(access_token: str) -> dict[str, Any]:
    identity: dict[str, Any] = {}
    businesses: list[dict[str, Any]] = []
    accounts: list[dict[str, Any]] = []
    pixels: list[dict[str, Any]] = []
    catalogs: list[dict[str, Any]] = []
    instagram_accounts: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    async with httpx.AsyncClient(timeout=25.0) as client:
        me = await _get_one(
            client,
            "me",
            access_token=access_token,
            fields="id,name,email",
        )
        identity = {
            "external_user_id": me.get("id"),
            "display_name": me.get("name"),
            "email": me.get("email"),
        }

        business_rows = await _get_all(
            client,
            "me/businesses",
            access_token=access_token,
            fields="id,name,verification_status,primary_page",
            limit=MAX_BUSINESSES,
        )
        for item in business_rows:
            business_id = str(item.get("id") or "").strip()
            if not business_id:
                continue
            businesses.append(
                {
                    "external_asset_id": business_id,
                    "business_id": business_id,
                    "display_name": item.get("name") or business_id,
                    "verification_status": item.get("verification_status"),
                    "primary_page": item.get("primary_page"),
                }
            )

        # Keep the required account-discovery call limited to broadly available
        # account metadata. Financial fields are requested separately below so
        # one restricted billing field can never hide an otherwise valid ad
        # account from Mezan.
        account_rows = await _get_all(
            client,
            "me/adaccounts",
            access_token=access_token,
            fields=(
                "id,account_id,name,currency,timezone_name,account_status,"
                "business{id,name}"
            ),
            limit=MAX_AD_ACCOUNTS,
        )
        for item in account_rows:
            raw_id = str(item.get("id") or item.get("account_id") or "").strip()
            if not raw_id:
                continue
            account_id = raw_id if raw_id.startswith("act_") else f"act_{raw_id}"
            business = item.get("business") if isinstance(item.get("business"), dict) else {}
            accounts.append(
                {
                    "external_account_id": account_id,
                    "ad_account_id": account_id,
                    "display_name": item.get("name") or account_id,
                    "currency": item.get("currency"),
                    "timezone": item.get("timezone_name"),
                    "account_status": item.get("account_status"),
                    "business_id": business.get("id"),
                    "business_name": business.get("name"),
                    "amount_spent_minor": None,
                    "balance_minor": None,
                    "spend_cap_minor": None,
                    "funding_source_present": None,
                }
            )

        for account in accounts:
            account_id = account["ad_account_id"]

            # Billing/funding visibility differs by account role, ownership,
            # region, and account configuration. Treat it as optional evidence
            # and store only numeric summaries plus a boolean—never the payment
            # instrument payload itself.
            try:
                finance = await _get_one(
                    client,
                    account_id,
                    access_token=access_token,
                    fields="amount_spent,balance,spend_cap,funding_source_details",
                )
                account["amount_spent_minor"] = finance.get("amount_spent")
                account["balance_minor"] = finance.get("balance")
                account["spend_cap_minor"] = finance.get("spend_cap")
                account["funding_source_present"] = bool(
                    finance.get("funding_source_details")
                )
            except Exception as exc:  # noqa: BLE001
                errors.append({"asset": f"finance:{account_id}", "code": str(exc)})

            try:
                pixel_rows = await _get_all(
                    client,
                    f"{account_id}/adspixels",
                    access_token=access_token,
                    fields="id,name,last_fired_time,creation_time,is_unavailable",
                    limit=MAX_ASSETS_PER_TYPE,
                )
                for item in pixel_rows:
                    pixel_id = str(item.get("id") or "").strip()
                    if pixel_id:
                        pixels.append(
                            {
                                "external_asset_id": pixel_id,
                                "pixel_id": pixel_id,
                                "display_name": item.get("name") or pixel_id,
                                "ad_account_id": account_id,
                                "last_fired_time": item.get("last_fired_time"),
                                "creation_time": item.get("creation_time"),
                                "is_unavailable": item.get("is_unavailable"),
                            }
                        )
            except Exception as exc:  # noqa: BLE001
                errors.append({"asset": f"pixels:{account_id}", "code": str(exc)})

            try:
                ig_rows = await _get_all(
                    client,
                    f"{account_id}/instagram_accounts",
                    access_token=access_token,
                    fields="id,username,profile_pic",
                    limit=MAX_ASSETS_PER_TYPE,
                )
                for item in ig_rows:
                    ig_id = str(item.get("id") or "").strip()
                    if ig_id:
                        instagram_accounts.append(
                            {
                                "external_asset_id": ig_id,
                                "instagram_account_id": ig_id,
                                "display_name": item.get("username") or ig_id,
                                "ad_account_id": account_id,
                            }
                        )
            except Exception as exc:  # noqa: BLE001
                errors.append({"asset": f"instagram:{account_id}", "code": str(exc)})

        for business in businesses:
            business_id = business["business_id"]
            try:
                catalog_rows = await _get_all(
                    client,
                    f"{business_id}/owned_product_catalogs",
                    access_token=access_token,
                    fields="id,name,vertical,product_count",
                    limit=MAX_ASSETS_PER_TYPE,
                )
                for item in catalog_rows:
                    catalog_id = str(item.get("id") or "").strip()
                    if catalog_id:
                        catalogs.append(
                            {
                                "external_asset_id": catalog_id,
                                "catalog_id": catalog_id,
                                "display_name": item.get("name") or catalog_id,
                                "business_id": business_id,
                                "vertical": item.get("vertical"),
                                "product_count": item.get("product_count"),
                            }
                        )
            except Exception as exc:  # noqa: BLE001
                errors.append({"asset": f"catalogs:{business_id}", "code": str(exc)})

    return {
        "identity": identity,
        "businesses": businesses,
        "accounts": accounts,
        "pixels": pixels[:MAX_ASSETS_PER_TYPE],
        "catalogs": catalogs[:MAX_ASSETS_PER_TYPE],
        "instagram_accounts": instagram_accounts[:MAX_ASSETS_PER_TYPE],
        "errors": errors[:100],
    }
