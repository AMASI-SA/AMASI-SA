"""Iter-139 — Verify ad-account half-hour sync replaced the 23:55 daily cron.

We don't actually run the 30-min sleep loop in unit tests — that would
take too long.  Instead we assert:

  1. `run_daily_cron` is still importable and now uses force=True
     internally so re-running on the same day reverses + reapplies.
  2. The old `_ad_account_daily_cron` symbol no longer exists in
     server.py and the new `_ad_account_halfhour_sync` does.
  3. The interval constant is exactly 30 minutes (1800 s).
"""
import re
from pathlib import Path

import pytest


SERVER_PY = Path("/app/backend/server.py").read_text(encoding="utf-8")
AD_PY = Path("/app/backend/ad_account_routes.py").read_text(encoding="utf-8")


def test_old_daily_cron_symbol_removed():
    assert "_ad_account_daily_cron" not in SERVER_PY, (
        "Iter-139 should have deleted _ad_account_daily_cron entirely."
    )


def test_new_halfhour_sync_registered():
    assert "_ad_account_halfhour_sync" in SERVER_PY
    assert "iter-139" in SERVER_PY


def test_interval_is_30_minutes():
    m = re.search(r"AD_ACCOUNT_SYNC_INTERVAL_SECONDS\s*=\s*(\d+)\s*\*\s*60",
                  SERVER_PY)
    assert m, "Expected `AD_ACCOUNT_SYNC_INTERVAL_SECONDS = 30 * 60`."
    assert int(m.group(1)) == 30, (
        f"Expected 30-minute interval, got {m.group(1)}."
    )


def test_run_daily_cron_uses_force():
    """The half-hour cadence requires force=True so repeated passes on
    the same day reverse previous cron rows before posting fresh
    totals (Iter-110 fix B)."""
    # Find the call inside run_daily_cron.
    m = re.search(
        r"async def run_daily_cron.*?_run_sync_for_all\((.*?)\)",
        AD_PY, re.DOTALL,
    )
    assert m, "_run_sync_for_all call not found inside run_daily_cron."
    call_args = m.group(1)
    assert "force=True" in call_args, (
        f"run_daily_cron must invoke _run_sync_for_all with force=True "
        f"so the half-hour cadence doesn't double-count. Got: {call_args}"
    )


def test_run_log_type_renamed():
    """Diagnostics UI / cron_runs collection uses a new `type` tag so
    older daily rows remain identifiable."""
    assert "ad_account_halfhour_sync" in SERVER_PY
    assert "ad_account_daily_sync" not in SERVER_PY
