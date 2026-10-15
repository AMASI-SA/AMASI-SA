import json

import httpx
import pytest

from external_browser_connection import probe_browser_connection


@pytest.mark.asyncio
async def test_missing_access_performs_no_network():
    def unexpected(request):
        pytest.fail('missing access must not create a browser session')
    result = await probe_browser_connection('', transport=httpx.MockTransport(unexpected))
    assert result == {'status': 'not_configured', 'storefront_checked': False}


@pytest.mark.asyncio
async def test_probe_uses_fixed_code_no_store_data_and_no_token_in_url():
    calls = []
    def respond(request):
        calls.append(request)
        assert request.url.host == 'production-sfo.browserless.io'
        assert 'secret' not in str(request.url)
        assert request.headers['authorization'] == 'Bearer secret'
        payload = json.loads(request.content)
        assert set(payload['context']) == {'nonce'}
        assert 'page.goto' not in payload['code']
        assert 'screenshot' not in payload['code']
        return httpx.Response(200, json={'protocol': 'mezan-browser-probe-v1',
            'nonce': payload['context']['nonce'], 'blank_page': True})
    result = await probe_browser_connection('secret', transport=httpx.MockTransport(respond))
    assert result == {'status': 'connection_verified', 'storefront_checked': False}
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('code,status', [(401, 'access_denied'), (403, 'access_denied'),
    (429, 'capacity_unavailable'), (408, 'timeout'), (500, 'service_unavailable'),
    (302, 'unexpected_response')])
async def test_errors_are_sanitized_and_never_retried(code, status):
    calls = []
    def respond(request):
        calls.append(request)
        return httpx.Response(code, text='secret raw error', headers={'location': 'https://other.example'})
    result = await probe_browser_connection('secret', transport=httpx.MockTransport(respond))
    assert result == {'status': status, 'storefront_checked': False}
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['wrong_nonce', 'extra_fields', 'not_blank', 'oversize', 'html', 'compressed'])
async def test_does_not_accept_unverified_or_unbounded_response(kind):
    def respond(request):
        nonce = json.loads(request.content)['context']['nonce']
        data = {'protocol': 'mezan-browser-probe-v1', 'nonce': nonce, 'blank_page': True}
        if kind == 'wrong_nonce':
            data['nonce'] = 'wrong'
        if kind == 'extra_fields':
            data['cookies'] = 'must not accept'
        if kind == 'not_blank':
            data['blank_page'] = False
        if kind == 'oversize':
            return httpx.Response(200, content=b' ' * 8193, headers={'content-type': 'application/json'})
        if kind == 'html':
            return httpx.Response(200, text='<html>login</html>')
        if kind == 'compressed':
            return httpx.Response(200, content=b'', headers={'content-type': 'application/json', 'content-encoding': 'gzip'})
        return httpx.Response(200, json=data)
    result = await probe_browser_connection('secret', transport=httpx.MockTransport(respond))
    assert result == {'status': 'unexpected_response', 'storefront_checked': False}


@pytest.mark.asyncio
async def test_transport_error_does_not_expose_exception_or_secret():
    def respond(request):
        raise httpx.ConnectError('secret private exception', request=request)
    result = await probe_browser_connection('secret', transport=httpx.MockTransport(respond))
    assert result == {'status': 'service_unavailable', 'storefront_checked': False}


@pytest.mark.asyncio
async def test_documented_envelope_is_supported():
    def respond(request):
        nonce = json.loads(request.content)['context']['nonce']
        return httpx.Response(200, json={'type': 'application/json', 'data': {
            'protocol': 'mezan-browser-probe-v1', 'nonce': nonce, 'blank_page': True}})
    assert (await probe_browser_connection('secret', transport=httpx.MockTransport(respond)))['status'] == 'connection_verified'


@pytest.mark.asyncio
async def test_timeout_is_not_retried():
    def respond(request):
        raise httpx.ReadTimeout('secret timeout', request=request)
    assert (await probe_browser_connection('secret', transport=httpx.MockTransport(respond)))['status'] == 'timeout'


@pytest.mark.asyncio
async def test_invalid_secret_configuration_is_rejected_before_network():
    def respond(request):
        pytest.fail('must not send malformed header')
    assert (await probe_browser_connection('secret\n', transport=httpx.MockTransport(respond)))['status'] == 'invalid_configuration'
