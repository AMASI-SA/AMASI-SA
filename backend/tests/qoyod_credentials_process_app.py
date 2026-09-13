"""Synthetic Qoyod credential app used by the real-Mongo restart test.

The process has no live-provider code path: the replacement client accepts
only the SHA-256 digest supplied by the isolated test process.
"""
from __future__ import annotations

import hashlib
import os

from fastapi import FastAPI
from motor.motor_asyncio import AsyncIOMotorClient

from integrations.qoyod import routes
from integrations.qoyod.api_client import QoyodAPIError


_mongo = AsyncIOMotorClient(
    os.environ["QOYOD_TEST_MONGO_URL"],
    serverSelectionTimeoutMS=5000,
)
_db = _mongo[os.environ["QOYOD_TEST_DB_NAME"]]
_expected_digest = os.environ["QOYOD_TEST_EXPECTED_KEY_SHA256"]


class _SyntheticQoyodClient:
    def __init__(self, api_key: str):
        self._matches = (
            hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            == _expected_digest
        )

    async def me(self) -> dict:
        if not self._matches:
            raise QoyodAPIError(
                status_code=401,
                code="qoyod_unauthorized",
                message="مفتاح API غير صالح أو منتهي",
                endpoint="synthetic-read-only-probe",
            )
        return {"id": "synthetic-qoyod-user"}


async def _current_user() -> dict:
    return {"id": "main", "email": "synthetic-owner@example.test"}


routes.QoyodAPIClient = _SyntheticQoyodClient
app = FastAPI()
app.include_router(routes.make_qoyod_router(_db, _current_user), prefix="/api")


@app.on_event("shutdown")
async def _close_mongo() -> None:
    _mongo.close()
