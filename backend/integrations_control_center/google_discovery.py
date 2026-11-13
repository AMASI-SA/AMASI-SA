"""Complete bounded account enumeration for atomic Google OAuth publication."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .google_oauth_security import (
    GOOGLE_PROVIDER_IDS, GOOGLE_SCOPE_BY_PROVIDER, GOOGLE_GA4_SUMMARIES_URL,
    GOOGLE_SEARCH_CONSOLE_SITES_URL, GOOGLE_MERCHANT_ACCOUNTS_URL, GOOGLE_USERINFO_URL,
)

MAX_ROWS = 400
MAX_PAGES = 10
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 8 * 1024 * 1024
DISCOVERY_SECONDS = 90
_SEAL = object()


class DiscoveryInvalid(ValueError):
    def __init__(self, code="google_discovery_unproved"):
        self.code = code
        super().__init__(code)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class _DiscoveryAccounts(dict):
    """Private evidence; repr excludes token binding and account data."""
    def __init__(self, accounts, errors, token, scopes):
        super().__init__(accounts)
        self._proof = _SEAL
        self._snapshot = _canonical([accounts, errors, sorted(scopes)])
        self._token = hashlib.sha256(token.encode()).digest()

    def __repr__(self):
        return "<GoogleDiscoveryEvidence>"


def validated_discovery(accounts, errors, access_token, granted_scopes):
    """Private publication boundary: reject bare, changed or foreign evidence."""
    try:
        valid = (type(accounts) is _DiscoveryAccounts and accounts._proof is _SEAL
            and isinstance(access_token, str) and access_token
            and accounts._token == hashlib.sha256(access_token.encode()).digest()
            and accounts._snapshot == _canonical([dict(accounts), errors, sorted(granted_scopes)])
            and not errors and set(accounts) == GOOGLE_PROVIDER_IDS)
    except Exception:
        valid = False
    if not valid:
        raise DiscoveryInvalid()
    return json.loads(_canonical(dict(accounts)))


def _text(value, limit=512):
    if (not isinstance(value, str) or not value or len(value) > limit
        or value != value.strip() or any(ord(char) < 32 for char in value)):
        raise DiscoveryInvalid("google_discovery_row_invalid")
    return value


def _resource(value, kind):
    value = _text(value, 128)
    match = re.fullmatch(re.escape(kind) + r"/([1-9][0-9]{0,18})", value)
    if not match or int(match[1]) > 9223372036854775807:
        raise DiscoveryInvalid("google_discovery_identity_invalid")
    return match[1]


def _list(payload, field, protobuf=True):
    # ProtoJSON omits default empty repeated fields; explicit null is refused.
    # Search Console is not presumed to have protobuf omission semantics.
    if field not in payload and not protobuf:
        raise DiscoveryInvalid("google_discovery_list_missing")
    rows = payload.get(field, [])
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise DiscoveryInvalid("google_discovery_list_invalid")
    return rows


def _row(identity, name=None, **extra):
    return {"external_account_id": identity, "display_name": _text(name) if name is not None else identity,
        "timezone": None, "currency": None, "account_access_verified": True, **extra}


def _parse_rows(provider, payload):
    output = []
    if provider == "google_ads":
        if set(payload) - {"resourceNames"}:
            raise DiscoveryInvalid("google_discovery_shape_invalid")
        for resource in _list(payload, "resourceNames"):
            identity = _resource(resource, "customers")
            output.append(_row(identity, "Google Ads " + identity, ad_account_id=identity))
    elif provider == "google_merchant_center":
        if set(payload) - {"accounts", "nextPageToken"}:
            raise DiscoveryInvalid("google_discovery_shape_invalid")
        for row in _list(payload, "accounts"):
            if not isinstance(row, dict):
                raise DiscoveryInvalid("google_discovery_row_invalid")
            identity = _resource(row.get("name"), "accounts")
            if "accountId" in row and row["accountId"] != identity:
                raise DiscoveryInvalid("google_discovery_identity_invalid")
            timezone = None
            if "timeZone" in row:
                if not isinstance(row["timeZone"], dict):
                    raise DiscoveryInvalid("google_discovery_row_invalid")
                timezone = _text(row["timeZone"].get("id"), 100)
                try:
                    ZoneInfo(timezone)
                except (ValueError, ZoneInfoNotFoundError):
                    raise DiscoveryInvalid("google_discovery_timezone_invalid") from None
            output.append(_row(identity, row.get("accountName", row.get("displayName")), timezone=timezone))
    elif provider == "google_analytics_4":
        if set(payload) - {"accountSummaries", "nextPageToken"}:
            raise DiscoveryInvalid("google_discovery_shape_invalid")
        summaries = set()
        for row in _list(payload, "accountSummaries"):
            if not isinstance(row, dict):
                raise DiscoveryInvalid("google_discovery_row_invalid")
            account = _resource(row.get("account"), "accounts")
            if _resource(row.get("name"), "accountSummaries") != account or account in summaries:
                raise DiscoveryInvalid("google_discovery_identity_invalid")
            summaries.add(account)
            for prop in _list(row, "propertySummaries"):
                if not isinstance(prop, dict):
                    raise DiscoveryInvalid("google_discovery_row_invalid")
                identity = _resource(prop.get("property"), "properties")
                parent = prop.get("parent")
                if parent is not None:
                    _resource(parent, "properties" if str(parent).startswith("properties/") else "accounts")
                    if parent.startswith("accounts/") and parent != row["account"]:
                        raise DiscoveryInvalid("google_discovery_identity_invalid")
                output.append(_row(identity, prop.get("displayName", row.get("displayName"))))
    elif provider == "google_search_console":
        if set(payload) - {"siteEntry"}:
            raise DiscoveryInvalid("google_discovery_shape_invalid")
        for row in _list(payload, "siteEntry", protobuf=False):
            if not isinstance(row, dict):
                raise DiscoveryInvalid("google_discovery_row_invalid")
            site = _text(row.get("siteUrl"), 2048)
            if site.startswith("sc-domain:"):
                if not re.fullmatch(r"sc-domain:[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?", site):
                    raise DiscoveryInvalid("google_discovery_identity_invalid")
            else:
                parts = urlsplit(site)
                if parts.scheme not in {"https", "http"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
                    raise DiscoveryInvalid("google_discovery_identity_invalid")
            permission = row.get("permissionLevel")
            if permission not in {"siteOwner", "siteFullUser", "siteRestrictedUser", "siteUnverifiedUser"}:
                raise DiscoveryInvalid("google_discovery_permission_unproved")
            output.append(_row(site, site, native_permission_level=permission,
                account_access_verified=permission != "siteUnverifiedUser"))
    else:
        raise DiscoveryInvalid("google_discovery_provider_invalid")
    if len(output) > MAX_ROWS:
        raise DiscoveryInvalid("google_discovery_row_limit")
    return output


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise DiscoveryInvalid("google_discovery_duplicate_json_key")
        result[key] = value
    return result


async def _get_json(client, url, *, headers, params=None, budget=None):
    fixed = {GOOGLE_GA4_SUMMARIES_URL, GOOGLE_SEARCH_CONSOLE_SITES_URL,
        GOOGLE_MERCHANT_ACCOUNTS_URL, GOOGLE_USERINFO_URL}
    if url not in fixed and not re.fullmatch(r"https://googleads\.googleapis\.com/v[0-9]{1,3}/customers:listAccessibleCustomers", url):
        return None, "origin_refused"
    raw = bytearray()
    budget = budget if budget is not None else [0]
    try:
        async with client.stream("GET", url, headers=headers, params=params, follow_redirects=False) as response:
            if response.status_code != 200:
                return None, f"http_{response.status_code}"
            async for chunk in response.aiter_bytes(chunk_size=8192):
                budget[0] += len(chunk)
                if len(raw) + len(chunk) > MAX_RESPONSE_BYTES or budget[0] > MAX_TOTAL_BYTES:
                    return None, "response_limit"
                raw.extend(chunk)
        value = json.loads(raw, object_pairs_hook=_unique_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError()))
        return (value, None) if isinstance(value, dict) else (None, "shape_invalid")
    except (httpx.HTTPError, TimeoutError):
        return None, "network_error"
    except (ValueError, TypeError):
        return None, "invalid_json"


async def _enumerate(client, provider, token, *, budget, developer_token=None, version="v25"):
    definitions = {
        "google_analytics_4": (GOOGLE_GA4_SUMMARIES_URL, 200),
        "google_search_console": (GOOGLE_SEARCH_CONSOLE_SITES_URL, None),
        "google_merchant_center": (GOOGLE_MERCHANT_ACCOUNTS_URL, 100),
        "google_ads": (f"https://googleads.googleapis.com/{version}/customers:listAccessibleCustomers", None),
    }
    url, size = definitions[provider]
    headers = {"Authorization": "Bearer " + token}
    if provider == "google_ads":
        if not developer_token:
            raise DiscoveryInvalid("developer_token_missing")
        headers["developer-token"] = developer_token
    accounts, identities, seen_tokens, summaries = [], set(), set(), set()
    next_token = None
    for _ in range(MAX_PAGES):
        params = {"pageSize": size} if size else None
        if next_token:
            params["pageToken"] = next_token
        payload, error = await _get_json(client, url, headers=headers, params=params, budget=budget)
        if error:
            raise DiscoveryInvalid(error)
        rows = _parse_rows(provider, payload)
        if provider == "google_analytics_4":
            page_accounts = [row["account"] for row in _list(payload, "accountSummaries")]
            if any(value in summaries for value in page_accounts):
                raise DiscoveryInvalid("google_discovery_duplicate_identity")
            summaries.update(page_accounts)
            if len(summaries) > MAX_ROWS:
                raise DiscoveryInvalid("google_discovery_row_limit")
        for row in rows:
            identity = row["external_account_id"]
            if identity in identities:
                raise DiscoveryInvalid("google_discovery_duplicate_identity")
            identities.add(identity)
            accounts.append(row)
        if len(accounts) > MAX_ROWS:
            raise DiscoveryInvalid("google_discovery_row_limit")
        next_token = payload.get("nextPageToken", "")
        if not isinstance(next_token, str) or len(next_token) > 4096 or next_token != next_token.strip():
            raise DiscoveryInvalid("google_discovery_page_invalid")
        if not next_token:
            return accounts
        if not size or next_token in seen_tokens:
            raise DiscoveryInvalid("google_discovery_page_cycle")
        seen_tokens.add(next_token)
    raise DiscoveryInvalid("google_discovery_page_limit")


async def _discover_google_accounts(access_token, granted_scopes):
    if not isinstance(access_token, str) or not access_token or not isinstance(granted_scopes, set) or any(not isinstance(v, str) for v in granted_scopes):
        raise DiscoveryInvalid()
    results = {provider: [] for provider in GOOGLE_PROVIDER_IDS}
    errors, budget = {}, [0]
    async with asyncio.timeout(DISCOVERY_SECONDS):
        async with httpx.AsyncClient(timeout=25.0, follow_redirects=False) as client:
            for provider in sorted(GOOGLE_PROVIDER_IDS):
                if GOOGLE_SCOPE_BY_PROVIDER[provider] not in granted_scopes:
                    continue
                try:
                    results[provider] = await _enumerate(client, provider, access_token, budget=budget,
                        developer_token=os.environ.get("GOOGLE_ADS_DEVELOPER_TOKEN", "").strip() if provider == "google_ads" else None,
                        version=os.environ.get("GOOGLE_ADS_API_VERSION", "v25").strip())
                except DiscoveryInvalid as exc:
                    errors[provider] = exc.code
    return _DiscoveryAccounts(results, errors, access_token, granted_scopes), errors


async def _userinfo(access_token):
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
        data, error = await _get_json(client, GOOGLE_USERINFO_URL,
            headers={"Authorization": "Bearer " + access_token})
    if error or not data:
        raise DiscoveryInvalid("google_identity_unproved")
    _text(data.get("sub"), 255)
    if "email" in data:
        _text(data["email"], 320)
    if "email_verified" in data and type(data["email_verified"]) is not bool:
        raise DiscoveryInvalid("google_identity_unproved")
    return data
