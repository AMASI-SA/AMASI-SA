"""Employee-role read identity differs from merchant identity inside fulfillment."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from operational_atomic import OperationalDatabase


def capability():
    assignments = SimpleNamespace(name='mezan_role_assignments_v2', find_one=AsyncMock(
        return_value={'owner_user_id': 'owner', 'user_id': 'employee'}))
    ledger = SimpleNamespace(name='general_ledger', insert_one=AsyncMock())
    state = {'profile': 'fulfillment', 'failed': False}
    session = object()
    db = OperationalDatabase({'mezan_role_assignments_v2': assignments, 'general_ledger': ledger},
                             session, state, 'owner')
    return db, assignments, ledger, session, state


@pytest.mark.asyncio
async def test_employee_assignment_read_retains_explicit_owner_and_session():
    db, assignments, _, session, state = capability()
    query = {'owner_user_id': 'owner', 'user_id': 'employee'}
    assert (await db['mezan_role_assignments_v2'].find_one(query))['user_id'] == 'employee'
    assignments.find_one.assert_awaited_once_with(query, session=session)
    assert state['failed'] is False


@pytest.mark.asyncio
@pytest.mark.parametrize('query', [
    {'owner_user_id': 'foreign', 'user_id': 'employee'},
    {'user_id': 'employee'},
    {'owner_user_id': None, 'created_by': 'foreign', 'user_id': 'employee'},
    {'owner_user_id': 'foreign', 'created_by': 'owner', 'user_id': 'employee'},
])
async def test_foreign_or_unproven_assignment_is_refused(query):
    db, assignments, _, _, state = capability()
    with pytest.raises(HTTPException) as caught:
        await db['mezan_role_assignments_v2'].find_one(query)
    assert caught.value.status_code == 409
    assert caught.value.detail['code'] == 'operational_owner_scope_conflict'
    assert state['failed'] is True
    assignments.find_one.assert_not_awaited()


@pytest.mark.asyncio
async def test_role_read_does_not_grant_financial_write_capability():
    db, _, ledger, _, state = capability()
    with pytest.raises(HTTPException) as caught:
        await db['general_ledger'].insert_one({'user_id': 'owner', 'amount': 1})
    assert caught.value.detail['code'] == 'operational_financial_write_forbidden'
    assert state['failed'] is True
    ledger.insert_one.assert_not_awaited()
