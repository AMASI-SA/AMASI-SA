"""Hermetic safety contract for Customer Intelligence Phase 1."""
from __future__ import annotations

import asyncio
import json
import socket
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from customer_intelligence.models import CustomerIntelligenceWorkspaceResponse
from customer_intelligence.routes import make_customer_intelligence_router
from customer_intelligence.service import CustomerIntelligencePreviewService


NOW = datetime(2026, 7, 28, 18, 0, tzinfo=timezone.utc)
OWNER = {"id": "owner-preview", "role": "owner"}


@pytest.fixture(autouse=True)
def enable_preview(monkeypatch):
    monkeypatch.setenv(
        "MEZAN_CUSTOMER_INTELLIGENCE_PHASE1_ENABLED",
        "true",
    )


def _service() -> CustomerIntelligencePreviewService:
    return CustomerIntelligencePreviewService(now=lambda: NOW)


def _app(user: dict, *, service=None) -> FastAPI:
    async def current_user() -> dict:
        return deepcopy(user)

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(
            current_user,
            service=service or _service(),
        ),
        prefix="/api",
    )
    return app


def test_router_exposes_exactly_one_owner_get_workspace_operation():
    async def current_user():
        return deepcopy(OWNER)

    router = make_customer_intelligence_router(
        current_user,
        service=_service(),
    )
    routes = [route for route in router.routes if isinstance(route, APIRoute)]

    assert len(routes) == 1
    assert routes[0].path == "/customer-intelligence/v1/workspace"
    assert routes[0].methods == {"GET"}


@pytest.mark.asyncio
async def test_workspace_is_owner_only_before_service_execution():
    class SpyService:
        calls = 0

        def workspace(self):
            self.calls += 1
            raise AssertionError("unauthorized request reached preview service")

    service = SpyService()
    app = _app({"id": "employee-preview", "role": "employee"}, service=service)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/customer-intelligence/v1/workspace"
        )

    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "owner_only"
    assert service.calls == 0


@pytest.mark.asyncio
async def test_owner_without_identity_is_rejected_before_service_execution():
    class SpyService:
        calls = 0

        def workspace(self):
            self.calls += 1
            raise AssertionError("owner without id reached preview service")

    service = SpyService()
    app = _app({"role": "owner"}, service=service)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/customer-intelligence/v1/workspace"
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "authenticated_owner_missing_id"
    assert service.calls == 0


@pytest.mark.asyncio
async def test_feature_kill_switch_returns_404_before_service_execution(
    monkeypatch,
):
    class SpyService:
        calls = 0

        def workspace(self):
            self.calls += 1
            raise AssertionError("disabled feature reached preview service")

    monkeypatch.setenv(
        "MEZAN_CUSTOMER_INTELLIGENCE_PHASE1_ENABLED",
        "false",
    )
    service = SpyService()
    app = _app(OWNER, service=service)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/customer-intelligence/v1/workspace"
        )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "feature_disabled"
    assert service.calls == 0


@pytest.mark.asyncio
async def test_real_preview_service_is_hermetic_and_all_execution_is_blocked(
    monkeypatch,
):
    def blocked_connection(*args, **kwargs):
        del args, kwargs
        raise AssertionError("network socket attempted during preview GET")

    async def blocked_async_connection(*args, **kwargs):
        del args, kwargs
        raise AssertionError("async network attempted during preview GET")

    monkeypatch.setattr(socket, "create_connection", blocked_connection)
    monkeypatch.setattr(asyncio, "open_connection", blocked_async_connection)

    app = _app(OWNER)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/customer-intelligence/v1/workspace"
        )

    assert response.status_code == 200, response.text
    payload = response.json()
    validated = CustomerIntelligenceWorkspaceResponse.model_validate(payload)

    assert validated.mode == "preview_fixture"
    assert validated.data_origin == "synthetic"
    assert validated.generated_at == NOW
    assert validated.safety_policy.mode == "observe_only"
    assert validated.safety_policy.preview_only is True
    assert validated.safety_policy.fixtures_are_synthetic is True

    policy = payload["safety_policy"]
    blocked_flags = {
        "writes_allowed",
        "external_calls_allowed",
        "whatsapp_send_allowed",
        "order_creation_allowed",
        "discount_creation_allowed",
        "payment_link_creation_allowed",
        "product_mutation_allowed",
        "campaign_mutation_allowed",
        "ai_execution_allowed",
    }
    assert all(policy[key] is False for key in blocked_flags)
    assert all(
        follow_up["execution_allowed"] is False
        for follow_up in payload["follow_ups"]
    )
    assert all(
        offer["application_allowed"] is False
        for offer in payload["approved_offers"]
    )
    assert payload["conversation_cart"]["create_order_allowed"] is False
    assert payload["conversation_cart"]["payment_link"]["is_real"] is False
    assert (
        payload["conversation_cart"]["payment_link"]["creation_allowed"]
        is False
    )
    assert all(
        integration["writes_allowed"] is False
        for integration in payload["workspace"]["integrations"]
    )


def test_literal_safety_contract_rejects_enabling_a_mutation():
    payload = _service().workspace()
    payload["safety_policy"]["whatsapp_send_allowed"] = True

    with pytest.raises(ValidationError):
        CustomerIntelligenceWorkspaceResponse.model_validate(payload)


@pytest.mark.asyncio
async def test_no_write_or_execution_endpoints_exist():
    app = _app(OWNER)
    blocked_paths = [
        "/api/customer-intelligence/v1/whatsapp/send",
        "/api/customer-intelligence/v1/orders",
        "/api/customer-intelligence/v1/discounts",
        "/api/customer-intelligence/v1/payment-links",
        "/api/customer-intelligence/v1/campaigns",
    ]
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        for path in blocked_paths:
            response = await client.post(path, json={"execute": True})
            assert response.status_code in {404, 405}, path


@pytest.mark.asyncio
async def test_preview_does_not_leak_credentials_or_real_payment_data():
    app = _app(OWNER)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.get(
            "/api/customer-intelligence/v1/workspace"
        )

    assert response.status_code == 200
    payload = response.json()
    rendered = json.dumps(payload, ensure_ascii=False).lower()

    forbidden_keys = {
        "access_token",
        "refresh_token",
        "authorization",
        "api_key",
        "client_secret",
        "card_number",
        "card_last_four",
        "cvv",
        "iban",
        "payment_token",
    }

    def assert_safe(value):
        if isinstance(value, dict):
            assert forbidden_keys.isdisjoint(value)
            for child in value.values():
                assert_safe(child)
        elif isinstance(value, list):
            for child in value:
                assert_safe(child)

    assert_safe(payload)
    assert "bearer " not in rendered
    assert "sk-proj-" not in rendered
    assert "sk-" not in rendered
    assert "@" not in rendered

    preview_url = payload["conversation_cart"]["payment_link"]["url"]
    parsed = urlsplit(preview_url)
    assert parsed.scheme == "https"
    assert parsed.hostname is not None
    assert parsed.hostname.endswith(".invalid")
    assert parsed.query == ""
    assert parsed.fragment == ""
    assert payload["conversation_cart"]["payment_link"]["is_real"] is False
