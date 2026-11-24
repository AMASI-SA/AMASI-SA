"""Financial coverage is proved by saved rows, never an advanced clock."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from operational_entity_freshness import (
    MAX_ENTITIES, build_coverage, coverage_rows, recovery_snapshot, source_entities,
)


STAMP = "2026-11-24T10:00:00+00:00"
START = "2026-11-20T00:00:00+03:00"


def base():
    return {"owner_id": "owner", "status": "active", "revision": 4, "started_at": START,
            "engine": {"as_of": STAMP, "obligations": {}, "salary_days": {}, "ad_days": {}, "issues": []},
            "movements": []}


def descriptor(kind="ad_account", identity="snap-sar", currency="SAR", zone="Asia/Riyadh"):
    return {"entity_type": kind, "entity_id": identity, "label": identity, "currency": currency,
            "timezone": zone, "platform": "snapchat" if kind == "ad_account" else None,
            "source_available": True}


def sources(*rows):
    return {"automatic_source_entities": {"schema_version": 1, "owner_id": "owner", "complete": True,
             "total_entities": len(rows), "rows": list(rows)}, "employees": [], "recurring": [], "issues": []}


def add_ad(state, identity, day, amount="10.00", currency="SAR", closed=True):
    key = f"advertising:{identity}:{day}"
    state["engine"]["ad_days"][key] = {"amount": amount, "closed": closed,
        "observed_at": STAMP, "timezone": "Asia/Riyadh", "funding_type": "postpaid"}
    state["engine"]["obligations"][key] = {"id": key, "kind": "advertising", "party_type": "ad_account",
        "party_id": identity, "currency": currency, "confirmed": amount if closed else "0.00",
        "expected": "0.00" if closed else amount, "business_date": day, "direction": "payable"}


def render(state, inputs, now=STAMP):
    saved = build_coverage(state, inputs, STAMP)
    return coverage_rows(saved, "owner", STAMP, now=datetime.fromisoformat(now), revision=5)


def test_advanced_calculation_clock_cannot_hide_an_interior_ad_day_gap():
    state = base()
    for day in ("2026-11-20", "2026-11-22", "2026-11-23"):
        add_ad(state, "snap-sar", day)
    before = deepcopy(state)
    result = render(state, sources(descriptor()))
    row = result["rows"][0]
    assert row["missing_days"] == ["2026-11-21"]
    assert row["missing_days_count"] == 1 and row["state"] == "stale"
    assert row["last_financial_day"] == "2026-11-23"
    assert state == before


def test_sar_and_usd_accounts_keep_separate_native_day_expectations():
    state = base()
    for day in ("2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"):
        add_ad(state, "snap-sar", day)
    # The operational start is19Nov in this account's timezone. Its partial
    # first day must also be proved; a timezone conversion cannot discard it.
    for day in ("2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22"):
        add_ad(state, "snap-usd", day, currency="USD")
        state["engine"]["ad_days"][f"advertising:snap-usd:{day}"]["timezone"] = "America/Los_Angeles"
    # 01:00 Pacific on24Nov:23Nov cannot yet close with the2h allowance.
    stamp = "2026-11-24T09:00:00+00:00"
    state["engine"]["as_of"] = stamp
    for value in state["engine"]["ad_days"].values():
        value["observed_at"] = stamp
    inputs = sources(descriptor(), descriptor(identity="snap-usd", currency="USD", zone="America/Los_Angeles"))
    saved = build_coverage(state, inputs, stamp)
    rows = coverage_rows(saved, "owner", stamp, now=datetime.fromisoformat(stamp), revision=5)["rows"]
    assert {row["entity_id"]: row["expected_through"] for row in rows} == {
        "snap-sar": "2026-11-23", "snap-usd": "2026-11-22"}
    assert all(row["state"] == "current" for row in rows)


@pytest.mark.parametrize("defect", ["missing_obligation", "provisional", "amount_conflict"])
def test_ad_timestamp_and_day_marker_are_insufficient_without_confirmed_obligation(defect):
    state = base()
    for day in ("2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"):
        add_ad(state, "snap-sar", day)
    key = "advertising:snap-sar:2026-11-21"
    if defect == "missing_obligation":
        del state["engine"]["obligations"][key]
    elif defect == "provisional":
        state["engine"]["ad_days"][key]["closed"] = False
    else:
        state["engine"]["obligations"][key]["confirmed"] = "11.00"
    assert render(state, sources(descriptor()))["rows"][0]["missing_days"] == ["2026-11-21"]


def test_confirmed_zero_ad_day_is_real_evidence():
    state = base()
    for day in ("2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"):
        add_ad(state, "snap-sar", day, amount="0.00")
    assert render(state, sources(descriptor()))["rows"][0]["state"] == "current"


def test_missing_employee_contract_remains_named_and_unknown():
    state = base()
    data = {"mezan_employees_v2": [{"user_id": "owner", "id": "staff", "name": "موظف التجربة", "status": "active"}]}
    catalog = source_entities(data, state)
    result = render(state, {"automatic_source_entities": catalog, "employees": [], "issues": []})
    row = result["rows"][0]
    assert row["label"] == "موظف التجربة" and row["state"] == "unknown"
    assert row["missing_days"] == [] and row["missing_days_count"] is None


def test_event_source_does_not_invent_one_missing_transaction_each_day():
    state = base()
    inputs = sources(descriptor(kind="courier", identity="carrier"))
    row = render(state, inputs)["rows"][0]
    assert row["missing_days"] == [] and row["missing_days_count"] is None
    assert row["last_financial_day"] is None
    assert row["state"] == "unknown"  # No entity financial evidence is not a proved zero.


def test_new_days_after_saved_reconciliation_are_visible_without_recalculation():
    state = base()
    for day in ("2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23"):
        add_ad(state, "snap-sar", day)
    result = render(state, sources(descriptor()), now="2026-11-26T10:00:00+00:00")
    row = result["rows"][0]
    assert row["missing_days"] == ["2026-11-24", "2026-11-25"]
    assert row["stale_for_seconds"] == 172800


@pytest.mark.parametrize("defect", ["owner", "as_of", "revision", "future", "count"])
def test_foreign_or_unbound_metadata_never_claims_current(defect):
    state = base()
    saved = build_coverage(state, sources(descriptor()), STAMP)
    if defect == "owner": saved["owner_id"] = "foreign"
    if defect == "as_of": saved["as_of"] = "2026-11-23T10:00:00+00:00"
    if defect == "revision": saved["calculation_revision"] = 6
    if defect == "future": saved["as_of"] = "2026-11-25T10:00:00+00:00"
    if defect == "count": saved["total_entities"] = 0
    result = coverage_rows(saved, "owner", STAMP, now=datetime.fromisoformat(STAMP), revision=5)
    assert result["state"] == "unknown" and not result["rows"]


def test_catalog_overflow_is_explicit_instead_of_silently_omitting_entities():
    state = base()
    rows = [descriptor(kind="courier", identity=f"c-{i}") for i in range(MAX_ENTITIES + 1)]
    result = render(state, sources(*rows))
    assert result["state"] == "unknown" and result["omitted_entities"] == 1
    assert len(result["rows"]) == MAX_ENTITIES


def test_source_catalog_rejects_foreign_rows_without_leaking_their_names():
    result = source_entities({"mezan_employees_v2": [
        {"user_id": "other", "id": "bad", "name": "foreign-private-name"}]}, base())
    assert result["complete"] is False
    assert all(row['entity_type'] == 'provider' and row['configured'] is False for row in result['rows'])
    assert "foreign-private-name" not in str(result)


def test_recovery_summary_counts_actual_rows_and_hashes_manual_payloads_only():
    state = base()
    add_ad(state, "snap-sar", "2026-11-21", amount="10.25")
    state["movements"] = [{"id": "manual", "actor_id": "owner", "amount": "9.00", "currency": "SAR",
        "occurred_at": STAMP, "reference": "private-customer-reference", "party_type": "bank", "party_id": "b"}]
    before = recovery_snapshot(state, "2026-11-20", "2026-11-24")
    add_ad(state, "snap-sar", "2026-11-22", amount="5.75")
    after = recovery_snapshot(state, "2026-11-20", "2026-11-24")
    assert before["manual_movements_digest"] == after["manual_movements_digest"]
    assert sum(row["count"] for row in before["obligations"]) == 1
    assert sum(row["count"] for row in after["obligations"]) == 2
    assert {row["confirmed"] for row in after["obligations"]} == {"10.25", "5.75"}
    assert "private-customer-reference" not in str(after)


def add_salary(state, day, amount='100.00'):
    key = 'salary:staff:'+day
    state['engine']['salary_days'][key] = {'amount': amount, 'currency': 'SAR'}
    state['engine']['obligations'][key] = {'id': key, 'party_type': 'employee', 'party_id': 'staff',
        'currency': 'SAR', 'business_date': day, 'confirmed': amount, 'expected': '0.00'}


def test_salary_preserves_approved_cutoff_and_exposes_missing_interior_days():
    state = base()
    inputs = sources(descriptor(kind='employee', identity='staff'))
    inputs['employees'] = [{'id': 'staff', 'currency': 'SAR'}]
    for day in ('2026-11-21', '2026-11-23', '2026-11-24'):
        add_salary(state, day)
    row = render(state, inputs)['rows'][0]
    assert row['missing_days'] == ['2026-11-22']
    assert row['expected_through'] == '2026-11-24'
    assert '2026-11-20' not in row['missing_days']  # Legacy opening-covered cutoff.


def test_stopped_salary_does_not_gain_missing_days_after_contract_end():
    state = base()
    inputs = sources(descriptor(kind='employee', identity='staff'))
    inputs['employees'] = [{'id': 'staff', 'currency': 'SAR', 'effective_to': '2026-11-22'}]
    for day in ('2026-11-21', '2026-11-22'):
        add_salary(state, day, '0.00')  # Explicit unpaid leave still has real evidence.
    row = render(state, inputs, now='2026-11-26T10:00:00+00:00')['rows'][0]
    assert row['missing_days'] == [] and row['missing_days_count'] == 0
    assert row['expected_through'] == '2026-11-22'


def test_last_complete_match_survives_a_later_incomplete_reconciliation():
    state, inputs = base(), sources(descriptor())
    for day in ('2026-11-20', '2026-11-21', '2026-11-22', '2026-11-23'):
        add_ad(state, 'snap-sar', day)
    state['engine']['automatic_source_coverage'] = build_coverage(state, inputs, STAMP)
    state['revision'] = 5
    later = '2026-11-25T10:00:00+00:00'
    state['engine']['as_of'] = later
    saved = build_coverage(state, inputs, later)
    row = coverage_rows(saved, 'owner', later, now=datetime.fromisoformat(later), revision=6)['rows'][0]
    assert row['last_success_at'] == STAMP
    assert row['stale_for_seconds'] == 86400 and row['missing_days'] == ['2026-11-24']


@pytest.mark.parametrize('code', ['operational_order_bank_occurrence_unproven',
    'operational_order_bank_in_progress_history_missing', 'operational_order_bank_in_progress_unproven'])
def test_bank_history_and_occurrence_gaps_cannot_be_green(code):
    state = base()
    state['movements'] = [{'id': 'a', 'automatic_order_bank': True, 'party_type': 'bank', 'party_id': 'account:rajhi',
        'amount': '12.00', 'currency': 'SAR', 'occurred_at': '2026-11-21T10:00:00+00:00'}]
    state['engine']['issues'] = [{'code': code, 'party_type': 'bank', 'party_id': 'account:rajhi'}]
    row = render(state, sources(descriptor(kind='bank', identity='account:rajhi')))['rows'][0]
    assert row['state'] == 'unknown' and row['last_financial_day'] == '2026-11-21'
    assert row['missing_days_count'] is None  # Daily bank income is not an entitlement.


def test_driver_original_date_hold_is_scoped_to_its_named_driver():
    state = base()
    for identity in ('held-driver', 'other-driver'):
        state['engine']['obligations']['shipping:' + identity] = {
            'id': 'shipping:' + identity, 'kind': 'shipping', 'party_type': 'store_driver',
            'party_id': identity, 'currency': 'SAR', 'confirmed': '10.00', 'expected': '0.00',
            'direction': 'payable', 'business_date': '2026-11-21'}
    state['engine']['issues'] = [{'code': 'operational_shipping_original_date_unproven',
        'party_type': 'store_driver', 'party_id': 'held-driver'}]
    rows = render(state, sources(*(descriptor(kind='store_driver', identity=value)
        for value in ('held-driver', 'other-driver'))))['rows']
    assert {row['entity_id']: row['state'] for row in rows} == {'held-driver': 'unknown', 'other-driver': 'current'}


def test_unbooked_provider_date_hold_is_named_unknown_before_its_first_claim():
    state = base()
    state['engine']['issues'] = [{'code': 'provider_original_capture_date_unproven',
        'party_type': 'provider', 'party_id': 'tabby'}]
    catalog = source_entities({}, state, {'orders': [{'id': 'new', 'currency': 'USD',
        'payment': {'provider': 'tabby'}}]})
    result = render(state, {'automatic_source_entities': catalog})
    row = next(row for row in result['rows'] if row['entity_type'] == 'provider'
        and row['entity_id'] == 'tabby' and row['currency'] == 'USD')
    assert row['label'] == 'تابي' and row['state'] == 'unknown'
    assert row['missing_days_count'] is None and row['last_financial_day'] is None


def test_catalog_keeps_unbound_ads_settings_carriers_and_pending_banks_named():
    state = base()
    data = {'settings': [{'user_id': 'owner', 'shipping_companies': [{'name': 'Aramex'}],
                         'payment_methods': [{'name': 'تابي'}]}],
        'mezan_integration_accounts_v2': [{'user_id': 'owner', 'mezan_integration_account_id': 'integration',
            'external_account_id': 'ad', 'display_name': 'Snap USD', 'provider': 'snapchat_ads',
            'currency': 'USD', 'timezone': 'America/Los_Angeles', 'connection_status': 'connected'}]}
    catalog = source_entities(data, state, {'orders': [{'bank_credit': {
        'bank_id': 'operational-bank:bank', 'bank_name': 'بنك التجربة', 'occurrence_issue': 'unproven'}}]})
    rows = render(state, {'automatic_source_entities': catalog})['rows']
    assert any(row['label'] == 'Snap USD' and row['state'] == 'unknown' for row in rows)
    assert any(row['entity_type'] == 'courier' and row['state'] == 'unknown' for row in rows)
    assert any(row['entity_id'] == 'operational-bank:bank' and row['label'] == 'بنك التجربة' for row in rows)
    assert next(row for row in rows if row['entity_id'] == 'tabby')['state'] == 'unknown'
    assert next(row for row in rows if row['entity_id'] == 'tamara')['state'] == 'inactive'


def test_recurring_coverage_requires_the_original_due_period_and_amount():
    state = base()
    inputs = sources(descriptor(kind='recurring', identity='rent'))
    inputs['recurring'] = [{'id': 'rent:period', 'obligation_id': 'rent', 'due_at': '2026-11-20', 'amount': '100.00'}]
    state['engine']['obligations']['recurring:rent:period'] = {'id': 'recurring:rent:period', 'currency': 'SAR',
        'business_date': '2026-11-20', 'confirmed': '20.00', 'expected': '80.00'}
    assert render(state, inputs)['rows'][0]['state'] == 'current'
    state['engine']['obligations']['recurring:rent:period']['business_date'] = '2026-11-24'
    assert render(state, inputs)['rows'][0]['state'] == 'unknown'


def test_recovery_summary_uses_original_bank_day_and_reports_duplicates_without_references():
    state = base()
    movement = {'id': 'bank-row', 'request_id': 'order-bank:private-order', 'order_number': 'private-order',
        'bank_transaction_reference': 'private-bank-reference', 'automatic_order_bank': True, 'party_type': 'bank',
        'party_id': 'bank', 'currency': 'SAR', 'direction': 'incoming', 'amount': '12.25',
        'occurred_at': '2026-11-21T20:30:00+00:00', 'recorded_at': STAMP}
    state['movements'] = [movement, {**movement, 'id': 'duplicate'}]
    result = recovery_snapshot(state, '2026-11-20', '2026-11-24')
    assert result['automatic_movements'] == [{'entity_type': 'bank', 'entity_id': 'bank', 'currency': 'SAR',
        'direction': 'incoming', 'business_date': '2026-11-21', 'count': 2, 'amount': '24.50'}]
    assert result['duplicate_automatic_keys'] == {'request_id': 1, 'order_number': 1, 'bank_transaction_reference': 1}
    assert result['manual_movement_count'] == 0
    assert 'private-' not in str(result)
