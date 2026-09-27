"""Atomic aggregate persistence, scoped by merchant. No existing collection writes.

MongoStore uses a single-document compare-and-swap for data, audit and outbox.
MemoryStore is a test double, not a substitute for the real-Mongo acceptance gate.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
from typing import Any, Protocol

from .domain import DomainError

COLLECTION = "mezan_special_orders_v1"
MAX_DOCUMENT_BYTES = 2_000_000
MAX_COMMANDS = 512


class Store(Protocol):
    async def get(self, tenant_id: str, order_id: str) -> dict | None: ...
    async def insert(self, document: dict) -> bool: ...
    async def replace(self, document: dict, expected_revision: int) -> bool: ...
    async def list(self, tenant_id: str, limit: int, before: tuple[str, str] | None = None) -> list[dict]: ...


def bounded(document: dict) -> None:
    if len(document.get("command_log", {})) > MAX_COMMANDS or len(json.dumps(document, ensure_ascii=False).encode()) > MAX_DOCUMENT_BYTES:
        raise DomainError("aggregate_capacity_requires_archival")


class MongoStore:
    def __init__(self, db: Any):
        self.collection = db[COLLECTION]

    async def ensure_indexes(self) -> None:
        # Called by controlled setup, never by imports or read endpoints.
        await self.collection.create_index([("tenant_id", 1), ("order_number", 1)], unique=True, name="special_order_number")
        await self.collection.create_index([("tenant_id", 1), ("created_at", -1), ("order_id", -1)], name="special_order_page")
        await self.collection.create_index([("tenant_id", 1), ("financial_uses.key", 1)], unique=True,
            partialFilterExpression={"financial_uses.key": {"$exists": True}}, name="special_order_financial_use")
        await self.collection.create_index([("tenant_id", 1), ("delivery.carrier_key", 1), ("delivery.tracking_number", 1)], unique=True,
            partialFilterExpression={"delivery.method": "carrier"}, name="special_order_carrier_tracking")

    async def get(self, tenant_id: str, order_id: str) -> dict | None:
        return await self.collection.find_one({"tenant_id": tenant_id, "_id": order_id}, {"_id": 0})

    async def insert(self, document: dict) -> bool:
        bounded(document)
        from pymongo.errors import DuplicateKeyError
        try:
            await self.collection.insert_one({**deepcopy(document), "_id": document["order_id"]})
            return True
        except DuplicateKeyError as exc:
            if await self.get(document["tenant_id"], document["order_id"]) is not None:
                return False
            raise DomainError("unique_resource_already_used") from exc

    async def replace(self, document: dict, expected_revision: int) -> bool:
        bounded(document)
        from pymongo.errors import DuplicateKeyError
        try:
            result = await self.collection.replace_one(
                {"tenant_id": document["tenant_id"], "_id": document["order_id"], "revision": expected_revision},
                {**deepcopy(document), "_id": document["order_id"]})
        except DuplicateKeyError as exc:
            raise DomainError("unique_resource_already_used") from exc
        return result.matched_count == 1

    async def list(self, tenant_id: str, limit: int, before: tuple[str, str] | None = None) -> list[dict]:
        limit = max(1, min(int(limit), 101))
        query: dict = {"tenant_id": tenant_id}
        if before:
            query["$or"] = [{"created_at": {"$lt": before[0]}}, {"created_at": before[0], "order_id": {"$lt": before[1]}}]
        return await self.collection.find(query, {"_id": 0}).sort([("created_at", -1), ("order_id", -1)]).limit(limit).to_list(limit)


class MemoryStore:
    """Deterministic concurrent test double implementing the same CAS contract."""
    def __init__(self):
        self.documents: dict[tuple[str, str], dict] = {}
        self.lock = asyncio.Lock()

    def check_uniqueness(self, document: dict) -> None:
        for (tenant, identity), other in self.documents.items():
            if tenant != document["tenant_id"] or identity == document["order_id"]:
                continue
            if {u["key"] for u in other["financial_uses"]} & {u["key"] for u in document["financial_uses"]}:
                raise DomainError("unique_resource_already_used")
            if other["order_number"] == document["order_number"]:
                raise DomainError("unique_resource_already_used")
            a, b = document["delivery"], other["delivery"]
            if a["method"] == b["method"] == "carrier" and (a["carrier_key"], a["tracking_number"]) == (b["carrier_key"], b["tracking_number"]):
                raise DomainError("unique_resource_already_used")

    async def get(self, tenant_id: str, order_id: str) -> dict | None:
        async with self.lock:
            return deepcopy(self.documents.get((tenant_id, order_id)))

    async def insert(self, document: dict) -> bool:
        bounded(document)
        async with self.lock:
            key = (document["tenant_id"], document["order_id"])
            if key in self.documents:
                return False
            self.check_uniqueness(document)
            self.documents[key] = deepcopy(document)
            return True

    async def replace(self, document: dict, expected_revision: int) -> bool:
        bounded(document)
        async with self.lock:
            key = (document["tenant_id"], document["order_id"])
            old = self.documents.get(key)
            if old is None or old["revision"] != expected_revision:
                return False
            self.check_uniqueness(document)
            self.documents[key] = deepcopy(document)
            return True

    async def list(self, tenant_id: str, limit: int, before: tuple[str, str] | None = None) -> list[dict]:
        async with self.lock:
            docs = [deepcopy(d) for (tenant, _), d in self.documents.items() if tenant == tenant_id]
        docs.sort(key=lambda d: (d["created_at"], d["order_id"]), reverse=True)
        if before:
            docs = [d for d in docs if (d["created_at"], d["order_id"]) < before]
        return docs[:max(1, min(int(limit), 101))]
