import pytest
from fastapi import HTTPException

import dashboard_v2_routes as dashboard
from resource_governor import ResourcePressure


class RefusingGovernor:
    def heavy(self, *args, **kwargs):
        class Context:
            async def __aenter__(self): raise ResourcePressure("resource_pressure")
            async def __aexit__(self, *args): return False
        return Context()


@pytest.mark.asyncio
async def test_dashboard_pressure_response_is_retryable_and_never_false_complete(monkeypatch):
    monkeypatch.setattr(dashboard, "governor", RefusingGovernor())
    called = False

    @dashboard._heavy_dashboard_stage("dashboard_v2_summary")
    async def endpoint():
        nonlocal called
        called = True
        return {"total_sales": 0}

    with pytest.raises(HTTPException) as caught:
        await endpoint()
    assert caught.value.status_code == 503
    assert caught.value.detail == {
        "code": "resource_pressure", "retryable": True, "data_complete": False,
    }
    assert called is False
