"""Refresh-only profit source: exact order cohort, partial days and bounded failure."""
import asyncio
from copy import deepcopy
from decimal import Decimal

import pytest

import operational_profit_sources as source
from operational_payment_evidence import _instant
from operational_profit_projection import project_profit

START = "2026-11-01T09:00:00+03:00"
END = "2026-11-03T12:00:00+03:00"


def order(identity, created, **changes):
    row = {"user_id": "owner", "salla_order_id": identity, "order_number": identity,
           "currency": "SAR", "total_amount": 100, "order_status": "under_review", **changes}
    raw = {"user_id": "owner", "order_number": identity, "raw_by_source": {"salla_direct": {
        "id": identity, "reference_id": identity, "date": created,
        "updated_at": "2026-11-02T10:00:00+03:00", "currency": "SAR", "total": {"amount": "100", "currency": "SAR"}}}}
    return row, raw


def values():
    return {"sales": {"total_sar": 100, "known_total_sar": 100, "conversion_complete": True},
        "products": {"total": 20, "missing_products_count": 0, "incomplete_orders_count": 0},
        "order_costs": {"shipping": {"known": Decimal(5), "known_orders": 1, "pending_orders": 0},
                        "payment_fees": {"known": Decimal(2), "known_orders": 1, "pending_orders": 0}, "adjusted_orders": 0},
        "ads": {"total": 10, "known_subtotal_sar": 10, "spend_quality": {"amount_complete": True},
                "bank_commissions": {"total_fee_sar": 1}},
        "payroll": {"salaries_total": 10}, "recurring": {"total": 5}}


def test_cohort_uses_exact_native_instants_and_same_tenant_and_not_import_date():
    pairs = [order("before", "2026-11-01T08:59:59+03:00"), order("boundary", START),
             order("inside", "2026-11-01T09:00:01+03:00"), order("after", "2026-11-03T12:00:01+03:00"),
             order("unknown", None, created_at=START), order("foreign", "2026-11-02T11:00:00+03:00", user_id="other")]
    rows, raw = [list(group) for group in zip(*pairs)]
    before = deepcopy((rows, raw))
    selected, coverage = source.select_order_cohort(rows, raw, {}, "owner", _instant(START), _instant(END))
    assert [row["order_number"] for row in selected] == ["inside"]
    assert coverage["pending_orders"] == 2 and not coverage["complete"]
    assert before == (rows, raw)
    assert coverage["identity_digest"] == source.select_order_cohort(rows, raw, {}, "owner", _instant(START), _instant(END))[1]["identity_digest"]


def test_future_native_version_and_duplicate_identity_do_not_supply_sales():
    row, raw = order("one", "2026-11-02T10:00:00+03:00")
    raw["raw_by_source"]["salla_direct"]["updated_at"] = "2026-11-03T12:00:01+03:00"
    assert not source.select_order_cohort([row], [raw], {}, "owner", _instant(START), _instant(END))[0]
    raw["raw_by_source"]["salla_direct"]["updated_at"] = "2026-11-02T10:00:00+03:00"
    assert not source.select_order_cohort([row, row], [raw, raw], {}, "owner", _instant(START), _instant(END))[0]


def test_daily_sources_never_invent_hourly_allocations_for_partial_boundary_days():
    days, complete = source.whole_days(_instant(START), _instant(END))
    assert tuple(day.isoformat() for day in days) == ("2026-11-02", "2026-11-02") and not complete
    snapshot = source.assemble_snapshot("owner", _instant(START), _instant(END),
        {"coverage": {"orders": 1, "complete": True}}, values(), {})
    for name in ("advertising", "salaries", "operating_expenses"):
        assert snapshot["components"][name]["amount"] is None
        assert snapshot["components"][name]["known_subtotal"] is not None
    assert snapshot["components"]["net_sales"]["amount"] == "100.00"
    assert snapshot["components"]["payment_fees"]["known_subtotal"] == "3.00"
    assert not snapshot["quality"]["operating_complete"]


def test_whole_day_cohort_and_costs_reuse_source_formula_before_financing_and_tax():
    start, end = "2026-11-01T00:00:00+03:00", "2026-11-03T00:00:00+03:00"
    snapshot = source.assemble_snapshot("owner", _instant(start), _instant(end),
        {"coverage": {"orders": 1, "complete": True}}, values(), {})
    result = project_profit({"owner_id": "owner", "started_at": start, "engine": {"as_of": end, "profit_snapshot": snapshot}}, "SAR")
    assert result["operating_profit"] == "47.00" and result["net_profit"] is None


def test_unknown_only_sales_and_product_costs_are_not_known_zero():
    amounts = values()
    amounts["sales"] = {"total_sar": None, "known_total_sar": 0, "conversion_complete": False,
                        "converted_orders_count": 0, "unverified_orders_count": 1}
    amounts["products"] = {"total": 0, "missing_products_count": 1, "incomplete_orders_count": 1,
                           "linked_products_count": 0, "source_lines": {"missing": 1}}
    snapshot = source.assemble_snapshot("owner", _instant(START), _instant(END),
        {"coverage": {"orders": 1, "complete": True}}, amounts, {})
    for key in ("net_sales", "cost_of_goods"):
        assert snapshot["components"][key]["amount"] is None
        assert snapshot["components"][key]["known_subtotal"] is None
    assert snapshot["components"]["shipping"]["known_subtotal"] == "5.00"


@pytest.mark.parametrize("cohort_complete,expected", [(True, "0.00"), (False, None)])
def test_zero_sales_and_products_require_a_proved_empty_or_zero_cost_cohort(cohort_complete, expected):
    amounts = values()
    amounts["sales"] = {"total_sar": 0, "known_total_sar": 0, "conversion_complete": True,
                        "converted_orders_count": 0, "unverified_orders_count": 0}
    amounts["products"] = {"total": 0, "missing_products_count": 0, "incomplete_orders_count": 0}
    snapshot = source.assemble_snapshot("owner", _instant(START), _instant(END),
        {"coverage": {"orders": 0, "complete": cohort_complete}}, amounts, {})
    assert snapshot["components"]["net_sales"]["known_subtotal"] == expected
    assert snapshot["components"]["cost_of_goods"]["known_subtotal"] == expected


def test_context_projection_preserves_native_order_number_identity_alias():
    row, projected = order("public-123", "2026-11-02T10:00:00+03:00")
    native = projected["raw_by_source"]["salla_direct"]
    native["order_number"] = native.pop("reference_id")
    full = {**row, "raw_by_source": projected["raw_by_source"]}

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        async def to_list(self, _limit):
            return deepcopy(self.rows)

    class Collection:
        def __init__(self, name):
            self.name = name

        def find(self, query, projection):
            assert query["user_id"] == "owner"
            if self.name == "settings":
                return Cursor([])
            assert self.name == "unified_orders"
            if projection.get("raw_by_source") == 0:
                return Cursor([row])
            result = {}
            for path, included in projection.items():
                if not included:
                    continue
                target, original = result, full
                parts = path.split(".")
                for part in parts[:-1]:
                    original = original.get(part, {})
                    target = target.setdefault(part, {})
                if parts[-1] in original:
                    target[parts[-1]] = deepcopy(original[parts[-1]])
            return Cursor([result])

    class ReadOnlyDB:
        def __getitem__(self, name):
            return Collection(name)

    result = asyncio.run(source._load_context(ReadOnlyDB(), "owner", _instant(START), _instant(END)))
    assert result["coverage"]["complete"] and result["coverage"]["orders"] == 1
    assert result["orders"][0]["order_number"] == "public-123"


def test_loader_deadline_keeps_finished_components_and_never_calls_a_writer(monkeypatch):
    async def scenario():
        observed = []
        async def context(*_args):
            observed.append("context")
            return {"orders": [], "settings": {}, "coverage": {"orders": 0, "complete": True}}
        async def fast():
            observed.append("sales")
            return {"total_sar": 0, "known_total_sar": 0, "conversion_complete": True}
        async def slow():
            await asyncio.sleep(2)
            observed.append("late")
            return {}
        monkeypatch.setattr(source, "_load_context", context)
        monkeypatch.setattr(source, "_component_readers", lambda *_: {"sales": fast, "products": slow})
        class ReadOnly:
            def __getitem__(self, name):
                raise AssertionError("No unplanned DB operation")
        state = {"owner_id": "owner", "started_at": START, "engine": {"as_of": END}}
        before = deepcopy(state)
        result = await source.load_profit_snapshot(ReadOnly(), "owner", state, as_of=END, budget_seconds=.03)
        assert observed == ["context", "sales"]
        assert result["components"]["net_sales"]["amount"] == "0.00"
        assert result["components"]["cost_of_goods"]["amount"] is None
        assert "operational_source_read_timeout" in result["quality"]["reason_codes"]
        assert state == before and result["read_only"]
        with pytest.raises(ValueError, match="owner_mismatch"):
            await source.load_profit_snapshot(ReadOnly(), "other", state, as_of=END)
    asyncio.run(scenario())


def test_cancelling_page_request_cleans_all_profit_read_tasks(monkeypatch):
    async def scenario():
        started, cleaned = asyncio.Event(), asyncio.Event()
        async def context(*_):
            return {"orders": [], "settings": {}, "coverage": {"orders": 0, "complete": True}}
        async def pending():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        monkeypatch.setattr(source, "_load_context", context)
        monkeypatch.setattr(source, "_component_readers", lambda *_: {"products": pending})
        state = {"owner_id": "owner", "started_at": START}
        task = asyncio.create_task(source.load_profit_snapshot(object(), "owner", state, as_of=END))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned.is_set()
        assert not [other for other in asyncio.all_tasks() if other is not asyncio.current_task()]
    asyncio.run(scenario())
