"""Executable WIP acceptance: a relink must fence an older Ads refresh.

The strict xfail records a known production-path gap, not a passing fix.
All data and the provider response below are synthetic; no network is used.
"""
from datetime import datetime, timezone

import pytest

from integrations_control_center import google_ads_reporting as reporting
from integrations_control_center import google_analytics_realtime_routes as ga4
from integrations_control_center import google_merchant_registration as merchant


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="current Ads writer is keyed only by user_id")
async def test_relink_wins_against_old_ads_refresh(monkeypatch):
    current = {"user_id": "owner-test", "access_token_ciphertext": "old-access"}

    class Collection:
        async def update_one(self, query, update):
            if all(current.get(k) == v for k, v in query.items()):
                current.update(update["$set"])

    class DB:
        def __getitem__(self, _name):
            return Collection()

    class Response:
        status_code = 200
        def json(self):
            return {"access_token": "stale-access", "expires_in": 3600}

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        async def post(self, *_args, **_kwargs):
            # The owner reconnects while the old refresh is in flight.
            current.update(access_token_ciphertext="relinked-access")
            return Response()

    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_ID", "synthetic")
    monkeypatch.setenv("GOOGLE_OAUTH_CLIENT_SECRET", "synthetic")
    monkeypatch.setattr(reporting, "decrypt_google_token", lambda _value: "synthetic")
    monkeypatch.setattr(reporting, "encrypt_google_token", lambda value: value)
    monkeypatch.setattr(reporting.httpx, "AsyncClient", Client)
    await reporting._refresh_access_token(
        DB(), {"user_id": "owner-test", "refresh_token_ciphertext": "old"},
        datetime.now(timezone.utc),
    )
    assert current["access_token_ciphertext"] == "relinked-access"


@pytest.mark.asyncio
@pytest.mark.xfail(strict=True, reason="current credential writer is keyed only by user_id")
@pytest.mark.parametrize("source", ["ga4", "merchant"])
async def test_relink_wins_against_old_analytics_or_merchant_refresh(monkeypatch, source):
    module = ga4 if source == "ga4" else merchant
    scope = (ga4.GOOGLE_SCOPE_BY_PROVIDER[ga4.GA4_PROVIDER_ID] if source == "ga4"
             else merchant.GOOGLE_SCOPE_BY_PROVIDER[merchant.MERCHANT_PROVIDER])
    current = {"user_id": "owner-test", "access_token_ciphertext": "old-access",
               "refresh_token_ciphertext": "old-refresh", "scope": [scope],
               "google_email": "synthetic@example.invalid"}

    class Collection:
        async def find_one(self, *_args): return current.copy()
        async def update_one(self, query, update):
            if all(current.get(k) == v for k, v in query.items()): current.update(update["$set"])

    class DB:
        def __getitem__(self, _name): return Collection()

    class Response:
        status_code = 200
        def json(self): return {"access_token": "stale-access", "expires_in": 3600}

    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        async def post(self, *_args, **_kwargs):
            current.update(access_token_ciphertext="relinked-access")
            return Response()

    monkeypatch.setattr(module, "decrypt_google_token", lambda value: value)
    monkeypatch.setattr(module, "encrypt_google_token", lambda value: value)
    monkeypatch.setattr(module.httpx, "AsyncClient", Client)
    if source == "merchant":
        monkeypatch.setattr(merchant, "_approved_developer_email", lambda: "synthetic@example.invalid")
        await merchant._fresh_google_context(DB(), "owner-test")
    else:
        await ga4._fresh_google_access_token(DB(), "owner-test")
    assert current["access_token_ciphertext"] == "relinked-access"
