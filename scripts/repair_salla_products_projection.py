#!/usr/bin/env python3
"""Preview an explicit Salla products projection repair; never run a full sync.

Requires MZ2_ITEMS_PROJECTION_REPAIR_MONGO_URI and
MZ2_ITEMS_PROJECTION_REPAIR_DB_NAME. Connection values are never printed.
The saved Salla access token must already work: this tool cannot rotate it.

Examples (synthetic identities; configure the connection separately):
  python scripts/repair_salla_products_projection.py --user-id synthetic-owner \
      --orders synthetic-order
  python scripts/repair_salla_products_projection.py --user-id synthetic-owner \
      --orders synthetic-order --apply --manifest-sha <reviewed-preview-sha>
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
from salla_products_projection_repair import (
    ProductsProjectionRepairRefused, apply_products_projection_repair,
    build_products_projection_repair_plan,
)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--user-id", required=True, help="Exact owner; no cross-owner discovery")
    result.add_argument("--orders", nargs="+", required=True, help="Explicit order numbers, maximum 25")
    result.add_argument("--apply", action="store_true", help="Apply only the exact reviewed plan")
    result.add_argument("--manifest-sha", help="Exact manifest_sha from a fresh reviewed preview")
    return result


async def run(args: argparse.Namespace) -> tuple[dict, int]:
    uri = os.environ.get("MZ2_ITEMS_PROJECTION_REPAIR_MONGO_URI", "")
    name = os.environ.get("MZ2_ITEMS_PROJECTION_REPAIR_DB_NAME", "")
    if not uri or not name:
        return {"ok": False, "error": "repair_connection_environment_required"}, 2
    client = None
    try:
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000,
            socketTimeoutMS=10000, appname="salla-products-projection-repair")
        if args.apply:
            result = await apply_products_projection_repair(client[name], args.user_id, args.orders,
                                                            expected_manifest_sha=args.manifest_sha)
        else:
            result = await build_products_projection_repair_plan(client[name], args.user_id, args.orders)
        return {"ok": True, **result}, 0 if result["ready"] else 3
    except ProductsProjectionRepairRefused as exc:
        return {"ok": False, "error": exc.code, "details": exc.details}, 3
    except Exception as exc:
        return {"ok": False, "error": "repair_failed", "error_type": type(exc).__name__}, 4
    finally:
        if client is not None:
            client.close()


def main() -> int:
    arguments = parser()
    args = arguments.parse_args()
    if args.apply and not args.manifest_sha:
        arguments.error("--apply requires --manifest-sha from a reviewed preview")
    if args.manifest_sha and not args.apply:
        arguments.error("--manifest-sha requires --apply")
    result, code = asyncio.run(run(args))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
