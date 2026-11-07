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
