"""Redact credential fields at the standard logging output boundary.

Requests, LogRecords and logger filters keep their original data. Redaction is
applied after the existing formatter has rendered the message and traceback.
"""
from __future__ import annotations

from copy import copy
import logging
import re
from urllib.parse import unquote_plus


_REDACTED = "[REDACTED]"
_SECRET_KEYS = frozenset({
    "access_token", "appsecret_proof", "client_secret", "app_secret",
    "fb_exchange_token", "input_token", "refresh_token", "id_token",
    "api_key", "api_secret", "authorization", "password",
})
_SECRET_QUERY_KEYS = _SECRET_KEYS | {"code"}
_QUERY_FIELD = re.compile(
    r"(?P<prefix>[?&;](?P<key>[\w%+.-]+)=)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s&#\"'<>]*)"
)
_SECRET_FIELD = re.compile(
    r"(?P<prefix>(?<![\w%])(?:"
    + "|".join(sorted(_SECRET_KEYS, key=len, reverse=True))
    + r")[\"']?\s*[:=]\s*)"
    r"(?P<value>(?:Bearer[ \t]+)?(?:\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,&\#<>\"']+))",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/%=-]+")


def redact_sensitive_log_text(text: str) -> str:
    def query_field(match: re.Match[str]) -> str:
        key = unquote_plus(match["key"]).casefold().replace("-", "_")
        if key in _SECRET_QUERY_KEYS:
            return match["prefix"] + _REDACTED
        return match[0]

    def secret_field(match: re.Match[str]) -> str:
        value = match["value"]
        quote = value[0] if value[0] in {"\"", "'"} else ""
        return match["prefix"] + quote + _REDACTED + quote

    text = _QUERY_FIELD.sub(query_field, text)
    text = _SECRET_FIELD.sub(secret_field, text)
    return _BEARER.sub("Bearer " + _REDACTED, text)


class SensitiveDataFormatter(logging.Formatter):
    """Keep custom formatters (including Uvicorn's) and sanitize their output."""

    def __init__(self, formatter: logging.Formatter | None = None) -> None:
        super().__init__()
        self.formatter = formatter or logging.Formatter()

    def format(self, record: logging.LogRecord) -> str:
        # Formatting caches message/exception text. Keep those copies private
        # so another handler or a structured access-log filter is unaffected.
        return redact_sensitive_log_text(self.formatter.format(copy(record)))


def configure_sensitive_logging() -> None:
    """Configure web-process logging before provider imports.

    Uvicorn configures its own non-propagating handlers before importing the
    app. Wrap those as well as root handlers. Call again after any deliberate
    runtime logging reconfiguration; repeated installation is harmless.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    loggers = [logging.getLogger(), *[
        logger for logger in logging.root.manager.loggerDict.values()
        if isinstance(logger, logging.Logger)
    ]]
    for logger in loggers:
        for handler in logger.handlers:
            if not isinstance(handler.formatter, SensitiveDataFormatter):
                handler.setFormatter(SensitiveDataFormatter(handler.formatter))
