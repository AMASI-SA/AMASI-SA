from __future__ import annotations

from copy import deepcopy

import httpx
import pytest
from fastapi import HTTPException

from integrations_control_center import google_ads_client as module


CUSTOMER = "1234567890"


def client(monkeypatch, handler):
    monkeypatch.setattr(module, "google_ads_api_version", lambda: "v25")
    return module.GoogleAdsClient("test-access-credential", {"ad_account_id": CUSTOMER, "login_customer_id": "555-555-5555"}, transport=httpx.MockTransport(handler), developer_token="test-developer-credential")


@pytest.mark.asyncio
async def test_native_transport_uses_fixed_google_origin_verified_customer_and_backend_headers(monkeypatch):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"results": [], "totalResultsCount": "0"}, headers={"request-id": "provider-proof-1"})
    instance = client(monkeypatch, handler)
    try:
        rows, complete = await instance.search("SELECT campaign.id FROM campaign")
    finally:
        await instance.close()
    assert rows == [] and complete is True
    assert len(seen) == 1
    request = seen[0]
    assert request.url.host == "googleads.googleapis.com"
    assert request.url.path == f"/v25/customers/{CUSTOMER}/googleAds:search"
    assert request.headers["login-customer-id"] == "5555555555"
    assert request.headers["authorization"] == "Bearer test-access-credential"
    assert request.headers["developer-token"] == "test-developer-credential"


@pytest.mark.parametrize("identity", ["1 OR 1=1", "https://foreign.example", "1/../2", "1234567890?x=1", None])
def test_customer_identity_cannot_change_query_or_request_origin(identity):
    with pytest.raises(HTTPException):
        module.identifier(identity)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,expected_complete", [
    ({"results": [{"campaign": {"id": "1"}}], "totalResultsCount": "1"}, True),
    ({"results": [{"campaign": {"id": "1"}}], "totalResultsCount": "2", "nextPageToken": "next"}, False),
    ({"results": [{"campaign": {"id": "1"}}], "totalResultsCount": "3"}, False),
])
async def test_pagination_and_report_total_cannot_be_mistaken_for_complete(monkeypatch, payload, expected_complete):
    instance = client(monkeypatch, lambda request: httpx.Response(200, json=payload))
    try:
        rows, complete = await instance.search("SELECT campaign.id FROM campaign")
    finally:
        await instance.close()
    assert len(rows) == 1 and complete is expected_complete


@pytest.mark.asyncio
async def test_row_limit_is_truncation_not_a_complete_zero_result(monkeypatch):
    instance = client(monkeypatch, lambda request: httpx.Response(200, json={"results": [{"campaign": {"id": "1"}}, {"campaign": {"id": "2"}}], "totalResultsCount": "2"}))
    try:
        rows, complete = await instance.search("SELECT campaign.id FROM campaign", max_rows=1)
    finally:
        await instance.close()
    assert rows == [{"campaign": {"id": "1"}}]
    assert complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_redirect_is_not_followed_or_accepted_as_success(monkeypatch, status):
    calls = []
    def handler(request):
        calls.append(request.url)
        return httpx.Response(status, headers={"location": "https://foreign.example/credential-sink"}, json={"results": [], "totalResultsCount": "0"})
    instance = client(monkeypatch, handler)
    try:
        with pytest.raises(HTTPException):
            await instance.search("SELECT campaign.id FROM campaign")
    finally:
        await instance.close()
    assert len(calls) == 1 and calls[0].host == "googleads.googleapis.com"


@pytest.mark.asyncio
async def test_provider_failure_evidence_is_sanitized_and_never_exposes_message_credentials(monkeypatch):
    private_message = "token=test-access-credential raw private customer payload"
    body = {"error": {"message": private_message, "details": [{"errors": [{"errorCode": {"campaignError": "BUDGET_CANNOT_BE_SHARED"}, "message": private_message, "location": {"fieldPathElements": [{"fieldName": "mutateOperations", "index": 0}, {"fieldName": "campaignBudgetOperation"}]}}]}]}}
    instance = client(monkeypatch, lambda request: httpx.Response(400, json=body, headers={"request-id": "provider-reject-1"}))
    try:
        with pytest.raises(HTTPException) as error:
            await instance.search("SELECT campaign.id FROM campaign")
    finally:
        await instance.close()
    detail = error.value.detail
    assert private_message not in str(detail) and "test-access-credential" not in str(detail)
    assert detail["provider_codes"] == ["campaignError:BUDGET_CANNOT_BE_SHARED"]
    assert detail["request_id"] == "provider-reject-1"


@pytest.mark.asyncio
async def test_mutation_is_atomic_and_sent_once_with_validate_only_flag(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json={})
    instance = client(monkeypatch, handler)
    operations = [{"campaignOperation": {"update": {"resourceName": f"customers/{CUSTOMER}/campaigns/9", "status": "PAUSED"}, "updateMask": "status"}}]
    try:
        await instance.mutate(operations, validate_only=True)
    finally:
        await instance.close()
    import json
    payload = json.loads(calls[0].content)
    assert len(calls) == 1
    assert payload == {"mutateOperations": operations, "partialFailure": False, "validateOnly": True, "responseContentType": "MUTABLE_RESOURCE"}


@pytest.mark.asyncio
async def test_mutation_timeout_does_not_trigger_transport_retry(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        raise httpx.ReadTimeout("transport interrupted", request=request)
    instance = client(monkeypatch, handler)
    try:
        with pytest.raises(httpx.ReadTimeout):
            await instance.mutate([{"campaignOperation": {"create": {"name": "Paused campaign", "status": "PAUSED"}}}])
    finally:
        await instance.close()
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [{"id": "9999999999"}, {"currencyCode": "unknown"}, {"timeZone": ""}, {"manager": True}])
async def test_account_identity_currency_timezone_and_manager_eligibility_are_proved(monkeypatch, change):
    raw = {"id": CUSTOMER, "currencyCode": "USD", "timeZone": "America/New_York", "manager": False, "descriptiveName": "AMASI"}
    raw.update(change)
    instance = client(monkeypatch, lambda request: httpx.Response(200, json={"results": [{"customer": raw}], "totalResultsCount": "1"}))
    try:
        with pytest.raises(HTTPException):
            await instance.account_metadata()
    finally:
        await instance.close()


def test_mutation_result_requires_each_correct_provider_resource_identity():
    operations = [{"campaignOperation": {"create": {"resourceName": f"customers/{CUSTOMER}/campaigns/-2", "name": "AMASI", "status": "PAUSED"}}}]
    response = {"mutateOperationResponses": [{"campaignResult": {"resourceName": f"customers/{CUSTOMER}/campaigns/900"}}]}
    assert module.mutation_resource_names(response, operations, CUSTOMER) == [f"customers/{CUSTOMER}/campaigns/900"]
    invalid = deepcopy(response)
    invalid["mutateOperationResponses"][0]["campaignResult"]["resourceName"] = f"customers/{CUSTOMER}/assets/900"
    with pytest.raises(HTTPException):
        module.mutation_resource_names(invalid, operations, CUSTOMER)


def test_malformed_update_result_cannot_borrow_known_request_identity_as_provider_proof():
    operations = [{"campaignOperation": {"update": {"resourceName": f"customers/{CUSTOMER}/campaigns/900", "status": "PAUSED"}, "updateMask": "status"}}]
    with pytest.raises(HTTPException):
        module.mutation_resource_names({"mutateOperationResponses": [{}]}, operations, CUSTOMER)


def test_cross_customer_and_partial_or_missing_mutation_results_are_inconclusive():
    operations = [{"campaignOperation": {"create": {"name": "AMASI"}}}]
    for response in ({"mutateOperationResponses": []}, {"mutateOperationResponses": [{"campaignResult": {"resourceName": "customers/9999999999/campaigns/900"}}]}, {"mutateOperationResponses": [{}], "partialFailureError": {"code": 3}}):
        with pytest.raises(HTTPException):
            module.mutation_resource_names(response, operations, CUSTOMER)
