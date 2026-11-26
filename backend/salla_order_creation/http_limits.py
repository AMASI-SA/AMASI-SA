"""Fail-closed bounded HTTP body read for signed service-to-service actions.

Keep FastAPI-specific request handling out of the pure HMAC modules so
synthetic tests remain hermetic. A malicious Content-Length is not trusted:
enforce the byte cap while streaming as well.
"""

from __future__ import annotations

from typing import Any

from .signature import MAX_BODY


class BodyTooLarge(ValueError):
    pass


async def read_bounded_action_body(request: Any) -> bytes:
    size = request.headers.get("content-length")
    if size is not None:
        try:
            size_num = int(size)
        except (ValueError, TypeError) as exc:
            raise BodyTooLarge("invalid_content_length") from exc
        if size_num < 0 or size_num > MAX_BODY:
            raise BodyTooLarge("signed_action_body_too_large")
    result = bytearray()
    async for chunk in request.stream():
        if type(chunk) is not bytes:
            raise BodyTooLarge("invalid_action_stream")
        if len(result) + len(chunk) > MAX_BODY:
            raise BodyTooLarge("signed_action_body_too_large")
        result.extend(chunk)
    if not result:
        raise BodyTooLarge("signed_action_body_missing")
    return bytes(result)
