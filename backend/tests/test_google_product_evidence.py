"""Pure input contract tests; no connector is invoked by this slice."""
from copy import deepcopy

import pytest
from pydantic import ValidationError

from integrations_control_center.google_product_evidence import ProductEvidenceRequest, build_product_evidence


def request(**changes):
    return {"plan_id": "seasonal-focus", "plan_revision": 3, "as_of": "2026-10-27T08:00:00+03:00", "max_observation_age_seconds": 86400,
        "products": [{"product_id": "123", "mezan_product_id": "mpv2_123", "evidence_ref": "catalog:123:rev1", "observed_at": "2026-10-27T04:00:00Z", "verification": "verified", "status": "available", "available_quantity": "5", "cost_quality": "unknown"}],
        "records": [], **changes}


def fact(**changes):
    result = {"product_id": "123", "provider": "store_orders", "account_id": "amasi-store", "currency_code": "SAR", "timezone": "Asia/Riyadh",
        "date_from": "2026-10-13", "date_to": "2026-10-26", "observed_at": "2026-10-27T04:00:00Z", "complete": True,
        "verification": "verified", "evidence_ref": "store:123:last14:rev1", "units_sold": "4", **changes}
    if result["provider"] != "store_orders":
        result.update(campaign_id="campaign-123", product_link_verification="verified", product_link_evidence_ref="association:123")
    return result


def build(payload): return build_product_evidence(ProductEvidenceRequest.model_validate(payload))


def test_missing_history_and_cost_remain_unknown_and_new_product_can_be_reviewed():
    out = build(request())
    assert out['provider_writes'] == 0 and out['execution_authorized'] is False
    product = out['products'][0]
    assert product['windows']['last_14_days']['availability'] == 'missing'
    assert product['profitability'] is None and product['roas'] is None
    assert product['cost_quality'] == 'unknown'
    assert product['candidate_status'] == 'new_product_test_review'
    assert 'separate_approved_test_budget_required' in product['limitations']


def test_exact_closed_riyadh_windows_and_no_data_is_synthesized():
    out = build(request(records=[fact()]))['products'][0]
    assert out['windows']['last_14_days']['period'] == {'from': '2026-10-13', 'to': '2026-10-26', 'timezone': 'Asia/Riyadh'}
    assert out['windows']['previous_14_days']['period']['from'] == '2026-09-29'
    assert out['windows']['last_30_days']['period']['from'] == '2026-09-27'
    assert out['windows']['last_90_days']['period']['from'] == '2026-07-29'
    assert out['windows']['previous_14_days']['availability'] == 'missing'
    assert out['windows']['last_14_days']['rows'][0]['units_sold'] == '4'
    assert out['growth_14_days_percent'] is None


def test_account_currency_periods_and_platform_conversions_remain_separate():
    records = [fact(provider='snapchat_ads', account_id='snap-a', units_sold=None, conversions='3', spend_native='10', conversion_value_native='60'),
        fact(provider='tiktok', account_id='tik-a', units_sold=None, conversions='2', currency_code='USD', evidence_ref='tik:123'),
        fact(provider='meta', account_id='meta-a', units_sold=None, conversions='5', evidence_ref='meta:123')]
    out = build(request(records=records))['products'][0]
    assert len(out['campaign_evidence']) == 3
    assert {row['currency_code'] for row in out['campaign_evidence']} == {'SAR', 'USD'}
    assert out['verified_store_orders'] is None
    assert out['candidate_status'] == 'cross_platform_test_review'
    assert out['roas'] is None  # no product-wide ROAS across independent sources
    assert out['campaign_evidence'][0]['reported_roas'] == '6'
    assert out['campaign_evidence'][1]['reported_roas'] is None
    assert out['success_classification'] == 'not_established'


@pytest.mark.parametrize('changes', [{'complete': False}, {'verification': 'unverified'}, {'observed_at': '2026-10-26T21:00:00Z'}])
def test_partial_unverified_stale_records_do_not_supply_comparison_facts(changes):
    out = build(request(records=[fact(**changes)], max_observation_age_seconds=3600))['products'][0]
    assert out['windows']['last_14_days']['availability'] == 'unproven'
    assert out['windows']['last_14_days']['rows'] == []
    assert out['growth_14_days_percent'] is None


def test_growth_requires_exact_same_store_account_currency_and_complete_periods():
    prior = fact(date_from='2026-09-29', date_to='2026-10-12', evidence_ref='store:previous', units_sold='2')
    product = build(request(records=[fact(), prior]))['products'][0]
    assert product['growth_14_days_percent'] == '100'
    prior['currency_code'] = 'QAR'
    assert build(request(records=[fact(), prior]))['products'][0]['growth_14_days_percent'] is None
    prior['currency_code'] = 'SAR'; prior['units_sold'] = '0'
    assert build(request(records=[fact(), prior]))['products'][0]['growth_14_days_percent'] is None


def test_store_order_count_requires_unique_exact_proof_refs_and_is_not_provider_conversion_count():
    payload = request(records=[fact(verified_orders=2, order_evidence_refs=['order-proof:a', 'order-proof:b'])])
    assert build(payload)['products'][0]['verified_store_orders'] == 2
    for value in [fact(verified_orders=2, order_evidence_refs=['order-proof:a']), fact(provider='google_ads', verified_orders=1, order_evidence_refs=['order-proof:a'])]:
        with pytest.raises(ValidationError): build(request(records=[value]))


@pytest.mark.parametrize('change', [{'units_sold': '-1'}, {'units_sold': 'NaN'}, {'complete': 1}, {'observed_at': '2026-10-27T04:00:00'},
    {'date_from': '2026-10-30'}, {'currency_code': ''}, {'account_id': ''}])
def test_invalid_source_contract_is_rejected(change):
    with pytest.raises(ValidationError): build(request(records=[fact(**change)]))


def test_duplicate_scope_or_foreign_product_rejected_not_double_counted():
    for records in [[fact(), fact(evidence_ref='different-ref')], [fact(product_id='foreign')]]:
        with pytest.raises(ValidationError): build(request(records=records))


def test_hidden_and_out_of_stock_products_cannot_be_test_candidates():
    for status in ['hidden', 'out_of_stock']:
        payload = request(); payload['products'][0]['status'] = status
        result = build(payload)['products'][0]
        assert result['candidate_status'] == 'excluded'
        assert result['execution_authorized'] is False


def test_cost_numbers_need_verified_same_currency_provenance_and_never_imply_profit():
    payload = request(); product = payload['products'][0]
    product.update(cost_quality='complete', unit_cost_native='20', cost_currency_code='SAR', cost_evidence_ref='cost:123:rev3', cost_verification='verified')
    result = build(payload)['products'][0]
    assert result['unit_cost_native'] == '20' and result['profitability'] is None
    product['cost_verification'] = 'unverified'
    with pytest.raises(ValidationError): build(payload)


def test_builder_does_not_mutate_inputs_and_future_observations_do_not_become_fresh():
    payload = request(records=[fact(observed_at='2026-10-28T00:00:00Z')]); before = deepcopy(payload)
    result = build(payload)
    assert payload == before
    assert result['products'][0]['windows']['last_14_days']['availability'] == 'unproven'


def test_future_or_old_campaign_periods_are_not_current_test_evidence():
    for period in [('2026-11-01', '2026-11-14'), ('2025-10-13', '2025-10-26')]:
        record = fact(provider='snapchat_ads', conversions='5', date_from=period[0], date_to=period[1],
            observed_at='2026-11-15T04:00:00Z' if period[0].startswith('2026-11') else '2026-10-27T04:00:00Z')
        product = build(request(records=[record]))['products'][0]
        assert product['campaign_evidence'] == []
        assert product['candidate_status'] == 'new_product_test_review'


def test_invalid_decimal_is_validation_error_instead_of_unhandled_decimal_exception():
    with pytest.raises(ValidationError): build(request(records=[fact(units_sold='invalid')]))


def test_campaign_records_without_verified_product_association_rejected():
    record = fact(provider='snapchat_ads', conversions='5'); record['product_link_verification'] = 'unverified'
    with pytest.raises(ValidationError): build(request(records=[record]))


def test_extreme_decimal_exponents_rejected_before_ratio_arithmetic():
    with pytest.raises(ValidationError): build(request(records=[fact(units_sold='1e999999999')]))


def test_cost_evidence_reference_is_retained_with_its_native_currency():
    payload = request(); payload['products'][0].update(cost_quality='complete', unit_cost_native='20', cost_currency_code='QAR', cost_evidence_ref='cost:123:rev3', cost_verification='verified')
    product = build(payload)['products'][0]
    assert product['cost_evidence_ref'] == 'cost:123:rev3'
    assert product['cost_currency_code'] == 'QAR' and product['profitability'] is None


@pytest.mark.parametrize('provider', ['store_orders', 'snapchat_ads'])
@pytest.mark.parametrize('observed', ['2026-10-24T22:00:00Z', '2026-10-26T20:59:59Z'])
def test_complete_period_cannot_be_observed_before_source_local_day_closed(provider, observed):
    with pytest.raises(ValidationError):
        build(request(records=[fact(provider=provider, observed_at=observed)]))


def test_closed_day_proof_uses_source_timezone_and_accepts_exact_next_midnight():
    from integrations_control_center.google_product_evidence import ProductSourceRecord
    riyadh = ProductSourceRecord.model_validate(fact(observed_at='2026-10-26T21:00:00Z'))
    assert riyadh.complete is True  # midnight 27 October in Riyadh
    with pytest.raises(ValidationError):
        ProductSourceRecord.model_validate(fact(timezone='America/New_York', observed_at='2026-10-27T03:59:59Z'))
    new_york = ProductSourceRecord.model_validate(fact(timezone='America/New_York', observed_at='2026-10-27T04:00:00Z'))
    assert new_york.complete is True  # midnight 27 October in New York


def test_incomplete_period_observation_remains_unproven_without_closed_day_claim():
    product = build(request(records=[fact(complete=False, observed_at='2026-10-26T20:59:59Z')]))['products'][0]
    assert product['windows']['last_14_days']['availability'] == 'unproven'


@pytest.mark.parametrize('changes', [{'account_id': 'second-account'}, {'currency_code': 'QAR'}, {'date_from': '2026-09-29', 'date_to': '2026-10-12'}])
def test_same_evidence_reference_cannot_claim_different_financial_scope(changes):
    with pytest.raises(ValidationError): build(request(records=[fact(), fact(**changes)]))


@pytest.mark.parametrize('key', ['evidence_ref', 'account_id', 'product_id'])
def test_whitespace_source_identifiers_are_rejected(key):
    with pytest.raises(ValidationError): build(request(records=[fact(**{key: '   '})]))


def test_whitespace_association_or_cost_references_rejected():
    record = fact(provider='snapchat_ads'); record['product_link_evidence_ref'] = ' '
    with pytest.raises(ValidationError): build(request(records=[record]))
    payload = request(); payload['products'][0].update(cost_quality='complete', unit_cost_native='20', cost_currency_code='SAR', cost_evidence_ref=' ', cost_verification='verified')
    with pytest.raises(ValidationError): build(payload)


def test_unknown_currency_code_rejected_using_existing_pinned_vocabulary():
    with pytest.raises(ValidationError): build(request(records=[fact(currency_code='ZZZ')]))
    payload = request(); payload['products'][0].update(cost_quality='complete', unit_cost_native='20', cost_currency_code='ZZZ', cost_evidence_ref='cost:1', cost_verification='verified')
    with pytest.raises(ValidationError): build(payload)
