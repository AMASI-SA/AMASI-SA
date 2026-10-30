"""Native discovery through fixed HTTP endpoints; all provider data is synthetic."""
import asyncio
import copy
import json

import httpx
import pytest

from integrations_control_center import google_discovery as discovery

TOKEN = "synthetic-only-access"
SCOPES = set(discovery.GOOGLE_SCOPE_BY_PROVIDER.values())


def responses():
    return {
        "googleads.googleapis.com": {"resourceNames": ["customers/1234567890"]},
        "analyticsadmin.googleapis.com": {"accountSummaries": [
            {"name": "accountSummaries/11", "account": "accounts/11",
             "propertySummaries": [{"property": "properties/22", "displayName": "GA4"}]}]},
        "merchantapi.googleapis.com": {"accounts": [
            {"name": "accounts/33", "accountName": "Merchant", "timeZone": {"id": "Asia/Riyadh"}}]},
        "www.googleapis.com": {"siteEntry": [
            {"siteUrl": "sc-domain:example.test", "permissionLevel": "siteOwner"}]},
    }


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setenv("GOOGLE_ADS_DEVELOPER_TOKEN", "synthetic-developer")
    monkeypatch.setenv("GOOGLE_ADS_API_VERSION", "v25")
    monkeypatch.setattr(discovery.httpx, "AsyncClient", lambda **kwargs:
        original(**kwargs, transport=httpx.MockTransport(handler), trust_env=False))


async def observe(monkeypatch, payloads=None, scopes=None):
    payloads = payloads or responses()
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=payloads[request.url.host])
    transport(monkeypatch, handler)
    accounts, errors = await discovery._discover_google_accounts(TOKEN, SCOPES if scopes is None else scopes)
    return accounts, errors, calls


@pytest.mark.asyncio
async def test_complete_four_services_preserve_native_identity_and_are_bound_to_token(monkeypatch):
    accounts, errors, calls = await observe(monkeypatch)
    public = discovery.validated_discovery(accounts, errors, TOKEN, SCOPES)
    assert errors == {} and len(calls) == 4
    assert public["google_ads"][0]["ad_account_id"] == "1234567890"
    assert public["google_analytics_4"][0]["external_account_id"] == "22"
    assert public["google_merchant_center"][0]["timezone"] == "Asia/Riyadh"
    assert public["google_search_console"][0]["external_account_id"] == "sc-domain:example.test"
    assert all(request.method == "GET" for request in calls)
    assert "synthetic" not in repr(accounts)
    with pytest.raises(discovery.DiscoveryInvalid):
        discovery.validated_discovery(accounts, {}, "other-token", SCOPES)
    with pytest.raises(discovery.DiscoveryInvalid):
        discovery.validated_discovery(dict(accounts), {}, TOKEN, SCOPES)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["row", "scope", "error"])
async def test_post_observation_tampering_never_becomes_complete_proof(monkeypatch, change):
    accounts, errors, _ = await observe(monkeypatch)
    scopes = set(SCOPES)
    if change == "row":
        accounts["google_ads"][0]["ad_account_id"] = "999"
    elif change == "scope":
        scopes.remove(discovery.GOOGLE_SCOPE_BY_PROVIDER["google_ads"])
    else:
        errors["google_ads"] = "http_429"
    with pytest.raises(discovery.DiscoveryInvalid):
        discovery.validated_discovery(accounts, errors, TOKEN, scopes)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["google_analytics_4", "google_merchant_center"])
async def test_all_pages_are_read_without_changing_scope(monkeypatch, provider):
    calls = []
    def handler(request):
        calls.append(request)
        second = request.url.params.get("pageToken") == "next"
        if provider == "google_merchant_center":
            row = {"name": "accounts/34" if second else "accounts/33"}
            payload = {"accounts": [row]}
        else:
            number = "34" if second else "33"
            payload = {"accountSummaries": [{"name": "accountSummaries/"+number,
                "account": "accounts/"+number, "propertySummaries": [{"property": "properties/"+number}]}]}
        if not second:
            payload["nextPageToken"] = "next"
        return httpx.Response(200, json=payload)
    transport(monkeypatch, handler)
    scopes = {discovery.GOOGLE_SCOPE_BY_PROVIDER[provider]}
    accounts, errors = await discovery._discover_google_accounts(TOKEN, scopes)
    assert errors == {} and len(calls) == 2
    assert [row["external_account_id"] for row in accounts[provider]] == ["33", "34"]
    assert calls[0].url.host == calls[1].url.host
    assert calls[0].url.params["pageSize"] == calls[1].url.params["pageSize"]
    assert discovery.validated_discovery(accounts, {}, TOKEN, scopes)


@pytest.mark.asyncio
@pytest.mark.parametrize("host,payload", [
    ("googleads.googleapis.com", []),
    ("googleads.googleapis.com", {"resourceNames": None}),
    ("googleads.googleapis.com", {"resourceNames": ["foreign/1"]}),
    ("googleads.googleapis.com", {"resourceNames": ["customers/1", "customers/1"]}),
    ("googleads.googleapis.com", {"resourceNames": ["customers/01"]}),
    ("googleads.googleapis.com", {"resourceNames": ["customers/9223372036854775808"]}),
    ("googleads.googleapis.com", {"resourceNames": ["customers/1"], "nextPageToken": "unmodeled"}),
    ("merchantapi.googleapis.com", {"accounts": [{}]}),
    ("merchantapi.googleapis.com", {"accounts": [None]}),
    ("merchantapi.googleapis.com", {"accounts": [{"name": "accounts/1", "accountId": "2"}]}),
    ("merchantapi.googleapis.com", {"accounts": [], "nextPageToken": None}),
    ("merchantapi.googleapis.com", {"accounts": [], "nextPageToken": ["wrong"]}),
    ("merchantapi.googleapis.com", {"accounts": [{"name": "accounts/1", "timeZone": "Asia/Riyadh"}]}),
    ("analyticsadmin.googleapis.com", {"accountSummaries": [{"name": "accountSummaries/1", "account": "accounts/2"}]}),
    ("analyticsadmin.googleapis.com", {"accountSummaries": [{"name": "accountSummaries/1", "account": "accounts/1", "propertySummaries": [{}]}]}),
    ("analyticsadmin.googleapis.com", {"accountSummaries": [{"name": "accountSummaries/1", "account": "accounts/1", "propertySummaries": [{"property": "properties/2", "parent": "accounts/3"}]}]}),
    ("www.googleapis.com", {}),
    ("www.googleapis.com", {"siteEntry": [None]}),
    ("www.googleapis.com", {"siteEntry": [{"siteUrl": "https://example.test/", "permissionLevel": "unknown"}]}),
    ("www.googleapis.com", {"siteEntry": [{"siteUrl": "javascript:bad", "permissionLevel": "siteOwner"}]}),
])
async def test_invalid_or_partial_service_cannot_publish_any_grant(monkeypatch, host, payload):
    payloads = responses()
    payloads[host] = payload
    accounts, errors, _ = await observe(monkeypatch, payloads)
    assert len(errors) == 1
    assert accounts[next(iter(errors))] == []
    with pytest.raises(discovery.DiscoveryInvalid):
        discovery.validated_discovery(accounts, errors, TOKEN, SCOPES)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cycle", "duplicate", "page_limit"])
async def test_pagination_cannot_hide_missing_or_duplicate_membership(monkeypatch, mode):
    calls = []
    def handler(request):
        calls.append(request)
        number = len(calls)
        return httpx.Response(200, json={"accounts": [{"name": "accounts/"+str(1 if mode == "duplicate" else number)}],
            "nextPageToken": "same" if mode == "cycle" else str(number)})
    transport(monkeypatch, handler)
    accounts, errors = await discovery._discover_google_accounts(TOKEN, {discovery.GOOGLE_SCOPE_BY_PROVIDER["google_merchant_center"]})
    assert errors and accounts["google_merchant_center"] == []
    assert len(calls) <= discovery.MAX_PAGES


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["row_limit", "response_bytes", "total_bytes", "duplicate_json", "redirect", "429"])
async def test_bounded_transport_refuses_without_retries_or_partial_rows(monkeypatch, mode):
    calls = []
    if mode == "total_bytes":
        monkeypatch.setattr(discovery, "MAX_TOTAL_BYTES", 10)
    def handler(request):
        calls.append(request)
        if mode == "redirect":
            return httpx.Response(302, headers={"location": "https://untrusted.example/"})
        if mode == "429":
            return httpx.Response(429, json={"error": {"message": "SYNTHETIC_PRIVATE_MESSAGE"}})
        if mode == "duplicate_json":
            return httpx.Response(200, content=b'{"accounts":[],"accounts":[]}')
        if mode == "response_bytes":
            return httpx.Response(200, content=b" "*(discovery.MAX_RESPONSE_BYTES+1))
        return httpx.Response(200, json={"accounts": [{"name": "accounts/"+str(i+1)} for i in range(401)]})
    transport(monkeypatch, handler)
    accounts, errors = await discovery._discover_google_accounts(TOKEN, {discovery.GOOGLE_SCOPE_BY_PROVIDER["google_merchant_center"]})
    assert errors and accounts["google_merchant_center"] == [] and len(calls) == 1
    assert "SYNTHETIC_PRIVATE_MESSAGE" not in str(errors)


@pytest.mark.asyncio
async def test_omitted_scope_never_calls_that_service_or_invents_permission(monkeypatch):
    accounts, errors, calls = await observe(monkeypatch, scopes=set())
    assert errors == {} and not calls and all(rows == [] for rows in accounts.values())


@pytest.mark.asyncio
async def test_documented_proto_empty_and_explicit_search_empty_are_complete(monkeypatch):
    accounts, errors, _ = await observe(monkeypatch, {
        "googleads.googleapis.com": {}, "analyticsadmin.googleapis.com": {},
        "merchantapi.googleapis.com": {}, "www.googleapis.com": {"siteEntry": []}})
    assert errors == {} and all(not rows for rows in accounts.values())
    assert discovery.validated_discovery(accounts, {}, TOKEN, SCOPES)


@pytest.mark.asyncio
async def test_search_unverified_site_is_retained_without_access_assertion(monkeypatch):
    payloads = responses()
    payloads["www.googleapis.com"]["siteEntry"][0]["permissionLevel"] = "siteUnverifiedUser"
    accounts, errors, _ = await observe(monkeypatch, payloads)
    row = discovery.validated_discovery(accounts, errors, TOKEN, SCOPES)["google_search_console"][0]
    assert row["native_permission_level"] == "siteUnverifiedUser"
    assert row["account_access_verified"] is False


@pytest.mark.asyncio
async def test_timeout_and_cancellation_do_not_return_a_partial_proof(monkeypatch):
    async def delayed(request):
        await asyncio.Event().wait()
    transport(monkeypatch, delayed)
    monkeypatch.setattr(discovery, "DISCOVERY_SECONDS", .01)
    with pytest.raises(TimeoutError):
        await discovery._discover_google_accounts(TOKEN, SCOPES)
    task = asyncio.create_task(discovery._discover_google_accounts(TOKEN, SCOPES))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{}, {"sub": ""}, {"sub": True}, {"sub": "identity", "email_verified": "true"}])
async def test_userinfo_requires_typed_nonempty_identity(monkeypatch, payload):
    transport(monkeypatch, lambda request: httpx.Response(200, json=payload))
    with pytest.raises(discovery.DiscoveryInvalid):
        await discovery._userinfo(TOKEN)
