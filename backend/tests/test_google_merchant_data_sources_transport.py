"""Additional allowlist seam tests; old products transport is unchanged."""
import pytest

from integrations_control_center import google_ads_reporting as reporting
from integrations_control_center.google_merchant_data_sources_proof import API_ROOT, DATA_SOURCE_FIELDS
from tests.test_google_bound_read_session import DB, USER, ACCOUNT, NOW, providers, session


@pytest.mark.asyncio
@pytest.mark.parametrize("token", [True, 12, [], {}, "x\nvalue", "x\rvalue", "x\x00value", "x\x7f", "x"*4097])
async def test_malformed_opaque_token_refused_before_http(monkeypatch, token):
    db = DB(); calls = providers(monkeypatch, db)
    with pytest.raises(reporting.GoogleBoundReadError, match="request_refused"):
        async with session(db) as bound:
            async with bound.stream_merchant_data_sources(page_token=token): pytest.fail("request escaped")
    assert calls["merchant_tokens"] == [] and calls["ads_closed"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("override", [{"url": "https://evil.invalid"}, {"merchant_id": "456"},
    {"params": {"fields": "*"}}, {"fields": "fetchSettings"}, {"method": "POST"},
    {"page_size": 1000}, {"follow_redirects": True}, {"headers": {}}])
async def test_no_arbitrary_request_surface(monkeypatch, override):
    db = DB(); calls = providers(monkeypatch, db)
    with pytest.raises(TypeError):
        async with session(db) as bound:
            async with bound.stream_merchant_data_sources(**override): pytest.fail("request escaped")
    assert calls["merchant_tokens"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("merchant", ["0", "01", "123/../456", "123?fields=*", "9223372036854775808"])
async def test_noncanonical_bound_merchant_cannot_reach_new_endpoint(monkeypatch, merchant):
    db = DB(); calls = providers(monkeypatch, db)
    with pytest.raises(reporting.GoogleBoundReadError):
        async with reporting.google_bound_read_session(db, USER, ads_account=ACCOUNT, merchant_id=merchant, clock=lambda: NOW) as bound:
            async with bound.stream_merchant_data_sources(): pytest.fail("request escaped")
    assert calls["merchant_tokens"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("url,params", [(API_ROOT+"123/dataSources", {"pageSize": 100}),
    (API_ROOT+"123/dataSources/18", {"pageSize": 100}),
    ("https://merchantapi.googleapis.com/products/v1/accounts/123/products", {"pageSize": 100, "fields": DATA_SOURCE_FIELDS}),
    (API_ROOT+"456/dataSources", {"pageSize": 100})])
async def test_existing_generic_product_stream_remains_closed_to_new_paths_and_masks(monkeypatch, url, params):
    db = DB(); calls = providers(monkeypatch, db)
    with pytest.raises(reporting.GoogleBoundReadError, match="request_refused"):
        async with session(db) as bound:
            async with bound.stream("GET", url, params=params, headers={"Accept-Encoding": "identity"}, follow_redirects=False):
                pytest.fail("request escaped")
    assert calls["merchant_tokens"] == []


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["before", "response", "after_body", "context_exit"])
async def test_new_stream_retains_credential_fences_at_every_boundary(monkeypatch, stage):
    db = DB()
    def changed(): db.collection.rows[0]["google_subject"] = "relinked"
    calls = providers(monkeypatch, db, merchant_effect=changed if stage == "response" else None)
    with pytest.raises(reporting.GoogleBoundReadError, match="credential_changed"):
        async with session(db) as bound:
            if stage == "before": changed()
            async with bound.stream_merchant_data_sources() as response:
                assert not hasattr(response, "request")
                assert response.headers == {"content-type": "application/json"}
                async for _ in response.aiter_bytes(): pass
                if stage == "after_body": changed()
            if stage == "context_exit": changed()
    assert calls["ads_closed"] == 1
    assert len(calls["merchant_tokens"]) == (0 if stage == "before" else 1)
