"""Unwired, at-most-once refresh operation registration and claim.

The trusted caller generates independent random UUIDv4 operation and attempt
IDs, and registers before an external refresh. A claim ACK permits at most one
attempt. An ambiguous registration or claim ACK NEVER authorizes sending; no
retry or inference from a later read. A relink fence must still be checked by
all credential writers; this module does not implement that integration.
Rows must not be deleted or TTL-expired while IDs or generation/revision scopes could be reused.
This collection is dedicated to this journal and must have no preexisting partial
or sparse unique scope indexes. A failed index creation prevents all sends.
"""
from datetime import datetime
from uuid import UUID
from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern
from pymongo.read_concern import ReadConcern
from pymongo import ReadPreference

class OperationInvalid(ValueError): pass
class OperationAlreadyExists(Exception): pass
class OperationNotClaimed(Exception): pass
class RetryWritesEnabled(Exception): pass

_PROOF = object()

class _RegistrationTicket:
    def __init__(self, scope, proof):
        self.scope = scope
        self.proof = proof
        self.used = False


def _uuid4(value):
    try:
        u = UUID(value)
        return u.version == 4 and str(u) == value
    except (TypeError, ValueError, AttributeError):
        return False


def _validate(user_id, generation, revision, operation_id):
    if (not isinstance(user_id,str) or not user_id.strip() or user_id != user_id.strip() or len(user_id) > 160
        or not isinstance(generation,str) or not generation.strip() or generation != generation.strip() or len(generation) > 160
        or type(revision) is not int or not 0 <= revision < 2**63-1 or not _uuid4(operation_id)):
        raise OperationInvalid('invalid operation scope')


def _writer(collection):
    try:
        retries = collection.database.client.options.retry_writes
    except (AttributeError, TypeError):
        raise RetryWritesEnabled('retryWrites not proven disabled') from None
    if retries is not False: raise RetryWritesEnabled('retryWrites not disabled')
    return collection.with_options(write_concern=WriteConcern(w='majority', j=True))


async def register_refresh_operation(collection, *, user_id, grant_generation, token_revision, operation_id, now):
    _validate(user_id, grant_generation, token_revision, operation_id)
    if not isinstance(now, datetime) or now.utcoffset() is None: raise OperationInvalid('invalid time')
    # Mongo _id unique index is intrinsic. A duplicate is never permission to
    # send, even if every field appears to match.
    writer = _writer(collection)
    # A distinct operation ID cannot authorize a concurrent refresh for the
    # same grant and token revision. Mongo builds/validates this non-sparse,
    # non-partial unique index before returning an acknowledged ticket.
    await writer.create_index([('user_id', 1), ('grant_generation', 1), ('token_revision', 1)],
        unique=True, name='google_refresh_grant_revision_unique')
    try:
        await writer.insert_one({'_id':operation_id,'user_id':user_id,'grant_generation':grant_generation,
            'token_revision':token_revision,'state':'registered','created_at':now})
    except DuplicateKeyError:
        raise OperationAlreadyExists('operation ID already registered') from None
    return _RegistrationTicket((user_id, grant_generation, token_revision, operation_id), _PROOF)


async def claim_refresh_operation(collection, *, user_id, grant_generation, token_revision, operation_id, attempt_id, now, ticket):
    _validate(user_id, grant_generation, token_revision, operation_id)
    if not _uuid4(attempt_id) or attempt_id == operation_id or not isinstance(now,datetime) or now.utcoffset() is None:
        raise OperationInvalid('invalid attempt identity')
    if (not isinstance(ticket, _RegistrationTicket) or ticket.proof is not _PROOF or ticket.used
        or ticket.scope != (user_id, grant_generation, token_revision, operation_id)):
        raise OperationNotClaimed('registration acknowledgement not proven')
    # Consume before the write; lost ACK or process interruption cannot replay.
    ticket.used = True
    result = await _writer(collection).update_one({'_id':operation_id,'user_id':user_id,'grant_generation':grant_generation,
        'token_revision':token_revision,'state':'registered'}, {'$set':{'state':'claimed','attempt_id':attempt_id,'claimed_at':now}}, upsert=False)
    if result.matched_count != 1: raise OperationNotClaimed('operation already claimed or scope changed')
    # Only this acknowledged call permits its caller to proceed once.


async def inspect_refresh_operation(collection, *, user_id, grant_generation, token_revision, operation_id):
    _validate(user_id, grant_generation, token_revision, operation_id)
    c = collection.with_options(read_concern=ReadConcern('majority'), read_preference=ReadPreference.PRIMARY)
    row = await c.find_one({'_id':operation_id,'user_id':user_id,'grant_generation':grant_generation,'token_revision':token_revision},
                           {'_id':0,'state':1})
    return row.get('state') if row and row.get('state') in ('registered','claimed') else 'unknown'
