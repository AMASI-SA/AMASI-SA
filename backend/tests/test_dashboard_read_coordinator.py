import asyncio
import pytest
from dashboard_read_coordinator import DashboardReadCoordinator


@pytest.mark.asyncio
async def test_independent_tenants_enter_without_waiting_for_slow_tenant():
    from dashboard_read_coordinator import BoundedDashboardAdmission
    from types import SimpleNamespace
    admission = BoundedDashboardAdmission()
    governor = SimpleNamespace(decision=lambda: ("normal", None))
    coordinator = DashboardReadCoordinator()
    entered, slow = [], asyncio.Event()
    @coordinator.endpoint(lambda user: user)
    async def endpoint(user=None):
        async with admission.admit(governor):
            entered.append(user['id'])
            if user['id'] == 'A':
                await slow.wait()
            return user['id']
    first = asyncio.create_task(endpoint(user={'id': 'A'}))
    for _ in range(3):
        await asyncio.sleep(0)
    assert await asyncio.wait_for(asyncio.gather(endpoint(user={'id': 'B'}), endpoint(user={'id': 'C'})), 1) == ['B', 'C']
    assert entered == ['A', 'B', 'C']
    assert not first.done()
    slow.set()
    assert await first == 'A'
    assert admission._loops[asyncio.get_running_loop()] == 0


@pytest.mark.asyncio
async def test_bounded_admission_rejects_pressure_and_capacity_then_releases_on_failure():
    from dashboard_read_coordinator import BoundedDashboardAdmission
    from resource_governor import ResourcePressure
    from fastapi import HTTPException
    from types import SimpleNamespace
    admission = BoundedDashboardAdmission(capacity=1)
    governor = SimpleNamespace(decision=lambda: ('normal', None))
    with pytest.raises(ValueError, match='fixture'):
        async with admission.admit(governor):
            with pytest.raises(HTTPException) as busy:
                async with admission.admit(governor):
                    pytest.fail('over-capacity request entered')
            assert busy.value.detail['code'] == 'dashboard_busy'
            raise ValueError('fixture')
    async with admission.admit(governor):
        pass
    for state in ['blocked', 'cancel']:
        governor.decision = lambda: (state, None)
        with pytest.raises(ResourcePressure):
            async with admission.admit(governor):
                pytest.fail('memory pressure ignored')


@pytest.mark.asyncio
async def test_duplicate_requests_share_work_but_not_future_or_other_owner_requests():
    coordinator = DashboardReadCoordinator()
    calls = []
    release = asyncio.Event()
    @coordinator.endpoint(lambda user: user)
    async def endpoint(period="today", user=None):
        calls.append((user["id"], period))
        await release.wait()
        return {"owner": user["id"], "period": period}
    pending = [asyncio.create_task(endpoint(user={"id": "a"})) for _ in range(20)]
    pending += [asyncio.create_task(endpoint(user={"id": "b"})),
                asyncio.create_task(endpoint(period="yesterday", user={"id": "a"}))]
    for _ in range(5):
        await asyncio.sleep(0)
    assert sorted(calls) == [("a", "today"), ("a", "yesterday"), ("b", "today")]
    release.set()
    results = await asyncio.gather(*pending)
    assert all(r == {"owner": "a", "period": "today"} for r in results[:20])
    assert results[-2]["owner"] == "b"
    await endpoint(user={"id": "a"})
    assert len(calls) == 4  # no retained cache or stale reuse


@pytest.mark.asyncio
async def test_disconnected_waiter_does_not_cancel_shared_work_and_errors_not_cached():
    coordinator = DashboardReadCoordinator()
    release = asyncio.Event()
    calls = 0
    async def fail():
        nonlocal calls
        calls += 1
        await release.wait()
        raise ValueError("fixture failure")
    first = asyncio.create_task(coordinator.run("key", fail))
    second = asyncio.create_task(coordinator.run("key", fail))
    for _ in range(3):
        await asyncio.sleep(0)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    release.set()
    with pytest.raises(ValueError, match="fixture failure"):
        await second
    with pytest.raises(ValueError):
        await coordinator.run("key", fail)
    assert calls == 2
