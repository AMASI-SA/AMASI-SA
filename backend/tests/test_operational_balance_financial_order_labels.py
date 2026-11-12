"""Canonical labels are bounded owner reads and never financial calculations."""
import asyncio
from copy import deepcopy

import pytest

import operational_financial_order_labels as labels


PROJECTION = {
    "_id": 0, "user_id": 1,
    "raw_by_source.salla_direct.id": 1,
    "raw_by_source.salla_direct.reference_id": 1,
    "raw_by_source.salla_direct.order_number": 1,
    "g47_salla_snapshot.requires_authoritative_refresh": 1,
}


def source(identity, reference=None, *, owner="owner", number=None, stale=False):
    return {"user_id": owner, "raw_by_source": {"salla_direct": {
        "id": identity, "reference_id": reference, "order_number": number}},
        "g47_salla_snapshot": {"requires_authoritative_refresh": stale},
        "customer": {"name": "PRIVATE CUSTOMER", "phone": "PRIVATE PHONE"}}


def report(*identities):
    return {"schema_version": 1, "authority": "operational_balance_report", "status": "active",
            "as_of": "2026-11-01T09:00:00+03:00", "currencies": {"SAR": {
                "metrics": {"monetary_position": "9007199254740992.03"},
                "tables": [{"id": "couriers", "totals": {"payable": "15.00"}}],
                "deliveries": {"complete": True, "notes": ["Existing source scope note."], "rows": [
                    {"id": "delivery:" + str(identity), "order_id": identity, "order_number": None,
                     "shipping_confirmed": "15.00", "shipping_settled": "5.00",
                     "shipping_outstanding": "10.00", "collection_amount": "100.00",
                     "driver_custody": "0.00", "collection_settled": "20.00",
                     "collection_outstanding": "80.00", "store_collection": None,
                     "customer_outstanding": "100.00"} for identity in identities]}}}}


class ReadOnlyDB:
    def __init__(self, records=(), *, inject_unscoped=False):
        self.records, self.inject_unscoped = deepcopy(records), inject_unscoped
        self.calls = []

    def __getitem__(self, name):
        assert name == "unified_orders", "Only canonical identity metadata may be read"
        return self

    def find(self, query, projection):
        assert projection == PROJECTION, "Do not read customer or financial data"
        assert set(query) == {"user_id", "raw_by_source.salla_direct.id"}
        assert set(query["raw_by_source.salla_direct.id"]) == {"$in"}
        self.calls.append({"query": deepcopy(query), "projection": deepcopy(projection)})
        candidates = query["raw_by_source.salla_direct.id"]["$in"]
        rows = self.records if self.inject_unscoped else [row for row in self.records
            if row["user_id"] == query["user_id"] and any(
                type(row["raw_by_source"]["salla_direct"]["id"]) is type(value)
                and row["raw_by_source"]["salla_direct"]["id"] == value for value in candidates)]
        # Return exactly the requested narrow projection, as Mongo would.
        self.selected = [{key: deepcopy(row[key]) for key in ("user_id", "raw_by_source", "g47_salla_snapshot")}
                         for row in rows]
        return self

    def limit(self, count):
        self.calls[-1]["limit"] = count
        self.selected = self.selected[:count]
        return self

    async def to_list(self, length):
        self.calls[-1]["length"] = length
        return deepcopy(self.selected[:length])

    def __getattr__(self, name):
        raise AssertionError("Unexpected read or write: " + name)


def attach(db, value, owner="owner"):
    return asyncio.run(labels.attach_order_numbers(db, owner, value))


def rows(value):
    return value["currencies"]["SAR"]["deliveries"]["rows"]


def without_label_metadata(value):
    result = deepcopy(value)
    for currency in result["currencies"].values():
        delivery = currency["deliveries"]
        delivery.pop("complete", None)
        delivery.pop("notes", None)
        for row in delivery["rows"]:
            row.pop("order_number", None)
    return result


def test_owner_scoped_mixed_numeric_ids_preserve_every_monetary_value():
    value = report("100", "200", "0003", "order-four")
    db = ReadOnlyDB([source(100, 501), source("200", "00502"), source("0003", "503"),
                     source("order-four", number="504"), source(100, "OTHER-TENANT", owner="other")])
    before = deepcopy(value)
    result = attach(db, value)
    assert [row["order_number"] for row in rows(result)] == ["501", "00502", "503", "504"]
    assert db.calls[0]["query"]["user_id"] == "owner"
    variants = db.calls[0]["query"]["raw_by_source.salla_direct.id"]["$in"]
    assert "100" in variants and 100 in variants and "200" in variants and 200 in variants
    assert "0003" in variants and 3 not in variants
    assert db.calls[0]["limit"] == db.calls[0]["length"] == labels.MAX_ROWS + 1
    assert "PRIVATE" not in str(result) and "OTHER-TENANT" not in str(result)
    assert without_label_metadata(result) == without_label_metadata(before)
    assert value == before and result is not value
    assert attach(db, result) == result


def test_empty_details_do_not_access_database():
    db = ReadOnlyDB()
    value = report()
    assert attach(db, value) == value
    assert db.calls == []
    assert attach(db, {"status": "draft", "currencies": {}}) == {"status": "draft", "currencies": {}}


@pytest.mark.parametrize("identity", [None, True, 1.5, "", " 100", "100\n"])
def test_invalid_order_identity_is_unavailable_without_query(identity):
    value = report(identity)
    db = ReadOnlyDB()
    result = attach(db, value)
    assert db.calls == []
    assert rows(result)[0]["order_number"] is None
    assert result["currencies"]["SAR"]["deliveries"]["complete"] is False
    assert without_label_metadata(result) == without_label_metadata(value)


@pytest.mark.parametrize("records", [
    [],
    [source("100", None)],
    [source("100", "501", stale=True)],
    [source("100", "501"), source(100, "501")],
    [source("100", True)],
])
def test_missing_stale_or_ambiguous_label_is_not_guessed(records):
    value = report("100")
    result = attach(ReadOnlyDB(records), value)
    details = result["currencies"]["SAR"]["deliveries"]
    assert details["rows"][0]["order_number"] is None
    assert details["complete"] is False and any("غير متاح" in note for note in details["notes"])
    assert without_label_metadata(result) == without_label_metadata(value)


def test_ambiguous_number_does_not_conflate_two_canonical_orders():
    result = attach(ReadOnlyDB([source("100", "501"), source("200", "501")]), report("100", "200"))
    assert [row["order_number"] for row in rows(result)] == [None, None]


def test_invalid_one_order_does_not_erase_a_separate_proven_label():
    result = attach(ReadOnlyDB([source("100", "501", stale=True), source("200", "502")]), report("100", "200"))
    assert [row["order_number"] for row in rows(result)] == [None, "502"]


def test_unexpected_cross_tenant_result_fails_closed_without_revealing_label():
    value = report("100")
    result = attach(ReadOnlyDB([source("100", "PRIVATE FOREIGN NUMBER", owner="other")], inject_unscoped=True), value)
    assert rows(result)[0]["order_number"] is None
    assert "PRIVATE" not in str(result)
    assert without_label_metadata(result) == without_label_metadata(value)


def test_large_numeric_strings_never_become_unsafe_numeric_query_variants():
    identity = "9007199254740992"
    db = ReadOnlyDB([source(identity, "501")])
    result = attach(db, report(identity))
    assert rows(result)[0]["order_number"] == "501"
    assert db.calls[0]["query"]["raw_by_source.salla_direct.id"]["$in"] == [identity]


def test_too_many_requested_ids_do_not_trigger_an_unbounded_query(monkeypatch):
    monkeypatch.setattr(labels, "MAX_ROWS", 2)
    db = ReadOnlyDB()
    value = report("100", "200", "300")
    result = attach(db, value)
    assert db.calls == []
    assert all(row["order_number"] is None for row in rows(result))
    assert result["currencies"]["SAR"]["deliveries"]["complete"] is False
    assert without_label_metadata(result) == without_label_metadata(value)


def test_truncated_duplicate_query_cannot_certify_partial_labels(monkeypatch):
    monkeypatch.setattr(labels, "MAX_ROWS", 2)
    db = ReadOnlyDB([source("100", "501"), source("200", "502"), source(100, "501")])
    result = attach(db, report("100", "200"))
    assert all(row["order_number"] is None for row in rows(result))
    assert db.calls[0]["limit"] == db.calls[0]["length"] == 3


def test_missing_owner_cannot_read_an_unscoped_label():
    db = ReadOnlyDB([source("100", "501")])
    result = attach(db, report("100"), owner="")
    assert db.calls == [] and rows(result)[0]["order_number"] is None


def pending(identity, number='unverified saved label'):
    return {'order_id': identity, 'order_number': number,
            'reasons': [{'code': 'missing_tariff', 'message': 'أجرة تحتاج مراجعة'}]}


def exceptions_report():
    value = report('100')
    value['currencies']['SAR']['tables'] = [
        {'id': 'couriers', 'complete': True, 'notes': [], 'totals': {'payable': '1234567890123456.78'},
         'rows': [{'id': 'courier:one:SAR', 'complete': True, 'payable': '1234567890123456.78',
                   'pending_orders': 1, 'pending_order_details': [pending('100')]}],
         'unassigned_order_details': [pending('200')]},
        {'id': 'banks_network', 'complete': True, 'notes': [], 'totals': {'actual': '500.00'},
         'rows': [{'id': 'bank:one:SAR', 'actual': '500.00', 'complete': True}]},
    ]
    value['parties'] = [
        {'id': 'courier-one', 'outstanding_payable': '1234567890123456.78', 'complete': True,
         'pending_orders': 2, 'pending_order_details': [pending('100'), pending('200')]},
        {'id': 'supplier-one', 'outstanding_payable': '5.00', 'complete': True},
    ]
    return value


def money_and_evidence(value):
    if isinstance(value, dict):
        return {key: money_and_evidence(item) for key, item in value.items() if key not in {'order_number', 'complete', 'notes'}}
    if isinstance(value, list):
        return [money_and_evidence(item) for item in value]
    return value


def test_all_exception_surfaces_share_one_exact_identity_query_without_duplicate_ids():
    value = exceptions_report()
    db = ReadOnlyDB([source('100', '501'), source(200, '502'), source('100', 'FOREIGN', owner='other')])
    before = deepcopy(value)
    result = attach(db, value)
    assert len(db.calls) == 1
    assert db.calls[0]['query']['raw_by_source.salla_direct.id']['$in'] == ['100', 100, '200', 200]
    table = result['currencies']['SAR']['tables'][0]
    assert rows(result)[0]['order_number'] == table['rows'][0]['pending_order_details'][0]['order_number'] == '501'
    assert table['unassigned_order_details'][0]['order_number'] == '502'
    assert [item['order_number'] for item in result['parties'][0]['pending_order_details']] == ['501', '502']
    assert money_and_evidence(result) == money_and_evidence(value)
    assert value == before and 'FOREIGN' not in str(result)


def test_main_party_report_without_delivery_details_still_enriches_exception_numbers():
    value = {'complete': True, 'parties': [{'party_type': 'courier', 'party_id': 'one', 'complete': True,
        'outstanding_payable': '150.25', 'pending_order_details': [pending('100')]}]}
    db = ReadOnlyDB([source('100', '501')])
    result = attach(db, value)
    assert len(db.calls) == 1
    assert result['parties'][0]['pending_order_details'][0]['order_number'] == '501'
    assert money_and_evidence(result) == money_and_evidence(value)


def test_missing_number_marks_only_its_counterparty_and_table_and_preserves_all_money():
    value = exceptions_report()
    result = attach(ReadOnlyDB([source('100', '501'), source('200', 'stale', stale=True)]), value)
    shipping, bank = result['currencies']['SAR']['tables']
    assert shipping['complete'] is False and shipping['rows'][0]['complete'] is True
    assert shipping['unassigned_order_details'][0]['order_number'] is None
    assert any('تحديث موثق' in text for text in shipping['notes'])
    assert bank == value['currencies']['SAR']['tables'][1]
    assert result['currencies']['SAR']['deliveries']['complete'] is True
    assert result['parties'][0]['complete'] is False
    assert result['parties'][1] == value['parties'][1]
    assert money_and_evidence(result) == money_and_evidence(value)


def test_limit_never_drops_pending_orders_or_certifies_only_the_first_batch(monkeypatch):
    monkeypatch.setattr(labels, 'MAX_ROWS', 2)
    value = exceptions_report()
    value['parties'][0]['pending_order_details'].append(pending('300'))
    db = ReadOnlyDB([source('100', '501'), source('200', '502'), source('300', '503')])
    result = attach(db, value)
    assert db.calls == []
    assert len(result['parties'][0]['pending_order_details']) == 3
    assert all(item['order_number'] is None for item in result['parties'][0]['pending_order_details'])
    assert result['parties'][0]['complete'] is False
    assert money_and_evidence(result) == money_and_evidence(value)


@pytest.mark.parametrize('error_kind', ['mongo', 'timeout'])
def test_label_lookup_failure_keeps_saved_finance_visible_with_qualified_labels(error_kind):
    from pymongo.errors import OperationFailure
    class Unavailable(ReadOnlyDB):
        async def to_list(self, length):
            raise OperationFailure('synthetic source unavailable') if error_kind == 'mongo' else asyncio.TimeoutError()
    value = exceptions_report()
    db = Unavailable([source('100', '501'), source('200', '502')])
    result = attach(db, value)
    assert len(db.calls) == 1
    assert result['currencies']['SAR']['tables'][0]['complete'] is False
    assert result['currencies']['SAR']['tables'][1] == value['currencies']['SAR']['tables'][1]
    assert result['parties'][0]['complete'] is False
    assert all(item['order_number'] is None for item in result['parties'][0]['pending_order_details'])
    assert money_and_evidence(result) == money_and_evidence(value)


def test_unknown_exception_id_never_uses_a_saved_number_as_proof():
    value = {'parties': [{'complete': True, 'outstanding_payable': '150.25',
                         'pending_order_details': [pending(None, 'apparently-valid-number')]}]}
    db = ReadOnlyDB([source('100', '501')])
    result = attach(db, value)
    assert db.calls == []
    assert result['parties'][0]['pending_order_details'][0]['order_number'] is None
    assert result['parties'][0]['complete'] is False
    assert money_and_evidence(result) == money_and_evidence(value)


def test_main_unassigned_currency_family_groups_share_the_party_identity_read():
    value = {'complete': True, 'parties': [{'complete': True, 'actual': '150.25',
        'pending_order_details': [pending('100')]}], 'unassigned_order_details': {
            'SAR': {'courier': [pending('100')], 'store_driver': [pending('200')]},
            'USD': {'courier': [pending('300')], 'store_driver': []}}}
    db = ReadOnlyDB([source('100', '501'), source('200', '502')])
    result = attach(db, value)
    assert len(db.calls) == 1
    grouped = result['unassigned_order_details']
    assert grouped['SAR']['courier'][0]['order_number'] == result['parties'][0]['pending_order_details'][0]['order_number'] == '501'
    assert grouped['SAR']['store_driver'][0]['order_number'] == '502'
    assert grouped['USD']['courier'][0]['order_number'] is None
    assert result['complete'] is False and result['parties'][0]['complete'] is True
    assert money_and_evidence(result) == money_and_evidence(value)
