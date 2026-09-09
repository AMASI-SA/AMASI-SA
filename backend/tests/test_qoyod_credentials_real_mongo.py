"""Real-Mongo acceptance tests for Qoyod credential rotation.

These tests are opt-in through QOYOD_TEST_MONGO_URL and use unique disposable
databases. They never contact Qoyod or load customer data.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import time
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _mongo_url() -> str:
    url = os.environ.get("QOYOD_TEST_MONGO_URL")
    if not url:
        pytest.skip("QOYOD_TEST_MONGO_URL is required for real-Mongo acceptance")
    return url


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start_app(env: dict[str, str], port: int) -> subprocess.Popen:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "tests.qoyod_credentials_process_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
            "--no-access-log",
        ],
        cwd=BACKEND_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("isolated Qoyod test app exited during startup")
        try:
            response = httpx.get(
                f"http://127.0.0.1:{port}/openapi.json",
                timeout=0.5,
            )
            if response.status_code == 200:
                return process
        except httpx.RequestError:
            pass
        time.sleep(0.1)
    process.terminate()
    process.wait(timeout=5)
    raise AssertionError("isolated Qoyod test app did not become ready")


def _stop_app(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _fingerprint(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{digest[:4]}…{digest[-4:]}"


def test_saved_credential_survives_real_process_restart_and_is_used() -> None:
    mongo_url = _mongo_url()
    db_name = f"qoyod_credentials_restart_{uuid4().hex}"
    synthetic_key = f"synthetic-qoyod-{secrets.token_hex(20)}"
    expected_fingerprint = _fingerprint(synthetic_key)
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(BACKEND_ROOT),
            "QOYOD_TEST_MONGO_URL": mongo_url,
            "QOYOD_TEST_DB_NAME": db_name,
            "QOYOD_TEST_EXPECTED_KEY_SHA256": hashlib.sha256(
                synthetic_key.encode("utf-8")
            ).hexdigest(),
            "QOYOD_TOKEN_ENC_KEY": Fernet.generate_key().decode("ascii"),
        }
    )
    mongo = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    mongo.admin.command("ping")
    process = None
    try:
        first_port = _unused_port()
        process = _start_app(env, first_port)
        with httpx.Client(base_url=f"http://127.0.0.1:{first_port}") as client:
            saved = client.post(
                "/api/integrations/qoyod/credentials",
                json={"api_key": f"  {synthetic_key}  "},
            )
        assert saved.status_code == 200
        assert saved.headers["cache-control"] == "no-store"
        assert saved.json()["fingerprint"] == expected_fingerprint
        assert synthetic_key not in saved.text
        _stop_app(process)
        process = None

        second_port = _unused_port()
        process = _start_app(env, second_port)
        with httpx.Client(base_url=f"http://127.0.0.1:{second_port}") as client:
            settings = client.get("/api/integrations/qoyod/settings")
            verified = client.post("/api/integrations/qoyod/test-connection")

        assert settings.status_code == 200
        assert settings.headers["cache-control"] == "no-store"
        assert settings.json()["credentials"] == {
            "configured": True,
            "fingerprint": expected_fingerprint,
        }
        assert settings.json()["enabled"] is False
        assert settings.json()["auto_send"] is False
        assert verified.status_code == 200
        assert verified.headers["cache-control"] == "no-store"
        assert verified.json()["ok"] is True
        assert verified.json()["fingerprint"] == expected_fingerprint
        assert synthetic_key not in verified.text

        stored = mongo[db_name].qoyod_credentials.find_one({"user_id": "main"})
        assert stored is not None
        assert stored["fingerprint"] == expected_fingerprint
        assert stored["credential_version"] == stored[
            "last_verified_credential_version"
        ]
        assert synthetic_key.encode("utf-8") not in stored["api_key_enc"]
    finally:
        _stop_app(process)
        mongo.drop_database(db_name)
        mongo.close()


@pytest.mark.asyncio
async def test_real_mongo_rejects_verification_after_concurrent_rotation(
    monkeypatch,
) -> None:
    mongo_url = _mongo_url()
    db_name = f"qoyod_credentials_concurrency_{uuid4().hex}"
    mongo = AsyncIOMotorClient(mongo_url, serverSelectionTimeoutMS=5000)
    db = mongo[db_name]
    request_started = asyncio.Event()
    release_request = asyncio.Event()
    from integrations.qoyod import crypto, routes

    monkeypatch.setenv("QOYOD_TOKEN_ENC_KEY", Fernet.generate_key().decode("ascii"))
    monkeypatch.delenv("QOYOD_TOKEN_ENC_KEY_OLD", raising=False)
    monkeypatch.setattr(crypto, "_fernet", None)

    class _DelayedClient:
        def __init__(self, _key: str):
            pass

        async def me(self) -> dict:
            request_started.set()
            await release_request.wait()
            return {"id": "synthetic-qoyod-user"}

    async def _current_user() -> dict:
        return {"id": "main", "email": "synthetic-owner@example.test"}

    monkeypatch.setattr(routes, "QoyodAPIClient", _DelayedClient)
    app = FastAPI()
    app.include_router(routes.make_qoyod_router(db, _current_user), prefix="/api")
    try:
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            first = await client.post(
                "/api/integrations/qoyod/credentials",
                json={"api_key": "synthetic-key-a"},
            )
            pending = asyncio.create_task(
                client.post("/api/integrations/qoyod/test-connection")
            )
            await asyncio.wait_for(request_started.wait(), timeout=5)
            rotated = await client.post(
                "/api/integrations/qoyod/credentials",
                json={"api_key": "synthetic-key-b"},
            )
            release_request.set()
            stale_result = await asyncio.wait_for(pending, timeout=5)

        assert first.status_code == 200
        assert rotated.status_code == 200
        assert stale_result.status_code == 200
        assert stale_result.json()["ok"] is False
        assert stale_result.json()["error"]["code"] == (
            "qoyod_credentials_changed_during_test"
        )
        stored = await db.qoyod_credentials.find_one({"user_id": "main"})
        assert stored["fingerprint"] == rotated.json()["fingerprint"]
        assert stored["last_verified_credential_version"] is None
        assert await db.qoyod_credentials.count_documents({"user_id": "main"}) == 1
    finally:
        await mongo.drop_database(db_name)
        mongo.close()
