"""Unwired credential-refresh CAS primitive for a future Google grant migration.

A CAS success is only a storage acknowledgement, not OAuth/account ownership
proof. The caller must separately prove the grant and handle provider failures.
Never retry an ambiguous database acknowledgement or return a stale token.
"""
from datetime import datetime
import re
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

_OPERATION_ID = re.compile(r"^[A-Za-z0-9_-]{16,128}$")


class InvalidGeneration(ValueError):
    pass


class RefreshConflict(Exception):
    pass


class RetryWritesNotDisabled(Exception):
    pass


async def commit_refreshed_credential(collection, *, user_id, grant_generation,
                                      token_revision, ciphertext, expires_at, updated_at, operation_id):
    if (not isinstance(user_id, str) or not user_id.strip() or user_id != user_id.strip()
        or not isinstance(grant_generation, str) or not grant_generation.strip()
        or grant_generation != grant_generation.strip() or len(grant_generation) > 160
        or type(token_revision) is not int or not 0 <= token_revision < 2**63-1
        or not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id)
        or not isinstance(ciphertext, str) or not ciphertext
        or not isinstance(expires_at, datetime) or expires_at.utcoffset() is None
        or not isinstance(updated_at, datetime) or updated_at.utcoffset() is None):
        raise InvalidGeneration('invalid refresh generation or encrypted material')
    # PyMongo/Motor may replay a retryable write after an uncertain ACK.
    # Refuse the write unless the actual client is configured not to retry.
    try:
        retry_writes = collection.database.client.options.retry_writes
    except (AttributeError, TypeError):
        raise RetryWritesNotDisabled("cannot prove retryWrites is disabled") from None
    if retry_writes is not False:
        raise RetryWritesNotDisabled("retryWrites must be disabled")
    next_revision = token_revision + 1
    collection = collection.with_options(write_concern=WriteConcern(w="majority", j=True))
    result = await collection.update_one(
        {'user_id': user_id, 'grant_generation': grant_generation, 'token_revision': token_revision},
        {'$set': {'access_token_ciphertext': ciphertext, 'expires_at': expires_at,
                  'updated_at': updated_at, 'token_revision': next_revision,
                  'last_refresh_operation_id': operation_id}},
        upsert=False,
    )
    if result.matched_count != 1:
        raise RefreshConflict('grant or token revision changed')
    return next_revision


async def resolve_refreshed_credential(collection, *, user_id, grant_generation, token_revision, operation_id):
    """Read-only positive proof; anything else remains unknown, never retry.

    The caller must retain its unique operation identity durably before sending
    the CAS. Competing writers must never reuse that identity. A later refresh
    may erase the marker: absence is unknown, not proof of failure.
    """
    if (not isinstance(user_id, str) or not user_id.strip() or user_id != user_id.strip()
        or not isinstance(grant_generation, str) or not grant_generation.strip()
        or grant_generation != grant_generation.strip() or len(grant_generation) > 160
        or type(token_revision) is not int or not 0 <= token_revision < 2**63-1
        or not isinstance(operation_id, str) or not _OPERATION_ID.fullmatch(operation_id)):
        raise InvalidGeneration('invalid recovery identity')
    collection = collection.with_options(read_concern=ReadConcern("majority"), read_preference=ReadPreference.PRIMARY)
    row = await collection.find_one(
        {'user_id': user_id, 'grant_generation': grant_generation,
         'token_revision': token_revision + 1, 'last_refresh_operation_id': operation_id},
        {'_id': 0, 'last_refresh_operation_id': 1})
    return 'committed' if row and row.get('last_refresh_operation_id') == operation_id else 'unknown'
