"""Hermetic bounded HMAC body streaming tests, no FastAPI requirement."""

import asyncio

import pytest

from salla_order_creation.http_limits import BodyTooLarge, read_bounded_action_body
from salla_order_creation.signature import MAX_BODY


class FakeRequest:
    def __init__(self, chunks, length=None):
        self.chunks = chunks
        self.headers = {}
        if length is not None:
            self.headers["content-length"] = str(length)

    async def stream(self):
        for part in self.chunks:
            yield part


def fetch(request):
    return asyncio.run(read_bounded_action_body(request))


def test_short_signed_body_read_with_matching_length():
    assert fetch(FakeRequest([b'{"',b'"a":1}'],length=7)) == b'{"a":1}'


def test_bounded_body_can_stream_without_content_length():
    assert fetch(FakeRequest([b"abc", b"def"])) == b"abcdef"


def test_rejects_declared_body_larger_than_limit():
    with pytest.raises(BodyTooLarge,match="too_large"):
        fetch(FakeRequest([b"x"],length=MAX_BODY+1))


def test_stream_size_bound_catches_forged_length():
    with pytest.raises(BodyTooLarge,match="too_large"):
        fetch(FakeRequest([b"x" * MAX_BODY, b"y"],length=1))


@pytest.mark.parametrize("length",["abc","-1","0x42"])
def test_invalid_or_negative_declared_length_rejected(length):
    with pytest.raises(BodyTooLarge):
        fetch(FakeRequest([b"x"],length=length))


def test_missing_body_denied():
    with pytest.raises(BodyTooLarge,match="body_missing"):
        fetch(FakeRequest([],length=0))


def test_invalid_stream_type_denied():
    with pytest.raises(BodyTooLarge,match="invalid_action_stream"):
        fetch(FakeRequest(["not bytes"]))
