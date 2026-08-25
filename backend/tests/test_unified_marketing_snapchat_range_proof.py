from __future__ import annotations

from datetime import date

import pytest

from unified_marketing.readers.snapchat_v2 import (
    _projection_financial_status,
    _reconciliation_status,
)


class Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    async def to_list(self, length=None):
        return self.rows[:length]


class Collection:
    def __init__(self, rows):
        self.rows = list(rows)

    def find(self, query, _projection):
        ids = set(query["sync_run_id"]["$in"])
        return Cursor([
            row
            for row in self.rows
            if row.get("sync_run_id") in ids
            and row.get("user_id") == query["user_id"]
            and row.get("ad_account_id") == query["ad_account_id"]
        ])


class Database(dict):
    pass


@pytest.mark.asyncio
async def test_range_financial_proof_ignores_newer_unrelated_partial_run():
    db = Database({
        "mezan_snapchat_sync_runs_v2": Collection([
            {
                "user_id": "owner-1",
                "ad_account_id": "snap-1",
                "sync_run_id": "closed-day-run",
                "financial_sync_status": "complete",
            },
            {
                "user_id": "owner-1",
                "ad_account_id": "snap-1",
                "sync_run_id": "new-open-day-run",
                "financial_sync_status": "partial",
            },
        ])
    })

    status = await _projection_financial_status(
        db,
        user_id="owner-1",
        ad_account_id="snap-1",
        projections=[{"source_sync_run_ids": ["closed-day-run"]}],
    )

    assert status == "complete"


@pytest.mark.asyncio
async def test_range_financial_proof_fails_closed_when_a_source_run_is_not_complete():
    db = Database({
        "mezan_snapchat_sync_runs_v2": Collection([
            {
                "user_id": "owner-1",
                "ad_account_id": "snap-1",
                "sync_run_id": "run-a",
                "financial_sync_status": "complete",
            },
            {
                "user_id": "owner-1",
                "ad_account_id": "snap-1",
                "sync_run_id": "run-b",
                "financial_sync_status": "partial",
            },
        ])
    })

    status = await _projection_financial_status(
        db,
        user_id="owner-1",
        ad_account_id="snap-1",
        projections=[{"source_sync_run_ids": ["run-a", "run-b"]}],
    )

    assert status == "partial"


def test_reconciliation_proof_requires_every_day_in_the_range():
    assert _reconciliation_status(
        [
            {"report_date": "2026-08-23", "reconciled": True},
            {"report_date": "2026-08-24", "reconciled": True},
        ],
        date_from=date(2026, 8, 23),
        date_to=date(2026, 8, 24),
    ) == "reconciled"

    assert _reconciliation_status(
        [{"report_date": "2026-08-23", "reconciled": True}],
        date_from=date(2026, 8, 23),
        date_to=date(2026, 8, 24),
    ) == "partial"
