"""Capability expiry, application-log hygiene and non-cacheable responses.

Proxy/CDN/APM capture outside Python logging needs separate deployment evidence.
"""
from datetime import datetime, timezone
import logging
import re

from shipping_print_document import DocumentError

_CAPABILITY = re.compile(r"((?:/|%2f)carrier-label(?:/|%2f)document(?:/|%2f))[^\s/?#\"'<>\\]+", re.I)
_SECRET_KEYS = {"capability_token", "document_token"}


def assert_document_unexpired(document, *, now=None):
    """Call after the final await, immediately before constructing the response."""
    expires = document.get("expires_at")
    if isinstance(expires, datetime) and expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)  # Mongo's default UTC codec.
    instant = now or datetime.now(timezone.utc)
    if not isinstance(expires, datetime) or expires <= instant:
        raise DocumentError("shipping_document_expired", status_code=410)


def redact_capability_data(value):
    """Sanitize ordinary structured monitoring data without depending on an SDK."""
    if isinstance(value, str):
        return _CAPABILITY.sub(r"\1[REDACTED]", value)
    if isinstance(value, bytes):
        return redact_capability_data(value.decode("utf-8", errors="replace")).encode("utf-8")
    if isinstance(value, dict):
        return {redact_capability_data(key): "[REDACTED]" if str(key).lower() in _SECRET_KEYS
                else redact_capability_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return tuple(redact_capability_data(item) for item in value)
    if isinstance(value, list):
        return [redact_capability_data(item) for item in value]
    return value


class CapabilityLogFilter(logging.Filter):
    def filter(self, record):
        # Preserve tuple arguments: uvicorn's AccessFormatter unpacks all five.
        record.msg = redact_capability_data(record.msg if isinstance(record.msg, str) else str(record.msg))
        record.args = redact_capability_data(record.args)
        if record.exc_info:
            record.exc_text = redact_capability_data(logging.Formatter().formatException(record.exc_info))
            record.exc_info = None  # No formatter can regenerate unsanitized text.
        elif record.exc_text:
            record.exc_text = redact_capability_data(record.exc_text)
        if record.stack_info:
            record.stack_info = redact_capability_data(record.stack_info)
        for key, value in list(record.__dict__.items()):
            if key not in {"msg", "args", "exc_info", "exc_text", "stack_info"}:
                record.__dict__[key] = "[REDACTED]" if key.lower() in _SECRET_KEYS else redact_capability_data(value)
        return True


_FILTER = CapabilityLogFilter()


def install_capability_log_redaction():
    """Install after logging configuration; safe to call repeatedly.

Handler filters cover propagated records. A record factory also covers handlers
added later, including ASGI server access handlers installed after app import.
"""
    previous = logging.getLogRecordFactory()
    if not getattr(previous, "_shipping_capability_redactor", False):
        def factory(*args, **kwargs):
            record = previous(*args, **kwargs)
            _FILTER.filter(record)
            return record
        factory._shipping_capability_redactor = True
        logging.setLogRecordFactory(factory)
    for name in (None, "uvicorn", "uvicorn.access", "uvicorn.error", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        if _FILTER not in logger.filters:
            logger.addFilter(_FILTER)
        for handler in logger.handlers:
            if _FILTER not in handler.filters:
                handler.addFilter(_FILTER)


class CapabilityResponseHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        protected = scope.get("type") == "http" and "/carrier-label" in path
        started = False
        async def guarded_send(message):
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            if protected and message["type"] == "http.response.start":
                headers = [(key, value) for key, value in message.get("headers", []) if key.lower() not in {
                    b"cache-control", b"referrer-policy", b"x-content-type-options"}]
                message = {**message, "headers": headers + [
                    (b"cache-control", b"no-store, private"),
                    (b"referrer-policy", b"no-referrer"),
                    (b"x-content-type-options", b"nosniff")]}
            await send(message)
        try:
            await self.app(scope, receive, guarded_send)
        except Exception:
            if not protected or started:
                raise
            # Never put a request URL, exception payload, or token in this log.
            logging.getLogger(__name__).error("Carrier label request failed")
            body = b'{"detail":{"code":"shipping_document_unavailable"}}'
            await guarded_send({"type": "http.response.start", "status": 500,
                "headers": [(b"content-type", b"application/json"),
                            (b"content-length", str(len(body)).encode())]})
            await guarded_send({"type": "http.response.body", "body": body})
