"""Order changes must invalidate a campaign result independently of campaign IDs."""
import asyncio

import pytest
from order_engine import campaign_hover as hover


@pytest.fixture(autouse=True)
def clear_snapshot_state():
    hover._CACHE.clear()
    hover._PENDING.clear()
    yield
    hover._CACHE.clear()
    hover._PENDING.clear()


@pytest.mark.asyncio
async def test_same_campaign_new_revision_recounts_salla_without_waiting_for_ttl(monkeypatch):
    calls = []
    async def read(db, owner, ids):
        calls.append((owner, tuple(ids)))
        return {"campaigns": {ids[0]: {"orders": 29 + len(calls)}}}
    monkeypatch.setattr(hover, "read_campaign_hover", read)
    first = await hover.cached_campaign_hover(None, "owner", ["campaign"], snapshot_revision="r:30")
    repeat = await hover.cached_campaign_hover(None, "owner", ["campaign"], snapshot_revision="r:30")
    changed = await hover.cached_campaign_hover(None, "owner", ["campaign"], snapshot_revision="r:31")
    assert first["campaigns"]["campaign"]["orders"] == 30
    assert repeat == first
    assert changed["campaigns"]["campaign"]["orders"] == 31
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_identical_revision_is_coalesced_and_tenant_scope_is_preserved(monkeypatch):
    calls = []
    async def read(db, owner, ids):
        calls.append(owner)
        await asyncio.sleep(0)
        return {"campaigns": {ids[0]: {"orders": len(calls)}}}
    monkeypatch.setattr(hover, "read_campaign_hover", read)
    await asyncio.gather(*[
        hover.cached_campaign_hover(None, "owner", ["campaign"], snapshot_revision="r:31")
        for _ in range(4)
    ])
    assert calls == ["owner"]
    await hover.cached_campaign_hover(None, "other-owner", ["campaign"], snapshot_revision="r:31")
    assert calls == ["owner", "other-owner"]


@pytest.mark.asyncio
async def test_legacy_client_without_revision_retains_bounded_cache(monkeypatch):
    calls = []
    async def read(db, owner, ids):
        calls.append(owner)
        return {"campaigns": {}}
    monkeypatch.setattr(hover, "read_campaign_hover", read)
    await hover.cached_campaign_hover(None, "owner", ["campaign"])
    await hover.cached_campaign_hover(None, "owner", ["campaign"])
    assert calls == ["owner"]
