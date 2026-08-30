import asyncio
from pathlib import Path

import pytest

import resource_governor as resources


def _write(root: Path, name: str, value: str) -> None:
    (root / name).write_text(value, encoding="ascii")


def test_cgroup_v2_snapshot_reads_limit_peak_and_events(tmp_path):
    _write(tmp_path, "memory.current", "700")
    _write(tmp_path, "memory.max", "1000")
    _write(tmp_path, "memory.peak", "850")
    _write(tmp_path, "memory.events", "low 2\noom 1\noom_kill 0\n")

    snapshot = resources.memory_snapshot(tmp_path)

    assert snapshot.current_bytes == 700
    assert snapshot.max_bytes == 1000
    assert snapshot.peak_bytes == 850
    assert snapshot.usage_ratio == pytest.approx(0.7)
    assert snapshot.events == {"low": 2, "oom": 1, "oom_kill": 0}


@pytest.mark.parametrize("raw", ["max", "", "not-a-number"])
def test_unlimited_or_invalid_memory_max_is_unknown(tmp_path, raw):
    _write(tmp_path, "memory.current", "123")
    if raw:
        _write(tmp_path, "memory.max", raw)
    snapshot = resources.memory_snapshot(tmp_path)
    assert snapshot.max_bytes is None
    assert snapshot.usage_ratio is None


def test_missing_cgroup_files_are_safe(tmp_path):
    snapshot = resources.memory_snapshot(tmp_path)
    assert snapshot.current_bytes is None
    assert snapshot.max_bytes is None
    assert snapshot.events == {}


def test_hysteresis_blocks_until_below_resume(monkeypatch):
    governor = resources.ResourceGovernor()
    values = iter([0.81, 0.75, 0.69])

    def snapshot():
        ratio = next(values)
        return resources.MemorySnapshot(1, 1, 1, {}, 1, None, 1, ratio)

    monkeypatch.setattr(resources, "memory_snapshot", snapshot)
    assert governor.decision()[0] == "blocked"
    assert governor.decision()[0] == "blocked"
    assert governor.decision()[0] == "normal"


@pytest.mark.asyncio
async def test_admission_blocks_only_heavy_work(monkeypatch):
    governor = resources.ResourceGovernor()
    monkeypatch.setattr(
        resources,
        "memory_snapshot",
        lambda: resources.MemorySnapshot(85, 100, 85, {}, 1, None, 1, 0.85),
    )
    with pytest.raises(resources.ResourcePressure):
        async with governor.heavy("snapchat", task_name="test"):
            pass
    # Ordinary event-loop/API work is not coupled to the heavy semaphore.
    assert await asyncio.wait_for(asyncio.sleep(0, result="read-ok"), 0.1) == "read-ok"


def test_critical_pressure_requests_checkpoint_cancellation(monkeypatch):
    governor = resources.ResourceGovernor()
    monkeypatch.setattr(
        resources,
        "memory_snapshot",
        lambda: resources.MemorySnapshot(86, 100, 86, {}, 1, None, 1, 0.86),
    )
    with pytest.raises(resources.CooperativeCancellation, match="resource_pressure"):
        governor.safe_checkpoint()
