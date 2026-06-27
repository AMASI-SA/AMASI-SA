"""Regression — Qoyod /customers POST body MUST use `contact` wrapper.

User scenario (2026-02-27, Order 268653181 production):
    The First Sync Monitor showed the resolver building
    `{"customer": {"name": "...", "contact_name": "...", ...}}`,
    yet Qoyod responded 422 `contact_name: Can't be blank`. Root
    cause: Rails-side strong_params expects `params.require(:contact)`
    not `:customer` — the legacy URL is `/customers` but the entity
    is `contact` (see `create_contact` method name + the
    `_extract_contact_id` response parser which already prefers the
    `contact` shape).

This test asserts that:
    1. The resolver builds `{"contact": {...}}` (NOT `{"customer": {...}}`).
    2. Both `name` AND `contact_name` are inside the `contact` wrapper.
    3. The api_client passes the body verbatim — no wrapping in transit.
    4. On failure, QoyodAPIError carries `request_body_json` so the
       operator can see the EXACT bytes sent (no more guessing).
"""
from __future__ import annotations

import respx
import httpx
import pytest

from integrations.qoyod.dto import CustomerDTO
from integrations.qoyod.customer_resolver import _build_contact_payload
from integrations.qoyod.api_client import QoyodAPIClient, QoyodAPIError


def test_resolver_wraps_under_contact_not_customer():
    body = _build_contact_payload(CustomerDTO(
        name="عبير ..",
        phone="+966503183617",
        email="ia7medxftw@gmail.com",
    ))
    assert "contact" in body
    assert "customer" not in body, \
        "Legacy `customer` wrapper triggered 422 — must NOT be used"
    inner = body["contact"]
    assert inner["name"] == "عبير .."
    assert inner["contact_name"] == "عبير .."
    assert inner["phone_number"] == "+966503183617"
    assert inner["email"] == "ia7medxftw@gmail.com"


@respx.mock
@pytest.mark.asyncio
async def test_api_client_sends_contact_wrapper_verbatim_no_double_wrap():
    """Capture the EXACT JSON httpx serializes — must equal the
    resolver payload, no double-wrapping, no rewriting."""
    payload = _build_contact_payload(CustomerDTO(
        name="عبير ..", phone="+966503183617",
        email="ia7medxftw@gmail.com"))

    captured: list[dict] = []

    def _capture(request):
        import json
        captured.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={"contact": {"id": 12345}})

    respx.post("https://www.qoyod.com/api/2.0/customers").mock(side_effect=_capture)

    client = QoyodAPIClient(api_key="TEST", base_url="https://www.qoyod.com/api/2.0")
    resp = await client.create_contact(payload, idem="mzn-test")
    assert resp == {"contact": {"id": 12345}}

    # The bytes Qoyod received must be IDENTICAL to what the resolver built.
    assert len(captured) == 1
    sent = captured[0]
    assert sent == payload, (
        f"Double-wrap detected.\nSent: {sent}\nExpected: {payload}")
    # Reinforce: top-level key is `contact` not `customer`.
    assert "contact" in sent and "customer" not in sent
    assert sent["contact"]["contact_name"] == "عبير .."


@respx.mock
@pytest.mark.asyncio
async def test_qoyod_api_error_carries_actual_request_body():
    """When Qoyod 422s, the operator must be able to read the EXACT
    JSON body we sent — straight from the QoyodAPIError. This is what
    gets persisted into `integration_inbox.customer_resolution`."""
    payload = _build_contact_payload(CustomerDTO(name="عبير .."))

    respx.post("https://www.qoyod.com/api/2.0/customers").mock(
        return_value=httpx.Response(
            422, json={"errors": {"contact_name": ["Can't be blank"]}}))

    client = QoyodAPIClient(api_key="TEST", base_url="https://www.qoyod.com/api/2.0")
    with pytest.raises(QoyodAPIError) as excinfo:
        await client.create_contact(payload, idem="mzn-fail")
    err = excinfo.value
    assert err.status_code == 422
    assert err.code == "qoyod_validation_error"
    # CRITICAL: request_body_json must be the actual payload we sent,
    # not None and not a transformation.
    assert err.request_body_json == payload
    assert "contact" in err.request_body_json
    assert err.request_body_json["contact"]["contact_name"] == "عبير .."
    # And it's serialisable into to_log_dict() for DB persistence.
    log = err.to_log_dict()
    assert log["request_body_json"] == payload
