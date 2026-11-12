"""Idle runtime must not read, calculate, acquire evidence or write balances."""
from pathlib import Path

import pytest

import operational_balance_worker as worker


class NoDatabaseAccess:
    def __getitem__(self, name):
        pytest.fail('idle operational worker accessed a collection')

    def __getattr__(self, name):
        pytest.fail('idle operational worker accessed the database')


@pytest.mark.asyncio
async def test_legacy_tick_is_inert_without_even_an_accounting_gate():
    assert await worker.tick(NoDatabaseAccess()) is None
    assert await worker.tick(NoDatabaseAccess(), clock='2026-11-12T10:00:00+00:00') is None


@pytest.mark.asyncio
async def test_legacy_run_returns_without_scheduling_or_polling(monkeypatch):
    import asyncio
    def forbidden(*args, **kwargs):
        pytest.fail('idle operational worker scheduled background work')
    monkeypatch.setattr(asyncio, 'create_task', forbidden)
    monkeypatch.setattr(asyncio, 'sleep', forbidden)
    assert await worker.run(NoDatabaseAccess()) is None
    assert await worker.run(NoDatabaseAccess(), interval=30, source_interval=300) is None


def test_canonical_runtime_has_no_operational_balance_job_or_import():
    import ast
    import background_worker_runtime
    module = ast.parse(Path(background_worker_runtime.__file__).read_text())
    assert not any(isinstance(node, ast.ImportFrom) and node.module == 'operational_balance_worker'
                   for node in ast.walk(module))
    assert not any(isinstance(node, ast.Constant) and node.value == 'operational-balances'
                   for node in ast.walk(module))
