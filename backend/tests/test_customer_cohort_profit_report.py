from datetime import date

import pytest

from customer_cohort_profit_report import (
    build_customer_cohort_report,
    build_customer_cohort_report_from_rows,
)
from mezan_attribution_owner_routes import make_mezan_attribution_owner_router
from mezan_attribution_order_ledger import LEDGER_COLLECTION


def _order(number, when, customer_id, *, status="delivered", total=100):
    return {
        "order_number": number,
        "order_date": when,
        "order_status": status,
        "total_amount_sar": total,
        "data_source": "salla_direct",
        "raw_by_source": {
            "salla_direct": {"customer": {"id": customer_id, "name": "خاص"}}
        },
    }


def _ledger(
    number,
    *,
    quality="confirmed",
    safe=True,
    source="snapchat",
    revenue=100,
    cogs=30,
    spend=10,
):
    return {
        "order_key": number,
        "attribution": {
            "quality": quality,
            "decision_safe": safe,
            "marketing_source": source,
        },
        "profit": {
            "known": revenue is not None and cogs is not None and spend is not None,
            "revenue_sar": revenue,
            "cogs_sar": cogs,
            "allocated_ad_spend_sar": spend,
        },
    }


def test_report_builds_new_and_returning_cohorts_without_customer_rows():
    orders = [
        _order("A1", "2026-09-10", "customer-a", total=150),
        _order("A2", "2026-09-18", "customer-a", total=100),
        _order("B1", "2026-08-01", "customer-b", total=200),
        _order("B2", "2026-09-01", "customer-b", total=120),
        _order("B3", "2026-09-15", "customer-b", total=80),
    ]
    ledgers = [
        _ledger("A1", spend=20),
        _ledger("A2", revenue=100, cogs=30, spend=10),
        _ledger("B1", quality="unattributed", safe=False, source="direct", spend=None),
        _ledger("B2", revenue=120, cogs=50, spend=10),
        _ledger("B3", revenue=80, cogs=20, spend=5),
    ]

    result = build_customer_cohort_report_from_rows(
        orders,
        ledgers,
        as_of=date(2026, 9, 20),
        included_statuses=["delivered"],
    )

    recent, older = result["cohorts"]
    assert recent["customers"] == 1
    assert recent["first_order_sales_sar"] == 150
    assert recent["first_acquisition_cost"]["known_amount_sar"] == 20
    assert recent["later_order_contribution_profit"]["known_amount_sar"] == 60
    assert older["customers"] == 1
    assert older["first_order_sources"]["explicit_non_ad"] == 1
    assert older["later_order_contribution_profit"]["known_amount_sar"] == 115
    assert result["returning"]["last_30_days"]["customers"] == 2
    assert result["returning"]["last_30_days"]["orders"] == 3
    assert result["summary"]["first_order_sources"] == {
        "confirmed_ad": 1,
        "explicit_non_ad": 1,
        "unresolved": 0,
    }
    assert result["privacy"]["customer_identifiers_returned"] is False
    assert "customer-a" not in str(result)
    assert "خاص" not in str(result)


def test_unknown_profit_and_acquisition_cost_are_never_zero_filled():
    later = _ledger("A2", revenue=100, cogs=40, spend=10)
    later["profit"]["known"] = False
    result = build_customer_cohort_report_from_rows(
        [
            _order("A1", "2026-09-01", "a"),
            _order("A2", "2026-09-10", "a"),
        ],
        [
            _ledger("A1", spend=None),
            later,
        ],
        as_of=date(2026, 9, 20),
        included_statuses=["delivered"],
    )

    acquisition = result["summary"]["first_acquisition_cost"]
    contribution = result["summary"]["later_order_contribution_profit"]
    assert acquisition["known_orders"] == 0
    assert acquisition["unknown_orders"] == 1
    assert acquisition["known_amount_sar"] == 0
    assert acquisition["unknown_is_zero"] is False
    assert contribution["known_orders"] == 0
    assert contribution["unknown_orders"] == 1
    assert contribution["partial"] is True


def test_report_excludes_non_salla_cancelled_future_and_unidentified_orders():
    orders = [
        _order("OK", "2026-09-01", "known"),
        _order("CANCEL", "2026-09-02", "cancelled", status="cancelled"),
        _order("FUTURE", "2026-09-21", "future"),
        {**_order("NO-ID", "2026-09-03", ""), "raw_by_source": {"salla_direct": {}}},
        {**_order("EXCEL", "2026-09-04", "excel"), "data_source": "excel", "raw_by_source": {}},
    ]

    result = build_customer_cohort_report_from_rows(
        orders, [], as_of=date(2026, 9, 20), included_statuses=["delivered"]
    )

    assert result["summary"]["identified_customers"] == 1
    assert result["coverage"]["salla_orders"] == 4
    assert result["coverage"]["excluded_status_orders"] == 1
    assert result["coverage"]["future_orders_excluded"] == 1
    assert result["coverage"]["missing_customer_identity"] == 1
    assert result["coverage"]["ledger_unmatched_orders"] == 1


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, *_args):
        return self

    def limit(self, value):
        self.rows = self.rows[:value]
        return self

    async def to_list(self, length):
        return self.rows[:length]


class _Collection:
    def __init__(self, rows):
        self.rows = rows
        self.find_calls = []
        self.write_calls = 0

    def find(self, query, projection):
        self.find_calls.append((query, projection))
        return _Cursor(self.rows)

    def __getattr__(self, name):
        if name in {"insert_one", "update_one", "delete_one", "replace_one"}:
            self.write_calls += 1
            raise AssertionError("read-only report attempted a write")
        raise AttributeError(name)


class _DB:
    def __init__(self, orders, ledgers):
        self.unified_orders = _Collection(orders)
        self.ledger = _Collection(ledgers)

    def __getitem__(self, name):
        assert name == LEDGER_COLLECTION
        return self.ledger


@pytest.mark.asyncio
async def test_database_report_is_tenant_scoped_bounded_and_read_only():
    db = _DB(
        [_order("A1", "2026-09-01", "a")],
        [_ledger("A1")],
    )

    result = await build_customer_cohort_report(
        db,
        "tenant-1",
        as_of=date(2026, 9, 20),
        included_statuses=["delivered"],
        max_orders=10,
    )

    assert db.unified_orders.find_calls[0][0]["user_id"] == "tenant-1"
    assert db.ledger.find_calls[0][0]["user_id"] == "tenant-1"
    assert db.unified_orders.write_calls == 0
    assert db.ledger.write_calls == 0
    assert result["read_only"] is True
    assert result["guardrails"]["external_writes"] is False


def test_owner_router_exposes_get_only_customer_cohort_endpoint():
    router = make_mezan_attribution_owner_router(object(), lambda: {})
    route = next(
        item for item in router.routes
        if item.path == "/mezan-attribution-v1/customer-cohorts"
    )
    assert route.methods == {"GET"}
