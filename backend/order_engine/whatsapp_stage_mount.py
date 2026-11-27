"""Explicit opt-in registration of verified WhatsApp stage lookup in Mezan.

Default OFF. Never accept runtime configuration from HTTP or a language model.
Must be deployed only with paired pinned CE read verifier and reviewed secrets.
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import os
import re
import time

from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from .repository import MongoOrderRepository
from .whatsapp_stage_route import make_whatsapp_stage_router
from .whatsapp_catalog_route import make_whatsapp_catalog_router
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


class MongoAssistantRateLimit:
    """Atomic shared per-service request ceiling. No customer identifiers."""
    def __init__(self,mongo_url: str,db_name: str,*,max_per_minute=30):
        self._client=MongoClient(mongo_url,serverSelectionTimeoutMS=1500,
                                 socketTimeoutMS=1500)
        self._collection=self._client[db_name]["wa_assistant_rate_limit_v1"]
        self._collection.create_index("expires_at",expireAfterSeconds=0)
        self._max=max_per_minute

    def __call__(self,key_id: str) -> bool:
        if type(key_id) is not str or len(key_id)>64:
            return False
        import hashlib
        bucket=int(time.time())//60
        marker=hashlib.sha256((key_id+":"+str(bucket)).encode()).hexdigest()
        try:
            row=self._collection.find_one_and_update(
                {"_id":marker},
                {"$inc":{"count":1},"$setOnInsert":{
                    "expires_at":datetime.now(timezone.utc)+timedelta(seconds=180)
                }},
                upsert=True,return_document=ReturnDocument.AFTER)
            return bool(row and row["count"]<=self._max)
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
    """Register read-only endpoints independently, preserving strict auth.

    Catalog-only mode needs signed HMAC and fixed owner but does NOT need
    Chatwoot buyer lookup credentials. Order-stage requires both. The legacy
    stage flag continues to mount catalog + stage together.
    """
    stage_mode=os.environ.get("AMASI_WHATSAPP_ORDER_STAGE_ENABLED","0")
    catalog_mode=os.environ.get("AMASI_WHATSAPP_CATALOG_ENABLED","0")
    if stage_mode=="0" and catalog_mode=="0":
        return False
    if stage_mode not in ("0","1") or catalog_mode not in ("0","1"):
        raise RuntimeError("invalid_whatsapp_readonly_flag")
    stage_on=stage_mode=="1"
    catalog_on=catalog_mode=="1" or stage_on
    names=[
        "AMASI_WHATSAPP_STAGE_OWNER_ID",
        "AMASI_WHATSAPP_STAGE_KEY_ID",
        "AMASI_WHATSAPP_STAGE_KEY_HEX",
        "MONGO_URL",
        "DB_NAME",
    ]
    if stage_on:
        names.extend(("AMASI_CHATWOOT_HOST","AMASI_CHATWOOT_BOT_TOKEN"))
    fields={name:os.environ.get(name,"") for name in names}
    if any(not val for val in fields.values()):
        raise RuntimeError("missing_whatsapp_readonly_configuration")
    try:
        key=bytes.fromhex(fields["AMASI_WHATSAPP_STAGE_KEY_HEX"])
    except ValueError:
        raise RuntimeError("invalid_whatsapp_stage_key") from None
    if len(key)<32 or len(key)>128:
        raise RuntimeError("invalid_whatsapp_stage_key")
    active_keys={fields["AMASI_WHATSAPP_STAGE_KEY_ID"]:key}
    nonce=MongoNonceClaim(fields["MONGO_URL"],fields["DB_NAME"])
    limit=MongoAssistantRateLimit(fields["MONGO_URL"],fields["DB_NAME"])
    if catalog_on:
        api_router.include_router(make_whatsapp_catalog_router(
            db=db,
            fixed_owner_id=fields["AMASI_WHATSAPP_STAGE_OWNER_ID"],
            active_keys=active_keys,
            claim_nonce=nonce,
            rate_limit=limit,
        ))
    if stage_on:
        host=fields["AMASI_CHATWOOT_HOST"]
        reader=ChatwootVerifiedSenderReader(
            base_url="https://"+host,approved_hostname=host,
            agentbot_token=fields["AMASI_CHATWOOT_BOT_TOKEN"])
        router=make_whatsapp_stage_router(
            repository=MongoOrderRepository(db),
            fixed_owner_id=fields["AMASI_WHATSAPP_STAGE_OWNER_ID"],
            active_keys=active_keys,
            claim_nonce=nonce,
            rate_limit=limit,
            resolve_verified_sender=reader,
            source_freshness=_fresh_salla_source,
        )
        api_router.include_router(router)
    return True
