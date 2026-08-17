from pathlib import Path


SERVER = Path(__file__).resolve().parents[1] / "server.py"


def test_heavy_background_jobs_do_not_start_inside_web_process():
    source = SERVER.read_text(encoding="utf-8")

    assert "app.state.campaign_ai_monitor_task = None" in source
    assert "app.state.customer_learning_task = None" in source

    assert "_asyncio.create_task(_ad_account_halfhour_sync())" not in source
    assert "_asyncio.create_task(_ad_spend_window_post_loop())" not in source
    assert "_asyncio.create_task(_bnpl_hourly_auto_sync())" not in source
    assert "_asyncio.create_task(_tamara_attribution_daily_sweep())" not in source
    assert "_asyncio.create_task(_tamara_attribution_startup_migration())" not in source


def test_core_salla_and_qoyod_paths_remain_enabled():
    source = SERVER.read_text(encoding="utf-8")

    assert "salla_token_maintenance_loop(db)" in source
    assert "_qoyod_worker_start(db, interval_sec=5.0, batch_limit=25)" in source
    assert "_qoyod_plan_b_auto_start(db, interval_sec=15.0, batch_limit=5)" in source
