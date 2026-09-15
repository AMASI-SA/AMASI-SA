"""Test-only entry boundary. Never a Production entrypoint or release identity."""
import os
import socket
import sys
from contextlib import asynccontextmanager
from pathlib import Path

SYNTHETIC_ENV = {
    "MEZAN_EXIT2A_REHEARSAL": "1",
    "MONGO_URL": "mongodb://127.0.0.1:27017",
    "DB_NAME": "mezan_exit2a",
    "JWT_SECRET": "exit2a-public-synthetic-key-not-for-production",
    "PYTHONDONTWRITEBYTECODE": "1",
    "PYTHONUNBUFFERED": "1",
}
SYSTEM_ENV_NAMES = {
    "PATH", "HOSTNAME", "LANG", "HOME", "PYTHON_VERSION", "PYTHON_SHA256",
    "GPG_KEY", "LC_ALL", "TZ",
}
ALLOWED_REQUESTS = {
    ("GET", "/api/live"), ("GET", "/api/ready"), ("GET", "/api/health"),
    ("GET", "/api/auth/me"), ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"), ("POST", "/api/auth/refresh"),
    ("GET", "/api/orders"),
}


def validate_environment(environ, backend_root):
    # Fail before dotenv/application imports. Never echo rejected values.
    if set(environ) - set(SYNTHETIC_ENV) - SYSTEM_ENV_NAMES:
        raise RuntimeError("rehearsal rejects unapproved environment names")
    if any(environ.get(k) != v for k, v in SYNTHETIC_ENV.items()):
        raise RuntimeError("rehearsal requires exact disposable configuration")
    if any(backend_root.rglob(".env*")):
        raise RuntimeError("rehearsal forbids packaged dotenv files")


def validate_network():
    # Docker harness supplies network=container:<network-none Mongo container>.
    # No routable interface, including IPv6; runtime has no NET_ADMIN capability.
    if sys.platform != "linux" or {name for _, name in socket.if_nameindex()} != {"lo"}:
        raise RuntimeError("rehearsal requires Linux loopback-only network namespace")


class RehearsalRequests:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and (scope["method"], scope["path"]) not in ALLOWED_REQUESTS:
            from starlette.responses import JSONResponse
            await JSONResponse({"detail": "rehearsal_route_not_allowed"}, status_code=403)(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await self.app(scope, receive, send)


def create_app(backend_root=None):
    root = Path(backend_root or "/opt/mezan/backend").resolve()
    validate_environment(os.environ, root)
    validate_network()
    sys.path.insert(0, str(root))
    import server  # Actual application, all real imports; no substitute modules.

    @asynccontextmanager
    async def lifespan(app):
        # Deliberately do not invoke router startup/shutdown event dispatch.
        # No migrations, indexes, seed_admin, global lease or scheduler tasks.
        from boot_runtime import process_local_readiness_event
        process_local_readiness_event.clear()
        app.state.readiness = "starting"
        app.state.startup_phase = "rehearsal_ping"
        try:
            import asyncio
            await asyncio.wait_for(server.client.admin.command("ping"), timeout=3)
            app.state.startup_phase = "rehearsal_ready_no_initialization"
            app.state.readiness = "ready"
            yield
        finally:
            app.state.readiness = "stopped"
            process_local_readiness_event.clear()
            server.client.close()

    server.app.router.lifespan_context = lifespan
    server.app.add_middleware(RehearsalRequests)
    return server.app
