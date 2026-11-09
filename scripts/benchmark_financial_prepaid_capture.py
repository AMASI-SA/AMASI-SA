#!/usr/bin/env python3
"""Synthetic, read-only reproduction of the reverted per-prepaid audit cost.

Loads only the activity helper from the pinned historical Git source, and uses
the checked-out report implementation/dependencies. No database, network, env
file, financial reprocessing or runtime source replacement is performed. This
measures capture overhead, not a full production refresh or incident causality.
"""
import argparse
from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
import socket
import statistics
import subprocess
import sys
import time
import types

ROOT = Path(__file__).resolve().parents[1]
os.environ["PYTHON_DOTENV_DISABLED"] = "1"
sys.path.insert(0, str(ROOT / "backend"))
SOURCE = "de76aed339be775e2878071c92063d0ab9df06df"
STAMP = "2026-11-08T00:00:00+00:00"


def fixture(count):
    from operational_balance_store import initial
    state = initial("synthetic-owner")
    state.update(status="active", started_at="2026-11-01T00:00:00+00:00")
    state["openings"]["cash"] = {
        "id": "cash", "party_type": "cash", "party_id": "cash", "name": "cash",
        "currency": "SAR", "amount": "10000.00", "direction": "for_us",
    }
    state["engine"] = {"obligations": {}, "issues": [], "as_of": STAMP}
    state["movements"] = [{
        "id": str(i), "party_type": "cash", "party_id": "cash", "name": "cash",
        "bank_kind": "cash", "bank_id": "cash", "bank_name": "cash",
        "amount": "1.00", "currency": "SAR", "direction": "incoming",
        "kind": "receipt", "actor_id": "operational_order_worker",
        "occurred_at": STAMP, "order_id": str(i), "allocations": [],
    } for i in range(count)]
    return state


def batch(activity, *, capture, previous, charges):
    import operational_balance_service as service
    state = fixture(previous)
    original = service.report
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    # Resolve ordinary report imports before measuring either branch.
    original(state)
    service.report = counted
    started = time.perf_counter()
    try:
        for i in range(charges):
            identity = "shipping-prepaid:" + str(i)
            state["engine"]["obligations"][identity] = {
                "id": identity, "kind": "shipping", "party_type": "courier",
                "party_id": "aramex", "name": "aramex", "currency": "SAR",
                "direction": "payable", "expected": "0.00", "confirmed": "34.50",
                "business_date": "2026-11-08",
            }
            movement = {
                "id": "pay:" + str(i), "party_type": "courier", "party_id": "aramex",
                "name": "aramex", "bank_kind": "cash", "bank_id": "cash",
                "bank_name": "cash", "amount": "34.50", "currency": "SAR",
                "direction": "outgoing", "kind": "payment",
                "actor_id": "operational_order_worker", "occurred_at": STAMP,
                "allocations": [{"obligation_id": identity, "amount": "34.50"}],
            }
            state["movements"].append(movement)
            event = {"id": str(i), "action": "prepaid_shipping_paid", "after": deepcopy(movement)}
            if capture:
                activity.capture_after_balances(state, event)
                snapshot, = event["financial_activity_snapshots"]
                expected = Decimal(10000 + previous) - Decimal("34.50") * (i + 1)
                assert snapshot["account_balance_after"] == format(expected, ".2f")
                assert snapshot["counterparty_balance_after"] == "0.00"
                assert snapshot["account_balance_status"] == "available"
            state["audit"].append(event)
    finally:
        service.report = original
    elapsed = time.perf_counter() - started
    report = original(state)
    cash = next(row["actual"] for row in report["parties"] if row["party_type"] == "cash")
    payable = next(row["outstanding_payable"] for row in report["parties"] if row["party_type"] == "courier")
    assert calls == (charges if capture else 0)
    return {"seconds": round(elapsed, 6), "full_report_calls": calls,
            "cash": cash, "courier_payable": payable}, state


def main():
    def forbidden_network(*args, **kwargs):
        raise RuntimeError("synthetic_prepaid_benchmark_forbids_network")

    socket.socket.connect = forbidden_network
    socket.socket.connect_ex = forbidden_network
    socket.create_connection = forbidden_network
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--previous-movements", type=int, default=5000)
    parser.add_argument("--charges", type=int, default=100)
    args = parser.parse_args()
    if min(args.repeats, args.previous_movements, args.charges) < 1:
        parser.error("all sizes must be positive")
    code = subprocess.check_output(
        ["git", "show", SOURCE + ":backend/operational_financial_activity.py"], cwd=ROOT,
        env={**os.environ, "GIT_NO_LAZY_FETCH": "1"}, text=True)
    activity = types.ModuleType("historical_activity_benchmark")
    exec(compile(code, SOURCE + ":operational_financial_activity.py", "exec"), activity.__dict__)
    samples = []
    for _ in range(args.repeats):
        base, before = batch(activity, capture=False, previous=args.previous_movements, charges=args.charges)
        capture, after = batch(activity, capture=True, previous=args.previous_movements, charges=args.charges)
        assert before["movements"] == after["movements"]
        assert before["engine"] == after["engine"]
        assert before["openings"] == after["openings"]
        assert (base["cash"], base["courier_payable"]) == (capture["cash"], capture["courier_payable"])
        samples.append({"capture_disabled": base, "capture_enabled": capture})
    print(json.dumps({"historical_activity_source": SOURCE,
        "report_implementation": "current checkout; no COD/provider cohort in this fixture",
        "previous_movements": args.previous_movements, "charges": args.charges,
        "samples": samples, "median_capture_seconds": statistics.median(
            s["capture_enabled"]["seconds"] for s in samples), "money_and_prefix_snapshot_parity": True,
        "limitation": "Synthetic capture-only cost, not full refresh or production incident attribution."}, indent=2))


if __name__ == "__main__":
    main()
