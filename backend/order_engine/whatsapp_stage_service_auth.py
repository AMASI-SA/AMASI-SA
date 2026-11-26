"""Isolated, fail-closed authentication primitive for Mezan WhatsApp stage reads.

NOT a FastAPI route or proof of WhatsApp sender ownership. Server must separately
validate a trusted Chatwoot inbound event, buyer match and indexed order lookup.
No order content or customer identifiers are ever logged here.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import time
from typing import Callable, Mapping

MAX_BODY_BYTES = 2048
MAX_AGE_SECONDS = 300
NONCE = re.compile(r"[A-Za-z0-9_-]{22,96}\Z", re.ASCII)
KEY_ID = re.compile(r"[A-Za-z0-9_-]{1,64}\Z", re.ASCII)
EXPECTED_PATH = "/internal/whatsapp/order-stage/read"
EXPECTED_METHOD = "POST"


class StageServiceUnauthorized(Exception):
    """Uniform denial; never disclose caller, key or order existence."""


def verify_stage_service_request(
    *,
    method: str,
    path: str,
    body: bytes,
    timestamp: str,
    nonce: str,
    key_id: str,
    signature: str,
    active_keys: Mapping[str, bytes],
    claim_nonce: Callable[[str, int], bool],
    now: int | None = None,
) -> None:
    """Validate a scoped HMAC request with atomic nonce replay prevention.

    Wire signature: hex HMAC-SHA256 over ASCII method, path, timestamp,
    nonce, key_id and SHA256(raw body), joined by linefeeds.

    claim_nonce MUST atomically reserve a hashed (key_id, nonce) tuple across
    every server instance for >= 2*MAX_AGE_SECONDS; local memory is unsafe.
    Failing closed on storage exceptions is mandatory. Keys are dedicated
    service-only secrets: do NOT use Mezan owner login or Salla credentials.
    """
    def deny() -> None:
        raise StageServiceUnauthorized("service_unauthorized")

    if (type(method) is not str or method != EXPECTED_METHOD
            or type(path) is not str or path != EXPECTED_PATH
            or type(body) is not bytes or len(body) > MAX_BODY_BYTES
            or type(timestamp) is not str or not re.fullmatch(r"[0-9]{10,11}", timestamp)
            or type(nonce) is not str or NONCE.fullmatch(nonce) is None
            or type(key_id) is not str or KEY_ID.fullmatch(key_id) is None
            or type(signature) is not str or re.fullmatch(r"[a-f0-9]{64}", signature) is None
            or not callable(claim_nonce)):
        deny()
    now_sec = int(time.time()) if now is None else now
    if type(now_sec) is not int or now_sec <= 0 or abs(now_sec - int(timestamp)) > MAX_AGE_SECONDS:
        deny()
    key = active_keys.get(key_id)
    if type(key) is not bytes or len(key) < 32:
        deny()
    digest = hashlib.sha256(body).hexdigest()
    message = "\n".join((method, path, timestamp, nonce, key_id, digest)).encode("ascii")
    expected = hmac.new(key, message, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        deny()
    # Keyed hash only, never persist the raw nonce or request body.
    nonce_hash = hmac.new(key, (key_id + ":" + nonce).encode("ascii"), hashlib.sha256).hexdigest()
    try:
        reserved = claim_nonce(nonce_hash, 2 * MAX_AGE_SECONDS + 1)
    except Exception:
        deny()
    if reserved is not True:
        deny()
