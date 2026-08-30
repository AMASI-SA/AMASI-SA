from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_health_is_local_and_does_not_wait_for_heavy_governor():
    source = (ROOT / "server.py").read_text(encoding="utf-8")
    start = source.index("async def health_check")
    end = source.index("# ── Dependencies", start)
    health_source = source[start:end]
    assert "release_health_payload()" in health_source
    assert "await db." not in health_source
    assert "governor.heavy" not in health_source


def test_startup_returns_after_scheduling_deferred_initialization():
    source = (ROOT / "server.py").read_text(encoding="utf-8")
    start = source.index('async def on_startup():')
    end = source.index('@app.on_event("shutdown")', start)
    startup_source = source[start:end]
    assert "deferred-backend-initialization" in startup_source
    assert "await _deferred_startup()" in startup_source  # inside delayed task
    assert "app.state.readiness = \"starting\"" in startup_source


def test_snapchat_scheduler_uses_single_existing_pipeline_lease_path():
    scheduler = (ROOT / "snapchat_v2" / "scheduler.py").read_text(encoding="utf-8")
    pipeline = (ROOT / "snapchat_v2" / "sync_pipeline.py").read_text(encoding="utf-8")
    assert "MAX_PARALLEL_ACCOUNTS = 1" in scheduler
    assert "SnapchatV2SyncPipeline(db).run" in scheduler
    assert "acquire_lease(" in pipeline
    assert "release_lease(" in pipeline
    assert "asyncio.gather" not in scheduler
