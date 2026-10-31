import asyncio
import pytest
from integrations_control_center.google_product_test_review_reader import ReviewAdmission,ReviewAdmissionUnavailable

@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['deadline','caller_cancel'])
async def test_original_task_retains_owner_and_resource_slot_until_cancellation_cleanup_finishes(kind):
    gate=ReviewAdmission(maximum=1);cleanup=asyncio.Event();entered=asyncio.Event();finished=asyncio.Event();calls=[]
    async def original():
        try:await asyncio.Event().wait()
        finally:
            entered.set();await cleanup.wait();finished.set()
    request=asyncio.create_task(gate.run('owner',original,timeout=0.01 if kind=='deadline' else 5))
    await asyncio.sleep(0)
    actual=gate.active['owner']
    if kind=='caller_cancel':request.cancel()
    with pytest.raises(ReviewAdmissionUnavailable if kind=='deadline' else asyncio.CancelledError):await request
    await asyncio.wait_for(entered.wait(),0.5)
    assert gate.active['owner'] is actual and not actual.done()
    async def replacement():calls.append(True)
    with pytest.raises(ReviewAdmissionUnavailable,match='owner_operation'):await gate.run('owner',replacement)
    with pytest.raises(ReviewAdmissionUnavailable,match='process_resource'):await gate.run('another',replacement)
    assert calls==[] and not finished.is_set()
    cleanup.set()
    with pytest.raises(asyncio.CancelledError):await asyncio.wait_for(actual,0.5)
    await asyncio.sleep(0)
    assert finished.is_set() and gate.active=={}
    await gate.run('owner',replacement)
    assert calls==[True]

@pytest.mark.asyncio
async def test_source_error_releases_only_its_completed_task_without_orphans():
    gate=ReviewAdmission()
    async def fail():raise ValueError('synthetic')
    with pytest.raises(ValueError):await gate.run('owner',fail)
    assert not gate.active
