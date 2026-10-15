"""Only provider evidence, never missing/synthesized numbers, proves daily spend."""
from copy import deepcopy
from datetime import date
import hashlib
import json

import httpx
import pytest

from integrations_control_center.ad_daily_close_proof import (
    StreamRows, close_proof, google_evidence, meta_evidence, tiktok_evidence,
)

DAY = date(2026, 9, 29)
BASE = dict(account_id="123", business_date=str(DAY), timezone="Asia/Riyadh",
            currency="SAR", source_mode="fixture", observed_at="2026-09-30T01:00:00+03:00")


def meta_payload(spend="0"):
    return {"data": [{"account_id": "123", "date_start": str(DAY), "date_stop": str(DAY), "spend": spend}]}


def test_meta_explicit_zero_and_fingerprint():
    proof = close_proof(**BASE, **meta_evidence(meta_payload(), "act_123", DAY))
    assert proof["complete"] is True and proof["zero_confirmed"] is True
    assert proof["spend_native"] == "0"
    fingerprint = proof.pop("fingerprint")
    assert fingerprint == hashlib.sha256(json.dumps(proof, sort_keys=True,
        ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


@pytest.mark.parametrize("change", ["empty", "missing", "null", "nan", "negative", "bool", "next", "duplicate", "date", "account"])
def test_meta_incomplete_response_cannot_prove_spend(change):
    payload = meta_payload()
    if change == "empty": payload["data"] = []
    elif change == "missing": payload["data"][0].pop("spend")
    elif change in {"null", "nan", "negative", "bool"}:
        payload["data"][0]["spend"] = {"null": None, "nan": "NaN", "negative": "-1", "bool": False}[change]
    elif change == "next": payload["paging"] = {"next": "unconsumed-page"}
    elif change == "duplicate": payload["data"] *= 2
    elif change == "date": payload["data"][0]["date_stop"] = "2026-09-30"
    elif change == "account": payload["data"][0]["account_id"] = "foreign"
    proof = close_proof(**BASE, **meta_evidence(payload, "123", DAY))
    assert proof["complete"] is False and proof["zero_confirmed"] is False


def tiktok_payload():
    return {"list": [{"dimensions": {"advertiser_id": "123"}, "metrics": {"spend": "0"}}],
            "page_info": {"page": 1, "total_page": 1, "total_number": 1}}


@pytest.mark.parametrize("change", [None, "empty", "missing_spend", "missing_page", "more_pages", "foreign"])
def test_tiktok_requires_explicit_advertiser_and_exhausted_page(change):
    payload = tiktok_payload()
    if change == "empty": payload["list"] = []
    elif change == "missing_spend": payload["list"][0]["metrics"] = {}
    elif change == "missing_page": payload.pop("page_info")
    elif change == "more_pages": payload["page_info"]["total_page"] = 2
    elif change == "foreign": payload["list"][0]["dimensions"]["advertiser_id"] = "foreign"
    proof = close_proof(**BASE, **tiktok_evidence(payload, "123"))
    assert proof["complete"] is (change is None)
    assert proof["zero_confirmed"] is (change is None)


def google_rows():
    return StreamRows([{"customer": {"id": "123", "currencyCode": "SAR", "timeZone": "Asia/Riyadh"},
        "segments": {"date": str(DAY), "hour": 0}, "metrics": {"costMicros": "0"}}], True)


@pytest.mark.parametrize("change", [None, "empty", "missing_cost", "fractional_micros", "unproven_timezone",
    "invalid_hour", "foreign", "foreign_currency", "duplicate", "wrong_day", "incomplete_stream"])
def test_google_requires_complete_stream_and_explicit_provider_identity(change):
    rows = google_rows()
    metadata = dict(currency="SAR", timezone="Asia/Riyadh", provider_identity_proven=True)
    if change == "empty": rows.clear()
    elif change == "missing_cost": rows[0]["metrics"] = {}
    elif change == "fractional_micros": rows[0]["metrics"]["costMicros"] = "0.5"
    elif change == "unproven_timezone": metadata["provider_identity_proven"] = False
    elif change == "invalid_hour": rows[0]["segments"]["hour"] = 25
    elif change == "foreign": rows[0]["customer"]["id"] = "foreign"
    elif change == "foreign_currency": rows[0]["customer"]["currencyCode"] = "USD"
    elif change == "duplicate": rows.append(deepcopy(rows[0]))
    elif change == "wrong_day": rows[0]["segments"]["date"] = "2026-10-01"
    elif change == "incomplete_stream": rows.complete_response = False
    proof = close_proof(**BASE, **google_evidence(rows, metadata, "123", DAY, DAY, DAY))
    assert proof["complete"] is (change is None)
    assert proof["zero_confirmed"] is (change is None)


@pytest.mark.asyncio
async def test_real_meta_fetch_carries_explicit_zero_proof(monkeypatch):
    from integrations_control_center import meta_native_reporting as module
    monkeypatch.setattr(module, "meta_appsecret_proof", lambda token: "test-proof")
    payload = meta_payload()
    payload["data"][0]["account_currency"] = "SAR"
    async def handler(request):
        assert "account_id" in request.url.params["fields"]
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        row = await module._fetch_day(client, "token", {"ad_account_id": "act_123", "currency": "SAR"}, DAY)
    assert close_proof(**BASE, **row["close_evidence"])["zero_confirmed"] is True


@pytest.mark.asyncio
async def test_real_tiktok_fetch_carries_explicit_zero_proof():
    from integrations_control_center import tiktok_native_reporting as module
    async def handler(request):
        assert request.url.params["start_date"] == request.url.params["end_date"] == str(DAY)
        return httpx.Response(200, json={"code": 0, "data": tiktok_payload()})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        row = await module._fetch_day(client, "token", {"ad_account_id": "123"}, DAY)
    assert close_proof(**BASE, **row["close_evidence"])["zero_confirmed"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,complete", [([{"results": list(google_rows())}], True),
    ([{"results": list(google_rows()), "nextPageToken": "next"}], False),
    ([{"results": [None]}], False),
    ([{"results": list(google_rows())}, {}], False), ({"results": list(google_rows())}, False)])
async def test_google_search_stream_retains_completion_provenance(monkeypatch, payload, complete):
    from integrations_control_center import google_ads_reporting as module
    monkeypatch.setattr(module, "_developer_token", lambda: "test-token")
    async def handler(request):
        return httpx.Response(200, json=payload)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        rows = await module._search_stream(client, access_token="token", account={"ad_account_id": "123"}, query="SELECT")
    assert rows.complete_response is complete


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [False, True])
async def test_snapchat_raw_missing_metric_cannot_become_explicit_zero(missing):
    from datetime import datetime, timezone
    from snapchat_v2.client import SnapchatV2Client
    class TokenStore:
        async def get_access_token(self, *args, **kwargs):
            return "test-token"
    points = [{"id": "c" + str(i), "timeseries": [{
        "start_time": "2026-09-29T00:00:00+00:00", "end_time": "2026-09-29T01:00:00+00:00",
        "stats": {"spend": None} if missing and i == 1 else {"spend": 0}}]} for i in range(2)]
    payload = {"request_status": "SUCCESS", "timeseries_stats": [{"sub_request_status": "SUCCESS",
        "timeseries_stat": {"granularity": "HOUR", "breakdown_stats": {"campaign": points}}}], "paging": {}}
    async def handler(request):
        return httpx.Response(200, json=payload)
    client = SnapchatV2Client(object(), "owner", token_store=TokenStore(),
        client_factory=lambda **kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await client.fetch_hourly_facts({"ad_account_id": "123", "currency": "SAR", "timezone": "UTC"},
        start_utc=datetime(2026, 9, 29, tzinfo=timezone.utc),
        end_utc=datetime(2026, 9, 29, 1, tzinfo=timezone.utc), sync_run_id="run")
    assert result["account_rows"][0]["spend_native"] == 0
    assert result["account_rows"][0]["source"]["explicit_spend_present"] is (not missing)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [None, "missing_spend", "missing_hour", "provisional", "incomplete", "no_data"])
async def test_snapchat_daily_proof_requires_all_actual_closed_hours(monkeypatch, change):
    from datetime import datetime, timedelta, timezone
    from snapchat_v2.projections import build_daily_projection
    start = datetime(2026, 9, 29, tzinfo=timezone.utc)
    observed = start + timedelta(days=1, hours=1)
    facts = [dict(ad_account_id="123", currency="SAR", account_timezone="UTC", spend_native=0,
        hour_start_utc=start + timedelta(hours=i), updated_at=observed, sync_run_id="run",
        source={"explicit_spend_present": True}) for i in range(24)]
    coverage = dict(status="complete", expected_requests=1, completed_requests=1, data_state="confirmed_zero")
    if change == "missing_spend": facts[4]["source"] = {}
    elif change == "missing_hour": facts.pop()
    elif change == "provisional": facts[4]["provisional"] = True
    elif change == "incomplete": coverage["status"] = "incomplete"
    elif change == "no_data": facts = []; coverage["data_state"] = "confirmed_no_data"
    async def load(*args, **kwargs):
        return facts
    monkeypatch.setattr("snapchat_v2.projections.load_hourly_facts", load)
    projection = await build_daily_projection(object(), user_id="owner",
        account={"ad_account_id": "123", "currency": "SAR", "timezone": "UTC"},
        report_date=DAY, projection_timezone="UTC", coverage=coverage, now=observed)
    assert projection["source_close_proof"]["complete"] is (change is None)
    assert projection["source_close_proof"]["zero_confirmed"] is (change is None)
