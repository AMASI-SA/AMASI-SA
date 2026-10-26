"""Unwired credential-refresh CAS primitive for a future Google grant migration.

A CAS success is only a storage acknowledgement, not OAuth/account ownership
proof. The caller must separately prove the grant and handle provider failures.
Never retry an ambiguous database acknowledgement or return a stale token.
"""
from datetime import datetime


class InvalidGeneration(ValueError):
    pass


class RefreshConflict(Exception):
    pass


async def commit_refreshed_credential(collection, *, user_id, grant_generation,
                                      token_revision, ciphertext, expires_at, updated_at):
    if (not isinstance(user_id, str) or not user_id.strip() or user_id != user_id.strip()
        or not isinstance(grant_generation, str) or not grant_generation.strip()
        or grant_generation != grant_generation.strip() or len(grant_generation) > 160
        or type(token_revision) is not int or not 0 <= token_revision < 2**63-1
        or not isinstance(ciphertext, str) or not ciphertext
        or not isinstance(expires_at, datetime) or expires_at.utcoffset() is None
        or not isinstance(updated_at, datetime) or updated_at.utcoffset() is None):
        raise InvalidGeneration('invalid refresh generation or encrypted material')
    next_revision = token_revision + 1
    result = await collection.update_one(
        {'user_id': user_id, 'grant_generation': grant_generation, 'token_revision': token_revision},
        {'$set': {'access_token_ciphertext': ciphertext, 'expires_at': expires_at,
                  'updated_at': updated_at, 'token_revision': next_revision}},
        upsert=False,
    )
    if result.matched_count != 1:
        raise RefreshConflict('grant or token revision changed')
    return next_revision
