"""Explicit independent roles; the existing server entrypoint is unchanged.

No operational Release Guard lease is created here. Schema/worker coordination
uses the application's existing fenced Mongo startup-claim mechanism.
Synthetic execution additionally requires the loopback-only test boundary.
"""
from __future__ import annotations

import argparse
import base64
import asyncio
from contextlib import asynccontextmanager
import os
from pathlib import Path
import signal
import socket
import sys


SYNTHETIC = {
    "MONGO_URL": "mongodb://127.0.0.1:27017",
    "DB_NAME": "mezan_exit2c",
    "JWT_SECRET": "exit2c-public-synthetic-key-not-for-production",
    "ADMIN_PASSWORD": "Exit2C-synthetic-owner-password!",
    "APP_ENV": "test",
    "TEST_RELEASE_STARTUP_KEY": "test:exit2c-candidate",
    "EMAIL_OTP_SMTP_HOST": "127.0.0.1",
    "EMAIL_OTP_SMTP_PORT": "8025",
    "EMAIL_OTP_SMTP_STARTTLS": "false",
    "EMAIL_OTP_FROM_EMAIL": "otp@example.test",
}

# Public test fixture only. Never use with real credentials/data.
SALLA_SIMULATOR_PROFILE = "salla_http_simulator_v1"
SALLA_SIMULATOR_KEY = base64.urlsafe_b64encode(bytes(range(32))).decode("ascii")


def validate_before_import(role: str) -> None:
    if role not in {"web", "worker", "migration", "worker-sync", "worker-accounting", "worker-reports"}:
        raise RuntimeError("unknown independent role")
    root = Path(__file__).resolve().parent
    if any(root.rglob(".env*")):
        raise RuntimeError("independent runtime forbids packaged dotenv files")
    simulator = "MEZAN_ACCEPTANCE_PROFILE" in os.environ
    if simulator:
        if (os.environ.get("MEZAN_ACCEPTANCE_PROFILE") != SALLA_SIMULATOR_PROFILE
                or os.environ.get("APP_ENV") != "test" or role != "web"):
            raise RuntimeError("simulator profile requires test web role")
        if (os.environ.get("SALLA_API_BASE") != "http://127.0.0.1:8093/admin/v2"
                or os.environ.get("SALLA_AUTH_BASE") != "http://127.0.0.1:8093"
                or os.environ.get("SALLA_TOKEN_ENC_KEY") != SALLA_SIMULATOR_KEY
                or os.environ.get("MEZAN_WORKER_ENABLED") not in {None, "", "0"}):
            raise RuntimeError("simulator fixture configuration rejected")
        if any(value for key, value in os.environ.items()
               if key.lower() in {"http_proxy", "https_proxy", "all_proxy"}):
            raise RuntimeError("simulator proxy configuration rejected")
    if os.environ.get("APP_ENV") == "test":
        if any(os.environ.get(k) != v for k, v in SYNTHETIC.items()):
            raise RuntimeError("test runtime requires exact synthetic configuration")
        if sys.platform != "linux" or {n for _, n in socket.if_nameindex()} != {"lo"}:
            raise RuntimeError("test runtime requires Linux loopback-only namespace")
        forbidden = [k for k in os.environ if (
            any(s in k for s in ("TOKEN_ENC_KEY", "API_KEY", "CLIENT_SECRET", "SMTP_PASSWORD"))
        ) and not (simulator and k == "SALLA_TOKEN_ENC_KEY")]
        if forbidden:
            raise RuntimeError("provider credentials forbidden in synthetic runtime")
    elif os.environ.get("APP_ENV") == "staging":
        from independent_staging import validate
        validate()
    elif os.environ.get("APP_ENV", "production") != "production":
        raise RuntimeError("unsupported independent environment")
    if role == "worker" and os.environ.get("APP_ENV") != "test":
        raise RuntimeError("combined worker is retained for isolated historical acceptance only")
    os.environ["MEZAN_INDEPENDENT_RUNTIME"] = "1"


def load_server(role: str):
    validate_before_import(role)
    import server
    return server


def release_key(server) -> str:
    if os.environ.get("APP_ENV") == "staging":
        from independent_staging import validate
        return validate()
    from startup_guard import verified_release_key
    return verified_release_key(server.release_health_payload())


async def migration_complete(server, key: str) -> bool:
    from startup_guard import COLLECTION, LEASE_ID
    row = await server.db[COLLECTION].find_one(
        {"_id": f"{LEASE_ID}:independent-schema-v1:{key}", "status": "completed"}, {"_id": 1}
    )
    return row is not None


async def load_process_configuration(server) -> None:
    # Existing OAuth cache initialization, read-only and process-local. Never
    # initialize it in migration and assume other processes inherit that cache.
    from salla_integration.config_store import get_config
    from salla_integration.service import update_credentials_cache
    cfg = await get_config(server.db) or {}
    update_credentials_cache(cfg.get("client_id") or "", cfg.get("client_secret") or "")


async def migrate(server) -> str:
    from startup_guard import new_owner_id, run_release_startup

    async def initialize():
        from independent_schema import ensure_independent_schema
        await ensure_independent_schema(server)

    async def no_local_workers():
        return None

    return await run_release_startup(
        server.db, release_key="independent-schema-v1:" + release_key(server), owner_id=new_owner_id(),
        governor=server.governor, global_initialization=initialize,
        local_initialization=no_local_workers, wait_timeout=40, ttl_seconds=15,
    )


def create_web_app(role="web"):
    if role not in {"web", "worker-reports"}:
        raise RuntimeError("HTTP role required")
    server = load_server(role)
    key = release_key(server)  # Production fails closed without verified artifact.

    @asynccontextmanager
    async def lifespan(app):
        from auth import install_runtime_security
        from boot_runtime import process_local_readiness_event
        from runtime_mongo import bounded_readiness
        process_local_readiness_event.clear()  # Never release scheduler readiness in web.
        app.state.readiness = "starting"
        app.state.startup_phase = "independent_security_install"
        # Security is per process, before any request is served, without seed/index writes.
        await install_runtime_security(app, server.db, initialize_indexes=False)
        configuration_loaded = False

        async def assess():
            nonlocal configuration_loaded
            try:
                schema_ok = await asyncio.wait_for(migration_complete(server, key), timeout=3)
                mongo_ok = schema_ok and await bounded_readiness(server.client)
                if mongo_ok and not configuration_loaded:
                    await asyncio.wait_for(load_process_configuration(server), timeout=3)
                    configuration_loaded = True
            except Exception:
                mongo_ok = schema_ok = False
            app.state.readiness = "ready" if mongo_ok else "starting"
            app.state.startup_phase = "independent_web_ready" if mongo_ok else "migration_or_mongo_unavailable"

        async def monitor():
            while True:
                await asyncio.sleep(2)
                await assess()

        await assess()
        task = asyncio.create_task(monitor(), name="independent-readiness-monitor")
        if role == "web" and os.environ.get("MEZAN_REPORT_WORKER_URL"):
            import httpx
            app.state.report_worker_client = httpx.AsyncClient(
                timeout=httpx.Timeout(60, connect=3, pool=1),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
                trust_env=False, follow_redirects=False)
        try:
            yield
        finally:
            app.state.readiness = "stopped"
            process_local_readiness_event.clear()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if app.state.report_worker_client is not None:
                await app.state.report_worker_client.aclose()
            server.client.close()

    # Full original route surface + actual security. No rehearsal allow-list.
    # Router scheduler events and the original mixed lifecycle are not dispatched.
    server.app.router.lifespan_context = lifespan
    from independent_reports import install_report_boundary
    install_report_boundary(server.app, role)
    return server.app


async def worker(server, stop: asyncio.Event, role="worker") -> None:
    from startup_guard import (
        COLLECTION, LEASE_ID, claim_startup_lease, heartbeat_startup_lease, new_owner_id,
    )
    from boot_runtime import process_local_readiness_event
    key = release_key(server)
    if not await migration_complete(server, key):
        raise RuntimeError("migration is incomplete; worker refused")
    if os.environ.get("MEZAN_WORKER_ENABLED") != "1":
        raise RuntimeError("worker requires explicit arming")
    if role not in {"worker", "worker-sync", "worker-accounting"}:
        raise RuntimeError("unknown background worker role")
    if os.environ.get("APP_ENV") != "test" and os.environ.get("MEZAN_LEGACY_WORKERS_STOPPED") != "1":
        raise RuntimeError("independent workers require confirmed legacy shutdown before cutover")
    from independent_callbacks import classify_callbacks
    callbacks, stops = classify_callbacks(
        [cb for cb in server.app.router.on_startup if cb is not server.on_startup],
        [cb for cb in server.app.router.on_shutdown if cb is not server.on_shutdown],
    )
    if role == "worker-accounting":
        callbacks, stops = (), ()
    print("classified worker callbacks:", ", ".join(cb.__module__ + "." + cb.__qualname__ for cb in callbacks), flush=True)
    # Stable across candidate SHAs: old and new workers may never coexist.
    claim_key = "independent-worker:singleton" if role == "worker" else "independent-worker:" + role
    claim = await claim_startup_lease(server.db, claim_key, new_owner_id(), ttl_seconds=15)
    if claim.state != "leader":
        raise RuntimeError("another worker holds the fenced claim")
    tasks = []

    async def heartbeat():
        while True:
            await asyncio.sleep(3)
            if not await heartbeat_startup_lease(server.db, claim, ttl_seconds=15):
                raise RuntimeError("worker fence lost")

    async def initialize():
        # All lifecycle pairs were classified before claiming or starting tasks.
        await load_process_configuration(server)
        if role in {"worker", "worker-accounting"}:
            from integrations.qoyod.worker import start_worker as start_legacy
            from integrations.qoyod_manual.auto_send import start_worker as start_plan_b
            tasks.extend([start_legacy(server.db), start_plan_b(server.db)])
            if role == "worker-accounting":
                from operational_balance_worker import run as run_balances
                tasks.append(asyncio.create_task(run_balances(server.db), name="operational-balances"))
        if role in {"worker", "worker-sync"}:
            from salla_integration.service import salla_token_maintenance_loop
            tasks.append(asyncio.create_task(salla_token_maintenance_loop(server.db), name="salla-token-maintenance"))
        if role == "worker-sync":
            from salla_orders_v3.worker import start_salla_orders_v3_shadow_runtime
            shadow = await start_salla_orders_v3_shadow_runtime(server.db)
            if shadow is not None:
                tasks.append(shadow)
            from order_review_resume_worker import start_worker as start_review
            from assembly_completion_delivery import start_worker as start_assembly
            from salla_integration.shipping_label_inbox import start_worker as start_shipping
            from salla_integration.payment_update_inbox import start_worker as start_payment
            from operational_push import run as run_push
            for start in (start_review, start_assembly, start_shipping, start_payment):
                task = await start(server.db)
                if task is not None:
                    tasks.append(task)
            tasks.append(asyncio.create_task(run_push(server.db), name="operational-push"))
        for callback in callbacks:
            await callback()
        process_local_readiness_event.set()

    # Heartbeat and shutdown supervision begin before potentially slow startup.
    pulse = asyncio.create_task(heartbeat(), name="independent-worker-heartbeat")
    halt = asyncio.create_task(stop.wait(), name="independent-worker-stop")
    init = asyncio.create_task(initialize(), name="independent-worker-initialize")
    try:
        done, _ = await asyncio.wait([pulse, halt, init], return_when=asyncio.FIRST_COMPLETED)
        if pulse in done:
            await pulse
        if halt in done:
            return
        await init
        done, _ = await asyncio.wait([pulse, halt, *tasks], return_when=asyncio.FIRST_COMPLETED)
        if halt not in done:
            for task in done:
                await task
            raise RuntimeError("worker task ended unexpectedly")
    finally:
        process_local_readiness_event.clear()
        init.cancel()
        for task in tasks:
            task.cancel()
        from independent_callbacks import drain_worker_tasks
        try:
            # Include unfinished initialization in the same bounded drain.
            await drain_worker_tasks([init, *tasks], reversed(stops))
        finally:
            # Heartbeat stays alive through normal draining; errors retain claim.
            for task in [pulse, halt]:
                task.cancel()
            await asyncio.gather(pulse, halt, return_exceptions=True)
        await server.db[COLLECTION].delete_one({
            "_id": f"{LEASE_ID}:{claim.release_key}", "owner_id": claim.owner_id,
            "fence": claim.fence, "status": "running",
        })


async def run_role(role):
    server = load_server(role)
    try:
        if role == "migration":
            print("migration role:", await migrate(server))
        else:
            stop = asyncio.Event()
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)
            await worker(server, stop, role)
    finally:
        server.client.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("role", choices=["web", "worker", "migration", "worker-sync", "worker-accounting", "worker-reports"])
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if args.role in {"web", "worker-reports"}:
        import uvicorn
        uvicorn.run(create_web_app(args.role), host="127.0.0.1" if os.environ.get("APP_ENV") == "test" else "0.0.0.0",
                    port=args.port, access_log=False)
    else:
        asyncio.run(run_role(args.role))


if __name__ == "__main__":
    main()
