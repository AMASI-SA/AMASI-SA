"""Real isolated Mongo/ASGI proof for the read-only historical forecast endpoint."""
from copy import deepcopy
import pytest
import operational_open_cost_exposure as exposure
from operational_balance_store import read
from test_operational_balance_app_permissions import grant, client, READ, WRITE
from test_operational_balance_integration import run, started, NOW


@pytest.fixture
def fixed_projection_time(monkeypatch):
    original = exposure.project
    async def fixed(db, owner, state, **kwargs):
        return await original(db, owner, state, as_of=NOW)
    monkeypatch.setattr(exposure, 'project', fixed)


@pytest.mark.parametrize('permissions', [[], [WRITE], [READ], [WRITE, READ]])
def test_cost_exposure_http_requires_report_permission_without_financial_writes(permissions, fixed_projection_time):
    async def scenario(db):
        await started(db)
        await grant(db, permissions)
        before = deepcopy(await read(db, 'owner'))
        async with client(db) as http:
            result = await http.get('/api/operational-balances/cost-exposure')
            assert result.status_code == (200 if READ in permissions else 403), result.text
            if READ in permissions:
                assert result.json()['status'] == 'preview'
        assert await read(db, 'owner') == before
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


def test_cost_exposure_http_old_orders_are_owner_scoped_and_do_not_alter_openings(fixed_projection_time):
    async def scenario(db):
        await started(db)
        await grant(db, [READ])
        for owner, identity, price in [('owner', 'ours', '40.00'), ('other', 'private-other', '999.00')]:
            await db.unified_orders.insert_one({'user_id': owner, 'raw_by_source': {'salla_direct': {
                'id': identity, 'reference_id': identity, 'created_at': '2026-10-01T10:00:00+00:00',
                'status': 'under_review', 'currency': 'SAR', 'items': [{'id': 'line', 'product_id': identity, 'quantity': 1}]
            }}})
            await db.mezan_product_cost_profiles_v2.insert_one({'user_id': owner, 'salla_product_id': identity, 'base_cost': price})
        before = deepcopy(await read(db, 'owner'))
        async with client(db) as http:
            result = await http.get('/api/operational-balances/cost-exposure')
            assert result.status_code == 200, result.text
            body = result.json()
            assert body['currencies']['SAR']['prestart_supplier_unreceived'] == '40.00'
            assert '999.00' not in result.text and 'private-other' not in result.text
            assert body['groups'][0]['party_id'] == ''
        assert await read(db, 'owner') == before
        assert await db.general_ledger.count_documents({}) == 0
    run(scenario)


def test_cost_exposure_http_rejects_unauthenticated_and_disabled_actor(fixed_projection_time):
    async def scenario(db):
        await started(db)
        await grant(db, [READ])
        async with client(db, None) as http:
            assert (await http.get('/api/operational-balances/cost-exposure')).status_code == 401
        await db.users.update_one({'id': 'staff'}, {'$set': {'disabled': True}})
        async with client(db) as http:
            assert (await http.get('/api/operational-balances/cost-exposure')).status_code == 403
    run(scenario)
