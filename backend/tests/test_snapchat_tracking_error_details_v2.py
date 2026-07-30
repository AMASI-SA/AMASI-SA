from __future__ import annotations

from copy import deepcopy

import pytest

from integrations_control_center import snapchat_native_tracking_diagnostics as tracking
from integrations_control_center.snapchat_tracking_error_details import (
    _detail_rows,
    install_snapchat_tracking_error_detail_persistence,
    persist_snapchat_tracking_error_details,
)


class FakeCollection:
    def __init__(self, rows):
        self.rows = rows

    async def update_one(self, query, update, upsert=False):
        target = next(
            (
                row
                for row in self.rows
                if all(row.get(key) == value for key, value in query.items())
            ),
            None,
        )
        inserted = False
        if target is None and upsert:
            target = deepcopy(query)
            self.rows.append(target)
            inserted = True
        if target is not None:
            if inserted:
                target.update(deepcopy(update.get("$setOnInsert") or {}))
            target.update(deepcopy(update.get("$set") or {}))
        return object()

    async def find_one(self, query, projection=None, sort=None):
        return next(
            (
                deepcopy(row)
                for row in self.rows
                if all(row.get(key) == value for key, value in query.items())
            ),
            None,
        )


class FakeDB:
    def __init__(self):
        self.rows: dict[str, list[dict]] = {}

    def __getitem__(self, name):
        return FakeCollection(self.rows.setdefault(name, []))

    def __getattr__(self, name):
        return self[name]


def _engine():
    pixel_errors = [
        {"kind": "signal_readiness", "error": "snapchat_tracking_http_403"},
        {"kind": "pixel_stats", "error": "snapchat_tracking_http_503"},
        {"kind": "pixel_stats", "error": "snapchat_tracking_http_503"},
    ]
    return {
        "status": "partial",
        "errors_count": 4,
        "fetched_at": "2026-07-30T15:20:24+00:00",
        "items": [{
            "ad_account_id": "account-1",
            "pixels": [{
                "pixel_id": "pixel-1",
                "errors": deepcopy(pixel_errors),
            }],
            "errors": [
                *deepcopy(pixel_errors),
                {"kind": "pixels", "error": "pixel_limit_reached"},
            ],
        }],
        "errors": [
            *deepcopy(pixel_errors),
            {"kind": "pixels", "error": "pixel_limit_reached"},
        ],
    }


def test_detail_rows_keep_context_and_aggregate_repeated_reasons():
    rows = _detail_rows(_engine())

    assert len(rows) == 3
    signal = next(row for row in rows if row["kind"] == "signal_readiness")
    stats = next(row for row in rows if row["kind"] == "pixel_stats")
    limit = next(row for row in rows if row["kind"] == "pixels")

    assert signal == {
        "ad_account_id": "account-1",
        "pixel_id": "pixel-1",
        "kind": "signal_readiness",
        "code": "snapchat_tracking_http_403",
        "occurrences": 1,
        "retryable": False,
    }
    assert stats["pixel_id"] == "pixel-1"
    assert stats["occurrences"] == 2
    assert stats["retryable"] is True
    assert limit["pixel_id"] is None
    assert limit["code"] == "pixel_limit_reached"


@pytest.mark.asyncio
async def test_detail_errors_are_idempotent_secret_safe_and_queryable_by_run():
    db = FakeDB()
    engine = _engine()

    first = await persist_snapchat_tracking_error_details(
        db,
        user_id="owner-1",
        run_id="run-1",
        engine=engine,
    )
    second = await persist_snapchat_tracking_error_details(
        db,
        user_id="owner-1",
        run_id="run-1",
        engine=engine,
    )

    assert first == 3
    assert second == 3
    rows = db.rows["mezan_integration_errors_v2"]
    assert len(rows) == 3
    assert {row["run_id"] for row in rows} == {"run-1"}
    assert {row["provider"] for row in rows} == {"snapchat_ads"}
    assert {row["source_mode"] for row in rows} == {
        tracking.TRACKING_SOURCE_MODE
    }

    signal = next(row for row in rows if row["diagnostic_kind"] == "signal_readiness")
    stats = next(row for row in rows if row["diagnostic_kind"] == "pixel_stats")
    assert signal["code"] == "snapchat_tracking_http_403"
    assert signal["retryable"] is False
    assert "account-1" in signal["message"]
    assert "pixel-1" in signal["message"]
    assert stats["occurrences"] == 2
    assert "تكرر السبب 2 مرات" in stats["message"]
    assert "token" not in repr(rows).lower()
    assert "secret" not in repr(rows).lower()


def test_installer_wraps_the_engine_only_once():
    install_snapchat_tracking_error_detail_persistence()
    first = tracking.SnapchatTrackingDiagnostics.run
    install_snapchat_tracking_error_detail_persistence()
    second = tracking.SnapchatTrackingDiagnostics.run

    assert first is second
    assert getattr(first, "_mezan_tracking_error_details", False) is True
