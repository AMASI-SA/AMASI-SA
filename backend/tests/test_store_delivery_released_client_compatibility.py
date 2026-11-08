"""Payloads emitted by unchanged signed-code18/current-code19 native API modules.

See FINANCIAL-ADMIN-INTEGRATION-20261108/COURIER-AND-REFUND-VERIFICATION.md.
The fixture contains synthetic values, not customer or authentication data.
"""
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from mongomock_motor import AsyncMongoMockClient

from store_delivery_driver_app_routes import DriverStatusUpdate, make_store_delivery_driver_app_router
from store_delivery_payment_evidence_routes import make_store_delivery_payment_evidence_router

PAYLOADS = json.loads(Path(__file__).with_name('courier_release_status_payloads.json').read_text())


@pytest.mark.parametrize('version', ['18', '19'])
@pytest.mark.parametrize('index', [0, 1, 2])
def test_released_driver_payload_is_accepted_without_new_required_fields(version, index):
    call = PAYLOADS[version][index]
    parsed = DriverStatusUpdate.model_validate(call['body'])
    assert parsed.model_dump(exclude_unset=True) == call['body']
    assert parsed.target_status == 'delivered'
    assert parsed.barcode == 'BAR-123'
    assert call['path'] == '/store-delivery/app/deliveries/status' and call['method'] == 'POST'


def test_unchanged_courier_routes_and_upload_field_names_remain_available():
    db = AsyncMongoMockClient()['synthetic_route_contract']
    app = FastAPI()
    app.include_router(make_store_delivery_driver_app_router(db, lambda: {}))
    app.include_router(make_store_delivery_payment_evidence_router(db, lambda: {}))
    schema = app.openapi()
    for path in ('/store-delivery/app/me', '/store-delivery/app/deliveries/home',
                 '/store-delivery/app/accounts/summary', '/store-delivery/app/payment-reviews',
                 '/store-delivery/app/bank-accounts', '/store-delivery/app/settlements/pending',
                 '/store-delivery/app/deliveries/receive-sessions/me'):
        assert 'get' in schema['paths'][path]
    for path in ('/store-delivery/evidence/receipt', '/store-delivery/evidence/delivery-proof',
                 '/store-delivery/evidence/customer-conversation'):
        body = schema['paths'][path]['post']['requestBody']['content']['multipart/form-data']['schema']
        model = schema['components']['schemas'][body['$ref'].rsplit('/', 1)[-1]]
        assert {'assignment_id', 'file'} <= set(model['properties'])
