"""Unwired refresh orchestration shared by Ads, GA4 and Merchant adapters.

This is a testable contract, not a production entry point. Callers must supply
validated generation, encrypted provider result and an exactly-once HTTP send
function. Relinks and every credential writer must use the same generation CAS.
No retry is performed after registration, claim, dispatch or commit ambiguity.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Awaitable, Callable

from integrations_control_center.google_oauth_generation import commit_refreshed_credential
from integrations_control_center.google_oauth_operation import (
    claim_refresh_operation,
    register_refresh_operation,
)

_SOURCES = frozenset({"ads", "ga4", "merchant"})


@dataclass(frozen=True)
class EncryptedRefresh:
    ciphertext: str
    expires_at: datetime


async def refresh_once(*, source: str, operations, credentials, user_id: str,
                       grant_generation: str, token_revision: int,
                       operation_id: str, attempt_id: str, now: datetime,
                       send: Callable[[], Awaitable[EncryptedRefresh]]) -> EncryptedRefresh:
    """Dispatch at most once; return only after acknowledged fenced commit.

    Exceptions and cancellation propagate; particularly, never retry or release
    the durable registration after an uncertain acknowledgement. A commit
    conflict must not return the provider's stale material. This function does
    not establish ownership, OAuth scope, account membership, safe HTTP client
    retry configuration, or projection atomicity.
    """
    if source not in _SOURCES:
        raise ValueError("unsupported Google source")
    ticket = await register_refresh_operation(
        operations, user_id=user_id, grant_generation=grant_generation,
        token_revision=token_revision, operation_id=operation_id, now=now)
    await claim_refresh_operation(
        operations, user_id=user_id, grant_generation=grant_generation,
        token_revision=token_revision, operation_id=operation_id,
        attempt_id=attempt_id, now=now, ticket=ticket)
    result = await send()
    if not isinstance(result, EncryptedRefresh):
        raise ValueError("invalid encrypted refresh result")
    await commit_refreshed_credential(
        credentials, user_id=user_id, grant_generation=grant_generation,
        token_revision=token_revision, ciphertext=result.ciphertext,
        expires_at=result.expires_at, updated_at=now, operation_id=operation_id)
    return result
