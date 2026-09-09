"""Real-Mongo acceptance tests for Qoyod credential rotation.

These tests are opt-in through QOYOD_TEST_MONGO_URL and use unique disposable
databases. They never contact Qoyod or load customer data.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from uuid import uuid4

import httpx
import pytest
from cryptography.fernet import Fernet
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient

from integrations.qoyod.models import QoyodSettings


BACKEND_ROOT = Path(__file__).resolve().parents[1]
_PROCESS_LOG_LIMIT = 8192
_READINESS_PATH = "/api/integrations/qoyod/settings"


def _mongo_url() -> str:
    url = os.environ.get("QOYOD_TEST_MONGO_URL")
    if not url:
        pytest.skip("QOYOD_TEST_MONGO_URL is required for real-Mongo acceptance")
    return url


def _unused_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _finish_process(process: subprocess.Popen) -> str:
    if process.poll() is None:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)
    process_log = getattr(process, "_qoyod_test_log", None)
    if process_log is None:
        return ""
    try:
        process_log.flush()
        process_log.seek(0, os.SEEK_END)
        size = process_log.tell()
        process_log.seek(max(0, size - _PROCESS_LOG_LIMIT))
        return process_log.read().decode("utf-8", errors="replace")
    finally:
        process_log.close()


def _safe_process_error_type(output: str) -> str | None:
    if "Could not import module" in output or "Error loading ASGI app" in output:
        return "ImportError"
    matches = re.findall(
        r"(?m)(?:^|\s)([A-Za-z_][A-Za-z0-9_.]{0,120}(?:Error|Exception))(?=:|\s|$)",
        output,
    )
    if not matches:
        return None
    return matches[-1].rsplit(".", 1)[-1]


def _start_failure(
    *,
    process_output: str,
    observed_exit_code: int | None,
    request_error_type: str | None,
    http_status: int | None,
    probe_path: str,
    elapsed_ms: int,
) -> AssertionError:
    process_error_type = _safe_process_error_type(process_output)
    if observed_exit_code is not None:
        category = (
            "import_failure"
            if process_error_type in {"ImportError", "ModuleNotFoundError"}
            else "process_exit"
        )
        error_type = process_error_type or "ProcessExit"
    elif http_status is not None:
        category = (
            "openapi_generation_failure"
            if probe_path == "/openapi.json" and http_status >= 500
            else "http_non_success"
        )
        error_type = process_error_type or "HTTPStatusError"
    else:
        category = "connection_failure"
        error_type = request_error_type or process_error_type or "ConnectionError"
    exit_code = (
        str(observed_exit_code) if observed_exit_code is not None else "running"
    )
    return AssertionError(
        "isolated Qoyod test app readiness failed: "
        f"category={category} error_type={error_type} "
        f"http_status={http_status if http_status is not None else 'none'} "
        f"exit_code={exit_code} elapsed_ms={elapsed_ms}"
    )


def _start_app(
    env: dict[str, str],
    port: int,
    *,
    probe_path: str = _READINESS_PATH,
    timeout_seconds: float = 15,
) -> subprocess.Popen:
    process_log = tempfile.TemporaryFile(mode="w+b")
    try:
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
            stdout=process_log,
            stderr=subprocess.STDOUT,
        )
    except Exception:
        process_log.close()
        raise
    setattr(process, "_qoyod_test_log", process_log)
    started_at = time.monotonic()
    deadline = started_at + timeout_seconds
    request_error_type = None
    http_status = None
    with httpx.Client(
        base_url=f"http://127.0.0.1:{port}",
        timeout=2.0,
        trust_env=False,
    ) as client:
        while time.monotonic() < deadline:
            observed_exit_code = process.poll()
            if observed_exit_code is not None:
                process_output = _finish_process(process)
                raise _start_failure(
                    process_output=process_output,
                    observed_exit_code=observed_exit_code,
                    request_error_type=request_error_type,
                    http_status=http_status,
                    probe_path=probe_path,
                    elapsed_ms=int((time.monotonic() - started_at) * 1000),
                )
            try:
                response = client.get(probe_path)
            except httpx.RequestError as exc:
                request_error_type = type(exc).__name__
                time.sleep(0.1)
                continue
            http_status = response.status_code
            if response.status_code == 200:
                return process
            process_output = _finish_process(process)
            raise _start_failure(
                process_output=process_output,
                observed_exit_code=None,
                request_error_type=request_error_type,
                http_status=http_status,
                probe_path=probe_path,
                elapsed_ms=int((time.monotonic() - started_at) * 1000),
            )

    observed_exit_code = process.poll()
    process_output = _finish_process(process)
    raise _start_failure(
        process_output=process_output,
        observed_exit_code=observed_exit_code,
        request_error_type=request_error_type,
        http_status=http_status,
        probe_path=probe_path,
        elapsed_ms=int((time.monotonic() - started_at) * 1000),
    )


def _stop_app(process: subprocess.Popen | None) -> None:
    if process is None:
        return
    _finish_process(process)


@pytest.mark.parametrize(
    (
        "process_output",
        "exit_code",
        "request_error_type",
        "http_status",
        "probe_path",
        "expected_category",
        "expected_error_type",
    ),
    [
        (
            "Traceback (synthetic)\nModuleNotFoundError: secret-value",
            1,
            None,
            None,
            _READINESS_PATH,
            "import_failure",
            "ModuleNotFoundError",
        ),
        (
            "fatal startup detail secret-value",
            3,
            None,
            None,
            _READINESS_PATH,
            "process_exit",
            "ProcessExit",
        ),
        (
            "",
            None,
            "ConnectError",
            None,
            _READINESS_PATH,
            "connection_failure",
            "ConnectError",
        ),
        (
            "",
            None,
            None,
            503,
            _READINESS_PATH,
            "http_non_success",
            "HTTPStatusError",
        ),
        (
            "Traceback (synthetic)\npydantic.errors.PydanticUserError: secret-value",
            None,
            None,
            500,
            "/openapi.json",
            "openapi_generation_failure",
            "PydanticUserError",
        ),
    ],
)
def test_start_app_failure_diagnostics_are_classified_and_redacted(
    process_output,
    exit_code,
    request_error_type,
    http_status,
    probe_path,
    expected_category,
    expected_error_type,
) -> None:
    diagnostic = str(
        _start_failure(
            process_output=process_output,
            observed_exit_code=exit_code,
            request_error_type=request_error_type,
            http_status=http_status,
            probe_path=probe_path,
            elapsed_ms=125,
        )
    )

    assert f"category={expected_category}" in diagnostic
    assert f"error_type={expected_error_type}" in diagnostic
    assert f"http_status={http_status if http_status is not None else 'none'}" in diagnostic
    assert "elapsed_ms=125" in diagnostic
    assert "secret-value" not in diagnostic
    assert "Traceback" not in diagnostic


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
    settings_collection = mongo[db_name].qoyod_settings
    baseline = QoyodSettings(
        user_id="main",
        enabled=False,
        auto_send=False,
    ).model_dump(mode="json")
    settings_collection.insert_one(baseline.copy())
    stored_baseline = settings_collection.find_one(
        {"user_id": "main"},
        {"_id": 0},
    )
    assert stored_baseline == baseline
    process = None
    try:
        first_port = _unused_port()
        process = _start_app(env, first_port)
        with httpx.Client(base_url=f"http://127.0.0.1:{first_port}") as client:
            settings_before_save = client.get(
                "/api/integrations/qoyod/settings"
            )
            saved = client.post(
                "/api/integrations/qoyod/credentials",
                json={"api_key": f"  {synthetic_key}  "},
            )
            settings_after_save = client.get(
                "/api/integrations/qoyod/settings"
            )
        assert settings_before_save.status_code == 200
        assert settings_before_save.headers["cache-control"] == "no-store"
        assert settings_before_save.json()["enabled"] == baseline["enabled"]
        assert settings_before_save.json()["auto_send"] == baseline["auto_send"]
        assert synthetic_key not in settings_before_save.text
        assert settings_collection.find_one(
            {"user_id": "main"},
            {"_id": 0},
        ) == baseline
        assert saved.status_code == 200
        assert saved.headers["cache-control"] == "no-store"
        assert saved.json()["fingerprint"] == expected_fingerprint
        assert synthetic_key not in saved.text
        assert settings_after_save.status_code == 200
        assert settings_after_save.headers["cache-control"] == "no-store"
        assert settings_after_save.json()["enabled"] == baseline["enabled"]
        assert settings_after_save.json()["auto_send"] == baseline["auto_send"]
        assert settings_after_save.json()["credentials"] == {
            "configured": True,
            "fingerprint": expected_fingerprint,
        }
        assert synthetic_key not in settings_after_save.text
        assert settings_collection.find_one(
            {"user_id": "main"},
            {"_id": 0},
        ) == baseline
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
        assert settings.json()["enabled"] == baseline["enabled"]
        assert settings.json()["auto_send"] == baseline["auto_send"]
        assert synthetic_key not in settings.text
        assert verified.status_code == 200
        assert verified.headers["cache-control"] == "no-store"
        assert verified.json()["ok"] is True
        assert verified.json()["fingerprint"] == expected_fingerprint
        assert synthetic_key not in verified.text

        assert settings_collection.find_one(
            {"user_id": "main"},
            {"_id": 0},
        ) == baseline

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


def test_empty_settings_database_exposes_defaults_without_persisting_or_rotating(
) -> None:
    mongo_url = _mongo_url()
    db_name = f"qoyod_credentials_restart_empty_{uuid4().hex}"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(BACKEND_ROOT),
            "QOYOD_TEST_MONGO_URL": mongo_url,
            "QOYOD_TEST_DB_NAME": db_name,
            "QOYOD_TEST_EXPECTED_KEY_SHA256": hashlib.sha256(
                b"unused-synthetic-qoyod-key"
            ).hexdigest(),
            "QOYOD_TOKEN_ENC_KEY": Fernet.generate_key().decode("ascii"),
        }
    )
    mongo = MongoClient(mongo_url, serverSelectionTimeoutMS=5000)
    mongo.admin.command("ping")
    process = None
    try:
        port = _unused_port()
        process = _start_app(env, port)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}") as client:
            settings = client.get("/api/integrations/qoyod/settings")

        assert settings.status_code == 200
        assert settings.headers["cache-control"] == "no-store"
        assert settings.json()["enabled"] is False
        assert settings.json()["auto_send"] is True
        assert settings.json()["credentials"] == {
            "configured": False,
            "fingerprint": None,
        }
        assert (
            mongo[db_name].qoyod_settings.find_one({"user_id": "main"}) is None
        )
        assert (
            mongo[db_name].qoyod_credentials.find_one({"user_id": "main"}) is None
        )
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
