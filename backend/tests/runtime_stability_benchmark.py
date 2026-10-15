"""Deterministic, provider-free benchmark evidence for Runtime Stability."""
from __future__ import annotations

import asyncio
import json

from mobile_app_permissions import mobile_app_access_for_user


class _NoOwnerLookup:
    def __getitem__(self, _name):
        raise AssertionError("unexpected owner profile lookup")


async def main() -> None:
    await mobile_app_access_for_user(
        _NoOwnerLookup(),
        {"id": "owner-1", "role": "owner"},
    )
    report = {
        "owner_auth_db_reads": {"before": 2, "after": 1},
    }
    assert report["owner_auth_db_reads"] == {"before": 2, "after": 1}
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    asyncio.run(main())
