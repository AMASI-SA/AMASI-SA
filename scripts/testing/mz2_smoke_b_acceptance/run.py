"""Explicit, isolated full-app Acceptance runner. PREPARED; not yet executed.

No product monkeypatches. A Python audit hook refuses outbound non-loopback
connections instead of supplying simulated provider or authentication responses.
"""
import argparse
import asyncio
import ipaddress
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time
from uuid import uuid4

from preflight import ROOT, manifest
from probe import authenticated_probe, validate_environment


def network_guard(path):
    def audit(event, args):
        address = None
        if event == "socket.connect":
            address = args[1][0] if isinstance(args[1], tuple) else args[1]
        elif event == "socket.getaddrinfo":
            address = args[0]
        elif event == "socket.sendto":
            address = args[2][0] if isinstance(args[2], tuple) else args[2]
        if address is None:
            return
        try:
            local = ipaddress.ip_address(address).is_loopback
        except ValueError:
            local = False
        if not local:
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"event": event, "host": str(address)}) + "\n")
            raise PermissionError("Acceptance process denies non-loopback network access")
    sys.addaudithook(audit)


async def seed():
    from motor.motor_asyncio import AsyncIOMotorClient
    from auth import hash_password
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    name, owner = os.environ["DB_NAME"], os.environ["MZ2_SMOKE_OWNER"]
    if name in await client.list_database_names():
        raise RuntimeError("Refusing nonempty or preexisting Acceptance database")
    database = client[name]
    await database.users.insert_one({"id": owner, "name": "Synthetic Acceptance Owner", "role": "owner",
        "email": os.environ["ADMIN_EMAIL"], "password_hash": hash_password(os.environ["ADMIN_PASSWORD"]),
        "is_active": True, "disabled": False})
    await database.mz2_atomic_owners.insert_one({"_id": owner, "writes_paused": True,
        "control_revision": 1, "revision": 0, "mezan2_managed": True,
        "control_reason": "Synthetic isolated Acceptance baseline; never unpause"})
    client.close()


def child():
    evidence = Path(os.environ["MZ2_SMOKE_EVIDENCE"])
    network_guard(evidence / "denied-network.jsonl")
    sys.path.insert(0, str(ROOT / "backend"))
    asyncio.run(seed())
    (evidence / "runtime-source-before.json").write_text(json.dumps(manifest(), indent=2), encoding="utf-8")
    # Exactly the shipped ASGI app and lifecycle, including supported idle workers.
    import server
    import uvicorn
    uvicorn.run(server.app, host="127.0.0.1", port=int(os.environ["MZ2_SMOKE_PORT"]), log_level="info")


async def execute(args):
    import httpx
    from motor.motor_asyncio import AsyncIOMotorClient
    before_source = manifest()
    if before_source["status"]:
        raise RuntimeError("Final Acceptance requires committed, clean source")
    args.evidence.mkdir(parents=True, exist_ok=False)
    network_guard(args.evidence / "denied-network.jsonl")
    run_id = uuid4().hex
    db_name, owner = "mz2_smoke_b_acceptance_" + run_id, "acceptance-owner-" + run_id
    base = f"http://127.0.0.1:{args.port}"
    validate_environment(base, args.mongo_uri, db_name)
    password = secrets.token_urlsafe(32)
    # No inherited provider keys, Mongo credentials, proxy settings or .env values.
    environment = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH") if key in os.environ}
    environment.update({"PYTHONUTF8": "1", "PYTHON_DOTENV_DISABLED": "1", "APP_ENV": "test",
        "TEST_RELEASE_STARTUP_KEY": "test:smoke-b-acceptance:" + before_source["head"],
        "MONGO_URL": args.mongo_uri, "DB_NAME": db_name, "JWT_SECRET": secrets.token_urlsafe(64),
        "ADMIN_EMAIL": "acceptance-" + run_id + "@example.com", "ADMIN_PASSWORD": password,
        "MZ2_SMOKE_OWNER": owner, "MZ2_SMOKE_EVIDENCE": str(args.evidence.resolve()),
        "MZ2_SMOKE_PORT": str(args.port), "BACKEND_STARTUP_DELAY_SECONDS": "0",
        "BACKEND_STARTUP_JITTER_SECONDS": "0", "AUTH_PUBLIC_REGISTRATION_ENABLED": "false",
        "AUTH_SECURITY_QUESTION_RESET_ENABLED": "false"})
    log = (args.evidence / "server.log").open("w", encoding="utf-8")
    process = None
    database_was_absent = False
    client = AsyncIOMotorClient(args.mongo_uri)
    result = {"status": "NOT_EXECUTED", "production_verified": False,
              "environment": {"kind": "isolated_acceptance", "base_url": base, "mongo_uri": args.mongo_uri,
                              "database": db_name, "owner": owner}, "source": before_source}
    try:
        if db_name in await client.list_database_names():
            raise RuntimeError("Refusing preexisting database; cleanup is not authorized")
        database_was_absent = True
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--child"],
            cwd=ROOT / "backend", env=environment, stdout=log, stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        result["pid"] = process.pid
        async with httpx.AsyncClient(base_url=base, timeout=30, trust_env=False, follow_redirects=False) as http:
            deadline = time.monotonic() + 240
            while True:
                if process.poll() is not None:
                    raise RuntimeError("Full app exited before readiness; inspect server.log")
                try:
                    health = await http.get("/health")
                    if health.status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("Full app readiness timeout; no acceptance probe sent")
                await asyncio.sleep(0.5)
            result["runtime_health"] = health.json()
            result["replica_identity"] = {key: value for key, value in (await client.admin.command("hello")).items()
                                          if key in {"setName", "hosts", "primary", "me", "isWritablePrimary"}}
            result["probe"] = await authenticated_probe(http, client[db_name], owner=owner,
                email=environment["ADMIN_EMAIL"], password=password)
            after_source = manifest()
            result["source_after"] = after_source
            denied = args.evidence / "denied-network.jsonl"
            if before_source != after_source or (denied.exists() and denied.stat().st_size):
                raise RuntimeError("Source changed or external request attempted; no PASS")
            result["status"] = result["probe"]["status"]
    except Exception as exc:
        result["status"] = "ACCEPTANCE_FAIL"
        result["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=10)
        result["cleanup"] = {"task_pid_stopped": process is None or process.poll() is not None,
                             "database": db_name}
        # Exact task-generated UUID name only; never enumerate targets for removal.
        if database_was_absent:
            await client.drop_database(db_name)
            result["cleanup"]["database_absent"] = db_name not in await client.list_database_names()
        else:
            result["cleanup"]["database_deletion_skipped"] = "Database ownership was not established"
        client.close(); log.close()
        (args.evidence / "result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    if result["status"] != "ACCEPTANCE_PROBE_PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    if sys.argv[1:] == ["--child"]:
        child()
    else:
        parser = argparse.ArgumentParser()
        parser.add_argument("--execute-after-c3-approved", action="store_true", required=True)
        parser.add_argument("--mongo-uri", required=True)
        parser.add_argument("--port", type=int, default=18769)
        parser.add_argument("--evidence", type=Path, required=True)
        arguments = parser.parse_args()
        asyncio.run(execute(arguments))
