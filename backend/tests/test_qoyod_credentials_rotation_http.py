from __future__ import annotations

import copy
import hashlib
import asyncio

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient


class _UpdateResult:
    acknowledged = True

    def __init__(self, *, matched_count=0, modified_count=0):
        self.matched_count = matched_count
        self.modified_count = modified_count


class _CredentialsCollection:
    def __init__(self):
        self.rows: list[dict] = []
        self.fail_updates = False
        self.fail_reads = False
        self.return_unconfirmed_update = False

    async def find_one(self, query, projection=None, **_kwargs):
        if self.fail_reads:
            raise RuntimeError("synthetic credential read failure")
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                selected = copy.deepcopy(row)
                if projection:
                    selected = {
                        key: selected[key]
                        for key, included in projection.items()
                        if included and key in selected
                    }
                return selected
        return None

    async def update_one(self, query, update, upsert=False):
        if self.fail_updates:
            raise RuntimeError("synthetic storage failure")
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                row.update(copy.deepcopy(update.get("$set") or {}))
                for key, value in (update.get("$push") or {}).items():
                    row.setdefault(key, []).append(copy.deepcopy(value))
                return _UpdateResult(matched_count=1, modified_count=1)
        if upsert:
            self.rows.append({
                **copy.deepcopy(query),
                **copy.deepcopy(update.get("$set") or {}),
                **copy.deepcopy(update.get("$setOnInsert") or {}),
            })
            return _UpdateResult(matched_count=0, modified_count=1)
        return _UpdateResult()

    async def find_one_and_update(
        self,
        query,
        update,
        *,
        upsert=False,
        projection=None,
        return_document=None,
    ):
        if self.fail_updates:
            raise RuntimeError("synthetic storage failure")
        selected = None
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                row.update(copy.deepcopy(update.get("$set") or {}))
                selected = row
                break
        if selected is None and upsert:
            selected = {
                **copy.deepcopy(query),
                **copy.deepcopy(update.get("$set") or {}),
                **copy.deepcopy(update.get("$setOnInsert") or {}),
            }
            self.rows.append(selected)
        if selected is None:
            return None
        if self.return_unconfirmed_update:
            return None
        result = copy.deepcopy(selected)
        if projection:
            result = {
                key: result[key]
                for key, included in projection.items()
                if included and key in result
            }
        return result

    async def delete_one(self, query):
        before = len(self.rows)
        self.rows = [
            row for row in self.rows
            if not all(row.get(key) == value for key, value in query.items())
        ]

        class _DeleteResult:
            deleted_count = before - len(self.rows)

        return _DeleteResult()


class _SettingsCollection:
    def __init__(self):
        self.rows = [_settings_row()]

    async def find_one(self, query, projection=None, **_kwargs):
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                return copy.deepcopy(row)
        return None

    async def update_one(self, query, update, upsert=False):
        raise AssertionError("credential replacement must not update settings")


class _DB:
    def __init__(self):
        self.qoyod_credentials = _CredentialsCollection()
        self.qoyod_settings = _SettingsCollection()


async def _current_user():
    return {"id": "owner", "email": "owner@example.test"}


def _settings_row():
    return {
        "user_id": "main",
        "enabled": False,
        "auto_send": False,
        "auto_receipt": True,
        "dry_run_mode": False,
        "invoice_trigger_statuses": ["completed"],
        "invoice_date_source": "send_date",
        "trigger_once_only": True,
    }


def _build_app(db):
    from integrations.qoyod.routes import make_qoyod_router

    app = FastAPI()
    app.include_router(make_qoyod_router(db, _current_user), prefix="/api")
    return app


def _expected_fingerprint(value: str) -> str:
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
    return f"{digest[:4]}…{digest[-4:]}"


@pytest.fixture
def isolated_crypto(monkeypatch):
    from cryptography.fernet import Fernet
    from integrations.qoyod import crypto

    monkeypatch.setenv("QOYOD_TOKEN_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("QOYOD_TOKEN_ENC_KEY_OLD", raising=False)
    monkeypatch.setattr(crypto, "_fernet", None)


@pytest.mark.asyncio
async def test_rotation_round_trips_and_test_connection_uses_the_same_record(
    monkeypatch,
    isolated_crypto,
):
    from integrations.qoyod import crypto, routes

    mongo = AsyncMongoMockClient()
    db = mongo["qoyod_rotation"]
    await db.qoyod_settings.insert_one(_settings_row())
    first_app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=first_app),
        base_url="http://test",
    ) as client:
        old = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "old-key"},
        )
        assert old.status_code == 200
        rotated = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "  new-key  "},
        )

    expected = _expected_fingerprint("new-key")
    assert rotated.status_code == 200, rotated.text
    assert rotated.json() == {
        "ok": True,
        "user_id": "main",
        "fingerprint": expected,
        "updated_at": rotated.json()["updated_at"],
    }
    assert "new-key" not in rotated.text
    assert rotated.headers["cache-control"] == "no-store"
    assert await db.qoyod_credentials.count_documents({"user_id": "main"}) == 1
    stored = await db.qoyod_credentials.find_one({"user_id": "main"})
    assert stored["api_key_enc"] != b"new-key"
    assert b"new-key" not in stored["api_key_enc"]
    assert stored["fingerprint"] == expected
    stored_settings = await db.qoyod_settings.find_one({"user_id": "main"})
    assert stored_settings["auto_send"] is False
    assert stored_settings["enabled"] is False

    used_keys = []

    class _Client:
        def __init__(self, key):
            used_keys.append(key)

        async def me(self):
            return {"products": [], "meta": {"total": 0}}

    monkeypatch.setattr(routes, "QoyodAPIClient", _Client)

    # Recreate both the crypto reader and the FastAPI router while retaining
    # only the isolated Mongo data, matching the persistence boundary of a
    # service restart without starting a real worker or server.
    crypto._fernet = None
    restarted_app = _build_app(mongo["qoyod_rotation"])
    async with AsyncClient(
        transport=ASGITransport(app=restarted_app),
        base_url="http://test",
    ) as client:
        settings = await client.get(
            "/api/integrations/qoyod/settings",
            headers={"Cache-Control": "no-cache"},
        )
        connection = await client.post(
            "/api/integrations/qoyod/test-connection",
        )

    assert settings.status_code == 200
    assert settings.json()["credentials"] == {
        "configured": True,
        "fingerprint": expected,
    }
    assert settings.headers["cache-control"] == "no-store"
    assert connection.status_code == 200
    assert connection.json()["ok"] is True
    assert connection.json()["fingerprint"] == expected
    assert connection.headers["cache-control"] == "no-store"
    assert used_keys == ["new-key"]


@pytest.mark.asyncio
async def test_legacy_credential_gets_internal_version_before_verification(
    monkeypatch,
    isolated_crypto,
):
    from integrations.qoyod import credentials, routes

    db = _DB()
    legacy_key = "synthetic-legacy-key"
    db.qoyod_credentials.rows.append(
        {
            "user_id": "main",
            "api_key_enc": credentials.encrypt_secret(legacy_key),
            "fingerprint": _expected_fingerprint(legacy_key),
        }
    )
    used_keys = []

    class _Client:
        def __init__(self, key):
            used_keys.append(key)

        async def me(self):
            return {"id": "synthetic-user"}

    monkeypatch.setattr(routes, "QoyodAPIClient", _Client)
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/integrations/qoyod/test-connection",
        )

    stored = db.qoyod_credentials.rows[0]
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert response.headers["cache-control"] == "no-store"
    assert used_keys == [legacy_key]
    assert stored["credential_version"]
    assert stored["last_verified_credential_version"] == stored["credential_version"]


@pytest.mark.asyncio
async def test_failed_rotation_preserves_the_existing_record(isolated_crypto):
    db = _DB()
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        saved = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "old-key"},
        )
        assert saved.status_code == 200
        before = copy.deepcopy(db.qoyod_credentials.rows)
        db.qoyod_credentials.fail_updates = True
        failed = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "replacement-key"},
        )

    assert failed.status_code == 503
    assert failed.json() == {
        "detail": {
            "code": "qoyod_credentials_storage_failed",
            "message": "تعذر حفظ اعتماد قيود بأمان",
        },
    }
    assert failed.headers["cache-control"] == "no-store"
    assert "synthetic storage failure" not in failed.text
    assert db.qoyod_credentials.rows == before
    assert b"replacement-key" not in repr(db.qoyod_credentials.rows).encode()


@pytest.mark.asyncio
async def test_credential_validation_encryption_and_missing_errors_are_safe_and_not_cached(
    monkeypatch,
    isolated_crypto,
):
    from integrations.qoyod import credentials

    db = _DB()
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        missing = await client.post("/api/integrations/qoyod/test-connection")
        invalid = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "   "},
        )
        monkeypatch.setattr(
            credentials,
            "encrypt_secret",
            lambda _value: (_ for _ in ()).throw(
                RuntimeError("synthetic encryption failure")
            ),
        )
        encryption_failed = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "never-persisted"},
        )

    assert missing.status_code == 400
    assert missing.headers["cache-control"] == "no-store"
    assert missing.json() == {"detail": "no_credentials"}
    assert invalid.status_code == 422
    assert invalid.headers["cache-control"] == "no-store"
    assert invalid.json()["detail"]["code"] == "qoyod_credentials_invalid"
    assert encryption_failed.status_code == 500
    assert encryption_failed.headers["cache-control"] == "no-store"
    assert encryption_failed.json()["detail"]["code"] == (
        "qoyod_credentials_encryption_failed"
    )
    combined = missing.text + invalid.text + encryption_failed.text
    assert "never-persisted" not in combined
    assert "synthetic encryption failure" not in combined


@pytest.mark.asyncio
async def test_test_connection_classifies_decryption_failure_without_calling_qoyod(
    monkeypatch,
):
    from integrations.qoyod import credentials, routes

    db = _DB()
    db.qoyod_credentials.rows.append({
        "user_id": "main",
        "api_key_enc": b"not-decryptable",
        "fingerprint": "safe…hash",
    })
    monkeypatch.setattr(
        credentials,
        "decrypt_secret",
        lambda _value: (_ for _ in ()).throw(ValueError("synthetic bad cipher")),
    )

    def _must_not_build(_key):
        raise AssertionError("Qoyod client must not be built after decrypt failure")

    monkeypatch.setattr(routes, "QoyodAPIClient", _must_not_build)
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/integrations/qoyod/test-connection",
        )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["fingerprint"] == "safe…hash"
    assert body["error"] == {
        "code": "qoyod_credentials_decryption_failed",
        "message": "تعذر قراءة اعتماد قيود المحفوظ بأمان",
    }
    assert response.headers["cache-control"] == "no-store"
    assert "synthetic bad cipher" not in response.text


@pytest.mark.asyncio
async def test_read_and_unconfirmed_write_failures_are_safe_and_not_success(
    monkeypatch,
    isolated_crypto,
):
    from integrations.qoyod import routes

    db = _DB()
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        db.qoyod_credentials.return_unconfirmed_update = True
        unconfirmed = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "synthetic-unconfirmed-key"},
        )
        db.qoyod_credentials.return_unconfirmed_update = False
        db.qoyod_credentials.fail_reads = True

        class _MustNotRun:
            def __init__(self, _key):
                raise AssertionError("provider client must not run after read failure")

        monkeypatch.setattr(routes, "QoyodAPIClient", _MustNotRun)
        read_failed = await client.post(
            "/api/integrations/qoyod/test-connection",
        )

    assert unconfirmed.status_code == 503
    assert unconfirmed.headers["cache-control"] == "no-store"
    assert unconfirmed.json()["detail"]["code"] == (
        "qoyod_credentials_storage_failed"
    )
    assert read_failed.status_code == 200
    assert read_failed.headers["cache-control"] == "no-store"
    assert read_failed.json() == {
        "ok": False,
        "fingerprint": None,
        "qoyod_user": None,
        "error": {
            "code": "qoyod_credentials_read_failed",
            "message": "تعذر قراءة اعتماد قيود المحفوظ بأمان",
        },
    }
    combined = unconfirmed.text + read_failed.text
    assert "synthetic-unconfirmed-key" not in combined
    assert "synthetic credential read failure" not in combined


@pytest.mark.asyncio
async def test_framework_input_shapes_do_not_echo_credentials_in_422(
    isolated_crypto,
):
    db = _DB()
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        responses = [
            await client.post(
                "/api/integrations/qoyod/credentials",
                json={"api_key": ["secret-fragment-never-return"]},
            ),
            await client.post(
                "/api/integrations/qoyod/credentials",
                json={"wrong_field": "secret-fragment-never-return"},
            ),
        ]

    for response in responses:
        assert response.status_code == 422
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["detail"]["code"] == "qoyod_credentials_invalid"
        assert "secret-fragment-never-return" not in response.text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    (
        "status_code",
        "provider_code",
        "provider_message",
        "expected_code",
        "expected_message",
    ),
    [
        (
            401,
            "qoyod_unauthorized",
            "مفتاح API غير صالح أو منتهي",
            "qoyod_unauthorized",
            "مفتاح API غير صالح أو منتهي",
        ),
        (
            0,
            "qoyod_network_error",
            "تعذر الاتصال بقيود",
            "qoyod_network_error",
            "تعذر الاتصال بقيود",
        ),
        (
            422,
            "provider-secret-fragment",
            "raw provider secret-fragment",
            "qoyod_connection_failed",
            "تعذر التحقق من اتصال قيود",
        ),
    ],
)
async def test_connection_keeps_provider_rejection_separate_from_network_failure(
    monkeypatch,
    isolated_crypto,
    status_code,
    provider_code,
    provider_message,
    expected_code,
    expected_message,
):
    from integrations.qoyod import routes
    from integrations.qoyod.api_client import QoyodAPIError

    db = _DB()
    app = _build_app(db)

    class _FailingClient:
        def __init__(self, _key):
            pass

        async def me(self):
            raise QoyodAPIError(
                status_code=status_code,
                code=provider_code,
                message=provider_message,
                response_excerpt="raw provider secret-fragment",
                endpoint="GET /products",
            )

    monkeypatch.setattr(routes, "QoyodAPIClient", _FailingClient)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        saved = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "synthetic-key"},
        )
        response = await client.post(
            "/api/integrations/qoyod/test-connection",
        )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["ok"] is False
    assert response.json()["fingerprint"] == saved.json()["fingerprint"]
    assert response.json()["error"]["code"] == expected_code
    assert response.json()["error"]["status_code"] == status_code
    assert response.json()["error"]["message"] == expected_message
    assert "qoyod_response_excerpt" not in response.json()["error"]
    assert "request_body_json" not in response.json()["error"]
    assert "secret-fragment" not in response.text


@pytest.mark.asyncio
async def test_provider_success_is_not_reported_when_verification_write_fails(
    monkeypatch,
    isolated_crypto,
):
    from integrations.qoyod import routes

    db = _DB()

    class _Client:
        def __init__(self, _key):
            pass

        async def me(self):
            return {"id": "synthetic-user"}

    monkeypatch.setattr(routes, "QoyodAPIClient", _Client)
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        saved = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "synthetic-key"},
        )
        db.qoyod_credentials.fail_updates = True
        response = await client.post(
            "/api/integrations/qoyod/test-connection",
        )

    assert saved.json()["ok"] is True
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == (
        "qoyod_credentials_verification_persist_failed"
    )
    assert "last_verified_at" not in db.qoyod_credentials.rows[0]


@pytest.mark.asyncio
async def test_late_old_connection_result_cannot_verify_the_rotated_record(
    monkeypatch,
    isolated_crypto,
):
    from integrations.qoyod import routes

    db = _DB()
    app = _build_app(db)
    request_started = asyncio.Event()
    release_request = asyncio.Event()
    used_keys = []

    class _DelayedClient:
        def __init__(self, key):
            used_keys.append(key)

        async def me(self):
            request_started.set()
            await release_request.wait()
            return {"id": "synthetic-user"}

    monkeypatch.setattr(routes, "QoyodAPIClient", _DelayedClient)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        saved = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "old-key"},
        )
        old_fingerprint = saved.json()["fingerprint"]
        pending_connection = asyncio.create_task(
            client.post("/api/integrations/qoyod/test-connection")
        )
        await request_started.wait()
        rotated = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "new-key"},
        )
        release_request.set()
        connection = await pending_connection

    assert used_keys == ["old-key"]
    assert connection.status_code == 200
    assert connection.json()["ok"] is False
    assert connection.json()["error"]["code"] == (
        "qoyod_credentials_changed_during_test"
    )
    assert connection.json()["fingerprint"] == old_fingerprint
    assert rotated.json()["fingerprint"] == _expected_fingerprint("new-key")
    assert db.qoyod_credentials.rows[0]["fingerprint"] == rotated.json()["fingerprint"]
    assert "last_verified_at" not in db.qoyod_credentials.rows[0]


@pytest.mark.asyncio
async def test_a_b_a_rotation_rejects_the_old_a_result(
    monkeypatch,
    isolated_crypto,
):
    from integrations.qoyod import routes

    db = _DB()
    app = _build_app(db)
    request_started = asyncio.Event()
    release_request = asyncio.Event()

    class _DelayedClient:
        def __init__(self, _key):
            pass

        async def me(self):
            request_started.set()
            await release_request.wait()
            return {"id": "synthetic-user"}

    monkeypatch.setattr(routes, "QoyodAPIClient", _DelayedClient)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        first_a = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "key-a"},
        )
        pending = asyncio.create_task(
            client.post("/api/integrations/qoyod/test-connection")
        )
        await request_started.wait()
        await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "key-b"},
        )
        latest_a = await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "key-a"},
        )
        release_request.set()
        connection = await pending

    assert first_a.json()["fingerprint"] == latest_a.json()["fingerprint"]
    assert connection.json()["ok"] is False
    assert connection.json()["error"]["code"] == (
        "qoyod_credentials_changed_during_test"
    )
    assert "last_verified_at" not in db.qoyod_credentials.rows[0]


@pytest.mark.asyncio
async def test_delete_during_connection_does_not_recreate_or_verify_credentials(
    monkeypatch,
    isolated_crypto,
):
    from integrations.qoyod import routes

    db = _DB()
    app = _build_app(db)
    request_started = asyncio.Event()
    release_request = asyncio.Event()

    class _DelayedClient:
        def __init__(self, _key):
            pass

        async def me(self):
            request_started.set()
            await release_request.wait()
            return {"id": "synthetic-user"}

    monkeypatch.setattr(routes, "QoyodAPIClient", _DelayedClient)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "key-a"},
        )
        pending = asyncio.create_task(
            client.post("/api/integrations/qoyod/test-connection")
        )
        await request_started.wait()
        await db.qoyod_credentials.delete_one({"user_id": "main"})
        release_request.set()
        connection = await pending

    assert connection.json()["ok"] is False
    assert connection.json()["error"]["code"] == (
        "qoyod_credentials_changed_during_test"
    )
    assert db.qoyod_credentials.rows == []


@pytest.mark.asyncio
async def test_rotation_invalidates_current_verification_but_preserves_audit_history(
    monkeypatch,
    isolated_crypto,
):
    from integrations.qoyod import routes

    db = _DB()

    class _Client:
        def __init__(self, _key):
            pass

        async def me(self):
            return {"id": "synthetic-user"}

    monkeypatch.setattr(routes, "QoyodAPIClient", _Client)
    app = _build_app(db)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "key-a"},
        )
        verified = await client.post(
            "/api/integrations/qoyod/test-connection",
        )
        before_rotation = copy.deepcopy(db.qoyod_credentials.rows[0])
        await client.post(
            "/api/integrations/qoyod/credentials",
            json={"api_key": "key-b"},
        )

    after_rotation = db.qoyod_credentials.rows[0]
    assert verified.json()["ok"] is True
    assert before_rotation["last_verified_credential_version"] == (
        before_rotation["credential_version"]
    )
    assert after_rotation["credential_version"] != before_rotation["credential_version"]
    assert after_rotation["last_verified_credential_version"] is None
    assert after_rotation["last_verified_at"] == before_rotation["last_verified_at"]
    assert db.qoyod_settings.rows[0]["enabled"] is False
    assert db.qoyod_settings.rows[0]["auto_send"] is False
