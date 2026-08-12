#!/usr/bin/env python3
"""CLI for provisioning a receive-only Meta WhatsApp binding for Mezan.

The Meta Phone Number ID is accepted only through
``MEZAN_WHATSAPP_PHONE_NUMBER_ID``. The command is read-only by default and
requires ``--apply`` for an insert. It never rebinds or updates an existing
channel. If a different channel already exists in the store, a new binding is
added beside it only with ``--allow-additional-channel``.

No phone number, Phone Number ID, credential, or reversible provider identifier
is printed or persisted. Required tenant identifiers are persisted in the
binding but are represented only by opaque references in command output.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = REPO_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from customer_intelligence.whatsapp_provisioning import (  # noqa: E402
    BINDING_HMAC_ENV,
    PHONE_NUMBER_ID_ENV,
    ProvisioningError,
    ProvisioningPlan,
    apply_plan,
    build_channel_account_key,
    build_plan,
)


def _text(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Safely provision one receive-only Meta WhatsApp channel",
    )
    parser.add_argument("--owner-id", help="Exact Mezan Owner id when needed")
    parser.add_argument(
        "--merchant-id", help="Exact connected Salla store id when needed"
    )
    parser.add_argument(
        "--allow-additional-channel",
        action="store_true",
        help="Preserve one existing safe channel and add the Meta binding beside it",
    )
    parser.add_argument(
        "--apply", action="store_true", help="Apply the planned database insert"
    )
    return parser


async def _main_async(args: argparse.Namespace) -> dict[str, Any]:
    mongo_url = _text(os.environ.get("MONGO_URL"))
    db_name = _text(os.environ.get("DB_NAME"))
    phone_number_id = _text(os.environ.get(PHONE_NUMBER_ID_ENV))
    if not mongo_url or not db_name:
        raise ProvisioningError("MONGO_URL and DB_NAME are required")

    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(mongo_url)
    try:
        db = client[db_name]
        plan = await build_plan(
            db,
            phone_number_id=phone_number_id,
            owner_id=args.owner_id,
            merchant_id=args.merchant_id,
            allow_additional_channel=args.allow_additional_channel,
        )
        if not args.apply:
            return plan.public(applied=False)
        return await apply_plan(
            db,
            plan,
            allow_additional_channel=args.allow_additional_channel,
        )
    finally:
        client.close()


def main() -> int:
    args = _parser().parse_args()
    try:
        payload = asyncio.run(_main_async(args))
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
        return 0
    except ProvisioningError as exc:
        print(
            json.dumps(
                {"ok": False, "error": str(exc)},
                ensure_ascii=False,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 2


__all__ = [
    "BINDING_HMAC_ENV",
    "PHONE_NUMBER_ID_ENV",
    "ProvisioningError",
    "ProvisioningPlan",
    "apply_plan",
    "build_channel_account_key",
    "build_plan",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
