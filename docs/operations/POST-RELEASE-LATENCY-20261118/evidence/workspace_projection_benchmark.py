"""Counterbalanced real-module ASGI benchmark using synthetic in-memory Mongo.

Run with the repository test dependencies. No provider/production data or network
is used. Results measure this fixture, not hosted end-to-end performance.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
import types


ROOT = Path(__file__).resolve().parents[4]
BASELINE = "d7c9ed77137bed0948f4d32a34c6bf571d75e6f8"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", default=BASELINE)
    parser.add_argument("--suppliers", type=int, default=250)
    parser.add_argument("--movements", type=int, default=6000)
    parser.add_argument("--audit-pages", type=int, default=16)
    parser.add_argument("--events-per-page", type=int, default=250)
    args = parser.parse_args()
    if min(args.suppliers, args.movements, args.events_per_page) < 1 or args.audit_pages < 0:
        parser.error("Fixture counts must be positive; audit-pages may be zero")
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ.pop("OPERATIONAL_TEST_MONGO_URI", None)
    sys.path[:0] = [str(ROOT / "backend"), str(ROOT / "backend/tests")]

    import operational_balance_workspace as candidate
    from operational_balance_archive import AUDIT_PAGES, MARKER, content_key, manifest
    from test_operational_balance_workspace import BASE, STATES, run

    baseline = types.ModuleType("workspace_latency_baseline")
    sys.modules[baseline.__name__] = baseline
    baseline_source = subprocess.check_output(
        ["git", "show", args.baseline + ":backend/operational_balance_workspace.py"], cwd=ROOT, text=True)
    exec(compile(baseline_source, "baseline:backend/operational_balance_workspace.py", "exec"), baseline.__dict__)
    results = []
    try:
        for index, label in enumerate(("baseline", "candidate", "candidate", "baseline", "baseline", "candidate")):
            sys.modules["operational_balance_workspace"] = baseline if label == "baseline" else candidate

            async def scenario(db, client):
                await db.mezan_suppliers_v2.insert_many([
                    {"id": f"supplier-{i:03d}", "user_id": "owner", "name": f"Supplier {i:03d}", "status": "active"}
                    for i in range(args.suppliers)])
                movements = [
                    {"id": f"movement-{i:06d}", "party_type": "supplier", "party_id": f"supplier-{i % args.suppliers:03d}",
                     "currency": "SAR", "amount": "1.00", "direction": "outgoing", "kind": "payment",
                     "bank_kind": "bank", "bank_id": "bank", "bank_name": "Bank", "allocations": [],
                     "actor_id": "owner", "business_date": "2026-11-17", "occurred_at": "2026-11-17T12:00:00+00:00"}
                    for i in range(args.movements)]
                changes = {"movements": movements}
                refs, pages = [], []
                for page in range(args.audit_pages):
                    start = page * args.events_per_page
                    events = [{"id": f"audit-{start + row}", "amount": "1.00", "source_id": "synthetic"}
                              for row in range(args.events_per_page)]
                    key = content_key("owner", start, events)
                    pages.append({"_id": key, "owner_id": "owner", "start": start, "events": events})
                    refs.append({"id": key, "count": len(events)})
                if pages:
                    await db[AUDIT_PAGES].insert_many(pages)
                    changes.update({MARKER: manifest(args.audit_pages * args.events_per_page, refs), "engine.audit": {"tail": []}})
                await db[STATES].update_one({"owner_id": "owner"}, {"$set": changes})
                collection_type = type(db[AUDIT_PAGES])
                original_find = collection_type.find_one
                audit_reads = []

                async def observed_find(collection, *positional, **keywords):
                    if collection.name == AUDIT_PAGES and collection.database.name == db.name:
                        audit_reads.append(1)
                    return await original_find(collection, *positional, **keywords)

                collection_type.find_one = observed_find
                try:
                    started, cpu = time.perf_counter(), time.process_time()
                    context = await client.get(BASE + "/context")
                    middle = time.perf_counter()
                    daily = await client.get(BASE + "/movements")
                    finished = time.perf_counter()
                finally:
                    collection_type.find_one = original_find
                assert context.status_code == 200, context.text
                assert daily.status_code == 200, daily.text
                payload = daily.json()
                assert payload["total"] == args.movements + 6 and len(payload["items"]) == 5
                data = {"context": context.json(), "movements": payload}
                results.append({"variant": label, "run": index + 1,
                    "context_ms": round((middle - started) * 1000, 3),
                    "movements_ms": round((finished - middle) * 1000, 3),
                    "combined_ms": round((finished - started) * 1000, 3),
                    "process_cpu_ms": round((time.process_time() - cpu) * 1000, 3),
                    "audit_page_reads": len(audit_reads),
                    "response_sha256": hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()})
            run(scenario)
    finally:
        sys.modules["operational_balance_workspace"] = candidate
    assert len({result["response_sha256"] for result in results}) == 1
    medians = {label: {key: statistics.median(row[key] for row in results if row["variant"] == label)
                      for key in ("context_ms", "movements_ms", "combined_ms", "process_cpu_ms", "audit_page_reads")}
               for label in ("baseline", "candidate")}
    print(json.dumps({"baseline_git_sha": args.baseline,
        "candidate_source_sha256": hashlib.sha256((ROOT / "backend/operational_balance_workspace.py").read_bytes()).hexdigest(),
        "fixture": {"suppliers": args.suppliers, "movements": args.movements, "audit_pages": args.audit_pages,
                    "events_per_page": args.events_per_page},
        "method": "Real module and ASGI routes; synthetic mongomock Mongo, no network; counterbalanced six runs",
        "production_latency_measured": False, "identical_responses": True, "runs": results, "median_ms": medians}, indent=2))


if __name__ == "__main__":
    main()
