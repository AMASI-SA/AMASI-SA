"""Run operational acceptance on loopback Mongo while retaining test databases.

Does not import the production server or load dotenv. Existing fixtures request
database cleanup; this runner records those requests without deleting databases.
"""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
URI = "mongodb://127.0.0.1:27486/?replicaSet=mezanReadiness20261020"
EVIDENCE = ROOT / "docs/operations/MZ2-OPERATIONAL-READINESS-20261020/evidence"


def main():
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    os.environ["PYTHON_DOTENV_DISABLED"] = "1"
    os.environ["OPERATIONAL_TEST_MONGO_URI"] = URI
    os.environ["MZ2_TEST_MONGO_URI"] = URI
    sys.path[:0] = [str(ROOT), str(ROOT / "backend"), str(ROOT / "backend/tests")]
    from motor.motor_asyncio import AsyncIOMotorClient
    from pymongo import MongoClient
    import pytest

    with MongoClient(URI, serverSelectionTimeoutMS=3000) as client:
        hello = client.admin.command("hello")
        if hello.get("setName") != "mezanReadiness20261020" or not hello.get("isWritablePrimary"):
            raise RuntimeError("Dedicated local test replica set is not ready")

    retained = []
    original = AsyncIOMotorClient.drop_database

    async def retain_database(self, name, *args, **kwargs):
        database_name = name if isinstance(name, str) else name.name
        if not database_name.startswith("operational_balance_test_"):
            raise RuntimeError("Unexpected cleanup target; no database was deleted")
        retained.append(database_name)

    AsyncIOMotorClient.drop_database = retain_database
    tests = sorted(str(path) for path in (ROOT / "backend/tests").glob("test_operational_balance*.py"))
    try:
        return pytest.main([
            "--noconftest", "-p", "no:cacheprovider", "-q", "--tb=short",
            f"--junitxml={EVIDENCE / 'operational-backend.xml'}", *tests,
        ])
    finally:
        AsyncIOMotorClient.drop_database = original
        (EVIDENCE / "retained-databases.json").write_text(
            json.dumps({"replica_set": "mezanReadiness20261020", "databases": sorted(set(retained))},
                       indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
