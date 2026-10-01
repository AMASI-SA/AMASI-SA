"""Prepared real-HTTP probe helpers. No runnable server or execution entry point.

Root C3 completion and explicit execution go are required first.
Authentication uses the real password login endpoint, never a dependency override.
"""
import hashlib
import json
from urllib.parse import urlparse, parse_qs
from uuid import uuid4


def validate_environment(base_url, mongo_uri, db_name):
    web, mongo = urlparse(base_url), urlparse(mongo_uri)
    if (web.scheme != "http" or web.hostname != "127.0.0.1" or not web.port
            or web.username or web.password or web.path not in {"", "/"}):
        raise ValueError("Explicit loopback HTTP listener required")
    if (mongo.scheme != "mongodb" or mongo.hostname != "127.0.0.1" or not mongo.port
            or mongo.username or mongo.password or not parse_qs(mongo.query).get("replicaSet")
            or not db_name.startswith("mz2_smoke_b_acceptance_")):
        raise ValueError("Disposable loopback replica and unique Acceptance database required")


async def full_fingerprint(db):
    """Trusted direct DB snapshot: all documents, indexes and collection options.

    No financial allowlist or skipped auth/lease collections: any change fails.
    Raw data stays in memory; persisted evidence contains hashes/counts only.
    """
    from bson.json_util import dumps, CANONICAL_JSON_OPTIONS
    def canonical(value):
        return dumps(value, json_options=CANONICAL_JSON_OPTIONS, sort_keys=True, separators=(",", ":"))
    rows = {}
    for name in sorted(await db.list_collection_names()):
        collection = db[name]
        documents = sorted(canonical(row) for row in await collection.find({}).to_list(None))
        indexes = sorted(canonical(row) for row in await collection.list_indexes().to_list(None))
        options = await collection.options()
        payload = canonical({"documents": documents, "indexes": indexes, "options": options})
        rows[name] = {"count": len(documents), "sha256": hashlib.sha256(payload.encode()).hexdigest()}
    return {"collections": rows, "sha256": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}


async def authenticated_probe(http, db, *, owner, email, password):
    """Caller supplies a real loopback HTTP client and isolated direct DB handle.

    Login occurs before baseline since supported login can initialize settings.
    Caller must prove clean committed runtime identity, no provider jobs/external access,
    exact connection identity and C3 execution approval before calling this helper.
    """
    transcript = []
    login = await http.post("/api/auth/login", json={"email": email, "password": password})
    if login.status_code != 200 or login.json().get("id") != owner:
        raise RuntimeError("Real login failed or returned another owner")
    token = login.json().get("access_token")
    if not token:
        raise RuntimeError("Real login did not return supported access token")
    http.headers["Authorization"] = "Bearer " + token
    transcript.append({"method": "POST", "path": "/api/auth/login", "status": 200, "owner": owner})
    control_path = "/api/accounting-module/write-control"
    before_control = await http.get(control_path)
    if before_control.status_code != 200 or before_control.json().get("paused") is not True:
        raise RuntimeError("Canonical write-control must already be paused")
    direct_before = await db.mz2_atomic_owners.find_one({"_id": owner})
    if not direct_before or direct_before.get("writes_paused") is not True:
        raise RuntimeError("Explicit seeded paused owner required")
    missing = "acceptance-nonexistent-" + uuid4().hex
    session_path = "/api/accounting-module/onboarding/sessions/" + missing
    if await db.mz2_onboarding_sessions.count_documents({"id": missing}):
        raise RuntimeError("Probe session unexpectedly exists")
    before = await full_fingerprint(db)
    absent = await http.get(session_path)
    transcript.append({"method": "GET", "path": session_path, "status": absent.status_code, "body": absent.json()})
    if absent.status_code != 404:
        raise RuntimeError("Nonexistent session did not return 404; no POST permitted")
    payload = {"version": 1, "idempotency_key": "acceptance-smoke-b-" + uuid4().hex,
               "note": "Isolated Acceptance paused barrier probe on nonexistent session"}
    denied = await http.post(session_path + "/opening-draft", json=payload)
    body = denied.json()
    transcript.append({"method": "POST", "path": session_path + "/opening-draft", "payload": payload,
                       "status": denied.status_code, "body": body})
    after_control = await http.get(control_path)
    after = await full_fingerprint(db)
    direct_after = await db.mz2_atomic_owners.find_one({"_id": owner})
    detail = body.get("detail")
    code = detail.get("code") if isinstance(detail, dict) else detail
    passed = (denied.status_code == 423 and code == "mz2_writes_paused"
              and after_control.status_code == 200 and before_control.json() == after_control.json()
              and direct_before == direct_after and before == after)
    return {"status": "ACCEPTANCE_PROBE_PASS" if passed else "ACCEPTANCE_PROBE_FAIL",
            "production_verified": False, "transcript": transcript,
            "canonical_control_before": before_control.json(), "canonical_control_after": after_control.json(),
            "database_before": before, "database_after": after}
