"""Iter-130 — BNPL settlements must group sales/refunds by Saudi Arabia
local time (Asia/Riyadh, UTC+3, no DST), matching how Tabby/Tamara
build their settlement invoices.

Regression scenario reproduced from production:
  - Merchant viewed Tabby invoice for "4 → 10 May 2026" (Saudi week).
  - Tabby's official invoice grossed 16,646.29 SAR over 69 orders.
  - MEZAN showed only 16,232.46 over 68 orders — a 413.83 SAR gap.
  - The "missing" order (`257819998`) had `created_at_provider =
    2026-05-03T23:50:41Z` (UTC) which is 2026-05-04T02:50 in Riyadh
    and therefore Tabby groups it under the May-4-to-May-10 invoice.
  - The old code filtered the Mongo string `created_at_provider`
    using a raw "2026-05-04" lower bound (UTC midnight), so anything
    in the last 3 UTC hours of the previous day was silently dropped.

The fix translates the Saudi-local date window into a UTC ISO range
(``2026-05-03T21:00:00Z`` → ``2026-05-10T20:59:59Z`` for the example
above) before querying Mongo.
"""

from datetime import datetime, timezone

import pytest

from backend.bnpl.settlements_service import (
    _local_date_window_utc,
    _compute_provider_totals,
)


# ── 1.  Pure helper: Saudi-local → UTC ISO conversion ─────────────


def test_local_date_window_basic_may_4_to_10():
    """Saudi-local 4-May → 10-May translates to a UTC window that
    spans 3 hours earlier on each side."""
    lo, hi = _local_date_window_utc("2026-05-04", "2026-05-10")
    assert lo == "2026-05-03T21:00:00Z"
    assert hi == "2026-05-10T20:59:59Z"


def test_local_date_window_handles_none():
    assert _local_date_window_utc(None, None) == (None, None)
    assert _local_date_window_utc("2026-05-04", None) == (
        "2026-05-03T21:00:00Z", None,
    )
    assert _local_date_window_utc(None, "2026-05-10") == (
        None, "2026-05-10T20:59:59Z",
    )


def test_local_date_window_single_day():
    lo, hi = _local_date_window_utc("2026-05-04", "2026-05-04")
    assert lo == "2026-05-03T21:00:00Z"
    assert hi == "2026-05-04T20:59:59Z"


def test_local_date_window_year_boundary():
    lo, hi = _local_date_window_utc("2027-01-01", "2027-01-01")
    assert lo == "2026-12-31T21:00:00Z"
    assert hi == "2027-01-01T20:59:59Z"


def test_local_date_window_ignores_trailing_time():
    """Caller may sometimes pass an ISO timestamp — we take just the
    YYYY-MM-DD prefix and treat the rest as Saudi-local midnight."""
    lo, hi = _local_date_window_utc(
        "2026-05-04T10:30:00", "2026-05-10T23:59:59",
    )
    assert lo == "2026-05-03T21:00:00Z"
    assert hi == "2026-05-10T20:59:59Z"


# ── 2.  Boundary case: the "missing" Tabby order ──────────────────


def test_riyadh_boundary_order_is_included():
    """An order at 2026-05-03 23:50:41 UTC (= 2026-05-04 02:50 Saudi)
    MUST fall inside the May-4-to-May-10 Saudi window."""
    lo, hi = _local_date_window_utc("2026-05-04", "2026-05-10")
    iso = "2026-05-03T23:50:41Z"
    assert lo <= iso <= hi


def test_riyadh_boundary_excludes_too_early():
    """Anything at or before 2026-05-03 20:59:59 UTC (= 2026-05-03
    23:59 Saudi) belongs to the previous week and must be excluded."""
    lo, _ = _local_date_window_utc("2026-05-04", "2026-05-10")
    assert "2026-05-03T20:59:59Z" < lo


def test_riyadh_boundary_excludes_next_week():
    """An order at 2026-05-10 21:00:00 UTC (= 2026-05-11 00:00 Saudi)
    belongs to the NEXT settlement period and must be excluded."""
    _, hi = _local_date_window_utc("2026-05-04", "2026-05-10")
    assert "2026-05-10T21:00:00Z" > hi


# ── 3.  End-to-end with a fake Mongo via mongomock-style stub. ────


class _FakeAgg:
    """Mimic motor's async aggregate iterator over an in-memory list."""

    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        async def gen():
            for d in self._docs:
                yield d
        return gen()


class _FakeCol:
    def __init__(self, docs):
        self.docs = docs

    def aggregate(self, pipeline):
        # Very small aggregator: just support {$match} + {$group sum}.
        match = next(
            (s["$match"] for s in pipeline if "$match" in s), {},
        )
        date_field = (
            "created_at_provider" if "created_at_provider" in match
            else "refunded_at" if "refunded_at" in match else None
        )
        rng = match.get(date_field) if date_field else None

        def keep(d):
            for k, v in match.items():
                if k == date_field:
                    continue
                if d.get(k) != v:
                    return False
            if rng:
                val = d.get(date_field) or ""
                if "$gte" in rng and val < rng["$gte"]:
                    return False
                if "$lte" in rng and val > rng["$lte"]:
                    return False
            return True

        kept = [d for d in self.docs if keep(d)]
        return _FakeAgg([
            {"n": len(kept), "gross": sum(x.get("amount", 0) for x in kept),
             "refunds": sum(x.get("amount", 0) for x in kept)},
        ])


class _FakeDB:
    def __init__(self, transactions=None, refunds=None):
        self.payment_transactions = _FakeCol(transactions or [])
        self.payment_refunds = _FakeCol(refunds or [])


@pytest.mark.asyncio
async def test_compute_provider_totals_includes_boundary_order():
    """Order 257819998 (413.83 SAR) at 23:50 UTC on May 3 must now
    be included in the May-4-to-May-10 (Saudi) period."""
    docs = [
        # The "missing" order from the production discrepancy.
        {"user_id": "u1", "provider": "tabby",
         "amount": 413.83,
         "created_at_provider": "2026-05-03T23:50:41Z"},
        # A normal order safely inside the window.
        {"user_id": "u1", "provider": "tabby",
         "amount": 100.0,
         "created_at_provider": "2026-05-05T10:00:00Z"},
        # An order from the previous week that must STAY excluded.
        {"user_id": "u1", "provider": "tabby",
         "amount": 999.0,
         "created_at_provider": "2026-05-03T20:00:00Z"},
        # An order from the next week (Saudi May 11) — also excluded.
        {"user_id": "u1", "provider": "tabby",
         "amount": 555.0,
         "created_at_provider": "2026-05-10T21:30:00Z"},
    ]
    db = _FakeDB(transactions=docs)

    totals = await _compute_provider_totals(
        db, "u1", "tabby", "2026-05-04", "2026-05-10",
    )
    assert totals["transactions_count"] == 2
    assert totals["gross_sales"] == 513.83
    assert totals["total_refunds"] == 0
    assert totals["net_sales"] == 513.83
