import httpx
import pytest

import campaign_ai_product_intelligence_v3 as module


class TrackedBody(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.reads = 0
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            self.reads += 1
            yield chunk

    async def aclose(self):
        self.closed = True


def setup_probe(monkeypatch, body, headers, status=200):
    requests = []
    real_client = httpx.AsyncClient

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, stream=body)

    monkeypatch.setattr(module, '_is_public_ip', lambda host: True)
    monkeypatch.setattr(module.httpx, 'AsyncClient', lambda **kw: real_client(
        transport=httpx.MockTransport(handler), **kw))
    return requests


@pytest.mark.asyncio
@pytest.mark.parametrize('mime', ['video/mp4', 'image/png', 'application/octet-stream', 'text/html-not-real', ''])
async def test_non_html_is_closed_without_reading_body(monkeypatch, mime):
    body = TrackedBody([b'media must not be consumed'])
    setup_probe(monkeypatch, body, {'content-type': mime})
    result = await module.probe_public_product_page('https://store.test/p1', canonical_url='https://store.test/p1')
    assert result['status'] == 'PRODUCT_PAGE_UNAVAILABLE'
    assert result['reason'] == 'non_html_response'
    assert body.reads == 0
    assert body.closed


@pytest.mark.asyncio
async def test_redirect_is_reported_without_following_or_reading(monkeypatch):
    body = TrackedBody([b'ignored'])
    requests = setup_probe(monkeypatch, body, {'location': 'http://127.0.0.1/private'}, 302)
    result = await module.probe_public_product_page('https://store.test/p1', canonical_url='https://store.test/p1')
    assert result['status'] == 'PRODUCT_URL_REDIRECTED'
    assert result['redirect_followed'] is False
    assert len(requests) == 1
    assert body.reads == 0
    assert body.closed


@pytest.mark.asyncio
@pytest.mark.parametrize('headers', [
    {'content-type': 'text/html', 'content-length': '1000001'},
    {'content-type': 'text/html', 'content-encoding': 'gzip'},
])
async def test_oversize_or_encoded_body_is_not_read(monkeypatch, headers):
    body = TrackedBody([b'ignored'])
    setup_probe(monkeypatch, body, headers)
    result = await module.probe_public_product_page('https://store.test/p1', canonical_url='https://store.test/p1')
    assert result['status'] == 'PRODUCT_PAGE_UNAVAILABLE'
    assert body.reads == 0
    assert body.closed


@pytest.mark.asyncio
async def test_stream_stops_at_budget_without_returning_partial_facts(monkeypatch):
    body = TrackedBody([b'x'*1000]*2000)
    setup_probe(monkeypatch, body, {'content-type': 'text/html'})
    result = await module.probe_public_product_page('https://store.test/p1', canonical_url='https://store.test/p1')
    assert result['reason'] == 'html_response_too_large'
    assert 'page_title' not in result
    assert body.reads < 1010
    assert body.closed


@pytest.mark.asyncio
async def test_small_html_retains_health_evidence_without_subresource_requests(monkeypatch):
    body = TrackedBody([b'<title>Product</title><img src="https://cdn.test/a.jpg">add-to-cart'])
    requests = setup_probe(monkeypatch, body, {'content-type': 'text/html; charset=utf-8'})
    result = await module.probe_public_product_page('https://store.test/p1', canonical_url='https://store.test/p1')
    assert result['status'] == 'PRODUCT_URL_OK'
    assert result['page_title'] == 'Product'
    assert result['add_to_cart_marker_present'] is True
    assert len(requests) == 1
    assert requests[0].headers['accept-encoding'] == 'identity'
    assert body.closed


@pytest.mark.asyncio
async def test_missing_page_is_classified_without_consuming_error_body(monkeypatch):
    body = TrackedBody([b'error body'])
    setup_probe(monkeypatch, body, {'content-type': 'text/html'}, 404)
    result = await module.probe_public_product_page('https://store.test/p1', canonical_url='https://store.test/p1')
    assert result['status'] == 'PRODUCT_URL_BROKEN'
    assert body.reads == 0
    assert body.closed


@pytest.mark.asyncio
async def test_canonical_host_mismatch_makes_no_network_request(monkeypatch):
    body = TrackedBody([])
    requests = setup_probe(monkeypatch, body, {'content-type': 'text/html'})
    result = await module.probe_public_product_page('https://other.test/p1', canonical_url='https://store.test/p1')
    assert result['status'] == 'PRODUCT_URL_WRONG_DESTINATION'
    assert requests == []
