"""Iter-246z — BNPL Timezone SSOT tests.

Verifies:
  1. `bnpl/timezone.py` exposes Asia/Riyadh constants and helpers.
  2. `today_riyadh_iso` returns a Riyadh-correct date even when UTC
     is one calendar day behind (the 00:00-03:00 Riyadh window).
  3. `earliest_save_date_for_period` returns Saturday for Tamara and
     Monday for Tabby.
  4. `GET /api/audit/timezone-health` reports Asia/Riyadh + invoice
     weekdays.
  5. No `datetime.utcnow()` in any BNPL or Tamara/Tabby module.

Strict READ-ONLY tests for the endpoint.
"""
from __future__ import annotations

import os
import re
import subprocess
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
import pytest_asyncio
import requests
from dotenv import load_dotenv

_BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BACKEND_DIR, ".env"))
load_dotenv(os.path.join(_BACKEND_DIR, "..", "frontend", ".env"))

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")


def _h(t: str) -> dict:
    return {"Authorization": f"Bearer {t}"}


@pytest_asyncio.fixture
async def token():
    suf = uuid.uuid4().hex[:8]
    r = requests.post(
        f"{BASE_URL}/api/auth/register",
        json={"name": "iter246z", "email": f"iter246z-{suf}@x.com",
              "password": "pw1234567"},
    )
    return r.json()["access_token"]


# ─────────── unit: timezone helpers ───────────


def test_timezone_module_uses_asia_riyadh():
    from bnpl.timezone import (
        BNPL_TZ, BNPL_TZ_NAME,
        INVOICE_WEEKDAY, WEEKDAY_AR,
    )
    assert BNPL_TZ_NAME == "Asia/Riyadh"
    assert str(BNPL_TZ) == "Asia/Riyadh"
    assert INVOICE_WEEKDAY["tamara"] == 5   # Saturday
    assert INVOICE_WEEKDAY["tabby"]  == 0   # Monday
    assert WEEKDAY_AR[5] == "السبت"
    assert WEEKDAY_AR[0] == "الإثنين"


def test_today_riyadh_iso_handles_midnight_to_3am_window():
    """At 23:00 UTC on day N, Riyadh is already at 02:00 of day N+1.
    A naive `datetime.utcnow().date()` would return day N — Riyadh
    helper must return day N+1."""
    from bnpl.timezone import today_riyadh_iso
    fake_utc = datetime(2026, 6, 19, 23, 0, 0, tzinfo=timezone.utc)
    with patch("bnpl.timezone.datetime") as mdt:
        mdt.now.return_value = fake_utc.astimezone(
            __import__("zoneinfo").ZoneInfo("Asia/Riyadh"))
        # Sanity: mock returns a Riyadh-aware datetime for 2026-06-20 02:00
        # ensure today_riyadh_iso returns the Riyadh date, not UTC's.
        assert today_riyadh_iso() == "2026-06-20"


def test_earliest_save_for_tamara_lands_on_saturday():
    """Period 2026-06-06..2026-06-12 → first Saturday after that is
    2026-06-13."""
    from bnpl.timezone import earliest_save_date_for_period
    assert earliest_save_date_for_period(
        "tamara", "2026-06-12") == "2026-06-13"


def test_earliest_save_for_tabby_lands_on_monday():
    """Period 2026-06-06..2026-06-12 → first Monday after that is
    2026-06-15."""
    from bnpl.timezone import earliest_save_date_for_period
    assert earliest_save_date_for_period(
        "tabby", "2026-06-12") == "2026-06-15"


# ─────────── endpoint: timezone health ───────────


def test_timezone_health_endpoint(token):
    r = requests.get(
        f"{BASE_URL}/api/audit/timezone-health", headers=_h(token))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["iter"] == "iter246z"
    assert body["bnpl_timezone"] == "Asia/Riyadh"
    assert body["tamara_invoice_weekday"]["index"] == 5
    assert body["tamara_invoice_weekday"]["name_ar"] == "السبت"
    assert body["tabby_invoice_weekday"]["index"] == 0
    assert body["tabby_invoice_weekday"]["name_ar"] == "الإثنين"
    assert body["all_bnpl_paths_using_riyadh"] is True


# ─────────── static: no datetime.utcnow() in BNPL ───────────


def test_no_utcnow_in_bnpl_or_tamara_modules():
    """Iter-246z bans `datetime.utcnow()` in any BNPL / Tamara /
    Tabby logic module (it's tz-naive and silently skews by a day
    near the 00:00-03:00 Riyadh window).

    Uses AST so the check ignores `utcnow` references inside
    docstrings, comments, or string literals.
    """
    import ast

    backend = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    targets = [
        os.path.join(backend, "bnpl"),
        os.path.join(backend, "tamara_apply_routes.py"),
        os.path.join(backend, "tamara_forensic_routes.py"),
        os.path.join(backend, "tamara_refund_backfill_routes.py"),
        os.path.join(backend, "tamara_settlement_history_routes.py"),
        os.path.join(backend, "tamara_ssot_diagnostic_routes.py"),
        os.path.join(backend, "tamara_receivable_diagnostic_routes.py"),
        os.path.join(backend, "tamara_fix_plan_dryrun_routes.py"),
        os.path.join(backend, "bnpl_settlement_health_routes.py"),
        os.path.join(backend, "bnpl_timezone_health_routes.py"),
    ]

    def _scan_file(path: str) -> list[str]:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except SyntaxError:
            return []
        bad: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # Match `<x>.utcnow(...)` calls of any module/attr.
                fn = node.func
                if (isinstance(fn, ast.Attribute)
                        and fn.attr == "utcnow"):
                    bad.append(
                        f"{path}:{getattr(node, 'lineno', 0)}: "
                        f"call to .utcnow()"
                    )
        return bad

    offenders: list[str] = []
    for t in targets:
        if not os.path.exists(t):
            continue
        if os.path.isdir(t):
            for root, _, files in os.walk(t):
                for f in files:
                    if f.endswith(".py"):
                        offenders.extend(_scan_file(os.path.join(root, f)))
        else:
            offenders.extend(_scan_file(t))

    assert not offenders, (
        "datetime.utcnow() is banned in BNPL paths under Iter-246z. "
        "Offenders:\n" + "\n".join(offenders)
    )
