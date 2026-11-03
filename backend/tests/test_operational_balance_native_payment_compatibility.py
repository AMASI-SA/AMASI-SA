"""Preserve published read-only returns and named-bank exchange routing."""
import pytest
from test_operational_balance_native_payment_evidence import normalize
from test_operational_balance_sources import DB, order, run


@pytest.mark.parametrize('method', ['تحويل بنكي الإنماء', 'حوالة بنكية الراجحي', 'Bank Transfer TestBank', 'wire_transfer TestBank'])
def test_existing_named_bank_routes_are_not_unknown_provider_warnings(method):
    p,issues=normalize({'payment_method':method})
    assert p is None and not issues


def test_unknown_original_method_keeps_read_only_order_available_without_provider(monkeypatch):
    import operational_customer_returns as returns
    async def read_only_state(db,owner):
        return {'customer_returns':[]}
    monkeypatch.setattr(returns,'read',read_only_state)
    raw={'payment':{'method':'unknown_payment_method'}, 'total':'150'}
    db=DB({'unified_orders':[order(**raw)]})
    result=run(returns.order_view(db,'owner','10'))
    assert result['total']=='150.00' and result['provider'] is None
    # The source normalizer still exposes missing routing evidence; no platform
    # may be inferred merely to let a monetary approval through.
    p,issues=normalize(raw)
    assert p is None and issues[0]['code']=='provider_payment_method_unknown'
