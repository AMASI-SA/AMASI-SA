from datetime import datetime, timezone

import httpx
import pytest

from integrations_control_center import google_oauth_refresh_transport as transport


@pytest.mark.asyncio
async def test_single_request_disables_http_retries_and_redirects(monkeypatch):
    calls = []
    class Response:
        status_code = 200
        def json(self): return {"access_token": "synthetic-plaintext", "expires_in": 3600}
    class Client:
        def __init__(self, **kwargs): calls.append(("client", kwargs))
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        async def post(self, url, **kwargs):
            calls.append(("post", url))
            return Response()
    monkeypatch.setattr(transport.httpx, "AsyncHTTPTransport", lambda **kwargs: ("transport", kwargs))
    monkeypatch.setattr(transport.httpx, "AsyncClient", Client)
    now = datetime(2026, 10, 27, tzinfo=timezone.utc)
    result = await transport.send_google_refresh_once(client_id="x", client_secret="y", refresh_token="z",
                                                      now=now, encrypt=lambda _: "encrypted")
    assert result.ciphertext == "encrypted"
    assert result.expires_at.timestamp() == now.timestamp() + 3600
    assert calls == [("client", {"transport": ("transport", {"retries": 0}),
                                 "follow_redirects": False, "timeout": 25.0, "trust_env": False}),
                     ("post", transport.GOOGLE_TOKEN_URL)]


@pytest.mark.asyncio
async def test_ambiguous_network_error_is_sanitized_and_not_retried(monkeypatch):
    calls = []
    class Client:
        def __init__(self, **_kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *_args): pass
        async def post(self, *_args, **_kwargs):
            calls.append(1)
            raise httpx.ReadTimeout("secret-synthetic")
    monkeypatch.setattr(transport.httpx, "AsyncHTTPTransport", lambda **_kwargs: object())
    monkeypatch.setattr(transport.httpx, "AsyncClient", Client)
    with pytest.raises(transport.RefreshProviderError) as error:
        await transport.send_google_refresh_once(client_id="x", client_secret="y", refresh_token="z",
            now=datetime(2026, 10, 27, tzinfo=timezone.utc), encrypt=lambda _: "encrypted")
    assert calls == [1] and "secret-synthetic" not in str(error.value)
    assert error.value.__suppress_context__
