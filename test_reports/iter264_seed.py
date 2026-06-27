"""Seed a known-fix candidate row for the live frontend test, then clean up."""
import asyncio, sys, uuid
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
import os
load_dotenv("/app/backend/.env")
mongo_url = os.environ["MONGO_URL"]
db_name = os.environ["DB_NAME"]

async def main(cmd, row_id=None):
    cli = AsyncIOMotorClient(mongo_url)
    db = cli[db_name]
    if cmd == "seed":
        rid = f"TEST_iter264_ui_{uuid.uuid4().hex[:8]}"
        await db.integration_inbox.insert_one({
            "id": rid,
            "trace_id": f"TEST_iter264_uitr_{uuid.uuid4().hex[:8]}",
            "user_id": "main",
            "connector_key": "qoyod",
            "idempotency_key": rid,
            "pipeline_stage": "DEAD_LETTER",
            "last_failed_stage": "FAILED_CUSTOMER",
            "pipeline_error": {
                "code": "qoyod_validation_error",
                "details": {"contact_name": ["Can't be blank"]},
            },
            "requeue_attempts": 0,
            "dry_run": False,
            "received_at": datetime.now(timezone.utc),
            "canonical_payload": {"order_id": "TEST_ui_o1", "order_number": "9999"},
        })
        print("SEEDED:", rid)
    elif cmd == "clean":
        res = await db.integration_inbox.delete_many({"id": {"$regex": "^TEST_iter264_"}})
        print("DELETED:", res.deleted_count)

if __name__ == "__main__":
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "seed"))
