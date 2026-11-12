"""Operational setup readers join preparation before releasing their request."""
import asyncio
from copy import deepcopy
from threading import Event

from fastapi import FastAPI
import pytest

from operational_balance_store import initial
from operational_financial_activity_runtime import preparation_checkpoint
from test_operational_balance_supplier_preview_lifetime import begin_request


@pytest.mark.parametrize('stage', [
    'salary_sources', 'salary_preview', 'opening_components', 'opening_preview',
    'catalog', 'catalog_products', 'catalog_locations', 'inventory_projection',
    'inventory_purchases', 'inventory_invoice', 'supplier_invoice',
])
def test_disconnect_joins_setup_preparation_and_closes_snapshot_afterwards(monkeypatch, stage):
    import operational_balance_inventory as inventory
    import operational_balance_routes as routes
    import operational_balance_service as service
    import operational_balance_sources as sources
    import operational_balance_store as store
    import operational_inventory_projection as physical
    import operational_opening_inventory as opening
    import operational_purchase_metadata as metadata
    import operational_salary_start as salary
    import operational_supplier_adjustments as adjustments
    import web_page_access

    async def scenario():
        started, stopped = Event(), Event()
        lifecycle = []
        state = initial('owner')
        state.update(status='active', started_at='2026-11-01T00:00:00+00:00')
        state['inventory_purchases'] = [{'id': 'invoice', 'supplier_id': 'supplier', 'invoice_number': 'INV'}]
        before = deepcopy(state)

        def cpu(*_args, **_kwargs):
            started.set()
            try:
                while True:
                    preparation_checkpoint()
            finally:
                stopped.set()
                lifecycle.append('cpu_stopped')

        class Session:
            async def __aenter__(self):
                lifecycle.append('session_open')
                return self

            async def __aexit__(self, *_args):
                lifecycle.append('session_closed')

            def start_transaction(self, **_kwargs):
                class Transaction:
                    async def __aenter__(self):
                        lifecycle.append('snapshot_open')

                    async def __aexit__(self, *_args):
                        lifecycle.append('snapshot_closed')
                return Transaction()

        class Collection:
            def find(self, *_args, **_kwargs):
                return self

            async def to_list(self, *_args, **_kwargs):
                return []

            async def find_one(self, *_args, **_kwargs):
                return None

        class Database:
            def __init__(self):
                self.client = self

            async def start_session(self):
                return Session()

            def __getitem__(self, _name):
                return Collection()

            def __getattr__(self, _name):
                return Collection()

        async def scope(*_args, **_kwargs):
            return {'id': 'owner', 'role': 'owner'}, 'owner', None

        async def owner(*_args, **_kwargs):
            return {'id': 'owner', 'role': 'owner'}

        async def read(*_args, **_kwargs):
            return deepcopy(state)

        async def empty(*_args, **_kwargs):
            return []

        async def noop(*_args, **_kwargs):
            return None

        async def evidence(*_args, **_kwargs):
            return {}

        monkeypatch.setattr(routes, 'scope', scope)
        monkeypatch.setattr(routes, 'read', read)
        monkeypatch.setattr(store, 'read', read)
        monkeypatch.setattr(service, 'active_gate', noop)
        monkeypatch.setattr(salary, '_owner', noop)
        monkeypatch.setattr(opening, 'owner_actor', owner)
        monkeypatch.setattr(sources, 'rows', empty)
        monkeypatch.setattr(inventory, 'rows', empty)
        monkeypatch.setattr(metadata, 'rows', empty)
        monkeypatch.setattr(physical, '_load_inventory_evidence', evidence)
        monkeypatch.setattr(web_page_access, 'delegated_page_owner', noop)

        method, payload = 'GET', None
        if stage.startswith('salary_'):
            path, method, payload = '/salary-start/preview', 'POST', {'first_day': '2026-11-01'}
            monkeypatch.setattr(salary, '_normalize_sources' if stage == 'salary_sources' else 'preview_from_sources', cpu)
        elif stage == 'opening_components':
            path = '/inventory-opening/context'
            monkeypatch.setattr(opening, '_components', cpu)
        elif stage == 'opening_preview':
            path, method = '/inventory-opening/preview', 'POST'
            payload = {'request_id': 'read-only-preview', 'lines': [{'kind': 'component', 'item_id': 'component',
                'location_id': 'location', 'quantity': 1, 'unit_cost': '1.00',
                'purchase_configuration': {'state': 'raw', 'selections': [], 'inputs': []}}]}
            monkeypatch.setattr(opening, '_compile_preview_rows', cpu)
        elif stage in {'catalog', 'catalog_products', 'catalog_locations'}:
            path = '/inventory-catalog'
            target, name = (inventory, '_catalog_rows') if stage == 'catalog' else (
                metadata, '_products' if stage == 'catalog_products' else '_locations')
            monkeypatch.setattr(target, name, cpu)
        elif stage == 'inventory_projection':
            path = '/inventory'
            monkeypatch.setattr(physical, '_project_rows', cpu)
        elif stage == 'inventory_purchases':
            path = '/inventory-purchases'
            monkeypatch.setattr(inventory, 'inventory_view', cpu)
        elif stage == 'inventory_invoice':
            path = '/inventory-purchases/entry/supplier/INV'
            monkeypatch.setattr(inventory, 'purchase_view', cpu)
        else:
            path = '/supplier-adjustments/entry/supplier/INV'
            monkeypatch.setattr(adjustments, 'lookup', cpu)

        async def principal():
            return {'id': 'owner', 'role': 'owner'}

        app = FastAPI()
        app.include_router(routes.make_operational_balance_router(Database(), principal), prefix='/api')
        task, incoming, sent = begin_request(app, path, method=method, payload=payload)
        try:
            assert await asyncio.to_thread(started.wait, 3), sent
            await incoming.put({'type': 'http.disconnect'})
            await asyncio.wait_for(task, 3)
            assert stopped.is_set()
            assert next(message['status'] for message in sent if message['type'] == 'http.response.start') == 499
            if stage == 'inventory_projection':
                assert lifecycle.index('cpu_stopped') < lifecycle.index('snapshot_closed') < lifecycle.index('session_closed')
            assert state == before
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize('identity', ['employee', None, ['ambiguous'], {'ambiguous': 'id'}, 1])
def test_indexed_salary_sources_preserve_existing_normalization_for_every_identity(identity):
    from operational_balance_sources import _employees
    from operational_salary_start import _normalize_sources

    data = {
        'mezan_employees_v2': [{'id': identity, 'status': 'active', 'hire_date': '2026-01-01'}],
        'mezan_employee_salary_contracts_v2': [
            {'employee_id': identity, 'monthly_amount': '3000', 'effective_from': '2026-01-01'},
            {'employee_id': 'unrelated', 'monthly_amount': '1000', 'effective_from': '2026-01-01'},
        ],
    }
    original = {'employees': [], 'issues': []}
    _employees(deepcopy(data), original)
    assert _normalize_sources(deepcopy(data)) == original
