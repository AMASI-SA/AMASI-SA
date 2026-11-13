"""Bounded decoding and completion tests without live providers."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import json

import pytest

from integrations_control_center import google_merchant_data_sources_reader as reader
from integrations_control_center.google_merchant_data_sources_proof import DataSourceProofError, MAX_PAGE_BYTES
from tests.test_google_merchant_data_sources import source


class Response:
    def __init__(self, payload=None, *, raw=None, status=200, headers=None, wait=False):
        self.status_code = status
        self.headers = {"content-type": "application/json"} if headers is None else headers
        self.raw = json.dumps({} if payload is None else payload).encode() if raw is None else raw
        self.bytes_read = 0
        self.wait = wait
        self.started = asyncio.Event()
    async def aiter_bytes(self, chunk_size):
        assert chunk_size == 8192
        self.started.set()
        if self.wait: await asyncio.Event().wait()
        for i in range(0, len(self.raw), chunk_size):
            chunk = self.raw[i:i+chunk_size]
            self.bytes_read += len(chunk)
            yield chunk


class Bound:
    def __init__(self, pages): self.pages, self.tokens, self.closed = pages, [], 0
    @asynccontextmanager
    async def stream_merchant_data_sources(self, *, page_token):
        self.tokens.append(page_token)
        try: yield self.pages[len(self.tokens)-1]
        finally: self.closed += 1


async def read(pages, **kwargs):
    return await reader.read_merchant_data_sources(Bound(pages), merchant_id="123", **kwargs)


@pytest.mark.asyncio
async def test_empty_complete_is_not_failed_or_unproved_membership():
    result = await read([Response()])
    assert result["sources"] == [] and result["sources_observed"] == 0
    assert result["pagination_traversal_complete"] is True and result["atomic_snapshot"] is False
    assert len(result["page_digests"]) == 1 and len(result["page_digests"][0]) == 64


@pytest.mark.asyncio
async def test_five_full_distinct_pages_allowed_and_no_silent_truncation():
    pages = [Response({"dataSources": [source(source_id=str(1+100*p+i)) for i in range(100)],
        **({"nextPageToken": f"opaque-{p}"} if p < 4 else {})}) for p in range(5)]
    bound = Bound(pages)
    result = await reader.read_merchant_data_sources(bound, merchant_id="123")
    assert result["sources_observed"] == 500 and result["pages_read"] == 5
    assert bound.tokens == [None, "opaque-0", "opaque-1", "opaque-2", "opaque-3"]
    assert bound.closed == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["duplicate", "cycle", "sixth_page", "too_many_rows"])
async def test_incomplete_or_ambiguous_traversal_discards_all_sources(mode):
    if mode == "duplicate": pages = [Response({"dataSources": [source()], "nextPageToken": "n"}), Response({"dataSources": [source()]})]
    elif mode == "cycle": pages = [Response({"nextPageToken": "n"}), Response({"nextPageToken": "n"})]
    elif mode == "sixth_page": pages = [Response({"nextPageToken": str(i)}) for i in range(5)]
    else: pages = [Response({"dataSources": [source(source_id=str(i+1)) for i in range(101)]})]
    bound = Bound(pages)
    with pytest.raises(DataSourceProofError): await reader.read_merchant_data_sources(bound, merchant_id="123")
    assert bound.closed == len(bound.tokens) <= 5


@pytest.mark.asyncio
@pytest.mark.parametrize("status,headers", [(302, {"content-type": "application/json"}),
    (401, {"content-type": "application/json"}), (403, {"content-type": "application/json"}),
    (429, {"content-type": "application/json"}), (500, {"content-type": "application/json"}),
    (200, {}), (200, {"content-type": "text/html"}),
    (200, {"content-type": "application/json", "content-encoding": "gzip"})])
async def test_error_redirect_wrong_content_or_encoding_never_reads_body(status, headers):
    page = Response(raw=b"PRIVATE_PASSWORD", status=status, headers=headers)
    with pytest.raises(DataSourceProofError) as error: await read([page])
    assert page.bytes_read == 0 and "PRIVATE" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("raw", [b'{"dataSources":[],"dataSources":[]}', b'{"dataSources":[{"input":"FILE","input":"API"}]}',
    b'{"nextPageToken":NaN}', b'\xff', b'[]', b'null', b'PRIVATE_INVALID_JSON',
    b'{"fetchSettings":{"password":"PRIVATE"}}'])
async def test_invalid_json_and_unrequested_fields_never_escape(raw):
    with pytest.raises(DataSourceProofError) as error: await read([Response(raw=raw)])
    assert "PRIVATE" not in str(error.value)


@pytest.mark.asyncio
async def test_page_bound_before_append_and_exact_boundary_allowed():
    valid = b'{}' + b' ' * (MAX_PAGE_BYTES - 2)
    assert (await read([Response(raw=valid)]))["sources"] == []
    oversized = Response(raw=valid + b' ')
    with pytest.raises(DataSourceProofError, match="bound_exceeded"): await read([oversized])
    assert oversized.bytes_read <= MAX_PAGE_BYTES + 8192


@pytest.mark.asyncio
async def test_total_accepted_input_bound_across_pages():
    pages = []
    for i in range(5):
        raw = json.dumps({"nextPageToken": str(i)}).encode()
        pages.append(Response(raw=raw+b' '*(MAX_PAGE_BYTES-len(raw))))
    bound = Bound(pages)
    with pytest.raises(DataSourceProofError, match="bound_exceeded"):
        await reader.read_merchant_data_sources(bound, merchant_id="123")
    assert sum(page.bytes_read for page in pages) == 1024*1024 + 8192
    assert bound.closed == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("which", ["PAGE_SECONDS", "READ_SECONDS"])
async def test_finite_timeout_discards_result_and_closes_stream(monkeypatch, which):
    monkeypatch.setattr(reader, which, .02)
    bound = Bound([Response(wait=True)])
    with pytest.raises(DataSourceProofError, match="timeout"):
        await reader.read_merchant_data_sources(bound, merchant_id="123")
    assert bound.closed == 1


@pytest.mark.asyncio
async def test_external_cancellation_propagates():
    page = Response(wait=True); bound = Bound([page])
    task = asyncio.create_task(reader.read_merchant_data_sources(bound, merchant_id="123"))
    await asyncio.wait_for(page.started.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError): await task
    assert bound.closed == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["naive", "backwards"])
async def test_invalid_observation_timestamps_refused(mode):
    now = datetime.now(timezone.utc)
    values = iter([now.replace(tzinfo=None)] if mode == "naive" else [now, now-timedelta(seconds=1)])
    with pytest.raises(DataSourceProofError): await read([Response()], clock=lambda: next(values))
