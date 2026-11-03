"""Synthetic statement checks; no real customer IDs or merchant financial records."""
from copy import deepcopy
import pytest
from operational_provider_statement_review import review_statement


def rows():
    return [dict(id='sale1',type='sale',currency='SAR',order_amount='1000',refundable_fee='50',
        nonrefundable_fee='20',fixed_fee='1',total_fee='71',vat_amount='10.65',
        total_deduction='81.65',transferred_amount='918.35'),
        dict(id='refund1',type='partial_refund',currency='SAR',order_amount='-200',refundable_fee='-10',
        nonrefundable_fee='0',fixed_fee='0',total_fee='-10',vat_amount='-1.50',
        total_deduction='-11.50',transferred_amount='-188.50'),
        dict(id='payout',type='payout_fee',currency='SAR',order_amount='0',refundable_fee='0',
        nonrefundable_fee='0',fixed_fee='6',total_fee='6',vat_amount='0.90',transferred_amount='-6.90')]


def review(**patch):
    kwargs=dict(provider='test-provider',account_id='test-account',statement_id='statement1',currency='SAR',rows=rows(),reported_payout='722.95')
    kwargs.update(patch)
    return review_statement(**kwargs)


def test_actual_statement_fees_separate_from_payout_fee_and_no_writes():
    source=rows(); before=deepcopy(source)
    r=review(rows=source)
    assert r['totals']==dict(orders='800.00',order_fee_net='61.00',order_fee_tax='9.15',
        payout_fee_net='6.00',payout_fee_tax='0.90',transferred='722.95')
    assert r['bank_receipt_matches'] is None and r['posting_allowed'] is False
    assert source==before


def test_same_reference_and_same_bytes_is_a_duplicate_not_an_extra_receipt():
    first=review(); records={first['statement_key']:first['statement_fingerprint']}; before=deepcopy(records)
    again=review(recorded_statements=records)
    assert again['already_recorded'] is True
    assert again['statement_fingerprint']==first['statement_fingerprint']
    assert again['posting_allowed'] is False and records==before


def test_reference_reused_with_different_financial_content_fails():
    first=review(); records={first['statement_key']:first['statement_fingerprint']}
    changed=rows(); changed[0]['order_amount']='1001'; changed[0]['transferred_amount']='919.35'
    with pytest.raises(ValueError,match='reference_content_conflict'):
        review(rows=changed,reported_payout='723.95',recorded_statements=records)


@pytest.mark.parametrize('field,value', [('total_fee','70'),('total_deduction','0'),('transferred_amount','900'),
    ('currency','QAR'),('type','unknown'),('vat_amount',None),('vat_amount','NaN'),('vat_amount','10.651')])
def test_incomplete_or_conflicting_statement_is_not_tolerated(field,value):
    r=rows();r[0][field]=value
    with pytest.raises(ValueError):review(rows=r)


def test_duplicate_source_row_is_not_another_transaction():
    r=rows();r.append(r[0])
    with pytest.raises(ValueError,match='row_identity_conflict'):review(rows=r)


def test_bank_transfer_is_a_separate_piece_of_evidence():
    assert review(bank_received='722.95')['bank_receipt_matches'] is True
    r=review(bank_received='722.94')
    assert r['bank_receipt_matches'] is False and r['bank_difference']=='-0.01'
    assert r['posting_allowed'] is False


def test_distinct_provider_accounts_cannot_share_statement_identity():
    first=review()
    records={first['statement_key']:first['statement_fingerprint']}
    other=review(account_id='other-account',recorded_statements=records)
    assert other['already_recorded'] is False and other['statement_key'] != first['statement_key']
