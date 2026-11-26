"""Explicit opt-in registration of verified WhatsApp stage lookup in Mezan.

Default OFF. Never accept runtime configuration from HTTP or a language model.
Must be deployed only with paired pinned CE read verifier and reviewed secrets.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os
import re
import time

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

from .repository import MongoOrderRepository
from .whatsapp_stage_route import make_whatsapp_stage_router
from .whatsapp_sender_reader import ChatwootVerifiedSenderReader

FRESH_SECONDS = 24 * 3600


class MongoNonceClaim:
    """Cross-worker atomic nonce reservation; never stores raw nonce or PII."""
    def __init__(self, mongo_url: str, db_name: str):
        self._client=MongoClient(mongo_url,serverSelectionTimeoutMS=1500,
                                 socketTimeoutMS=1500)
        self._collection=self._client[db_name]["wa_order_stage_nonces_v1"]
        self._collection.create_index("expires_at",expireAfterSeconds=0)

    def __call__(self, digest: str, ttl_seconds: int) -> bool:
        if (type(digest) is not str or
                re.fullmatch(r"[a-f0-9]{64}",digest) is None
                or type(ttl_seconds) is not int or not 600<=ttl_seconds<=1800):
            return False
        expiry=datetime.now(timezone.utc)+timedelta(seconds=ttl_seconds)
        try:
            self._collection.insert_one({"_id":digest,"expires_at":expiry})
            return True
        except DuplicateKeyError:
            return False
        except PyMongoError:
            return False


async def _fresh_salla_source(dto) -> bool:
    """Do not confuse Mezan's local updated_at with upstream Salla freshness."""
    fetched=getattr(getattr(dto,"source",None),"fetched_at",None)
    if not isinstance(fetched,datetime) or fetched.tzinfo is None:
        return False
    now=datetime.now(timezone.utc)
    age=(now-fetched.astimezone(timezone.utc)).total_seconds()
    return -300<=age<=FRESH_SECONDS


def mount_if_enabled(api_router, db):
    """Mount only when all security dependencies are configured.

    Disabled mode imports no secrets, performs no DB access or network I/O.
    Missing settings in enabled mode are deployment failures, not fallbacks.
    """
    mode=os.environ.get("AMASI_WHATSAPP_ORDER_STAGE_ENABLED","0")
    if mode=="0":
        return False
    if mode!="1":
        raise RuntimeError("invalid_whatsapp_stage_flag")
    fields={
        name:os.environ.get(name,"")
        for name in (
            "AMASI_WHATSAPP_STAGE_OWNER_ID",
            "AMASI_WHATSAPP_STAGE_KEY_ID",
            "AMASI_WHATSAPP_STAGE_KEY_HEX",
            "AMASI_CHATWOOT_HOST",
            "AMASI_CHATWOOT_BOT_TOKEN",
            "MONGO_URL",
            "DB_NAME",
        )
    }
    if any(not val for val in fields.values()):
        raise RuntimeError("missing_whatsapp_stage_configuration")
    try:
        key=bytes.fromhex(fields["AMASI_WHATSAPP_STAGE_KEY_HEX"])
    except ValueError:
        raise RuntimeError("invalid_whatsapp_stage_key") from None
    if len(key)<32 or len(key)>128:
        raise RuntimeError("invalid_whatsapp_stage_key")

    host=fields["AMASI_CHATWOOT_HOST"]
    reader=ChatwootVerifiedSenderReader(
        base_url="https://"+host,approved_hostname=host,
        agentbot_token=fields["AMASI_CHATWOOT_BOT_TOKEN"])
    nonce=MongoNonceClaim(fields["MONGO_URL"],fields["DB_NAME"])
    router=make_whatsapp_stage_router(
        repository=MongoOrderRepository(db),
        fixed_owner_id=fields["AMASI_WHATSAPP_STAGE_OWNER_ID"],
        active_keys={fields["AMASI_WHATSAPP_STAGE_KEY_ID"]:key},
        claim_nonce=nonce,
        resolve_verified_sender=reader,
        source_freshness=_fresh_salla_source)
    api_router.include_router(router)
    return True
