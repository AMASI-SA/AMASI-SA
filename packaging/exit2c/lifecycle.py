"""Owned synthetic database state transitions; never accepts arbitrary targets."""
import sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, "/opt/mezan/backend")
from independent_runtime import validate_before_import
validate_before_import("web")
from pymongo import MongoClient
db = MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=1500)["mezan_exit2c"]
key = "backend-heavy-initialization:test:exit2c-candidate"
leases = db.backend_startup_leases_v1
phase = sys.argv[1]
if phase == "duplicate-fixture":
    assert not db.list_collection_names(), "refuse existing database"
    db.users.insert_many([{"email": "duplicate@example.test", "id": "dup1"}, {"email": "duplicate@example.test", "id": "dup2"}])
elif phase == "repair-fixture":
    assert leases.find_one({"_id": key})["status"] != "completed"
    db.users.delete_many({"id": {"$in": ["dup1", "dup2"]}})
    leases.update_one({"_id": key}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
    print("PASS failed migration never marked complete; repaired synthetic duplicate fixture")
elif phase == "completed":
    assert leases.find_one({"_id": key})["status"] == "completed"
    assert db.users.count_documents({"role": "owner"}) == 1
    print("PASS migration completion and initial owner")
elif phase == "profile":
    db.command("profile", 0)
    db.system.profile.drop()
    db.command("profile", 2)
elif phase == "no-writes":
    rows = list(db.system.profile.find({"$or": [
        {"op": {"$in": ["insert", "update", "remove"]}},
        *[{"command." + k: {"$exists": True}} for k in ["insert", "update", "delete", "create", "createIndexes", "drop", "collMod", "findAndModify"]],
    ]}, {"op": 1, "ns": 1}))
    assert not rows, [(r.get("op"), r.get("ns")) for r in rows]
    print("PASS zero import/startup/shutdown writes in profiled web window")
elif phase == "worker-held":
    row = leases.find_one({"_id": "backend-heavy-initialization:independent-worker:singleton", "status": "running"})
    assert row and row["heartbeat_at"] > row["acquired_at"], "worker must remain alive and renew its fence"
elif phase == "lose-worker-fence":
    result = leases.update_one({"_id": "backend-heavy-initialization:independent-worker:singleton"}, {"$set": {"fence": "synthetic-successor-fence"}})
    assert result.matched_count == 1
elif phase == "expire-worker":
    leases.update_one({"_id": "backend-heavy-initialization:independent-worker:singleton"}, {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}})
elif phase == "no-worker":
    assert not leases.find_one({"_id": "backend-heavy-initialization:independent-worker:singleton"})
else:
    raise RuntimeError("unknown lifecycle fixture phase")
