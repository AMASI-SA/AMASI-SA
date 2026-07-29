from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from integrations_control_center.models import ProviderCard
from integrations_control_center import google_error_resolution
from integrations_control_center import google_merchant_registration as merchant_registration
from openai_integration_status_support import _recount, openai_integration_card


def test_connected_openai_card_is_schema_valid_and_secret_safe(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-appear")
    monkeypatch.delenv("MEZAN_AI_IMAGE_ENABLED", raising=False)
    monkeypatch.delenv("MEZAN_OPENAI_IMAGE_MODEL", raising=False)
    validated = ProviderCard.model_validate(openai_integration_card()).model_dump()
    assert validated["connection_status"] == "connected"
    assert validated["connection_provenance"] == "api_connection"
    assert validated["capabilities"]["analysis.generate"]["state"] == "available"
    assert validated["capabilities"]["images.execute"]["state"] == "planned"
    assert "must-not-appear" not in str(validated)


def test_disconnected_openai_card_is_not_counted_as_api_connection(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    validated = ProviderCard.model_validate(openai_integration_card()).model_dump()
    assert validated["connection_status"] == "not_configured"
    assert validated["connection_provenance"] == "disconnected"


def test_summary_is_recounted_after_openai_card_is_added(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "configured")
    providers = [
        {"connection_status": "not_configured", "connection_provenance": "disconnected", "health": {"status": "not_available"}, "permissions": {"missing": []}},
        openai_integration_card(),
    ]
    summary = _recount({}, providers)
    assert summary["total"] == 2
    assert summary["connected"] == 1
    assert summary["api_connections"] == 1
    assert summary["disconnected"] == 1


class _CredentialCollection:
    def __init__(self, document):
        self.document = document
        self.updates = []

    async def find_one(self, query, projection=None):
        if query.get("user_id") != self.document.get("user_id"):
            return None
        return dict(self.document)

    async def update_one(self, query, update, upsert=False):
        self.updates.append((query, update, upsert))
        return object()


class _MerchantDB:
    def __init__(self, credential):
        self.credentials = _CredentialCollection(credential)

    def __getitem__(self, name):
        if name == merchant_registration.GOOGLE_CREDENTIALS_COLLECTION:
            return self.credentials
        raise KeyError(name)


class _MerchantResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self.payload = payload

    def json(self):
        return self.payload


class _MerchantClient:
    calls = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, **kwargs):
        type(self).calls.append(("POST", url, kwargs))
        assert url == (
            "https://merchantapi.googleapis.com/accounts/v1/accounts/"
            "626368690/developerRegistration:registerGcp"
        )
        assert kwargs["json"] == {
            "developerEmail": "amasi.jewelery@gmail.com"
        }
        assert kwargs["headers"]["Authorization"] == "Bearer access-secret"
        return _MerchantResponse(
            200,
            {
                "name": "accounts/626368690/developerRegistration",
                "gcpIds": ["646727261677"],
            },
        )

    async def get(self, url, **kwargs):
        type(self).calls.append(("GET", url, kwargs))
        assert url == merchant_registration.MERCHANT_ACCOUNTS_URL
        assert kwargs["headers"]["Authorization"] == "Bearer access-secret"
        return _MerchantResponse(
            200,
            {
                "accounts": [
                    {
                        "name": "accounts/626368690",
                        "accountName": "متجر أماسي",
                        "timeZone": {"id": "Asia/Riyadh"},
                    }
                ]
            },
        )


@pytest.mark.asyncio
async def test_merchant_registration_is_exact_owner_scoped_and_secret_safe(
    monkeypatch,
):
    monkeypatch.delenv("GOOGLE_MERCHANT_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_MERCHANT_DEVELOPER_EMAIL", raising=False)
    monkeypatch.setattr(
        merchant_registration,
        "decrypt_google_token",
        lambda value: "access-secret",
    )
    _MerchantClient.calls = []
    monkeypatch.setattr(
        merchant_registration.httpx,
        "AsyncClient",
        _MerchantClient,
    )
    persisted = {}

    async def _capture_persist(db, **kwargs):
        persisted.update(kwargs)
        return "merchant-run-1"

    monkeypatch.setattr(
        merchant_registration,
        "_persist_merchant_result",
        _capture_persist,
    )
    db = _MerchantDB(
        {
            "user_id": "owner-1",
            "access_token_ciphertext": b"encrypted-access",
            "refresh_token_ciphertext": b"encrypted-refresh",
            "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "scope": [
                "openid",
                "https://www.googleapis.com/auth/content",
            ],
            "google_subject": "google-subject-1",
            "google_email": "amasi.jewelery@gmail.com",
            "token_type": "Bearer",
        }
    )

    result = await merchant_registration.register_google_merchant_developer(
        db, "owner-1"
    )

    assert result == {
        "provider": "google_merchant_center",
        "status": "complete",
        "run_id": "merchant-run-1",
        "merchant_account_id": "626368690",
        "developer_email_verified": True,
        "registration_state": "registered",
        "registration_name": "accounts/626368690/developerRegistration",
        "account_count": 1,
        "target_account_found": True,
        "retry_after_seconds": 0,
        "source_only": True,
        "accounting_write_reached": False,
        "qoyod_write_reached": False,
        "campaign_write_reached": False,
    }
    assert persisted["user_id"] == "owner-1"
    assert persisted["registration_state"] == "registered"
    assert persisted["provider_error"] is None
    assert persisted["accounts"][0]["external_account_id"] == "626368690"
    assert "access-secret" not in str(result)
    assert "encrypted-access" not in str(result)
    assert [call[0] for call in _MerchantClient.calls] == ["POST", "GET"]


@pytest.mark.asyncio
async def test_merchant_registration_rejects_wrong_google_identity_before_network(
    monkeypatch,
):
    monkeypatch.delenv("GOOGLE_MERCHANT_DEVELOPER_EMAIL", raising=False)

    class _NetworkMustNotStart:
        def __init__(self, *args, **kwargs):
            raise AssertionError("provider network started for wrong Google user")

    monkeypatch.setattr(
        merchant_registration.httpx,
        "AsyncClient",
        _NetworkMustNotStart,
    )
    db = _MerchantDB(
        {
            "user_id": "owner-1",
            "access_token_ciphertext": b"encrypted-access",
            "expires_at": datetime(2099, 1, 1, tzinfo=timezone.utc),
            "scope": ["https://www.googleapis.com/auth/content"],
            "google_email": "other-account@gmail.com",
        }
    )

    with pytest.raises(HTTPException) as exc:
        await merchant_registration.register_google_merchant_developer(
            db, "owner-1"
        )

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == (
        "google_merchant_developer_email_mismatch"
    )


def test_google_discovery_error_is_resolved_by_newer_successful_sync():
    snapshot = {
        "provider": "google_ads",
        "has_data": True,
        "last_sync_at": "2026-07-29T16:35:00+00:00",
        "latest_error": {
            "code": "google_discovery_developer_token_missing",
            "occurred_at": "2026-07-29T14:03:06+00:00",
        },
    }

    assert google_error_resolution.google_discovery_error_is_resolved(snapshot)


def test_google_discovery_error_is_not_hidden_without_newer_success():
    base = {
        "provider": "google_ads",
        "has_data": True,
        "last_sync_at": "2026-07-29T14:00:00+00:00",
        "latest_error": {
            "code": "google_discovery_http_401",
            "occurred_at": "2026-07-29T14:03:06+00:00",
        },
    }
    assert not google_error_resolution.google_discovery_error_is_resolved(base)

    base["has_data"] = False
    base["last_sync_at"] = "2026-07-29T15:00:00+00:00"
    assert not google_error_resolution.google_discovery_error_is_resolved(base)

    base["provider"] = "meta_ads"
    base["has_data"] = True
    assert not google_error_resolution.google_discovery_error_is_resolved(base)
