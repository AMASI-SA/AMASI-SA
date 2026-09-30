"""Isolate historical pending-list fixtures from the machine's current date.

The affected suites seed July 5 orders and intentionally test tabs/deduplication,
not a rolling date boundary. Use the public ``now`` dependency so those fixtures
stay inside their unchanged 60-day query. No production clock is patched.
"""
from datetime import datetime, timezone
from functools import partial

import pytest


@pytest.fixture(autouse=True)
def isolated_pending_clock(request, monkeypatch):
    if request.module.__name__.rsplit(".", 1)[-1] not in {
        "test_pending_cross_tab_dedupe",
        "test_pending_limit_and_status_filter_ordering",
    }:
        return
    monkeypatch.setattr(
        request.module,
        "list_pending_orders",
        partial(
            request.module.list_pending_orders,
            now=datetime(2026, 7, 12, tzinfo=timezone.utc),
        ),
    )
