"""Composition has durable fairness and a conservative global account budget."""
import asyncio
import importlib

import pytest

import google_ads_automation_cycles as cycles


class Control:
    def __init__(self): self.sequence = 0; self.fail = False; self.bad = None
    async def find_one_and_update(self, query, update, **kwargs):
        assert query == {"_id": cycles.ROTATION_ID}
        assert update == {"$inc": {"sequence": 1}} and kwargs["upsert"] is True
        self.sequence += 1
        if self.fail: raise RuntimeError("sensitive postcommit acknowledgement lost")
        return self.bad if self.bad is not None else {"_id": cycles.ROTATION_ID, "sequence": self.sequence}


class DB:
    def __init__(self): self.control = Control()
    def __getitem__(self, name): assert name == cycles.CONTROL; return self.control


def deps(monkeypatch, creation_count=0, management_count=0, status="complete"):
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", "true")
    calls = []
    async def creation(db, *, max_accounts):
        calls.append(("creation", max_accounts)); return {"status": status, "accounts": creation_count, "results": []}
    async def management(db):
        calls.append(("management", 4)); return {"status": status, "accounts": management_count, "results": []}
    return calls, dict(creation_cycle=creation, management_cycle=management)


@pytest.mark.asyncio
async def test_disabled_zero_db_and_no_feature_import(monkeypatch):
    monkeypatch.delenv("GOOGLE_ADS_AUTOMATION_WORKER_ENABLED", raising=False)
    class NoDB:
        def __getitem__(self, name): raise AssertionError("database accessed")
    assert await cycles.run_google_automation_cycles(NoDB()) == {"status": "disabled", "accounts": 0, "results": []}


@pytest.mark.asyncio
async def test_rotation_survives_module_restart_and_changes_priority(monkeypatch):
    db = DB(); calls, kwargs = deps(monkeypatch, creation_count=1, management_count=1)
    first = await cycles.run_google_automation_cycles(db, **kwargs)
    assert first["accounts"] == 1 and calls == [("creation", 4)]
    importlib.reload(cycles)
    second = await cycles.run_google_automation_cycles(db, **kwargs)
    assert second["accounts"] == 2 and calls[1:] == [("management", 4), ("creation", 3)]


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [0, 1, 2, 3, 4])
async def test_creation_priority_never_calls_unlimited_management_after_any_work(monkeypatch, n):
    calls, kwargs = deps(monkeypatch, creation_count=n, management_count=4)
    result = await cycles.run_google_automation_cycles(DB(), **kwargs)
    assert calls == ([("creation", 4), ("management", 4)] if n == 0 else [("creation", 4)])
    assert result["accounts"] <= 4


@pytest.mark.asyncio
@pytest.mark.parametrize("n", [0, 1, 2, 3, 4])
async def test_management_priority_limits_remaining_creation_slots(monkeypatch, n):
    db = DB(); db.control.sequence = 1
    calls, kwargs = deps(monkeypatch, management_count=n, creation_count=4-n)
    result = await cycles.run_google_automation_cycles(db, **kwargs)
    assert result["accounts"] == 4
    assert calls == ([("management", 4), ("creation", 4-n)] if n < 4 else [("management", 4)])


@pytest.mark.asyncio
async def test_lost_rotation_ack_aborts_and_next_child_uses_persisted_counter(monkeypatch):
    db = DB(); db.control.fail = True
    calls, kwargs = deps(monkeypatch)
    result = await cycles.run_google_automation_cycles(db, **kwargs)
    assert result["status"] == "failed" and not calls and db.control.sequence == 1
    assert "sensitive" not in str(result)
    db.control.fail = False
    await cycles.run_google_automation_cycles(db, **kwargs)
    assert calls == [("management", 4), ("creation", 4)]


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [{}, {"_id": "wrong", "sequence": 1}, {"_id": cycles.ROTATION_ID, "sequence": True}, {"_id": cycles.ROTATION_ID, "sequence": 0}])
async def test_malformed_rotation_ack_never_runs_features(monkeypatch, bad):
    db = DB(); db.control.bad = bad
    calls, kwargs = deps(monkeypatch)
    result = await cycles.run_google_automation_cycles(db, **kwargs)
    assert result["status"] == "failed" and not calls


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [True, -1, 5, "1", None])
async def test_unproved_feature_result_stops_second_feature(monkeypatch, count):
    calls, kwargs = deps(monkeypatch, creation_count=count)
    result = await cycles.run_google_automation_cycles(DB(), **kwargs)
    assert result["status"] == "failed" and calls == [("creation", 4)]


@pytest.mark.asyncio
async def test_recovery_runs_both_features_even_when_creation_flag_off(monkeypatch):
    calls, kwargs = deps(monkeypatch, status="recovery_only")
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED", "false")
    monkeypatch.setenv("GOOGLE_ADS_AUTOMATION_CREATION_ENABLED", "false")
    result = await cycles.run_google_automation_cycles(DB(), **kwargs)
    assert result["status"] == "recovery_only" and calls == [("creation", 4), ("management", 4)]


@pytest.mark.asyncio
async def test_cancel_is_propagated(monkeypatch):
    _, kwargs = deps(monkeypatch)
    async def cancelled(db, **kwargs): raise asyncio.CancelledError()
    kwargs["creation_cycle"] = cancelled
    with pytest.raises(asyncio.CancelledError): await cycles.run_google_automation_cycles(DB(), **kwargs)


def test_runner_loads_composite_without_importing_server(monkeypatch):
    import google_ads_automation_runner as runner
    import dotenv
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)
    factory, function = runner._load_runtime()
    assert function is cycles.run_google_automation_cycles
    assert runner.CYCLE_TIMEOUT_SECONDS == 450.0


@pytest.mark.asyncio
async def test_feature_errors_are_sanitized_and_prevent_other_feature(monkeypatch):
    calls, kwargs = deps(monkeypatch)
    async def bad(db, **kwargs):
        calls.append(("failed", 4)); raise RuntimeError("private account provider response")
    kwargs["creation_cycle"] = bad
    result = await cycles.run_google_automation_cycles(DB(), **kwargs)
    assert calls == [("failed", 4)] and result["status"] == "failed"
    assert "private" not in str(result) and "provider response" not in str(result)


@pytest.mark.asyncio
async def test_failed_feature_rows_remain_visible_to_runner_without_raw_data(monkeypatch):
    _, kwargs = deps(monkeypatch)
    async def failed(db, **kwargs):
        return {"status": "complete", "accounts": 1, "results": [{"status": "failed", "account_id": "private", "code": "provider_body"}]}
    kwargs["creation_cycle"] = failed
    result = await cycles.run_google_automation_cycles(DB(), **kwargs)
    assert result["results"] == [{"feature": "creation", "status": "failed", "accounts": 1}]
    assert result["accounts"] == 1 and "private" not in str(result)
