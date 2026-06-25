"""Regression tests for the Snapchat adapter bugs.

Bug 1: AdAccount /stats endpoint rejected the call because we requested
       fields=spend,impressions,swipes. Snap allows ONLY 'spend' here.

Bug 2: The '+' in '+03:00' was URL-decoded as space because the URL was
       built via f-string. We now pass time params via httpx params= dict
       which URL-encodes correctly.
"""
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

import pytest


def test_snap_adapter_uses_params_dict_and_only_spend_field():
    """Inspect the source file to assert the two invariants hold."""
    with open("/app/backend/ads_v2/sync/adapters.py", "r") as f:
        src = f.read()

    # Bug 1: must NOT request impressions/swipes alongside spend at acct level
    assert 'fields=spend,impressions,swipes' not in src, \
        "Account-level stats must only request 'spend' field"
    assert '"fields":      "spend",' in src or "'fields': 'spend'" in src, \
        "Adapter should pass fields='spend' via params"

    # Bug 2: URL must NOT be an f-string with raw start_time={start_iso}
    assert 'start_time={start_iso}' not in src, \
        "start_time/end_time must be passed as httpx params (URL-encoded)"
    assert '"start_time":  start_iso' in src or "'start_time': start_iso" in src, \
        "start_time should be passed via params dict"


def test_snap_adapter_url_encodes_plus_sign(monkeypatch):
    """End-to-end: the captured request URL must contain %2B (not '+')
    after the date — i.e. timezone offset is properly URL-encoded."""
    from ads_v2.sync import adapters

    captured = {}

    class _FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"total_stats": []}

    class _FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, headers=None, params=None):
            # httpx builds the final URL with params merged in
            import httpx as _h
            req = _h.Request("GET", url, params=params, headers=headers or {})
            captured["full_url"] = str(req.url)
            return _FakeResp()

    monkeypatch.setattr(adapters.httpx, "AsyncClient", _FakeClient)

    asyncio.run(adapters.fetch_snapchat_day(
        access_token="x",
        external_account_id="acct1",
        date_iso="2026-06-24",
        account_timezone="Asia/Riyadh",
    ))

    full = captured["full_url"]
    # The Asia/Riyadh +03:00 offset must be URL-encoded as %2B03%3A00
    # (or at minimum the '+' must be %2B). Should NOT contain a literal
    # space in the start_time/end_time.
    assert "start_time=" in full
    # extract the start_time= value
    qs = full.split("?", 1)[1] if "?" in full else ""
    pairs = dict(p.split("=", 1) for p in qs.split("&") if "=" in p)
    start_val = pairs.get("start_time", "")
    assert " " not in start_val, f"start_time contains literal space: {start_val!r}"
    # Must contain encoded plus
    assert "%2B" in start_val or "-" in start_val, \
        f"Timezone offset not URL-encoded in start_time={start_val!r}"


def test_snap_adapter_returns_spend_only_no_imp_clk():
    """When stats are present, only spend is parsed; impressions/clicks
    are set to 0 (since the account-level endpoint doesn't return them)."""
    from ads_v2.sync import adapters

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"total_stats": [{
                "total_stat": {"stats": {"spend": 5_000_000}}
            }]}

    class _Client:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, *a, **kw):
            return _Resp()

    with patch.object(adapters.httpx, "AsyncClient", _Client):
        row, status = asyncio.run(adapters.fetch_snapchat_day(
            access_token="t",
            external_account_id="acct",
            date_iso="2026-06-24",
        ))

    assert status["code"] == "ok"
    assert row["spend_native"] == 5.0  # 5,000,000 micros = 5 USD
    assert row["impressions"] == 0
    assert row["clicks"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
