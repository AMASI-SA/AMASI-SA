#!/usr/bin/env python3
"""Plan an explicit historical reporting repair; apply only a reviewed manifest.

Connection settings are read from MZ2_REPORTING_REPAIR_MONGO_URI and
MZ2_REPORTING_REPAIR_DB_NAME. Their values are never included in output.
There is no default connection, owner, date range, order discovery or live run.

Example with synthetic identities (connection configured separately):
    python scripts/repair_order_cost_reporting.py --user-id synthetic-owner \
        --orders synthetic-order --include-base-product-id synthetic-product
    python scripts/repair_order_cost_reporting.py --user-id synthetic-owner \
        --orders synthetic-order --include-base-product-id synthetic-product \
        --apply --manifest-sha <exact SHA from the reviewed dry run>
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from motor.motor_asyncio import AsyncIOMotorClient
from order_cost_reporting_repair import (
    ReportingRepairRefused, apply_reporting_repair, build_reporting_repair_plan,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--user-id", required=True, help="Exact owner scope; no cross-owner discovery")
    result.add_argument("--orders", nargs="+", required=True, help="Explicit order numbers (maximum250)")
    result.add_argument("--include-base-product-id", action="append", default=[],
                        help="Explicit physical parent to add only from frozen missing-base evidence")
    result.add_argument("--apply", action="store_true", help="Commit an atomic reporting-only correction batch")
    result.add_argument("--manifest-sha", help="Exact manifest_sha of the reviewed dry-run plan")
    return result


async def run(args: argparse.Namespace) -> tuple[dict, int]:
    uri = os.environ.get("MZ2_REPORTING_REPAIR_MONGO_URI", "")
    database = os.environ.get("MZ2_REPORTING_REPAIR_DB_NAME", "")
    if not uri or not database:
        return {"ok": False, "error": "repair_connection_environment_required"}, 2
    client = None
    try:
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000)
        db = client[database]
        if args.apply:
            result = await apply_reporting_repair(db, args.user_id, args.orders,
                include_base_product_ids=args.include_base_product_id, expected_manifest_sha=args.manifest_sha)
        else:
            result = await build_reporting_repair_plan(db, args.user_id, args.orders,
                include_base_product_ids=args.include_base_product_id)
        return {"ok": True, **result}, 0 if result["ready"] else 3
    except ReportingRepairRefused as error:
        return {"ok": False, "error": error.code, "details": error.details}, 3
    except Exception as error:
        # Driver messages can contain server names, connection parameters or
        # application data; retain only the exception class for local diagnosis.
        return {"ok": False, "error": "repair_failed", "error_type": type(error).__name__}, 4
    finally:
        if client is not None:
            client.close()


def main() -> int:
    arguments = parser()
    args = arguments.parse_args()
    if args.apply and not args.manifest_sha:
        arguments.error("--apply requires --manifest-sha from a reviewed dry-run plan")
    if args.manifest_sha and not args.apply:
        arguments.error("--manifest-sha requires --apply")
    result, code = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False, default=str))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
