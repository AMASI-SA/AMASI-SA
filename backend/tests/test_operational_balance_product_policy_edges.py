"""Missing status/currency evidence must not become a false complete zero."""
import pytest
from test_operational_open_cost_exposure import seeded_db, native_order, piece, preview, operating_state, group, codes
from test_operational_balance_sources import row


@pytest.mark.parametrize('status', [None, '', 'unknown', {'slug':'under_review','name':'تمت المراجعة'}])
def test_unknown_product_status_is_excluded_and_never_claimed_complete(status):
    result=preview(seeded_db(order_row=native_order(status=status)))
    assert group(result)==[]
    assert 'product_order_status_unverified' in codes(result)
    assert result['component_complete']['unreceived_products'] is False


def test_legacy_zero_foreign_rows_do_not_hide_a_known_empty_sar_pool():
    old={'id':'supplier:o:i','kind':'supplier','currency':'KWD',
         'expected':'0.00','confirmed':'0.00','party_id':None}
    result=preview(seeded_db(order_row=native_order(status='delivered')),operating_state({old['id']:old}))
    assert set(result['currencies'])=={'SAR'}
    assert result['component_complete']['unreceived_products'] is True


def test_explicit_foreign_invoice_requires_reconciliation_without_relabelling():
    invoice=row(id='foreign',supplier_id='supplier1',currency='KWD',
        approved_at='2026-11-01T09:00:00+00:00',
        lines=[{'piece_ids':['physical1'],'product_price_authority':'mezan_v2',
                'product_unit_price_halalas':2000}])
    result=preview(seeded_db(invoices=[invoice],physical=piece(received=True)))
    assert not group(result)
    assert 'supplier_invoice_tax_or_amount_incomplete' in codes(result)
