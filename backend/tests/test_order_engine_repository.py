"""Repository tests for the Mezan Order Engine."""

from copy import deepcopy

import pytest

from order_engine.mapper import map_salla_order
from order_engine.repository import MongoOrderRepository


class FakeAsyncCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, fields):
        for key, direction in reversed(fields):
            self.rows.sort(
                key=lambda row: str(row.get(key) or ""),
                reverse=direction < 0,
            )
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    def __aiter__(self):
        self._iterator = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class FakeCollection:
    def __init__(self, rows):
        self.rows = deepcopy(rows)
        self.last_query = None
        self.last_projection = None
        self.write_calls = 0

    def find(self, query, projection):
        self.last_query = deepcopy(query)
        self.last_projection = deepcopy(projection)

        rows = [
            row for row in self.rows
            if row.get("user_id") == query.get("user_id")
            and isinstance(
                (row.get("raw_by_source") or {}).get("salla_direct"),
                dict,
            )
        ]

        requested_numbers = (query.get("order_number") or {}).get("$in")
        if requested_numbers is not None:
            rows = [
                row for row in rows
                if row.get("order_number") in set(requested_numbers)
            ]

        conditions = query.get("$or") or []
        if not conditions:
            for clause in query.get("$and") or []:
                if isinstance(clause, dict) and isinstance(clause.get("$or"), list):
                    conditions = clause["$or"]
                    break

        if conditions:
            before_date = conditions[0]["order_date"]["$lt"]
            exact_date = conditions[1]["order_date"]
            before_number = conditions[1]["order_number"]["$lt"]

            rows = [
                row for row in rows
                if (
                    str(row.get("order_date") or "") < before_date
                    or (
                        str(row.get("order_date") or "") == exact_date
                        and str(row.get("order_number") or "")
                        < before_number
                    )
                )
            ]

        return FakeAsyncCursor(rows)

    async def find_one(self, query, projection):
        self.last_query = deepcopy(query)
        self.last_projection = deepcopy(projection)

        for row in self.rows:
            if (
                row.get("user_id") == query.get("user_id")
                and row.get("order_number") == query.get("order_number")
                and isinstance(
                    (row.get("raw_by_source") or {}).get("salla_direct"),
                    dict,
                )
            ):
                return deepcopy(row)

        return None

    async def insert_one(self, *args, **kwargs):
        self.write_calls += 1
        raise AssertionError("repository must not write")

    async def update_one(self, *args, **kwargs):
        self.write_calls += 1
        raise AssertionError("repository must not write")


class FakeDB:
    def __init__(self, rows):
        self.unified_orders = FakeCollection(rows)


@pytest.fixture
def rows():
    return [
        {
            "user_id": "u1",
            "order_number": "300",
            "order_date": "2026-07-13",
            "raw_by_source": {
                "salla_direct": {
                    "id": "300",
                    "reference_id": "300",
                }
            },
        },
        {
            "user_id": "u1",
            "order_number": "200",
            "order_date": "2026-07-12",
            "raw_by_source": {
                "salla_direct": {
                    "id": "200",
                    "reference_id": "200",
                }
            },
        },
        {
            "user_id": "u1",
            "order_number": "legacy",
            "order_date": "2026-07-11",
            "raw_by_source": {
                "make": {"order_number": "legacy"},
            },
        },
        {
            "user_id": "other",
            "order_number": "999",
            "order_date": "2026-07-14",
            "raw_by_source": {
                "salla_direct": {
                    "id": "999",
                    "reference_id": "999",
                }
            },
        },
    ]


@pytest.mark.asyncio
async def test_repository_lists_only_tenant_salla_rows(rows):
    db = FakeDB(rows)
    repository = MongoOrderRepository(db)

    result = await repository.list_salla_orders(
        user_id="u1",
        limit=10,
    )

    assert [row.order_number for row in result] == ["300", "200"]
    assert db.unified_orders.write_calls == 0


@pytest.mark.asyncio
async def test_repository_applies_keyset_cursor(rows):
    db = FakeDB(rows)
    repository = MongoOrderRepository(db)

    result = await repository.list_salla_orders(
        user_id="u1",
        limit=10,
        before_order_date="2026-07-13",
        before_order_number="300",
    )

    assert [row.order_number for row in result] == ["200"]


@pytest.mark.asyncio
async def test_repository_gets_exact_order(rows):
    db = FakeDB(rows)
    repository = MongoOrderRepository(db)

    result = await repository.get_salla_order(
        user_id="u1",
        order_number="200",
    )

    assert result is not None
    assert result.order_number == "200"


@pytest.mark.asyncio
async def test_repository_gets_exact_order_set_with_one_query(rows):
    db = FakeDB(rows)
    repository = MongoOrderRepository(db)

    result = await repository.get_salla_orders(
        user_id="u1",
        order_numbers=["300", "200", "300", "999"],
    )

    assert {row.order_number for row in result} == {"300", "200"}
    assert db.unified_orders.last_query["order_number"] == {
        "$in": ["300", "200", "999"],
    }
    assert db.unified_orders.write_calls == 0


@pytest.mark.asyncio
async def test_repository_rejects_cross_tenant_order(rows):
    db = FakeDB(rows)
    repository = MongoOrderRepository(db)

    result = await repository.get_salla_order(
        user_id="u1",
        order_number="999",
    )

    assert result is None


def test_repository_has_no_write_methods():
    forbidden = {
        "insert_one",
        "insert_many",
        "update_one",
        "update_many",
        "delete_one",
        "delete_many",
        "replace_one",
        "bulk_write",
    }

    assert set(dir(MongoOrderRepository)).isdisjoint(forbidden)



@pytest.mark.asyncio
async def test_repository_rehydrates_shipping_from_v2_root_snapshot():
    rows = [{
        "user_id": "u1",
        "order_number": "274682897",
        "order_date": "2026-07-28",
        "customer_name": "عبدالله جمعي",
        "customer_mobile": "561752841",
        "shipping_company": "iMile",
        "shipping_company_code": "imile",
        "shipping_method": "shipping",
        "shipping_city": "الرياض",
        "customer_city": "الرياض",
        "shipping_district": "العليا",
        "shipping_street": "طريق الملك فهد",
        "shipping_country": "السعودية",
        "shipping_postal_code": "12262",
        "shipping_short_address": "RRRD2929",
        "shipping_address_raw": {
            "city": "الرياض",
            "block": "العليا",
            "street_number": "طريق الملك فهد",
            "country": "السعودية",
        },
        "payment_receipt_url": "https://cdn.salla.sa/receipt.jpg",
        # Simulates the modern/light Order Details snapshot that replaced the
        # richer provider payload after an explicit review refresh.
        "raw_by_source": {
            "salla_direct": {
                "id": "901",
                "reference_id": "274682897",
                "date": "2026-07-28T09:00:00+03:00",
                "customer": {
                    "full_name": "عبدالله جمعي",
                    "mobile": "561752841",
                },
                "payment_method": "credit_card",
                "amounts": {"total": {"amount": 127.60, "currency": "SAR"}},
            },
        },
    }]
    db = FakeDB(rows)
    repository = MongoOrderRepository(db)

    result = await repository.get_salla_order(
        user_id="u1",
        order_number="274682897",
    )

    assert result is not None
    dto = map_salla_order(result.salla_raw)
    assert dto.shipping.company == "iMile"
    assert dto.shipping.company_code == "imile"
    assert dto.shipping.method == "shipping"
    assert dto.shipping.address is not None
    assert dto.shipping.address.country == "السعودية"
    assert dto.shipping.address.city == "الرياض"
    assert dto.shipping.address.district == "العليا"
    assert dto.shipping.address.street == "طريق الملك فهد"
    assert dto.shipping.address.postal_code == "12262"
    assert dto.shipping.address.short_address == "RRRD2929"
    assert dto.customer.shipping_address.city == "الرياض"
    assert dto.payment.receipt_url == "https://cdn.salla.sa/receipt.jpg"

    projection = db.unified_orders.last_projection
    assert projection["shipping_address_raw"] == 1
    assert projection["shipping_company"] == 1
    assert projection["shipping_city"] == 1


@pytest.mark.asyncio
async def test_repository_keeps_provider_address_over_root_fallback():
    rows = [{
        "user_id": "u1",
        "order_number": "274724433",
        "order_date": "2026-07-28",
        "shipping_city": "الرياض",
        "shipping_district": "العليا",
        "raw_by_source": {
            "salla_direct": {
                "id": "902",
                "reference_id": "274724433",
                "date": "2026-07-28T10:00:00+03:00",
                "shipping_address": {
                    "city": "جدة",
                    "block": "الروضة",
                    "street_number": "شارع الأمير",
                },
                "amounts": {"total": {"amount": 134, "currency": "SAR"}},
            },
        },
    }]
    repository = MongoOrderRepository(FakeDB(rows))

    result = await repository.get_salla_order(user_id="u1", order_number="274724433")
    dto = map_salla_order(result.salla_raw)

    assert dto.shipping.address.city == "جدة"
    assert dto.shipping.address.district == "الروضة"
    assert dto.shipping.address.street == "شارع الأمير"
