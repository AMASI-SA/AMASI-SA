"""Require a real Mongo TTL index before saving temporary customer PII.

The purge_after field alone does NOT delete data. Production endpoints must
fail closed until the operator installs the index in an approved migration.
No index changes or deletions occur during normal user requests.
"""

from __future__ import annotations

from typing import Any

INDEX_NAME = "salla_assisted_quote_purge_ttl_v1"


class RetentionNotReady(RuntimeError):
    pass


def _valid_ttl(index: Any) -> bool:
    if not isinstance(index, dict) or index.get("expireAfterSeconds") != 0:
        return False
    keys = index.get("key")
    return (isinstance(keys, (list, tuple)) and len(keys) == 1 and
            tuple(keys[0]) == ("purge_after", 1))


async def require_assisted_quote_ttl(db: Any) -> None:
    try:
        indexes = await db.salla_ai_order_quotes.index_information()
    except Exception as exc:
        raise RetentionNotReady("quote_retention_index_unreadable") from exc
    if (not isinstance(indexes, dict) or
            not _valid_ttl(indexes.get(INDEX_NAME))):
        raise RetentionNotReady("quote_pii_retention_ttl_missing")


async def install_assisted_quote_ttl(db: Any) -> str:
    """Explicit approved migration helper, NEVER called per request."""
    await db.salla_ai_order_quotes.create_index(
        [("purge_after", 1)], expireAfterSeconds=0, name=INDEX_NAME,
    )
    await require_assisted_quote_ttl(db)
    return INDEX_NAME
