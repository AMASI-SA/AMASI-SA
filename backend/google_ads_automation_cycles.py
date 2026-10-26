"""Compose bounded creation and management cycles inside one existing child.

A durable atomic counter alternates priority across child restarts and replicas.
An unacknowledged rotation never permits feature work. The runner owns the
450-second total deadline; this module creates no process, timer, or model.
"""
from __future__ import annotations

import asyncio
import os

CONTROL = "mezan_google_ads_automation_cycle_control_v1"
ROTATION_ID = "feature_rotation_v1"
MAX_ACCOUNTS = 4


def worker_enabled():
    return os.getenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "false").strip().lower() in {"1", "true", "yes", "on", "enabled"}


async def _creation_first(db):
    from pymongo import ReturnDocument
    row = await db[CONTROL].find_one_and_update({"_id": ROTATION_ID},
        {"$inc": {"sequence": 1}}, upsert=True, return_document=ReturnDocument.AFTER)
    if (not isinstance(row, dict) or row.get("_id") != ROTATION_ID
        or type(row.get("sequence")) is not int or not 1 <= row["sequence"] <= 2**63-1):
        raise RuntimeError("google_automation_rotation_unproved")
    return row["sequence"] % 2 == 1


def _count(summary, limit):
    if (not isinstance(summary, dict) or summary.get("status") not in {"complete", "recovery_only", "disabled"}
        or type(summary.get("accounts")) is not int or not 0 <= summary["accounts"] <= limit):
        raise RuntimeError("google_automation_feature_summary_unproved")
    return summary["accounts"]


def _feature_summary(feature, summary, count):
    rows = summary.get("results")
    failed = isinstance(rows, list) and any(isinstance(row, dict) and row.get("status") == "failed" for row in rows)
    return {"feature": feature, "status": "failed" if failed else summary["status"], "accounts": count}


async def run_google_automation_cycles(db, *, creation_cycle=None, management_cycle=None):
    """Use at most four account slots; uncertain feature results stop composition.

    Management's legacy API lacks a limit argument. If creation uses any slots,
    management waits until the next child, whose durable priority is reversed.
    Management's reported count may conservatively include not-due accounts;
    this can reduce throughput but cannot exceed the global account bound.
    """
    if not worker_enabled(): return {"status": "disabled", "accounts": 0, "results": []}
    results, total = [], 0
    try:
        creation_first = await _creation_first(db)
        if creation_cycle is None:
            from integrations_control_center.google_ads_automation_creation_worker import run_google_creation_cycle
            creation_cycle = run_google_creation_cycle
        if management_cycle is None:
            from integrations_control_center.google_ads_automation_worker import run_google_automation_cycle
            management_cycle = run_google_automation_cycle
        order = ("creation", "management") if creation_first else ("management", "creation")
        for feature in order:
            if total >= MAX_ACCOUNTS: break
            if not worker_enabled(): break
            if feature == "management":
                if total: break
                summary = await management_cycle(db)
            else:
                summary = await creation_cycle(db, max_accounts=MAX_ACCOUNTS-total)
            count = _count(summary, MAX_ACCOUNTS-total)
            total += count
            results.append(_feature_summary(feature, summary, count))
        status = "complete" if any(row["status"] == "complete" for row in results) else "recovery_only"
        return {"status": status, "accounts": total, "results": results}
    except asyncio.CancelledError: raise
    except Exception:
        # Never include DB errors, provider output, account IDs or exception text.
        return {"status": "failed", "accounts": total,
            "results": [*results, {"status": "failed", "code": "google_automation_composition_failed"}]}
