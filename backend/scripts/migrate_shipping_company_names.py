"""Iter-98 — Normalise existing shipping company names.

Walks every row in `transfers` and `shipping_payments` and rewrites the
shipping_company / company_name field through
`shipping_companies.scrub_shipping_company()`.

  • Idempotent: re-running is safe; already-canonical names are skipped.
  • Non-destructive: never deletes a row, only updates the name field.
  • Safe for production: prints a dry-run summary first; pass --apply to
    write.

Usage:
    cd /app/backend
    python scripts/migrate_shipping_company_names.py          # dry-run
    python scripts/migrate_shipping_company_names.py --apply  # commit
"""
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, "/app/backend")

from motor.motor_asyncio import AsyncIOMotorClient
from shipping_companies import scrub_shipping_company


def _mongo_url() -> str:
    return os.environ.get("MONGO_URL") or (
        open("/app/backend/.env").read()
        .split("MONGO_URL=")[1].split("\n")[0].strip('"')
    )


async def _migrate_collection(db, coll_name: str, field: str, apply: bool):
    coll = db[coll_name]
    cursor = coll.find(
        {field: {"$ne": None, "$exists": True}},
        {"_id": 1, "user_id": 1, field: 1},
    )
    stats = Counter()
    changes = Counter()
    examples = []

    async for doc in cursor:
        raw = (doc.get(field) or "").strip()
        if not raw:
            stats["empty"] += 1
            continue
        clean = scrub_shipping_company(raw) or raw
        stats["total"] += 1
        if clean == raw:
            stats["already_canonical"] += 1
        else:
            stats["needs_update"] += 1
            changes[f"{raw}  →  {clean}"] += 1
            if len(examples) < 5:
                examples.append((doc["_id"], raw, clean))
            if apply:
                await coll.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {field: clean}},
                )

    print(f"\n── {coll_name}.{field} ─────────────────────────────────")
    print(f"  rows scanned:       {stats['total']}")
    print(f"  already canonical:  {stats['already_canonical']}")
    print(f"  needs normalising:  {stats['needs_update']}")
    print(f"  empty/null:         {stats['empty']}")
    if changes:
        print("  top renames:")
        for rename, count in changes.most_common(10):
            print(f"    {rename}  ({count}×)")
    return stats["needs_update"]


async def main():
    apply = "--apply" in sys.argv
    print(f"Mode: {'APPLY (writes will be committed)' if apply else 'DRY-RUN (no writes)'}")
    client = AsyncIOMotorClient(_mongo_url())
    # Hit ALL user databases — we don't filter by user_id here.
    db_name = os.environ.get("DB_NAME") or "test_database"
    db = client[db_name]

    total_changes = 0
    total_changes += await _migrate_collection(db, "transfers", "shipping_company", apply)
    total_changes += await _migrate_collection(db, "shipping_payments", "company_name", apply)

    print("\n" + "=" * 60)
    if apply:
        print(f"✅ Applied. {total_changes} rows updated.")
    else:
        print(f"DRY-RUN summary: {total_changes} rows would be updated.")
        print("Re-run with --apply to commit the changes.")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
