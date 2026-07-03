"""Iter-2026-02.rev30 — Salla Easy Mode Token strategy support.

The Partners Portal setting "Webhook Security Strategy" decides
which credential Salla sends on each webhook request:

  • Signature — HMAC-SHA256(raw_body, secret) in `x-salla-signature`
  • Token     — the exact webhook secret in `Authorization: Bearer <token>`
                (or fallback header `X-Salla-Token: <token>`)

Since the currently-published app is on `Token` and changing it
would require a Publish Request, we support BOTH modes.

Safety invariants (NON-NEGOTIABLE):
  • constant-time comparisons (hmac.compare_digest)
  • NEVER log the token / secret / signature values
  • NEVER accept an unsigned/untokened request
  • default to Signature mode when the strategy header is missing
"""
from __future__ import annotations

import hashlib
import hmac
import sys

import pytest

sys.path.insert(0, "/app/backend")


SECRET = "s3cr3t-webhook-key"


# ── resolve_strategy ─────────────────────────────────────────────
def test_resolve_strategy_returns_signature_when_missing():
    from salla_integration.easy_mode_webhook import (
        resolve_strategy, STRATEGY_SIGNATURE,
    )
    assert resolve_strategy({}) == STRATEGY_SIGNATURE


def test_resolve_strategy_recognises_token_case_insensitive():
    from salla_integration.easy_mode_webhook import (
        resolve_strategy, STRATEGY_TOKEN,
    )
    assert resolve_strategy(
        {"x-salla-security-strategy": "Token"}) == STRATEGY_TOKEN
    assert resolve_strategy(
        {"x-salla-security-strategy": "TOKEN"}) == STRATEGY_TOKEN
    assert resolve_strategy(
        {"x-salla-security-strategy": " token "}) == STRATEGY_TOKEN


def test_resolve_strategy_defaults_signature_for_unknown_values():
    from salla_integration.easy_mode_webhook import (
        resolve_strategy, STRATEGY_SIGNATURE,
    )
    assert resolve_strategy(
        {"x-salla-security-strategy": "hmac_v2"}) == STRATEGY_SIGNATURE


# ── _extract_provided_token ──────────────────────────────────────
def test_extract_token_from_authorization_bearer():
    from salla_integration.easy_mode_webhook import _extract_provided_token
    assert _extract_provided_token(
        {"authorization": "Bearer abc123"}) == "abc123"


def test_extract_token_bearer_case_insensitive():
    from salla_integration.easy_mode_webhook import _extract_provided_token
    assert _extract_provided_token(
        {"authorization": "bearer abc123"}) == "abc123"
    assert _extract_provided_token(
        {"authorization": "BEARER abc123"}) == "abc123"


def test_extract_token_from_raw_authorization_value():
    """Some Salla clients omit the 'Bearer ' prefix — accept raw."""
    from salla_integration.easy_mode_webhook import _extract_provided_token
    assert _extract_provided_token({"authorization": "abc123"}) == "abc123"


def test_extract_token_from_x_salla_token_header():
    from salla_integration.easy_mode_webhook import _extract_provided_token
    assert _extract_provided_token(
        {"x-salla-token": "xyz789"}) == "xyz789"


def test_extract_token_returns_none_when_absent():
    from salla_integration.easy_mode_webhook import _extract_provided_token
    assert _extract_provided_token({}) is None
    assert _extract_provided_token({"authorization": ""}) is None
    assert _extract_provided_token({"authorization": "   "}) is None


# ── verify_token ─────────────────────────────────────────────────
def test_verify_token_accepts_exact_match():
    from salla_integration.easy_mode_webhook import verify_token
    assert verify_token(SECRET, SECRET) is True


def test_verify_token_tolerates_leading_trailing_whitespace():
    from salla_integration.easy_mode_webhook import verify_token
    assert verify_token(f"  {SECRET}  ", SECRET) is True


def test_verify_token_rejects_wrong_value():
    from salla_integration.easy_mode_webhook import verify_token
    assert verify_token("wrong-token", SECRET) is False


def test_verify_token_rejects_empty():
    from salla_integration.easy_mode_webhook import verify_token
    assert verify_token(None, SECRET) is False
    assert verify_token("", SECRET) is False


def test_verify_token_rejects_when_secret_empty():
    from salla_integration.easy_mode_webhook import verify_token
    assert verify_token(SECRET, "") is False


# ── verify_signature (regression: existing behaviour unchanged) ──
def test_verify_signature_still_works():
    from salla_integration.easy_mode_webhook import verify_signature
    body = b'{"event":"app.store.authorize"}'
    sig = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, sig, SECRET) is True
    assert verify_signature(body, "wrong-sig", SECRET) is False


# ── Constants exposed for the route ──────────────────────────────
def test_constants_are_lowercase_case_conventions():
    from salla_integration.easy_mode_webhook import (
        SIGNATURE_HEADER, STRATEGY_HEADER, TOKEN_HEADER_ALT,
        STRATEGY_SIGNATURE, STRATEGY_TOKEN,
    )
    # Header names lowercase (Starlette normalises to lowercase).
    assert SIGNATURE_HEADER == "x-salla-signature"
    assert STRATEGY_HEADER  == "x-salla-security-strategy"
    assert TOKEN_HEADER_ALT == "x-salla-token"
    assert STRATEGY_SIGNATURE == "signature"
    assert STRATEGY_TOKEN     == "token"


# ── Safety: values never appear in module-level log formatting ───
def test_no_secret_or_token_in_module_log_format_strings():
    """Static check — the module source must NOT contain a log call
    that formats the raw token / secret. We accept only presence
    flags (bool) and header names."""
    import inspect
    from salla_integration import easy_mode_webhook as m
    src = inspect.getsource(m)
    # These are the placeholder patterns that would leak the value.
    forbidden = [
        "log.info(\"easy_mode.token=%s\"",
        "log.info(\"easy_mode.secret=%s\"",
        "log.warning(\"easy_mode.token=%s\"",
        "log.warning(\"easy_mode.secret=%s\"",
        # Never format the token variable directly:
        "log.info(f\"token={",
        "log.info(f\"secret={",
    ]
    for pat in forbidden:
        assert pat not in src, (
            f"leak risk: `{pat}` appears in easy_mode_webhook.py")
