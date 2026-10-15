"""External collector contracts; mocked network, no Production requests."""
import importlib.util
import json
import os
from pathlib import Path
import time
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location("phase1_collector", Path(__file__).resolve().parents[2] / "scripts" / "observability_phase1_collector.py")
c = importlib.util.module_from_spec(spec)
spec.loader.exec_module(c)


def test_disabled_has_no_network_or_disk(tmp_path):
    with patch.object(c, "fetch", side_effect=AssertionError), patch.object(c, "BoundedStore", side_effect=AssertionError):
        assert c.main(["--output", str(tmp_path / "no")]) == 0
    assert not (tmp_path / "no").exists()


def test_schema_drops_secrets_and_unknown_labels():
    raw = {"token": "SECRET", "memory": {"process_rss_bytes": 12, "raw": "SECRET"}, "mongo": {"recent_query_shapes": ["SECRET"], "active_connections": 3}, "phase1": {"enabled": True, "worker": {"pid": 4, "token": "SECRET"}, "api": {"active": 2}, "histograms": {"api.duration.ready": {"count": 1, "sum": 0.2, "bounds": [1], "buckets": [1, 0], "customer": "SECRET"}, "SECRET": {"count": 1}}, "counters": {"api.status.503": 1, "SECRET": 2}}}
    out = c.sanitize(raw)
    assert "SECRET" not in json.dumps(out)
    assert out["histograms"]["api.duration.ready"]["count"] == 1
    assert out["mongo"]["active_connections"] == 3


def test_numeric_bounds_and_invalid_schema():
    assert c.sanitize({"phase1": []}) == {}
    out = c.sanitize({"phase1": {"worker": {"pid": "SECRET", "cpu_seconds": float("nan")}, "histograms": {"event_loop.lag": {"bounds": list(range(65)), "sum": float("inf")}}}})
    assert out["worker"] == {}
    assert out["histograms"]["event_loop.lag"] == {}


def test_unavailable_endpoint_no_exception_details():
    with patch.object(c, "build_opener") as mock:
        mock.return_value.open.side_effect = OSError("SECRET URL")
        out = c.fetch("https://127.0.0.1", "/api/health/diagnostics", "SECRET")
    assert out["outcome"] == "unavailable"
    assert "SECRET" not in json.dumps(out)


def test_no_redirect_and_private_inventory():
    assert c.NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil") is None
    for value in ("https://public.example", "http://8.8.8.8", "http://10.0.0.1", "http://user:pass@127.0.0.1", "http://127.0.0.1?token=a"):
        with pytest.raises(ValueError):
            c.validate_origin(value, private=True)
    assert c.validate_origin("http://127.0.0.1:8001", private=True)
    assert c.validate_origin("https://10.0.0.1:8001", private=True)


def test_rotation_retention_bounded(tmp_path):
    store = c.BoundedStore(tmp_path)
    old = tmp_path / "phase1-7.jsonl"
    old.write_text("{}")
    os.utime(old, (time.time() - 8 * 86400,) * 2)
    with patch.object(c, "FILE_BYTES", 256), patch.object(c, "MAX_FILES", 3):
        for _ in range(100):
            store.append({"sample": 123, "metric": [1] * 20})
    files = list(tmp_path.glob("phase1-*.jsonl"))
    assert len(files) <= 3
    assert sum(p.stat().st_size for p in files) <= 3 * 256
    assert not old.exists()


def test_once_serial_health_has_no_token(tmp_path, monkeypatch):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"workers": {"worker-1": "http://127.0.0.1:8001"}, "health_origin": "https://example.test"}))
    monkeypatch.setenv("INTERNAL_DIAGNOSTICS_TOKEN", "SECRET")
    with patch.object(c, "fetch", return_value={"outcome": "unavailable"}) as fetch:
        assert c.main(["--enabled", "--once", "--inventory", str(inventory), "--output", str(tmp_path / "output")]) == 0
    assert fetch.call_args_list[0].args[2] == "SECRET"
    assert len(fetch.call_args_list[1].args) == 2
    assert "SECRET" not in (tmp_path / "output" / "phase1-0.jsonl").read_text()


def test_worker_restart_observation_bounded():
    boots = c.WorkerBoots()
    def sample(pid, started):
        return {"metrics": {"worker": {"pid": pid, "started_at": started}}}
    assert boots.annotate("worker-1", sample(1, 100))["observed_restarts"] == 0
    assert boots.annotate("worker-1", sample(1, 100))["observed_boot_change"] is False
    boots.annotate("worker-1", {"outcome": "unavailable"})
    assert boots.annotate("worker-1", sample(1, 200))["observed_restarts"] == 1
    for n in range(100):
        boots.annotate(f"worker-{n}", sample(n, 200))
    assert len(boots.boots) == c.MAX_TARGETS
    assert len(boots.changes) == c.MAX_TARGETS


def test_retention_uses_oldest_record_not_recent_mtime(tmp_path):
    store = c.BoundedStore(tmp_path)
    old = tmp_path / "phase1-0.jsonl"
    old.write_text(json.dumps({"_stored_at": time.time() - 8 * 86400, "old": True}) + "\n")
    os.utime(old, None)
    store.append({"new": True})
    assert "old" not in old.read_text()


def test_daily_rotation(tmp_path):
    store = c.BoundedStore(tmp_path)
    with patch.object(c.time, "time", return_value=100000):
        store.append({"day": 1})
    with patch.object(c.time, "time", return_value=200000):
        store.append({"day": 2})
    assert (tmp_path / "phase1-1.jsonl").exists()


def test_health_not_repeated_within_sixty_seconds(tmp_path, monkeypatch):
    inventory = tmp_path / "inventory.json"
    inventory.write_text(json.dumps({"workers": {"worker-1": "http://127.0.0.1:8001"}, "health_origin": "https://example.test"}))
    monkeypatch.setenv("INTERNAL_DIAGNOSTICS_TOKEN", "SECRET")
    with patch.object(c, "fetch", return_value={"outcome": "unavailable"}) as fetch, patch.object(c.time, "monotonic", return_value=0), patch.object(c.time, "sleep", side_effect=[None, None, KeyboardInterrupt]):
        with pytest.raises(KeyboardInterrupt):
            c.main(["--enabled", "--inventory", str(inventory), "--output", str(tmp_path / "output")])
    assert len([call for call in fetch.call_args_list if call.args[1] == "/api/live"]) == 1
    assert len([call for call in fetch.call_args_list if call.args[1] == "/api/health/diagnostics"]) == 3
