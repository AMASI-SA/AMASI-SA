from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from recurring_obligations_routes import (
    INVOICES,
    OBLIGATIONS,
    compute_recurring_obligations_for_range,
    cycle_bounds,
    obligation_daily_amount,
)


ROOT = Path(__file__).resolve().parents[2]


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, _limit):
        return list(self.rows)


class Collection:
    def __init__(self, rows):
        self.rows = list(rows)

    def find(self, _query, _projection):
        return Cursor(self.rows)


class Db:
    def __init__(self, obligations, invoices=()):
        self.collections = {
            OBLIGATIONS: Collection(obligations),
            INVOICES: Collection(invoices),
        }

    def __getitem__(self, name):
        return self.collections[name]


def obligation(**overrides):
    row = {
        "id": "obligation-1",
        "user_id": "owner",
        "title": "إيجار فرع الرياض",
        "expense_type": "rent",
        "entity_type": "warehouse",
        "entity_name": "فرع الرياض",
        "cycle": "semiannual",
        "period_amount": 60000,
        "start_date": "2026-07-01",
        "auto_renew": True,
        "status": "active",
    }
    row.update(overrides)
    return row


def invoice(**overrides):
    row = {
        "id": "invoice-1",
        "user_id": "owner",
        "obligation_id": "obligation-1",
        "amount": 9000,
        "period_start": "2026-01-01",
        "period_end": "2026-03-31",
    }
    row.update(overrides)
    return row


def test_calendar_cycles_use_actual_days_and_roll_forward():
    assert cycle_bounds(date(2026, 7, 1), "semiannual", date(2026, 8, 14)) == (
        date(2026, 7, 1),
        date(2026, 12, 31),
    )
    assert cycle_bounds(date(2026, 7, 1), "semiannual", date(2027, 2, 1)) == (
        date(2027, 1, 1),
        date(2027, 6, 30),
    )
    assert cycle_bounds(date(2024, 2, 29), "annual", date(2025, 2, 28)) == (
        date(2025, 2, 28),
        date(2026, 2, 27),
    )


def test_six_month_fixed_obligation_accrues_exact_period_amount():
    result = asyncio.run(compute_recurring_obligations_for_range(
        Db([obligation()]), "owner", date(2026, 7, 1), date(2026, 12, 31)
    ))

    assert result["total"] == pytest.approx(60000.0, abs=0.01)
    assert result["rentals_total"] == pytest.approx(60000.0, abs=0.01)
    assert result["utilities_total"] == 0


def test_non_renewing_obligation_stops_after_first_cycle():
    row = obligation(auto_renew=False)
    result = asyncio.run(compute_recurring_obligations_for_range(
        Db([row]), "owner", date(2026, 7, 1), date(2027, 6, 30)
    ))

    assert result["total"] == pytest.approx(60000.0, abs=0.01)


def test_stopped_obligation_keeps_history_and_stops_on_effective_day():
    row = obligation(status="stopped", stopped_at="2026-07-11")
    result = asyncio.run(compute_recurring_obligations_for_range(
        Db([row]), "owner", date(2026, 7, 1), date(2026, 7, 31)
    ))

    expected_daily = 60000 / 184
    assert result["total"] == pytest.approx(expected_daily * 10, abs=0.01)


def test_last_three_utility_invoices_use_weighted_daily_benchmark():
    row = obligation(
        expense_type="electricity",
        cycle="monthly",
        period_amount=0,
        start_date="2026-04-01",
        estimation_basis="last_3_invoices",
    )
    invoices = [
        invoice(id="i1", amount=3100, period_start="2026-01-01", period_end="2026-01-31"),
        invoice(id="i2", amount=2800, period_start="2026-02-01", period_end="2026-02-28"),
        invoice(id="i3", amount=3100, period_start="2026-03-01", period_end="2026-03-31"),
    ]

    daily = obligation_daily_amount(row, invoices, date(2026, 4, 15))

    assert daily == pytest.approx(9000 / 90, abs=0.0001)


def test_actual_utility_invoice_replaces_estimate_for_covered_days():
    row = obligation(
        expense_type="water",
        cycle="monthly",
        period_amount=3000,
        start_date="2026-04-01",
        estimation_basis="manual",
    )
    actual = invoice(
        amount=4500,
        period_start="2026-04-01",
        period_end="2026-04-30",
    )
    result = asyncio.run(compute_recurring_obligations_for_range(
        Db([row], [actual]), "owner", date(2026, 4, 1), date(2026, 4, 30)
    ))

    assert result["total"] == pytest.approx(4500.0, abs=0.01)
    assert result["utilities_total"] == pytest.approx(4500.0, abs=0.01)


def test_dashboard_v2_declares_and_deducts_recurring_source():
    source = (ROOT / "backend/dashboard_v2_routes.py").read_text(encoding="utf-8")

    assert '"recurring_obligations": "operating_recurring_obligations_v2"' in source
    assert 'operating_total = salary_total + recurring_total' in source
    assert '"operating_utilities_total": recurring["utilities_total"]' in source
