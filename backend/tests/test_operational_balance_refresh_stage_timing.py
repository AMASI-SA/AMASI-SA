"""Visible refresh diagnostics expose timings, never financial identities."""
import asyncio
import logging

from fastapi import HTTPException
import pytest

from operational_demand_refresh import LEASES, refresh_stage, run_visible_refresh
from test_operational_balance_demand_refresh import DB, Request, allowed
from test_operational_balance_demand_refresh_routes import post


def timing_messages(caplog):
    return [record.getMessage() for record in caplog.records
            if record.name == 'operational_demand_refresh'
            and record.getMessage().startswith('operational_refresh_stage ')]


def test_actual_visible_route_records_completed_read_and_commit_stages(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    database = DB()
    response, _ = asyncio.run(post(database, monkeypatch))
    assert response.status_code == 200
    messages = timing_messages(caplog)
    for stage in ('native_evidence', 'snapshot_load', 'snapshot_compare',
                  'snapshot_pack', 'snapshot_commit', 'aggregate'):
        rows = [message for message in messages if f'stage={stage} ' in message]
        assert len(rows) == 1
        assert rows[0].endswith('outcome=complete')
        assert float(rows[0].split('duration_ms=')[1].split()[0]) >= 0
    assert database.data[LEASES] == {}


@pytest.mark.parametrize('payload,principal', [({'visible': False}, None),
                                               ({'visible': True}, {})])
def test_hidden_or_unauthorized_request_emits_no_refresh_stage(monkeypatch, caplog,
                                                            payload, principal):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    database = DB()
    response, _ = asyncio.run(post(database, monkeypatch, payload=payload, principal=principal))
    assert response.status_code in {401, 422}
    assert timing_messages(caplog) == []
    assert database.writes == []


def test_deadline_identifies_cancelled_stage_and_joins_before_return(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    monkeypatch.setattr('operational_demand_refresh.REQUEST_SECONDS', 0.02)

    async def scenario():
        database, request = DB(), Request()
        stopped = False

        async def operation():
            nonlocal stopped
            with refresh_stage('source_collection'):
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped = True

        with pytest.raises(HTTPException) as failure:
            await run_visible_refresh(request, database, 'owner', 'actor-private-fixture',
                                      allowed, operation)
        assert failure.value.status_code == 408
        assert stopped and database.data[LEASES] == {}
        assert not [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]

    asyncio.run(scenario())
    messages = timing_messages(caplog)
    assert len(messages) == 1
    assert 'stage=source_collection ' in messages[0]
    assert messages[0].endswith('outcome=cancelled')
    assert 'actor-private-fixture' not in messages[0] and 'owner' not in messages[0]


def test_failure_log_omits_exception_details_and_stays_request_local(caplog):
    caplog.set_level(logging.INFO, logger='operational_demand_refresh')
    private_details = 'synthetic order=secret-fixture amount=9182.37 token=never-log'

    async def scenario():
        database = DB()

        async def operation():
            with refresh_stage('profit_snapshot'):
                raise ValueError(private_details)

        with pytest.raises(ValueError, match='synthetic order'):
            await run_visible_refresh(Request(), database, 'owner', 'staff', allowed, operation)
        assert database.data[LEASES] == {}
        with refresh_stage('outside_request'):
            pass

    asyncio.run(scenario())
    messages = timing_messages(caplog)
    assert len(messages) == 1
    assert 'stage=profit_snapshot ' in messages[0]
    assert messages[0].endswith('outcome=failed')
    assert private_details not in caplog.text
    assert 'outside_request' not in caplog.text
