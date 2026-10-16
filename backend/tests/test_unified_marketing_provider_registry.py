from unittest.mock import AsyncMock
import pytest
import unified_marketing.gateway as gateway
import unified_marketing.providers as providers

@pytest.mark.asyncio
async def test_gateway_preserves_existing_instrumentation(monkeypatch):
    read = AsyncMock(return_value={"id": "account"})
    monkeypatch.setattr(gateway, "load_snapchat_v2_account_identity", read)
    assert await gateway.load_unified_marketing_account_identity(None, "owner", provider="snapchat_ads") == {"id": "account"}
    read.assert_awaited_once_with(None, "owner")

@pytest.mark.asyncio
async def test_future_provider_uses_same_gateway_and_missing_capability_blocks(monkeypatch):
    read = AsyncMock(return_value={"id": "future-account"})
    monkeypatch.setitem(providers.PROVIDERS, "future_ads", providers.MarketingProvider("future_ads", "future", "future-contract", {"account_identity": read}))
    assert await gateway.load_unified_marketing_account_identity(None, "owner", provider="future_ads") == {"id": "future-account"}
    read.assert_awaited_once_with(None, "owner")
    with pytest.raises(ValueError, match="unsupported_unified_marketing_capability"):
        providers.get_reader("future_ads", "account_report")

@pytest.mark.asyncio
async def test_unsupported_provider_never_falls_back(monkeypatch):
    read = AsyncMock()
    monkeypatch.setattr(gateway, "load_snapchat_v2_account_identity", read)
    with pytest.raises(ValueError, match="unsupported_unified_marketing_provider"):
        await gateway.load_unified_marketing_account_identity(None, "owner", provider="meta_ads")
    read.assert_not_called()
