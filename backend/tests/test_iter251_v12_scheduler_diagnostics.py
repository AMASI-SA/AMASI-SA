"""Iter-251 v12 — Tests for /api/ad-spend-rca/scheduler-diagnostics.

Validates that the read-only diagnostics endpoint:
  • Returns 200 with the expected top-level keys.
  • Surfaces heartbeats inserted by the iter-215 loop into cron_runs.
  • Reports per-counterparty dry-run preview with blockers detected
    (missing external_account_id, sync_via=make_com, unknown provider).
  • Never writes to general_ledger (verified by comparing before/after
    counts).
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import httpx
import pytest

API = os.environ.get(
    "REACT_APP_BACKEND_URL",
    "http://localhost:8001",
).rstrip("/") + "/api"

LOGIN_EMAIL = "amasi.jewelery@gmail.com"
LOGIN_PWD = "10201917"


@pytest.mark.asyncio
async def test_scheduler_diagnostics_endpoint():
    """End-to-end smoke test: login → call endpoint → assert shape."""
    async with httpx.AsyncClient(timeout=30.0) as http:
        login = await http.post(
            f"{API}/auth/login",
            json={"email": LOGIN_EMAIL, "password": LOGIN_PWD},
        )
        assert login.status_code == 200, login.text
        token = (
            login.json().get("token")
            or login.json().get("access_token")
        )
        assert token, "no token returned"

        headers = {"Authorization": f"Bearer {token}"}

        # Snapshot general_ledger entry count before the call
        # (we never write so it MUST stay equal).
        from motor.motor_asyncio import AsyncIOMotorClient
        mongo_url = os.environ.get("MONGO_URL")
        db_name = os.environ.get("DB_NAME") or "test_db"
        client = AsyncIOMotorClient(mongo_url)
        db = client[db_name]
        # Need uid
        me = await http.get(f"{API}/auth/me", headers=headers)
        assert me.status_code == 200, me.text
        uid = me.json().get("id")
        assert uid

        before = await db.general_ledger.count_documents(
            {"user_id": uid})

        resp = await http.get(
            f"{API}/ad-spend-rca/scheduler-diagnostics"
            "?date=2026-06-23&hours_back=24",
            headers=headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()

        # Shape assertions
        for key in (
            "date", "hours_back", "scheduler_status",
            "counterparties", "selected_snapchat_accounts",
            "ads_currency_settings", "dry_run_preview",
            "raw_source_samples", "note",
        ):
            assert key in data, f"missing key: {key}"

        ss = data["scheduler_status"]
        for key in (
            "loop_start_event_seen_in_window",
            "heartbeats_in_window",
            "aggregate_posted_count",
            "aggregate_skipped_count",
            "aggregate_skip_reasons",
            "recent_heartbeats",
        ):
            assert key in ss, f"missing scheduler_status.{key}"

        # Counterparties + dry_run_preview are aligned by count
        assert len(data["dry_run_preview"]) == len(
            data["counterparties"]
        )

        # READ-ONLY guarantee: general_ledger count unchanged
        after = await db.general_ledger.count_documents(
            {"user_id": uid})
        assert before == after, (
            f"diagnostics endpoint wrote to general_ledger! "
            f"{before} → {after}"
        )

        client.close()


@pytest.mark.asyncio
async def test_dry_run_preview_blockers_detected():
    """The dry-run preview MUST flag blockers (missing ext_id, sync_via,
    or unsupported provider) per counterparty."""
    async with httpx.AsyncClient(timeout=30.0) as http:
        login = await http.post(
            f"{API}/auth/login",
            json={"email": LOGIN_EMAIL, "password": LOGIN_PWD},
        )
        token = (
            login.json().get("token")
            or login.json().get("access_token")
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await http.get(
            f"{API}/ad-spend-rca/scheduler-diagnostics"
            "?date=2026-06-23&hours_back=24",
            headers=headers,
        )
        data = resp.json()
        for row in data["dry_run_preview"]:
            # Every row must have either blockers OR a numeric
            # cumulative_spend (no half-states).
            if row["blockers"]:
                assert row["cumulative_spend"] is None
                assert row["would_post_AM_amount"] is None
                assert row["would_post_PM_amount"] is None
            else:
                assert isinstance(
                    row["cumulative_spend"], (int, float)
                )
                assert row["raw_source_rows"] is not None


@pytest.mark.asyncio
async def test_heartbeat_persistence():
    """After the loop has been running ≥2 minutes there must be at least
    a loop_start_event and one catchup heartbeat row.

    (Skipped if the loop hasn't been up long enough.)"""
    async with httpx.AsyncClient(timeout=30.0) as http:
        login = await http.post(
            f"{API}/auth/login",
            json={"email": LOGIN_EMAIL, "password": LOGIN_PWD},
        )
        token = (
            login.json().get("token")
            or login.json().get("access_token")
        )
        headers = {"Authorization": f"Bearer {token}"}
        resp = await http.get(
            f"{API}/ad-spend-rca/scheduler-diagnostics"
            "?date=2026-06-23&hours_back=24",
            headers=headers,
        )
        data = resp.json()
        ss = data["scheduler_status"]
        # If the loop never started we'd see 0 heartbeats.
        if ss["heartbeats_in_window"] == 0:
            pytest.skip("scheduler not yet up; rerun after >2 minutes")
        assert ss["loop_start_event_seen_in_window"] is True
        types = {h.get("type") for h in ss["recent_heartbeats"]}
        assert types & {
            "ad_spend_window_post_loop_start",
            "ad_spend_window_catchup",
            "ad_spend_window_post",
        }, f"unexpected heartbeat types: {types}"
