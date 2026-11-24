"""Real pipe/signal shutdown exercises the new runner without live application I/O."""
import asyncio
import os
from pathlib import Path
import signal
import sys

import pytest


def test_priority_only_requests_a_lower_child_priority(monkeypatch):
    import operational_balance_automation_runner as runner
    called = []
    monkeypatch.setattr(runner.os, 'nice', lambda increment: called.append(increment) or 5, raising=False)
    assert runner.lower_cpu_priority() is True and called == [5]


def test_priority_failure_is_observable_and_portable(monkeypatch, caplog):
    import operational_balance_automation_runner as runner
    def denied(increment):
        raise PermissionError('PRIVATE-HOST-DETAILS')
    monkeypatch.setattr(runner.os, 'nice', denied, raising=False)
    assert runner.lower_cpu_priority() is False
    assert 'CPU priority unavailable' in caplog.text and 'PRIVATE' not in caplog.text
    monkeypatch.delattr(runner.os, 'nice')
    assert runner.lower_cpu_priority() is False
    assert 'CPU priority unsupported' in caplog.text


@pytest.mark.asyncio
async def test_automation_runner_refuses_unowned_start(monkeypatch):
    from operational_balance_automation_runner import run
    monkeypatch.delenv('MEZAN_BACKGROUND_WORKER_PROCESS', raising=False)
    with pytest.raises(RuntimeError, match='operational_automation_requires_parent'):
        await run()


@pytest.mark.asyncio
@pytest.mark.parametrize('stop_kind', ['parent_eof', 'sigterm'])
async def test_real_child_parent_death_or_signal_joins_work_before_client_close(tmp_path, stop_kind):
    backend = Path(__file__).resolve().parents[1]
    ready, joined, closed = (tmp_path / name for name in ('ready', 'joined', 'closed'))
    script = tmp_path / 'isolated_automation_runner_test.py'
    script.write_text(
        'import asyncio, pathlib, runpy, sys, types\n'
        f'sys.path.insert(0, {str(backend)!r})\n'
        f'ready, joined, closed = [pathlib.Path(p) for p in {[str(ready), str(joined), str(closed)]!r}]\n'
        'async def install(db):\n pass\n'
        'class Client:\n def close(self):\n  closed.write_text("joined" if joined.exists() else "premature")\n'
        'sys.modules["server"] = types.SimpleNamespace(db=object(), client=Client(), install_process_local_auth_security=install)\n'
        'import operational_balance_automation\n'
        'async def isolated(db, *, stop_event, cpu_priority_lowered):\n'
        ' ready.write_text("lowered" if cpu_priority_lowered else "unchanged")\n'
        ' try:\n  await asyncio.Event().wait()\n'
        ' finally:\n  await asyncio.sleep(.05)\n  joined.write_text("finished")\n'
        'operational_balance_automation.run = isolated\n'
        f'runpy.run_path({str(backend / "operational_balance_automation_runner.py")!r}, run_name="__main__")\n',
        encoding='utf-8')
    env = {**os.environ, 'MEZAN_BACKGROUND_WORKER_PROCESS': '1'}
    process = await asyncio.create_subprocess_exec(sys.executable, '-B', str(script), env=env,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(5):
            while not ready.exists():
                assert process.returncode is None
                await asyncio.sleep(.01)
        assert process.pid != os.getpid()
        assert ready.read_text() == 'lowered'
        if stop_kind == 'parent_eof':
            process.stdin.close()
        else:
            process.send_signal(signal.SIGTERM)
        assert await asyncio.wait_for(process.wait(), 5) == 0
        assert joined.read_text() == 'finished' and closed.read_text() == 'joined'
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
