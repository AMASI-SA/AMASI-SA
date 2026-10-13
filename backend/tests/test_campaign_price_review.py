from datetime import datetime, timedelta, timezone

import pytest

from campaign_price_review import Offer, PriceReviewInput, assess_price, unavailable_price_review

NOW = datetime(2026, 10, 13, 14, tzinfo=timezone.utc)


def offer(host='shop.example', amount=14900, **changes):
    return Offer(**dict(dict(merchant_key=host, url=f'https://{host}/product', observed_at=NOW,
        product_match='exact', specification_key='cotton-personalized-size-m-quantity-1',
        currency='SAR', destination='SA-RUH', item_minor=amount, price_basis='single_configuration', shipping_minor=0,
        mandatory_fees_minor=0, unconditional_discount_minor=0, tax_included=True,
        in_stock=True, delivery_days=3, returns_key='14-days', warranty_key='one-year',
        payment_terms_key='same', quality_verified=True), **changes))


def review(own=None, others=None):
    return PriceReviewInput(user_id='owner', store_id='store', product_id='product',
        own=own or offer(), competitors=tuple(others or [offer('one.example',11900),offer('two.example',12900)]))


def test_higher_sticker_price_is_not_higher_delivered_price():
    result=assess_price(review(others=[offer('one.example',11900,shipping_minor=4000),offer('two.example',12900,shipping_minor=3000)]),user_id='owner',store_id='store',product_id='product',now=NOW)
    assert result['position']=='not_above_observed_offers'
    assert result['price_cause_verified'] is False
    assert result['price_change_allowed'] is False


@pytest.mark.parametrize('change',[
    {'shipping_minor':None}, {'tax_included':None}, {'quality_verified':False},
    {'specification_key':'different-material'}, {'product_match':'similar'},
    {'currency':'USD'}, {'destination':'QA-DOH'}, {'delivery_days':10},
    {'returns_key':'no-returns'}, {'warranty_key':'no-warranty'}, {'payment_terms_key':'cash-only'},
    {'in_stock':False}, {'observed_at':NOW-timedelta(days=2)}, {'observed_at':NOW+timedelta(minutes=2)}
])
def test_unmatched_or_unknown_offer_cannot_support_price_hypothesis(change):
    result=assess_price(review(others=[offer('one.example',11900,**change),offer('two.example',12900)]),user_id='owner',store_id='store',product_id='product',now=NOW)
    assert result['status']=='insufficient_evidence'
    assert result['position']=='unknown'


def test_duplicate_site_and_own_site_are_not_independent_competitors():
    result=assess_price(review(others=[offer('one.example',11900),offer('one.example',12900),offer('shop.example',10900)]),user_id='owner',store_id='store',product_id='product',now=NOW)
    assert result['status']=='insufficient_evidence'


@pytest.mark.parametrize('field',['user_id','store_id','product_id'])
def test_rejects_cross_owner_store_or_product(field):
    args=dict(user_id='owner',store_id='store',product_id='product',now=NOW)
    args[field]='other'
    with pytest.raises(ValueError,match='scope_mismatch'):
        assess_price(review(),**args)


def test_higher_comparable_total_is_observation_not_causality_or_discount_permission():
    result=assess_price(review(),user_id='owner',store_id='store',product_id='product',now=NOW)
    assert result['position']=='above_observed_offers'
    assert len(result['comparisons'])==2
    assert result['comparisons'][0]['difference_minor']==3000
    assert result['price_cause_verified'] is False
    assert result['price_change_allowed'] is False


def test_missing_service_does_not_claim_research_performed():
    result=unavailable_price_review()
    assert result['status']=='not_checked'
    assert result['comparisons']==[]
    assert result['position']=='unknown'


def test_same_merchant_on_different_domains_does_not_inflate_sample():
    result=assess_price(review(others=[offer('one.example',11900,merchant_key='merchant'),offer('two.example',12900,merchant_key='merchant')]),user_id='owner',store_id='store',product_id='product',now=NOW)
    assert result['status']=='insufficient_evidence'


def test_unknown_own_shipping_blocks_comparison():
    result=assess_price(review(own=offer(shipping_minor=None)),user_id='owner',store_id='store',product_id='product',now=NOW)
    assert result['status']=='insufficient_evidence'
    assert result['comparisons']==[]


def test_unknown_selected_option_price_blocks_comparison():
    own = offer(price_basis='unknown')
    result = assess_price(review(own=own), user_id='owner', store_id='store', product_id='product', now=NOW)
    assert result['comparisons'] == []
    assert result['position'] == 'unknown'


def test_starting_price_cannot_be_used_as_selected_variant_price():
    result = assess_price(review(others=[offer('one.example',13900,price_basis='starting_from'), offer('two.example',13900)]), user_id='owner', store_id='store', product_id='product', now=NOW)
    assert result['status'] == 'insufficient_evidence'


def test_children_base_price_not_comparable_with_adult_practical_selection():
    own = offer(selected_options_key='adult-practical', price_basis='selected_configuration', base_item_minor=13900)
    competitors = [offer('one.example',13900,selected_options_key='children',price_basis='selected_configuration'), offer('two.example',14900,selected_options_key='adult-practical',price_basis='selected_configuration')]
    result = assess_price(review(own=own,others=competitors), user_id='owner', store_id='store', product_id='product', now=NOW)
    assert result['status'] == 'insufficient_evidence'
    assert result['excluded_offers'] == 1


def test_selected_final_price_is_not_added_to_base_price_twice():
    own = offer(price_basis='selected_configuration',selected_options_key='adult-practical',base_item_minor=13900)
    others = [offer(host,14900,price_basis='selected_configuration',selected_options_key='adult-practical',base_item_minor=13900) for host in ('one.example','two.example')]
    result = assess_price(review(own=own,others=others), user_id='owner', store_id='store', product_id='product', now=NOW)
    assert result['status'] == 'comparable_sample'
    assert result['comparisons'][0]['own_total_minor'] == 14900
    assert result['comparisons'][0]['difference_minor'] == 0


def test_selected_configuration_requires_identified_options():
    result = assess_price(review(own=offer(price_basis='selected_configuration',selected_options_key=None)),user_id='owner',store_id='store',product_id='product',now=NOW)
    assert result['comparisons'] == []
