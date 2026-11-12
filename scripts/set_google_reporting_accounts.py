#!/usr/bin/env python3
"""One-time, owner-scoped Google reporting selection; dry run by default.

Run in the backend environment with MONGO_URL and DB_NAME configured. Supply
the complete expected set of connected API account IDs, and a nonempty subset
to select. --apply changes only local selection fields and writes an audit in
one Mongo transaction. Transactions are required; there is no partial fallback.
No Google request, credential repair, report deletion, or scheduled job runs.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys

from pymongo import ReadPreference

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from integrations_control_center.google_account_selection import (  # noqa: E402
    ACCOUNTS, AUDIT, INTEGRATIONS, NO_EXTERNAL_WRITES, POLICY_FIELDS, SELECTION_FIELDS, SelectionError,
    set_reporting_accounts,
)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--owner-id", required=True)
    parser.add_argument("--selected-account-ids", required=True, nargs="+", help="Nonempty reporting subset")
    parser.add_argument("--expected-account-ids", required=True, nargs="+", help="Complete connected API account set")
    parser.add_argument("--apply", action="store_true", help="Apply the reviewed selection transaction")
    return parser


async def _main_async(args):
    mongo_url, db_name = os.environ.get("MONGO_URL"), os.environ.get("DB_NAME")
    if not mongo_url or not db_name:
        raise SelectionError("mongo_environment_required")
    from motor.motor_asyncio import AsyncIOMotorClient
    client = AsyncIOMotorClient(
        mongo_url, serverSelectionTimeoutMS=10000, connectTimeoutMS=10000, socketTimeoutMS=10000,
        read_preference=ReadPreference.PRIMARY,
    )
    try:
        return await asyncio.wait_for(set_reporting_accounts(
            client[db_name], owner_id=args.owner_id,
            selected_account_ids=args.selected_account_ids,
            expected_account_ids=args.expected_account_ids, apply=args.apply,
        ), timeout=30)
    finally:
        client.close()


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        result = asyncio.run(_main_async(args))
    except SelectionError as exc:
        print(json.dumps({"ok": False, "error": str(exc), "apply_outcome_unknown": exc.outcome_unknown}), file=sys.stderr)
        return 2
    except Exception:
        # Driver messages can contain hosts/URIs or credentials. Also, a lost
        # commit acknowledgement does not establish whether the write committed.
        print(json.dumps({"ok": False, "error": "google_selection_database_unavailable",
                          "apply_outcome_unknown": args.apply}), file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
