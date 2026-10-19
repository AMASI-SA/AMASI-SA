"""Preview a single stuck component snapshot; --apply revalidates under a fence.

No receipt, review, provider request, stock reservation, or workflow transition.
Requires the deployed backend environment (MONGO_URL and DB_NAME). Never clears
the pending flag unless the current contract matches existing acceptance.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


async def run(args):
    from motor.motor_asyncio import AsyncIOMotorClient
    from fulfillment_v2_routes import revalidate_unchanged_component_snapshot

    url, name = os.environ.get("MONGO_URL"), os.environ.get("DB_NAME")
    if not url or not name:
        raise SystemExit("MONGO_URL and DB_NAME are required")
    client = AsyncIOMotorClient(url, serverSelectionTimeoutMS=10000)
    try:
        result = await revalidate_unchanged_component_snapshot(
            client[name], user_id=args.user_id, order_number=args.order_number,
            source_revision=args.expected_revision, dry_run=not args.apply,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("accepted") or result.get("eligible_for_revalidation") else 2
    except Exception as exc:
        # Driver messages can include connection details; do not print them.
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--order-number", required=True)
    parser.add_argument("--expected-revision", type=int, required=True)
    parser.add_argument("--apply", action="store_true")
    sys.exit(asyncio.run(run(parser.parse_args())))
