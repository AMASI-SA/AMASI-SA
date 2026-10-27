"""Legacy-compatible single-document fence for the actual Google refresh writers.

Relinks already replace encrypted credential material and its update timestamp.
Compare the exact observed document fields, including absent versus null, so an
older refresh cannot overwrite a relink or a competing refresh. This is not an
OAuth/account-membership proof or a grant-generation migration. Never log the
snapshot or storage query: both contain encrypted credential material.
"""
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from .google_oauth_security import GOOGLE_CREDENTIALS_COLLECTION

_FENCE_FIELDS = (
    "access_token_ciphertext", "refresh_token_ciphertext", "expires_at", "updated_at",
    "scope", "google_subject", "google_email", "token_type", "grant_generation",
    "token_revision", "last_refresh_operation_id",
)
CREDENTIAL_REFRESH_PROJECTION = {"_id": 0, "user_id": 1, **{key: 1 for key in _FENCE_FIELDS}}


class GoogleRefreshPersistenceError(Exception):
    """Only a safe code/status may cross the credential boundary."""
    def __init__(self, *, conflict=False):
        self.code = "google_oauth_refresh_conflict" if conflict else "google_oauth_refresh_unconfirmed"
        self.status_code = 409 if conflict else 503
        super().__init__(self.code)


def _snapshot_query(user_id, row):
    return {"user_id": user_id, **{
        key: {"$eq": row[key], "$exists": True} if key in row else {"$exists": False}
        for key in _FENCE_FIELDS
    }}


async def persist_refreshed_google_credential(db, *, user_id, previous, values):
    """One fenced write and exact committed-state read; never retry or upsert.

    Missing, unacknowledged, changed or unreadable evidence returns no token.
    All three provider-facing helpers invoke this before returning their token.
    No client, connection status, refresh token or account projection is changed.
    """
    if (not isinstance(user_id, str) or not user_id or not isinstance(previous, dict)
        or previous.get("user_id") != user_id or not isinstance(values, dict)
        or set(values) - {"access_token_ciphertext", "expires_at", "updated_at", "token_type"}
        or not {"access_token_ciphertext", "expires_at", "updated_at"} <= set(values)):
        raise GoogleRefreshPersistenceError()
    try:
        collection = db[GOOGLE_CREDENTIALS_COLLECTION].with_options(
            write_concern=WriteConcern(w="majority", j=True),
            read_concern=ReadConcern("majority"), read_preference=ReadPreference.PRIMARY)
        result = await collection.update_one(_snapshot_query(user_id, previous),
            {"$set": values}, upsert=False)
        if result.acknowledged is not True:
            raise GoogleRefreshPersistenceError()
        if result.matched_count != 1:
            raise GoogleRefreshPersistenceError(conflict=True)
        committed = await collection.find_one(_snapshot_query(user_id, {**previous, **values}),
            {"_id": 0, "user_id": 1})
        if not committed or committed.get("user_id") != user_id:
            raise GoogleRefreshPersistenceError(conflict=True)
    except GoogleRefreshPersistenceError:
        raise
    except Exception:
        # Driver exceptions can embed credential queries or transport details.
        # Ambiguous acknowledgement is not permission to return or resend.
        raise GoogleRefreshPersistenceError() from None
