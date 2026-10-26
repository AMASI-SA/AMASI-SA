"""Executable WIP acceptance: a relink must fence an older Ads refresh.

The strict xfail records a known production-path gap, not a passing fix.
All data and the provider response below are synthetic; no network is used.
"""
from datetime import datetime, timezone

import pytest

from integrations_control_center import google_ads_reporting as reporting


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
