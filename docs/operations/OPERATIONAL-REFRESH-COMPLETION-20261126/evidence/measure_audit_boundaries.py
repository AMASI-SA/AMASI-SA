"""Count real archive boundary operations using isolated synthetic fixtures.

Run from the repository with the existing test environment and bytecode off:
    PYTHONDONTWRITEBYTECODE=1 PYTHON_DOTENV_DISABLED=1 python -B \
        docs/operations/OPERATIONAL-REFRESH-COMPLETION-20261126/evidence/measure_audit_boundaries.py

No Mongo connection, production records, timing sleeps or source mutations.
The latency calculation is an explicitly hypothetical cost model, not a
wall-clock measurement or a prediction for the production page count.
"""
import argparse
import asyncio
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

import pytest
from test_operational_balance_archive_checkpoint_boundaries import observed_read


async def measure(pages):
    observations = []
    for page_count in pages:
        for reader in ("serial", "prefetch"):
            with pytest.MonkeyPatch.context() as monkeypatch:
                observations.append(await observed_read(
                    monkeypatch, reader=reader, pages=page_count, packed=True))
    return observations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pages", type=int, nargs="+", default=[1, 8, 9, 16, 17, 64])
    args = parser.parse_args()
    if any(not 1 <= pages <= 128 for pages in args.pages) or len(args.pages) > 12:
        parser.error("use at most twelve synthetic page counts between 1 and 128")
    observations = asyncio.run(measure(args.pages))
    # Source-derived owner/browser route: four fresh metadata reads and one
    # lease renewal per successful checkpoint. The fixture observes the fresh
    # callback/renewal counts; it does not simulate those four production reads.
    for row in observations:
        row["synthetic_cost_model"] = {
            "db_operations_per_gate": 5,
            "assumed_ms_per_db_operation": 3,
            "modelled_page_gate_db_operations": len(row["page_checkpoints"]) * 5,
            "modelled_page_gate_db_ms": len(row["page_checkpoints"]) * 5 * 3,
        }
    sources = (
        "backend/operational_audit_prefetch.py",
        "backend/operational_balance_archive.py",
        "backend/tests/test_operational_balance_archive_checkpoint_boundaries.py",
    )
    result = {
        "measurement_kind": "isolated_operation_count_not_wall_clock",
        "python_version": platform.python_version(),
        "head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "source_sha256": {name: sha256((ROOT / name).read_bytes()).hexdigest() for name in sources},
        "fixture": "one synthetic BSON event per immutable page plus one tail event; packed financial/issue fields",
        "owner_browser_gate_model_sources": [
            "backend/operational_demand_refresh_routes.py:gate_state/verify",
            "backend/operational_balance_routes.py:scope",
            "backend/operational_balance_service.py:active_gate/accounting_inactive",
            "backend/operational_demand_refresh.py:DemandRefreshContext.checkpoint",
        ],
        "limitations": [
            "No production page count, gate latency, CPU duration or performance gain was measured.",
            "Five DB operations per gate is a source-derived owner-browser model, not fixture I/O.",
            "No additional query, polling, native evidence acquisition or financial write was executed.",
        ],
        "observations": observations,
    }
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
