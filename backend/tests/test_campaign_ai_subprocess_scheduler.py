import asyncio
from types import SimpleNamespace

import campaign_ai_subprocess_scheduler as scheduler


def test_scheduler_defaults_to_enabled_outside_tests(monkeypatch):
    monkeypatch.delenv("MEZAN_CAMPAIGN_AI_SUBPROCESS_SCHEDULER_ENABLED", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("MEZAN_TESTING", raising=False)
    assert scheduler.scheduler_enabled() is True


def test_scheduler_can_be_disabled_explicitly(monkeypatch):
    monkeypatch.setenv("MEZAN_CAMPAIGN_AI_SUBPROCESS_SCHEDULER_ENABLED", "false")
    assert scheduler.scheduler_enabled() is False


def test_pytest_guard_disables_implicit_child_launch(monkeypatch):
    monkeypatch.delenv("MEZAN_CAMPAIGN_AI_SUBPROCESS_SCHEDULER_ENABLED", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_example.py::test_example (call)")
    assert scheduler.scheduler_enabled() is False


def test_worker_is_launched_as_separate_python_process(monkeypatch):
    captured = {}

    class FakeProcess:
        returncode = 0

        async def communicate(self):
            return b'{"users": 1, "completed": 1, "failed": 0}', b""

        def terminate(self):
            captured["terminated"] = True

        def kill(self):
            captured["killed"] = True

        async def wait(self):
            return 0

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    code = asyncio.run(scheduler.run_worker_once(timeout_seconds=60))

    assert code == 0
    assert captured["args"][0] == scheduler.sys.executable
    assert captured["args"][1] == str(scheduler.WORKER_PATH)
    assert captured["kwargs"]["cwd"] == str(scheduler.ROOT_DIR)
    assert captured["kwargs"]["env"]["MEZAN_CAMPAIGN_AI_CHILD_PROCESS"] == "1"


def test_successful_cycle_waits_remaining_five_hour_interval():
    delay = scheduler.next_scheduler_delay(
        0,
        elapsed=120,
        interval=5 * 60 * 60,
        retry_delay=15 * 60,
        cadence_recheck=5 * 60,
    )
    assert delay == 5 * 60 * 60 - 120


def test_global_cadence_skip_rechecks_in_five_minutes_not_five_hours():
    delay = scheduler.next_scheduler_delay(
        scheduler.CADENCE_SKIP_EXIT_CODE,
        elapsed=1,
        interval=5 * 60 * 60,
        retry_delay=15 * 60,
        cadence_recheck=5 * 60,
    )
    assert delay == 5 * 60


def test_real_worker_failure_keeps_distinct_fifteen_minute_retry():
    delay = scheduler.next_scheduler_delay(
        2,
        elapsed=5,
        interval=5 * 60 * 60,
        retry_delay=15 * 60,
        cadence_recheck=5 * 60,
    )
    assert delay == 15 * 60


def test_router_registration_is_idempotent(monkeypatch):
    registered = {"startup": [], "shutdown": []}

    class FakeRouter:
        def on_event(self, event):
            def decorator(func):
                registered[event].append(func)
                return func
            return decorator

    router = FakeRouter()
    scheduler.attach_campaign_ai_subprocess_scheduler(router)
    scheduler.attach_campaign_ai_subprocess_scheduler(router)

    assert len(registered["startup"]) == 1
    assert len(registered["shutdown"]) == 1
