"""Owner option-role settings use isolated Mongo and HTTP, never a live app."""
import asyncio
from copy import deepcopy

import httpx
import pytest
from fastapi import FastAPI, Header, HTTPException

from operational_balance_routes import make_operational_balance_router
from operational_balance_store import STATES
from test_operational_balance_integration import run


FIELD = 'mezan_inventory_option_policy'
URL = '/operational-balances/inventory-products/product/option-roles'
ROLES = {
    'size': 'physical', 'embroider': 'customization', 'name': 'customization',
    'note': 'informational', 'gift': 'informational', 'gift_text': 'informational',
    'wrap': 'informational',
}


def product_source():
    fields = [
        {'id': 'size', 'name': 'المقاس', 'type': 'radio', 'required': True,
         'values': [{'id': 'two', 'name': 'سنتان'}]},
        {'id': 'embroider', 'name': 'تطريز الاسم', 'type': 'radio', 'required': True,
         'inventory_role': 'customization',
         'values': [{'id': 'yes', 'name': 'نعم'}, {'id': 'no', 'name': 'لا'}]},
        {'id': 'name', 'name': 'الاسم', 'type': 'text', 'required': True,
         'inventory_role': 'customization',
         'show_if': [{'option_id': 'embroider', 'value_id': 'yes'}]},
        {'id': 'note', 'name': 'ملاحظة', 'type': 'textarea', 'required': False,
         'inventory_role': 'informational'},
        {'id': 'gift', 'name': 'هل تريد كرت إهداء', 'type': 'radio', 'required': True,
         'inventory_role': 'informational',
         'values': [{'id': 'yes', 'name': 'نعم'}, {'id': 'no', 'name': 'لا'}]},
        {'id': 'gift_text', 'name': 'نص الكرت', 'type': 'textarea', 'required': True,
         'inventory_role': 'informational',
         'show_if': [{'option_id': 'gift', 'value_id': 'yes'}]},
        {'id': 'wrap', 'name': 'التغليف', 'type': 'select', 'required': False,
         'inventory_role': 'informational',
         'values': [{'id': 'paper', 'name': 'ورق'}]},
    ]
    return {
        'id': 'local-product', 'user_id': 'owner', 'mezan_product_id': 'product',
        'salla_product_id': '101', 'name': 'منتج اختباري', 'sku': 'TEST-1',
        'options': fields, 'options_count': len(fields),
        'variants': [{'id': 'variant-two', 'sku': 'TEST-1-2', 'name': 'المقاس سنتان',
                      'selections': [{'option_id': 'size', 'value_id': 'two'}]}],
        'variants_count': 1, 'source_revision': 'provider-version-one',
        'unrelated_local_metadata': {'retain': True},
    }


async def seed(db, row=None):
    await db.mezan_products_v2.insert_one(deepcopy(row or product_source()))


def client_for(db):
    app = FastAPI()

    async def principal(x_test_user: str = Header(default='owner'),
                        x_test_actor: str = Header(default='')):
        return {'id': x_test_user, **({'_mobile_actor_id': x_test_actor} if x_test_actor else {})}

    app.include_router(make_operational_balance_router(db, principal))
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://isolated')


def command(view, roles=None):
    return {'expected_revision': view['revision'],
            'catalog_fingerprint': view['catalog_fingerprint'],
            'roles': deepcopy(ROLES if roles is None else roles)}


def test_get_exposes_current_stable_roles_without_writing_or_loading_provider():
    async def scenario(db):
        await seed(db)
        before = await db.mezan_products_v2.find_one({})
        async with client_for(db) as client:
            response = await client.get(URL)
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['product_id'] == 'product' and result['revision'] == 0
        assert len(result['catalog_fingerprint']) == 64
        assert result['roles'] == ROLES
        options = {row['id']: row for row in result['options']}
        assert options['size']['role_locked'] is True
        assert options['size']['inventory_role'] == 'physical'
        assert options['size']['stock_identity'] is True
        assert options['size']['defer_allowed'] is False
        assert options['gift_text']['visibility_rules'] == [
            {'option_id': 'gift', 'value_id': 'yes', 'operator': '='}]
        assert result['product']['options'] == result['options']
        assert result['product']['inventory_option_policy_revision'] == 0
        assert await db.mezan_products_v2.find_one({}) == before
        assert await db[STATES].count_documents({}) == 0
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
    run(scenario)


def test_put_persists_only_local_policy_and_same_request_replays_without_revision_change():
    async def scenario(db):
        await seed(db)
        before = await db.mezan_products_v2.find_one({})
        async with client_for(db) as client:
            view = (await client.get(URL)).json()
            payload = command(view)
            first = await client.put(URL, json=payload)
            assert first.status_code == 200, first.text
            replay = await client.put(URL, json=payload)
            assert replay.status_code == 200, replay.text
            assert replay.json() == first.json()
            assert first.json()['revision'] == 1
            assert first.json()['product']['inventory_option_policy_revision'] == 1
            assert first.json()['catalog_fingerprint'] == view['catalog_fingerprint']
        after = await db.mezan_products_v2.find_one({})
        saved = after.pop(FIELD)
        assert after == before
        assert saved['roles'] == ROLES and saved['revision'] == 1
        assert saved['updated_by'] == 'owner' and isinstance(saved['updated_at'], str)
        assert await db.mezan_inventory_receipts_v2.count_documents({}) == 0
        assert await db.accounting_general_ledger_v2.count_documents({}) == 0
    run(scenario)


@pytest.mark.parametrize('revision', [None, 0, 1])
def test_new_legacy_purchase_shape_cannot_bypass_saved_policy(revision):
    from test_operational_balance_integration import started, NOW
    from operational_balance_inventory import save_purchase
    from operational_balance_store import read, digest
    async def scenario(db):
        await seed(db)
        await started(db, supplier='0')
        async with client_for(db) as client:
            definition = (await client.get(URL)).json()
            assert (await client.put(URL, json=command(definition))).status_code == 200
        before = await read(db, 'owner')
        payload = {'request_id': 'legacy-after-policy', 'expected_session_scope': digest(['owner', 'owner']),
            'supplier_id': 'supplier', 'invoice_number': 'POLICY-LEGACY', 'invoice_date': '2026-10-07',
            'lines': [{'kind': 'product', 'item_id': 'product', 'variant_id': 'variant-two',
                'quantity': 1, 'unit_price': '10', 'tax': '0',
                **({'inventory_option_policy_revision': revision} if revision is not None else {})}]}
        with pytest.raises(HTTPException) as exc:
            await save_purchase(db, 'owner', 'owner', payload, clock=NOW)
        assert exc.value.status_code == 409
        assert exc.value.detail['code'] == 'inventory_option_policy_changed'
        assert exc.value.detail['not_applied'] is True
        assert (await read(db, 'owner')).get('inventory_purchases', []) == before.get('inventory_purchases', [])
    run(scenario)


def test_completed_legacy_purchase_replays_after_later_policy_without_second_invoice():
    from test_operational_balance_integration import started, NOW
    from operational_balance_inventory import save_purchase
    from operational_balance_store import read, digest
    async def scenario(db):
        await seed(db)
        await started(db, supplier='0')
        payload = {'request_id': 'legacy-before-policy', 'expected_session_scope': digest(['owner', 'owner']),
            'supplier_id': 'supplier', 'invoice_number': 'POLICY-LEGACY-OLD', 'invoice_date': '2026-10-07',
            'lines': [{'kind': 'product', 'item_id': 'product', 'variant_id': 'variant-two',
                'quantity': 1, 'unit_price': '10', 'tax': '0'}]}
        original = await save_purchase(db, 'owner', 'owner', payload, clock=NOW)
        async with client_for(db) as client:
            definition = (await client.get(URL)).json()
            assert (await client.put(URL, json=command(definition))).status_code == 200
        assert await save_purchase(db, 'owner', 'owner', payload, clock=NOW) == original
        assert len((await read(db, 'owner'))['inventory_purchases']) == 1
    run(scenario)


@pytest.mark.parametrize('headers', [
    {'x-test-user': 'staff'}, {'x-test-user': 'viewer'},
    {'x-test-user': 'owner', 'x-test-actor': 'staff'},
])
def test_staff_and_merchant_shaped_mobile_principals_cannot_read_or_write_roles(headers):
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            view = (await client.get(URL)).json()
            assert (await client.get(URL, headers=headers)).status_code == 403
            assert (await client.put(URL, headers=headers, json=command(view))).status_code == 403
        assert FIELD not in await db.mezan_products_v2.find_one({})
    run(scenario)


def test_owner_isolation_and_fresh_disabled_actor_check():
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            view = (await client.get(URL)).json()
            assert (await client.get(URL, headers={'x-test-user': 'other'})).status_code == 404
            assert (await client.put(URL, headers={'x-test-user': 'other'}, json=command(view))).status_code == 404
            await db.users.update_one({'id': 'owner'}, {'$set': {'disabled': True}})
            assert (await client.put(URL, json=command(view))).status_code == 403
        assert FIELD not in await db.mezan_products_v2.find_one({})
    run(scenario)


def test_changed_revision_and_full_map_are_rejected_without_overwrite():
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            initial = (await client.get(URL)).json()
            saved = await client.put(URL, json=command(initial))
            assert saved.status_code == 200, saved.text
            changed = {**ROLES, 'note': 'customization'}
            stale = await client.put(URL, json=command(initial, changed))
            assert stale.status_code == 409
            for bad in ({key: value for key, value in ROLES.items() if key != 'note'},
                        {**ROLES, 'unknown': 'informational'}):
                assert (await client.put(URL, json=command(saved.json(), bad))).status_code == 409
        assert (await db.mezan_products_v2.find_one({}))[FIELD]['roles'] == ROLES
    run(scenario)


@pytest.mark.parametrize('patch', [
    {'expected_revision': True}, {'expected_revision': -1},
    {'catalog_fingerprint': 'not-a-fingerprint'}, {'extra': 'not-allowed'},
    {'roles': {**ROLES, 'note': 'automatic'}},
])
def test_request_contract_is_strict(patch):
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            view = (await client.get(URL)).json()
            result = await client.put(URL, json={**command(view), **patch})
            assert result.status_code == 422, result.text
        assert FIELD not in await db.mezan_products_v2.find_one({})
    run(scenario)


@pytest.mark.parametrize('change', [
    {'size': 'informational'}, {'size': 'customization'},
    {'embroider': 'informational'}, {'gift_text': 'customization'},
])
def test_variant_dimensions_and_side_parent_dependencies_cannot_be_weakened(change):
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            view = (await client.get(URL)).json()
            result = await client.put(URL, json=command(view, {**ROLES, **change}))
            assert result.status_code == 422, result.text
        assert FIELD not in await db.mezan_products_v2.find_one({})
    run(scenario)


def test_real_concurrent_updates_have_one_winner_and_identical_updates_are_idempotent():
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            view = (await client.get(URL)).json()
            changed = {**ROLES, 'note': 'customization'}
            replies = await asyncio.gather(client.put(URL, json=command(view)),
                                           client.put(URL, json=command(view, changed)))
            assert sorted(reply.status_code for reply in replies) == [200, 409]
            current = next(reply.json() for reply in replies if reply.status_code == 200)
            same = command(current, current['roles'])
            replays = await asyncio.gather(client.put(URL, json=same), client.put(URL, json=same))
            assert all(reply.status_code == 200 for reply in replays)
            assert all(reply.json()['revision'] == 1 for reply in replays)
        assert (await db.mezan_products_v2.find_one({}))[FIELD]['revision'] == 1
    run(scenario)


@pytest.mark.parametrize('change', [
    {'options_count': 8},
    {'raw_salla': {'options': [{'id': 'other', 'name': 'تغير تعريف سلة'}]}},
    {'raw_salla_details': {'options': [{'id': 'size', 'inventory_role': 'physical'}]}},
    {'variants': []},
])
def test_catalog_fingerprint_changes_when_underlying_provider_definition_changes(change):
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            view = (await client.get(URL)).json()
            await db.mezan_products_v2.update_one({'mezan_product_id': 'product'}, {'$set': change})
            result = await client.put(URL, json=command(view))
            assert result.status_code == 409, result.text
        assert FIELD not in await db.mezan_products_v2.find_one({})
    run(scenario)


class RaceProductCollection:
    """Inject a provider refresh at the storage boundary before the real CAS."""
    def __init__(self, collection):
        self.collection = collection
        self.triggered = False

    def __getattr__(self, name):
        return getattr(self.collection, name)

    async def update_one(self, selector, update, *args, **kwargs):
        if FIELD in update.get('$set', {}) and not self.triggered:
            self.triggered = True
            await self.collection.update_one({'mezan_product_id': 'product'}, {'$set': {
                'raw_salla_details': {'options': [{'id': 'size', 'inventory_role': 'physical'}]}}})
        return await self.collection.update_one(selector, update, *args, **kwargs)


class RaceDatabase:
    def __init__(self, db):
        self.db = db
        self.products = RaceProductCollection(db.mezan_products_v2)

    def __getitem__(self, name):
        return self.products if name == 'mezan_products_v2' else self.db[name]

    def __getattr__(self, name):
        return self.products if name == 'mezan_products_v2' else getattr(self.db, name)


def test_provider_refresh_between_validation_and_cas_prevents_save():
    async def scenario(db):
        await seed(db)
        wrapped = RaceDatabase(db)
        async with client_for(wrapped) as client:
            view = (await client.get(URL)).json()
            result = await client.put(URL, json=command(view))
            assert result.status_code == 409, result.text
        assert wrapped.products.triggered
        assert FIELD not in await db.mezan_products_v2.find_one({})
    run(scenario)


def test_editor_can_review_added_or_removed_options_without_reusing_old_role_map():
    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            before = (await client.get(URL)).json()
            first = await client.put(URL, json=command(before))
            assert first.status_code == 200, first.text
            row = await db.mezan_products_v2.find_one({})
            options = [option for option in row['options'] if option['id'] != 'wrap'] + [
                {'id': 'new_note', 'name': 'ملاحظة جديدة', 'type': 'textarea', 'required': False}]
            await db.mezan_products_v2.update_one({'mezan_product_id': 'product'},
                {'$set': {'options': options, 'options_count': len(options)}})
            current = await client.get(URL)
            assert current.status_code == 200, current.text
            assert current.json()['product']['configuration_issues']
            assert 'new_note' in current.json()['roles'] and 'wrap' not in current.json()['roles']
            old_map = await client.put(URL, json=command(current.json()))
            assert old_map.status_code == 409
            fresh_roles = {**{key: role for key, role in ROLES.items() if key != 'wrap'},
                           'new_note': 'informational'}
            saved = await client.put(URL, json=command(current.json(), fresh_roles))
            assert saved.status_code == 200, saved.text
            assert saved.json()['revision'] == 2
            assert saved.json()['product']['configuration_issues'] == []
            assert saved.json()['roles'] == fresh_roles
    run(scenario)


def test_provider_detail_refresh_preserves_local_policy_and_other_fields():
    from product_v2_details_routes import _details_patch

    async def scenario(db):
        await seed(db)
        async with client_for(db) as client:
            before = (await client.get(URL)).json()
            local_roles = {**ROLES, 'note': 'customization'}
            saved = await client.put(URL, json=command(before, local_roles))
            assert saved.status_code == 200, saved.text
            policy_before = (await db.mezan_products_v2.find_one({}))[FIELD]
            options = [{key: value for key, value in option.items() if key != 'inventory_role'}
                       for option in product_source()['options']]
            patch = _details_patch({'id': '101', 'name': 'تحديث من سلة', 'options': options,
                'variants': [{'id': 'variant-two', 'options': [{'option_id': 'size', 'value_id': 'two'}]}]},
                user_id='owner')
            await db.mezan_products_v2.update_one({'mezan_product_id': 'product'}, {'$set': patch})
            # Provider normalization uses the canonical mpv2 identity.
            current = await client.get(URL.replace('/product/', '/mpv2_101/'))
            assert current.status_code == 200, current.text
            assert current.json()['roles'] == local_roles
            assert current.json()['revision'] == 1
            row = await db.mezan_products_v2.find_one({})
            assert row[FIELD] == policy_before
            assert row['unrelated_local_metadata'] == {'retain': True}
    run(scenario)


def test_cached_provider_options_keep_their_real_ids_when_classified():
    async def scenario(db):
        row = product_source()
        row.pop('options')
        row.update(options_count=2, variants=[], variants_count=0,
            raw_salla={'options': [
                {'id': 12, 'name': 'اللون', 'type': 'select', 'required': True,
                 'values': [{'id': 9, 'name': 'فضي'}]},
                {'id': 13, 'name': 'ملاحظة', 'type': 'text', 'required': False}]})
        await seed(db, row)
        async with client_for(db) as client:
            current = await client.get(URL)
            assert current.status_code == 200, current.text
            assert set(current.json()['roles']) == {'12', '13'}
            result = await client.put(URL, json=command(current.json(),
                {'12': 'physical', '13': 'informational'}))
            assert result.status_code == 200, result.text
        stored = await db.mezan_products_v2.find_one({})
        assert 'options' not in stored
        assert stored['raw_salla'] == row['raw_salla']
        assert stored[FIELD]['roles'] == {'12': 'physical', '13': 'informational'}
    run(scenario)


@pytest.mark.parametrize('value_id', ['yes', 'no', 'filled', '__option__'])
def test_existing_supplier_service_binding_blocks_informational_role(value_id):
    async def scenario(db):
        await seed(db)
        await db.mezan_product_option_cost_bindings_v2.insert_one({
            'id': 'binding', 'user_id': 'owner', 'salla_product_id': '101',
            'option_id': 'gift', 'value_id': value_id, 'mode': 'resource', 'resource_id': 'service'})
        await db.mezan_cost_resources_v2.insert_one({
            'id': 'service', 'user_id': 'owner', 'kind': 'service', 'name': 'خدمة مورد', 'status': 'inactive'})
        async with client_for(db) as client:
            view = (await client.get(URL)).json()
            result = await client.put(URL, json=command(view))
            assert result.status_code == 409, result.text
            assert result.json()['detail']['code'] == 'inventory_option_supplier_service_conflict'
            assert result.json()['detail']['conflicts'][0]['option_id'] == 'gift'
        assert FIELD not in await db.mezan_products_v2.find_one({})
    run(scenario)


def test_direct_cost_and_stock_component_bindings_do_not_block_side_options():
    async def scenario(db):
        await seed(db)
        await db.mezan_product_option_cost_bindings_v2.insert_many([
            {'id': 'direct', 'user_id': 'owner', 'salla_product_id': '101',
             'option_id': 'gift', 'value_id': 'yes', 'mode': 'direct', 'amount': 5},
            {'id': 'component', 'user_id': 'owner', 'salla_product_id': '101',
             'option_id': 'wrap', 'value_id': 'paper', 'mode': 'resource', 'resource_id': 'paper'},
        ])
        await db.mezan_cost_resources_v2.insert_one({
            'id': 'paper', 'user_id': 'owner', 'kind': 'component', 'name': 'ورق'})
        async with client_for(db) as client:
            view = (await client.get(URL)).json()
            result = await client.put(URL, json=command(view))
            assert result.status_code == 200, result.text
        assert await db.mezan_product_option_cost_bindings_v2.count_documents({}) == 2
    run(scenario)
