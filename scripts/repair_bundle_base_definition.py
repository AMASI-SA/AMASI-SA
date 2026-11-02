"""Preview an explicitly selected physical-base bundle repair; apply a reviewed file.

Uses MONGO_URL and DB_NAME already present in the operator's environment.
Preview is read-only and requires an explicit source policy. Example:

  python scripts/repair_bundle_base_definition.py --user-id OWNER \
    --product-id 735616589 --sourcing-mode auto > reviewed-plan.json

After reviewing the exact added inventory unit, sourcing and resource scopes:

  python scripts/repair_bundle_base_definition.py --user-id OWNER \
    --apply --manifest reviewed-plan.json --expected-manifest-sha SHA --actor-id ACTOR

No catalog costs, source policies, historical snapshots or journals are edited.
The apply path uses one normal catalog transaction per selected product. A
partial run is reported and the original manifest can be retried safely.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    result.add_argument("--user-id", required=True)
    result.add_argument("--product-id", action="append", dest="product_ids", help="Explicit product identity; repeat up to 20 times")
    result.add_argument("--sourcing-mode", choices=("auto", "stock", "supplier"))
    result.add_argument("--supplier-id")
    result.add_argument("--location-id")
    result.add_argument("--variant-id")
    result.add_argument("--slots-file", type=Path, help="Optional reviewed JSON array with exactly one current-product slot")
    result.add_argument("--apply", action="store_true")
    result.add_argument("--manifest", type=Path)
    result.add_argument("--expected-manifest-sha")
    result.add_argument("--actor-id")
    return result


def _inputs(args):
    if args.apply:
        if (not args.manifest or not args.expected_manifest_sha or not args.actor_id
                or args.product_ids or args.sourcing_mode or args.supplier_id
                or args.location_id or args.variant_id or args.slots_file):
            raise ValueError("apply_requires_only_reviewed_manifest_hash_actor_and_owner")
        return json.loads(args.manifest.read_text(encoding="utf-8")), None
    if (not args.product_ids or not args.sourcing_mode or args.manifest
            or args.expected_manifest_sha or args.actor_id):
        raise ValueError("preview_requires_explicit_products_and_sourcing_mode")
    policy = {"sourcing_mode": args.sourcing_mode}
    for field in ("supplier_id", "location_id", "variant_id"):
        if getattr(args, field):
            policy[field] = getattr(args, field)
    if args.slots_file:
        policy["slots"] = json.loads(args.slots_file.read_text(encoding="utf-8"))
    return None, policy


async def run(args) -> int:
    from fastapi import HTTPException
    from motor.motor_asyncio import AsyncIOMotorClient
    from bundle_base_definition_repair import (
        BundleBaseDefinitionRepairRefused,
        apply_bundle_base_definition_repair,
        build_bundle_base_definition_repair_plan,
    )

    client = None
    try:
        plan, policy = _inputs(args)
        uri, database = os.environ.get("MONGO_URL"), os.environ.get("DB_NAME")
        if not uri or not database:
            raise ValueError("mongo_environment_required")
        client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=10000)
        if args.apply:
            result = await apply_bundle_base_definition_repair(
                client[database], args.user_id, plan, expected_manifest_sha=args.expected_manifest_sha,
                actor_id=args.actor_id)
        else:
            result = await build_bundle_base_definition_repair_plan(
                client[database], args.user_id, args.product_ids, source_policy=policy)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str, allow_nan=False))
        return 0 if result.get("ready", result.get("ok")) else 2
    except BundleBaseDefinitionRepairRefused as exc:
        print(json.dumps({"ok": False, "code": exc.code, "products": exc.results}, ensure_ascii=False))
        return 2
    except ValueError as exc:
        # Parse errors can contain file contents. Emit only our fixed codes.
        safe = {"apply_requires_only_reviewed_manifest_hash_actor_and_owner",
                "preview_requires_explicit_products_and_sourcing_mode", "mongo_environment_required"}
        print(json.dumps({"ok": False, "code": str(exc) if str(exc) in safe else "invalid_input"}))
        return 2
    except HTTPException as exc:
        code = exc.detail.get("code", "validation_failed") if isinstance(exc.detail, dict) else "validation_failed"
        print(json.dumps({"ok": False, "code": code}))
        return 2
    except Exception as exc:
        # Never expose driver messages, connection URLs, credentials or file data.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        return 1
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parser().parse_args())))
