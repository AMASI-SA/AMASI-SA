"""Offline acceptance for the executive operating ratio used by campaign hover."""
import asyncio
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from mongomock_motor import AsyncMongoMockClient

from order_engine import campaign_hover_operating as operating


OWNER = "hover-operating-owner"
NOW = datetime(2026, 11, 13, 22, 0, tzinfo=timezone.utc)  # Already Nov 14 in Riyadh.
DAY = "2026-11-14"


class ReadCursor:
    def __init__(self, cursor, call):
        self.cursor, self.call = cursor, call

    def max_time_ms(self, value):
        assert 0 < value <= 1500
        self.call["timeout"] = value
        return self

    def limit(self, value):
        self.call["limit"] = value
        self.cursor = self.cursor.limit(value)
        return self

    async def to_list(self, length):
        assert self.call.get("timeout")
        assert self.call["limit"] == length
        return await self.cursor.to_list(length)


class ReadCollection:
    def __init__(self, db, name):
        self.db, self.name = db, name

    def find(self, query, projection):
        assert query["user_id"] == OWNER
        call = {"collection": self.name, "query": deepcopy(query), "projection": projection}
        self.db.calls.append(call)
        if self.name == self.db.unavailable:
            raise RuntimeError("synthetic unavailable source")
        return ReadCursor(self.db.source[self.name].find(query, projection), call)


class ReadDB:
    ALLOWED = {"settings", "unified_orders", "operating_salaries",
        "mezan_employee_salary_contracts_v2", "mezan_employees_v2",
        "operating_recurring_obligations_v2", "operating_recurring_invoices_v2"}

    def __init__(self, source, unavailable=None):
        self.source, self.calls, self.unavailable = source, [], unavailable

    def __getitem__(self, name):
        assert name in self.ALLOWED, f"unexpected read: {name}"
        return ReadCollection(self, name)


def sale(number, amount=1000, **values):
    return {"user_id": OWNER, "order_number": number, "order_date": DAY,
        "currency": "SAR", "total_amount": amount, "order_status": "بانتظار المراجعة", **values}


async def fixture_db(*orders, settings=None):
    db = AsyncMongoMockClient().campaign_hover_operating
    if settings is not None:
        await db.settings.insert_one({"user_id": OWNER, **settings})
    if orders:
        await db.unified_orders.insert_many(list(orders))
    return db


async def add_salary(db, *, monthly=3000, revisions=None, suspensions=None):
    await db.mezan_employees_v2.insert_one({"user_id": OWNER, "id": "employee",
        "status": "active", "hire_date": "2026-11-01"})
    row = {"user_id": OWNER, "id": "contract", "employee_id": "employee",
        "currency": "SAR", "monthly_amount": monthly, "effective_from": "2026-11-01"}
    if revisions is not None:
        row["salary_revisions"] = revisions
    if suspensions is not None:
        row["suspension_periods"] = suspensions
    await db.mezan_employee_salary_contracts_v2.insert_one(row)


def obligation(identity, **values):
    return {"user_id": OWNER, "id": identity, "expense_type": "rent", "cycle": "monthly",
        "period_amount": 3000, "start_date": "2026-11-01", "status": "active",
        "auto_renew": True, **values}


def test_basis_matches_executive_salary_recurring_sales_and_status_scope_without_writes():
    async def check():
        foreign = sale("foreign", 100, currency="KWD", order_status="بإنتظار المراجعة",
            raw_by_source={"salla_direct": {"currency": "KWD", "total_amount": 100,
                "exchange_rate": {"rate": "12.2", "base_currency": "SAR", "exchange_currency": "KWD"}}})
        db = await fixture_db(sale("sar"), foreign,
            sale("inferred", 900, order_date_inferred=True), sale("cancelled", 900, order_status="ملغي"),
            sale("yesterday", 900, order_date="2026-11-13"), sale("other-owner", 900, user_id="other"),
            settings={"hide_inferred_date_orders": True, "report_included_statuses": ["بانتظار المراجعة"]})
        await add_salary(db)
        await db.mezan_employees_v2.insert_many([
            {"user_id": OWNER, "id": "archived", "status": "active", "archived": True},
            {"user_id": "other", "id": "other-employee", "status": "active"}])
        await db.mezan_employee_salary_contracts_v2.insert_many([
            {"user_id": OWNER, "id": "archived-contract", "employee_id": "archived",
                "monthly_amount": 90000, "effective_from": "2026-11-01"},
            {"user_id": "other", "id": "other-contract", "employee_id": "other-employee",
                "monthly_amount": 90000, "effective_from": "2026-11-01"}])
        await db.operating_salaries.insert_many([
            {"user_id": OWNER, "category": "household", "monthly_amount": 1500, "status": "active"},
            {"user_id": OWNER, "category": "charity", "monthly_amount": 300, "status": "active"},
            {"user_id": OWNER, "category": "employee", "monthly_amount": 90000, "status": "active"},
            {"user_id": "other", "category": "household", "monthly_amount": 90000, "status": "active"}])
        await db.operating_recurring_obligations_v2.insert_many([
            obligation("rent"), obligation("water", expense_type="water", period_amount=0),
            obligation("other-owner", user_id="other", period_amount=90000)])
        await db.operating_recurring_invoices_v2.insert_one({"user_id": OWNER,
            "obligation_id": "water", "amount": 600, "period_start": "2026-11-01", "period_end": "2026-11-30"})
        before = deepcopy(await db.unified_orders.find({}).to_list(20))
        read_db = ReadDB(db)
        result = await operating.read_operating_basis(read_db, OWNER, NOW)
        # Nov salary 100 + household 50 + charity 10 + rent 100 + water 20.
        assert result == {"complete": True, "ratio": 280 / 2220,
            "expenses_sar": 280.0, "sales_sar": 2220.0, "date": DAY,
            "timezone": "Asia/Riyadh", "reasons": []}
        assert result["ratio"] != round(result["ratio"], 2)
        assert all(count == 1 for count in Counter(c["collection"] for c in read_db.calls).values())
        order_read = next(c for c in read_db.calls if c["collection"] == "unified_orders")
        assert order_read["limit"] == 4001
        assert order_read["query"]["order_date"] == {"$gte": DAY, "$lte": DAY}
        assert await db.unified_orders.find({}).to_list(20) == before
    asyncio.run(check())


def test_missing_settings_uses_executive_unfiltered_status_default_without_creating_settings():
    async def check():
        db = await fixture_db(sale("ordinary", 100), sale("cancelled", 200, order_status="ملغي"))
        result = await operating.read_operating_basis(ReadDB(db), OWNER, NOW)
        assert result["complete"] and result["sales_sar"] == 300
        assert result["expenses_sar"] == 0 and result["ratio"] == 0
        assert await db.settings.count_documents({}) == 0
    asyncio.run(check())


@pytest.mark.parametrize("day,expected", [(1, 0), (2, 100), (10, 200), (12, 0), (14, 200)])
def test_salary_effective_revisions_first_unpaid_day_and_leave_return(day, expected):
    async def check():
        today = f"2026-11-{day:02d}"
        db = await fixture_db(sale("salary-day", 1000, order_date=today))
        await add_salary(db, monthly=6000, revisions=[
            {"effective_from": "2026-11-01", "monthly_amount": 3000},
            {"effective_from": "2026-11-10", "monthly_amount": 6000}],
            suspensions=[{"started_on": "2026-11-12", "returned_on": "2026-11-14"}])
        result = await operating.read_operating_basis(ReadDB(db), OWNER,
            datetime(2026, 11, day, 12, tzinfo=timezone.utc))
        assert result["complete"] and result["expenses_sar"] == expected
        assert result["ratio"] == expected / 1000
    asyncio.run(check())


@pytest.mark.parametrize("orders", [[], [sale("zero", 0)]])
def test_zero_sales_has_no_invented_ratio(orders):
    async def check():
        db = await fixture_db(*orders)
        result = await operating.read_operating_basis(ReadDB(db), OWNER, NOW)
        assert result["complete"] is False and result["ratio"] is None
        assert result["sales_sar"] == 0 and result["expenses_sar"] == 0
        assert "operating_basis_zero_sales" in result["reasons"]
    asyncio.run(check())


def test_missing_order_fx_keeps_denominator_unknown():
    async def check():
        db = await fixture_db(sale("sar", 100), sale("missing-fx", 10, currency="KWD"))
        result = await operating.read_operating_basis(ReadDB(db), OWNER, NOW)
        assert result["complete"] is False and result["ratio"] is None
        assert result["sales_sar"] is None
        assert "operating_basis_sales_currency_unverified" in result["reasons"]
    asyncio.run(check())


@pytest.mark.parametrize("basis", ["last_invoice", "last_3_invoices"])
@pytest.mark.parametrize("amount", [None, 0])
def test_valid_unpriced_utility_keeps_the_executive_zero_contribution(basis, amount):
    async def check():
        from recurring_obligations_routes import ObligationCreate, compute_recurring_obligations_for_range

        db = await fixture_db(sale("sale"))
        # This is accepted source configuration, including no historic invoices.
        ObligationCreate(title="Water", expense_type="water", entity_type="business",
            entity_name="Store", cycle="monthly", start_date="2026-11-01",
            period_amount=amount, estimation_basis=basis)
        await db.operating_recurring_obligations_v2.insert_many([
            obligation("rent"),
            obligation("water", expense_type="water", period_amount=amount, estimation_basis=basis)])
        await add_salary(db)
        day = datetime.fromisoformat(DAY).date()
        executive = await compute_recurring_obligations_for_range(db, OWNER, day, day)
        result = await operating.read_operating_basis(ReadDB(db), OWNER, NOW)
        # Rent 3,000/30 = 100; the canonical water contribution is zero. Salary
        # adds 3,000/30 = 100. The campaign uses this same executive total/1,000.
        assert executive["total"] == 100 and executive["utilities_total"] == 0
        assert result["complete"] is True and result["reasons"] == []
        assert result["expenses_sar"] == 100 + executive["total"] == 200
        assert result["ratio"] == result["expenses_sar"] / 1000 == .2
    asyncio.run(check())


@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf"), "invalid"])
def test_invalid_recurring_amount_does_not_become_known_zero(amount):
    async def check():
        db = await fixture_db(sale("sale"))
        await db.operating_recurring_obligations_v2.insert_one(obligation("invalid", period_amount=amount))
        result = await operating.read_operating_basis(ReadDB(db), OWNER, NOW)
        assert result["complete"] is False and result["ratio"] is None
        assert result["expenses_sar"] is None
        assert result["reasons"]
    asyncio.run(check())


@pytest.mark.parametrize("source", ["salary", "household"])
def test_active_salary_with_missing_price_never_certifies_zero_expense(source):
    async def check():
        db = await fixture_db(sale("sale"))
        if source == "salary":
            await add_salary(db, monthly=None)
        else:
            await db.operating_salaries.insert_one({"user_id": OWNER,
                "category": "household", "status": "active"})
        result = await operating.read_operating_basis(ReadDB(db), OWNER, NOW)
        assert result["complete"] is False and result["ratio"] is None
        assert result["expenses_sar"] is None
        assert result["reasons"] == ["operating_basis_cost_missing"]
    asyncio.run(check())


@pytest.mark.parametrize("failure", ["overflow", "unavailable"])
def test_unreadable_or_truncated_basis_never_becomes_zero(monkeypatch, failure):
    async def check():
        db = await fixture_db(sale("one"), sale("two"))
        if failure == "overflow":
            monkeypatch.setattr(operating, "MAX_STORE_ORDERS", 1)
        read_db = ReadDB(db, unavailable="operating_recurring_obligations_v2" if failure == "unavailable" else None)
        result = await operating.read_operating_basis(read_db, OWNER, NOW)
        assert result["complete"] is False and result["ratio"] is None
        assert result["expenses_sar"] is None and result["sales_sar"] is None
        assert result["reasons"]
    asyncio.run(check())


def test_deadline_cancels_pending_reads_and_returns_unknown(monkeypatch):
    async def check():
        ended = asyncio.Event()

        async def stalled(*args):
            try:
                await asyncio.Event().wait()
            finally:
                ended.set()

        monkeypatch.setattr(operating, "BASIS_TIMEOUT_SECONDS", .01)
        monkeypatch.setattr(operating, "_read", stalled)
        result = await operating.read_operating_basis(object(), OWNER, NOW)
        assert result["complete"] is False and result["ratio"] is None
        assert result["reasons"] == ["operating_basis_timeout"]
        assert ended.is_set()
    asyncio.run(check())


def extra_salary(scope, invalid):
    employee = {"user_id": OWNER, "id": "extra-employee", "status": "active",
        "hire_date": "2026-11-01"}
    contract = {"user_id": OWNER, "id": "extra-contract", "employee_id": "extra-employee",
        "currency": "SAR", "monthly_amount": 9000, "effective_from": "2026-11-01"}
    if scope in {"archived", "is_archived", "deleted", "is_deleted"}:
        employee[scope] = True
    elif scope == "orphan":
        employee = None
    elif scope == "stopped":
        employee.update(status="inactive", updated_at="2026-11-13T12:00:00Z")
    elif scope in {"unpaid_leave", "returned"}:
        employee["status"] = "unpaid_leave" if scope == "unpaid_leave" else "active"
        contract["suspension_periods"] = [{"started_on": "2026-11-12",
            "returned_on": None if scope == "unpaid_leave" else DAY}]
    elif scope == "future_start":
        contract["effective_from"] = "2026-11-15"
    elif scope == "hire_day":
        employee["hire_date"] = DAY
    elif scope == "ended":
        contract["effective_to"] = "2026-11-13"
    if invalid == "missing_amount":
        contract["monthly_amount"] = None
    elif invalid == "invalid_history":
        contract["salary_revisions"] = [{"effective_from": "invalid", "monthly_amount": 9000}]
    elif invalid == "foreign_currency":
        contract["currency"] = "USD"
    return employee, contract


@pytest.mark.parametrize("scope", ["archived", "is_archived", "deleted", "is_deleted", "orphan",
    "stopped", "unpaid_leave", "future_start", "hire_day", "ended"])
@pytest.mark.parametrize("invalid", ["missing_amount", "invalid_history", "foreign_currency"])
def test_noncontributing_employee_contract_cannot_invalidate_known_daily_basis(scope, invalid):
    async def check():
        db = await fixture_db(sale("sale"))
        await add_salary(db)
        employee, contract = extra_salary(scope, invalid)
        if employee is not None:
            await db.mezan_employees_v2.insert_one(employee)
        await db.mezan_employee_salary_contracts_v2.insert_one(contract)
        before = await db.mezan_employee_salary_contracts_v2.find({}).to_list(10)
        readonly = ReadDB(db)
        result = await operating.read_operating_basis(readonly, OWNER, NOW)
        # One contributing employee earns 3,000 / 30 days; the other contributes
        # nothing by identity or calendar, irrespective of its monetary fields.
        assert result == {"complete": True, "ratio": .1, "expenses_sar": 100.0,
            "sales_sar": 1000.0, "date": DAY, "timezone": "Asia/Riyadh", "reasons": []}
        assert all(count == 1 for count in Counter(c["collection"] for c in readonly.calls).values())
        assert await db.mezan_employee_salary_contracts_v2.find({}).to_list(10) == before
    asyncio.run(check())


@pytest.mark.parametrize("invalid", ["missing_amount", "invalid_history", "foreign_currency"])
def test_contributing_employee_cost_gap_still_invalidates_daily_basis(invalid):
    async def check():
        db = await fixture_db(sale("sale"))
        await add_salary(db)
        employee, contract = extra_salary("active", invalid)
        await db.mezan_employees_v2.insert_one(employee)
        await db.mezan_employee_salary_contracts_v2.insert_one(contract)
        result = await operating.read_operating_basis(ReadDB(db), OWNER, NOW)
        assert result["complete"] is False and result["ratio"] is None
        assert result["expenses_sar"] is None and result["reasons"]
    asyncio.run(check())


def test_returned_employee_cost_gap_is_required_on_first_paid_day():
    async def check():
        db = await fixture_db(sale("sale"))
        await add_salary(db)
        employee, contract = extra_salary("returned", "missing_amount")
        await db.mezan_employees_v2.insert_one(employee)
        await db.mezan_employee_salary_contracts_v2.insert_one(contract)
        result = await operating.read_operating_basis(ReadDB(db), OWNER, NOW)
        assert result["complete"] is False and result["ratio"] is None
        assert result["expenses_sar"] is None
    asyncio.run(check())


def test_revision_derived_future_start_is_excluded_before_currency_validation():
    async def check():
        db = await fixture_db(sale("sale"))
        await add_salary(db)
        await db.mezan_employees_v2.insert_one({"user_id": OWNER, "id": "future", "status": "active"})
        await db.mezan_employee_salary_contracts_v2.insert_one({"user_id": OWNER, "id": "future-contract",
            "employee_id": "future", "currency": "USD",
            "salary_revisions": [{"effective_from": "2026-11-15", "monthly_amount": 9000}]})
        result = await operating.read_operating_basis(ReadDB(db), OWNER, NOW)
        assert result["complete"] and result["expenses_sar"] == 100 and result["ratio"] == .1
    asyncio.run(check())


def test_duplicate_employee_contracts_remain_ambiguous_even_when_one_has_ended():
    async def check():
        db = await fixture_db(sale("sale"))
        await add_salary(db)
        await db.mezan_employee_salary_contracts_v2.insert_one({"user_id": OWNER,
            "id": "duplicate", "employee_id": "employee", "monthly_amount": None,
            "effective_from": "2026-11-01", "effective_to": "2026-11-13"})
        result = await operating.read_operating_basis(ReadDB(db), OWNER, NOW)
        assert result["complete"] is False and result["ratio"] is None
        assert result["expenses_sar"] is None
    asyncio.run(check())


def test_profit_enrichment_preserves_the_real_operating_deadline_reason(monkeypatch):
    from order_engine import campaign_hover_profit as profit

    async def check():
        ended = asyncio.Event()

        async def slow_read(*args):
            try:
                await asyncio.sleep(.2)
                return []
            finally:
                ended.set()

        # Separate durations deterministically expose the old outer wrapper's
        # cancellation. The real operating reader still owns and handles its
        # own deadline; its result is not replaced by an external fake.
        monkeypatch.setattr(profit, "COST_TIMEOUT_SECONDS", .001)
        monkeypatch.setattr(operating, "BASIS_TIMEOUT_SECONDS", .02)
        monkeypatch.setattr(operating, "_read", slow_read)
        db = await fixture_db()
        row = {**profit.blank_profit_fields(), "orders": 0, "sales_sar": 0,
            "spend_sar": 0, "provider": "snapchat", "account_id": "account"}
        await asyncio.wait_for(profit.enrich_campaign_profit(db, OWNER,
            {"campaign": row}, {"campaign": []}, NOW), timeout=2)
        assert ended.is_set()
        assert row["operating_basis"]["reasons"] == ["operating_basis_timeout"]
        assert row["operating_basis"]["complete"] is False
        assert row["operating_expenses_sar"] is None and row["operating_expenses_ratio"] is None
        assert row["net_profit_available"] is False and row["net_profit_sar"] is None
    asyncio.run(check())
