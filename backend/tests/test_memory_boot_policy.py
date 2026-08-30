from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# Liveness/readiness behavior is exercised through real ASGI requests and
# lifespan events in test_asgi_boot_readiness.py. Keep only the lease-path
# contract here because it spans independently composed routers.


def test_snapchat_scheduler_uses_single_existing_pipeline_lease_path():
    scheduler = (ROOT / "snapchat_v2" / "scheduler.py").read_text(encoding="utf-8")
    pipeline = (ROOT / "snapchat_v2" / "sync_pipeline.py").read_text(encoding="utf-8")
    assert "MAX_PARALLEL_ACCOUNTS = 1" in scheduler
    assert "SnapchatV2SyncPipeline(db).run" in scheduler
    assert "acquire_lease(" in pipeline
    assert "release_lease(" in pipeline
    assert "asyncio.gather" not in scheduler
