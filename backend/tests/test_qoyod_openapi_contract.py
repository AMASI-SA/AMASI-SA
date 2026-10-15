"""OpenAPI and request-contract coverage for composed Qoyod routes."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


_DISMISS_PATH = (
    "/api/integrations/qoyod/admin/"
    "unallocated-receipts/{receipt_id}/dismiss"
)
_PAYMENT_PROBE_PATH = (
    "/api/integrations/qoyod/admin/payment-method-field-probe"
)


class _NoAccessDB:
    def __init__(self) -> None:
        self.accesses: list[str] = []

    def __getattr__(self, name: str):
        self.accesses.append(name)
        raise AssertionError(f"OpenAPI must not access database collection {name}")


class _User:
    id = "main"
    email = "operator@example.test"


def _build_app(monkeypatch):
    from integrations.qoyod import payment_method_field_probe as probe_module
    from integrations.qoyod import routes as qroutes
    from integrations.qoyod import unallocated_receipts_report as report

    calls = {"auth": 0, "dismiss": [], "probe": [], "provider": 0}
    db = _NoAccessDB()

    async def current_user():
        calls["auth"] += 1
        return _User()

    async def dismiss_receipt(db_arg, **kwargs):
        assert db_arg is db
        calls["dismiss"].append(kwargs)
        return {"receipt_id": kwargs["qoyod_receipt_id"]}

    async def probe_payment_method_field(db_arg, **kwargs):
        assert db_arg is db
        calls["probe"].append(kwargs)
        return {"ok": True, "probe": kwargs}

    class RefuseProviderClient:
        def __init__(self, *_args, **_kwargs):
            calls["provider"] += 1
            raise AssertionError("OpenAPI must not construct a Qoyod client")

    monkeypatch.setattr(report, "dismiss_receipt", dismiss_receipt)
    monkeypatch.setattr(
        probe_module,
        "probe_payment_method_field",
        probe_payment_method_field,
    )
    monkeypatch.setattr(qroutes, "QoyodAPIClient", RefuseProviderClient)

    app = FastAPI()
    app.include_router(qroutes.make_qoyod_router(db, current_user), prefix="/api")
    return app, db, calls


def _model_schema_for_request(schema: dict, path: str) -> tuple[dict, dict]:
    operation = schema["paths"][path]["post"]
    request_body = operation["requestBody"]
    body_schema = request_body["content"]["application/json"]["schema"]
    model_name = body_schema["$ref"].rsplit("/", 1)[-1]
    return request_body, schema["components"]["schemas"][model_name]


def _assert_qoyod_openapi_contract(schema: dict) -> None:
    dismiss_body, dismiss_schema = _model_schema_for_request(
        schema, _DISMISS_PATH)
    assert dismiss_body.get("required", False) is False
    assert dismiss_schema["additionalProperties"] is False
    note_schema = dismiss_schema["properties"]["note"]
    assert any(
        option.get("type") == "string" and option.get("maxLength") == 500
        for option in note_schema["anyOf"]
    )

    probe_body, probe_schema = _model_schema_for_request(
        schema, _PAYMENT_PROBE_PATH)
    assert probe_body["required"] is True
    assert probe_schema["additionalProperties"] is False
    assert set(probe_schema["required"]) == {
        "empty_payment_method_invoice_id",
        "reference_invoice_id_with_payment",
    }
    for field_name in probe_schema["required"]:
        field_schema = probe_schema["properties"][field_name]
        assert field_schema["type"] == "string"
        assert field_schema["minLength"] == 1
        assert field_schema["maxLength"] == 64


@pytest.mark.asyncio
async def test_qoyod_openapi_generation_and_http_document_are_side_effect_free(
    monkeypatch,
) -> None:
    direct_app, direct_db, direct_calls = _build_app(monkeypatch)
    direct_schema = direct_app.openapi()
    _assert_qoyod_openapi_contract(direct_schema)
    assert direct_db.accesses == []
    assert direct_calls == {
        "auth": 0,
        "dismiss": [],
        "probe": [],
        "provider": 0,
    }

    http_app, http_db, http_calls = _build_app(monkeypatch)
    assert http_app.openapi_schema is None
    async with AsyncClient(
        transport=ASGITransport(app=http_app),
        base_url="http://test",
    ) as client:
        response = await client.get("/openapi.json")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    _assert_qoyod_openapi_contract(response.json())
    assert http_db.accesses == []
    assert http_calls == {
        "auth": 0,
        "dismiss": [],
        "probe": [],
        "provider": 0,
    }


@pytest.mark.asyncio
async def test_qoyod_dismiss_body_remains_optional_and_forbids_invalid_fields(
    monkeypatch,
) -> None:
    app, db, calls = _build_app(monkeypatch)
    path = _DISMISS_PATH.replace("{receipt_id}", "receipt-1")

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        without_body = await client.post(path)
        with_note = await client.post(path, json={"note": "reviewed locally"})
        unknown_field = await client.post(path, json={"unexpected": True})
        oversized_note = await client.post(path, json={"note": "x" * 501})

    assert without_body.status_code == 200
    assert with_note.status_code == 200
    assert unknown_field.status_code == 422
    assert oversized_note.status_code == 422
    assert db.accesses == []
    assert calls["provider"] == 0
    assert calls["auth"] == 4
    assert calls["dismiss"] == [
        {
            "user_id": "main",
            "qoyod_receipt_id": "receipt-1",
            "actor": "operator@example.test",
            "note": None,
        },
        {
            "user_id": "main",
            "qoyod_receipt_id": "receipt-1",
            "actor": "operator@example.test",
            "note": "reviewed locally",
        },
    ]
    assert calls["probe"] == []


@pytest.mark.asyncio
async def test_qoyod_payment_probe_request_contract_forwards_fields_and_rejects_invalid_bodies(
    monkeypatch,
) -> None:
    app, db, calls = _build_app(monkeypatch)
    valid_body = {
        "empty_payment_method_invoice_id": "invoice-empty-1",
        "reference_invoice_id_with_payment": "invoice-paid-2",
    }
    invalid_bodies = [
        None,
        {"reference_invoice_id_with_payment": "invoice-paid-2"},
        {"empty_payment_method_invoice_id": "invoice-empty-1"},
        {**valid_body, "empty_payment_method_invoice_id": ""},
        {**valid_body, "reference_invoice_id_with_payment": ""},
        {**valid_body, "empty_payment_method_invoice_id": "x" * 65},
        {**valid_body, "reference_invoice_id_with_payment": "x" * 65},
        {**valid_body, "unexpected": True},
    ]

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        valid = await client.post(_PAYMENT_PROBE_PATH, json=valid_body)
        invalid_responses = []
        for invalid_body in invalid_bodies:
            if invalid_body is None:
                response = await client.post(_PAYMENT_PROBE_PATH)
            else:
                response = await client.post(
                    _PAYMENT_PROBE_PATH, json=invalid_body)
            invalid_responses.append(response)

    assert valid.status_code == 200
    assert all(response.status_code == 422 for response in invalid_responses)
    assert db.accesses == []
    assert calls["provider"] == 0
    assert calls["dismiss"] == []
    assert calls["probe"] == [
        {
            "user_id": "main",
            "empty_payment_method_invoice_id": "invoice-empty-1",
            "reference_invoice_id_with_payment": "invoice-paid-2",
        }
    ]
