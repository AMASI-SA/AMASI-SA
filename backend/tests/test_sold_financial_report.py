import asyncio
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
import dashboard_v2_routes as routes
from sold_financial_report import validate_report_statuses


@pytest.mark.parametrize('value', ['', 'default,reviewed', 'cancelled', 'reviewed,'])
def test_rejects_unsupported_cohort(value):
    with pytest.raises(HTTPException) as error:
        validate_report_statuses(value)
    assert error.value.status_code == 422


def test_status_override_matches_sold_products_without_changing_default(monkeypatch):
    rows = [{'order_status': 'تم المراجعة'}, {'order_status': 'قيد التنفيذ'}, {'order_status': 'ملغي'}]
    class Cursor:
        async def to_list(self, length=None):
            return [dict(row) for row in rows]
    class Collection:
        def find(self, *args):
            return Cursor()
    async def settings(*args):
        return {'report_included_statuses': ['قيد التنفيذ']}
    async def noop(*args, **kwargs):
        pass
    monkeypatch.setattr(routes, 'ensure_user_settings', settings)
    monkeypatch.setattr(routes, 'hydrate_order_currency_fields_cooperative', noop)
    import report_option_recovery
    monkeypatch.setattr(report_option_recovery, 'attach_report_option_evidence_cooperative', noop)
    async def run(status=None):
        return await routes._filtered_orders(SimpleNamespace(unified_orders=Collection()), 'owner', from_date='2026-11-01', to_date='2026-11-07', payment_methods=None, shipping_companies=None, report_statuses=status)
    assert asyncio.run(run()) == [rows[1]]
    assert asyncio.run(run('default')) == [rows[1]]
    assert asyncio.run(run('reviewed')) == [rows[0]]
    assert asyncio.run(run('reviewed,in_progress')) == rows[:2]

from copy import deepcopy
import httpx
from fastapi import FastAPI
from test_dashboard_bundle_cost_authority import db, ReadOnlyDB, OWNER, sale, sale_line


@pytest.mark.asyncio
async def test_http_summary_and_product_table_share_status_cohort(db, monkeypatch):
    # This cohort contract must not depend on unrelated processes consuming the
    # host cgroup. Keep the real admission policy, with a deterministic sample.
    import resource_governor
    from dataclasses import replace
    healthy = replace(resource_governor.memory_snapshot(), usage_ratio=0.25)
    monkeypatch.setattr(resource_governor, 'memory_snapshot', lambda: healthy)
    from auth import DEFAULT_PAYMENT_METHODS, PAYMENT_FEE_DEFAULTS_VERSION
    await db.settings.insert_one({'user_id': OWNER, 'payment_methods': deepcopy(DEFAULT_PAYMENT_METHODS), 'payment_fee_defaults_version': PAYMENT_FEE_DEFAULTS_VERSION, 'report_included_statuses': ['قيد التنفيذ']})
    await db.unified_orders.insert_many([
        sale('reviewed', sale_line('a', product='plain'), order_status='تم المراجعة'),
        sale('processing', sale_line('b', quantity=2, product='plain'), order_status='قيد التنفيذ'),
        sale('cancelled', sale_line('c', product='plain'), order_status='ملغي'),
    ])
    captured = []
    async def legacy(**kwargs):
        captured.append(kwargs)
        return {'totals': {'total_orders': 0, 'total_sales': 0, 'total_product_cost': 0, 'total_ads_cost': 0, 'operating_expenses_total': 0, 'operating_salaries_total': 0, 'total_payment_fees': 0, 'net_profit': 0, 'net_sales': 0}, 'payment_breakdown': []}
    async def ads(*args, **kwargs):
        return {'total': 0, 'breakdown': {}, 'spend_quality': {'status': 'complete', 'amount_available': True, 'amount_complete': True}, 'bank_commissions': {'total_fee_sar': 0}, 'source_contract': {}, 'providers': {key: {'spend': 0, 'orders': 0, 'revenue': 0, 'roas': 0} for key in ('snapchat', 'tiktok', 'meta', 'google')}}
    async def user():
        return {'id': OWNER, 'role': 'owner'}
    monkeypatch.setattr(routes, 'build_mezan_v2_ads', ads)
    app = FastAPI()
    app.include_router(routes.make_dashboard_v2_router(ReadOnlyDB(db), user, legacy, lambda current: None))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        for selected, expected_cost, expected_sales in [('default', 18, 200), ('reviewed', 9, 100), ('reviewed,in_progress', 27, 300)]:
            params = {'from_date': '2026-11-02', 'to_date': '2026-11-02'}
            report = await client.get('/dashboard-v2/sold-products', params={**params, 'statuses': selected})
            summary = await client.get('/dashboard-v2', params={**params, 'report_statuses': selected})
            assert report.status_code == summary.status_code == 200
            assert summary.json()['totals']['total_product_cost'] == report.json()['totals']['total_cost'] == expected_cost
            assert summary.json()['totals']['total_sales'] == expected_sales
            assert captured[-1]['report_statuses'] == selected
            assert captured[-1]['allow_self_heal'] is False
        await client.get('/dashboard-v2', params=params)
        assert 'report_statuses' not in captured[-1]
        bad = await client.get('/dashboard-v2', params={**params, 'report_statuses': 'invalid'})
        assert bad.status_code == 422
