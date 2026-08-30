import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Response
import httpx
import pytest
from startup_guard import verified_release_key

from boot_runtime import (
    cancel_deferred_task, is_ready, readiness_traffic_gate, start_deferred_task,
)


def _boot_app(initializer):
    app = FastAPI()
    app.state.readiness = "starting"
    app.state.startup_phase = "post_liveness_delay"
    app.middleware("http")(readiness_traffic_gate)

    @app.get("/live")
    @app.get("/api/live")
    async def live(): return {"live": True}

    @app.get("/health")
    @app.get("/api/health")
    async def health(response: Response):
        response.status_code = 200 if is_ready(app) else 503
        return {"ok": True, "release": {"source_git_sha": "a" * 40}}

    @app.get("/ready")
    async def ready(response: Response):
        value = app.state.readiness == "ready"
        response.status_code = 200 if value else 503
        return {"ready": value}

    @app.get("/api/read")
    async def api_read(): return {"ok": True}

    @app.on_event("startup")
    async def startup(): start_deferred_task(app, initializer)

    @app.on_event("shutdown")
    async def shutdown(): await cancel_deferred_task(app)
    return app


@pytest.mark.asyncio
async def test_asgi_liveness_readiness_success_and_no_early_heavy_work():
    release = asyncio.Event()
    started = asyncio.Event()
    app = None

    async def initializer():
        started.set()
        await release.wait()
        app.state.readiness = "ready"
        app.state.startup_phase = "ready"

    app = _boot_app(initializer)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/live")).status_code == 200
            assert (await client.get("/api/live")).status_code == 200
            assert (await client.get("/health")).status_code == 503
            assert (await client.get("/api/health")).status_code == 503
            assert (await client.get("/ready")).status_code == 503
            assert (await client.get("/api/read")).status_code == 503
            await started.wait()
            release.set()
            await app.state.deferred_startup_task
            assert (await client.get("/health")).status_code == 200
            assert (await client.get("/api/health")).status_code == 200
            assert (await client.get("/ready")).status_code == 200
            assert (await client.get("/api/read")).status_code == 200


@pytest.mark.asyncio
async def test_asgi_failure_stays_failed_and_shutdown_cancels_task():
    async def failing(): raise RuntimeError("boom")
    app = _boot_app(failing)
    async with app.router.lifespan_context(app):
        with pytest.raises(RuntimeError):
            await app.state.deferred_startup_task
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/live")).status_code == 200
            assert (await client.get("/health")).status_code == 503
            assert (await client.get("/ready")).status_code == 503
        assert app.state.readiness == "failed"

    blocker = asyncio.Event()
    async def hanging(): await blocker.wait()
    app2 = _boot_app(hanging)
    async with app2.router.lifespan_context(app2):
        task = app2.state.deferred_startup_task
    assert task.done()


@pytest.mark.asyncio
async def test_missing_release_identity_fails_readiness_before_initialization():
    initialization_called = False

    async def initializer():
        nonlocal initialization_called
        verified_release_key(
            {"release": {"source_git_sha": None}},
            environment={"APP_ENV": "production"},
        )
        initialization_called = True

    app = _boot_app(initializer)
    async with app.router.lifespan_context(app):
        with pytest.raises(ValueError, match="verified release source_git_sha"):
            await app.state.deferred_startup_task
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            assert (await client.get("/live")).status_code == 200
            assert (await client.get("/health")).status_code == 503
            assert (await client.get("/ready")).status_code == 503
        assert app.state.readiness == "failed"
        assert initialization_called is False
