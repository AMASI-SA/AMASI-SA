"""A proven local terminal state cannot invent missing common-guard evidence.

These are deterministic tests against a transactional fake, not provider or
production evidence. No test enables a production flag or invokes a provider.
"""
from copy import deepcopy
from fastapi import HTTPException
import pytest
from integrations_control_center import google_ads_automation_authority as authority
from integrations_control_center import google_ads_automation_budget_guard as guard
from integrations_control_center.google_ads_automation_policy import AUTOMATION_POLICIES
from integrations_control_center.google_ads_management import FENCES, PROPOSALS
from tests.test_google_ads_automation_authority import DB, NOW, authorize, case_documents, freeze_guard_clock


@pytest.mark.asyncio
@pytest.mark.parametrize('terminal', ['cancelled_before_dispatch', 'verified_completed'])
async def test_terminal_legacy_claim_without_shared_allocation_remains_fenced(monkeypatch, terminal):
    freeze_guard_clock(monkeypatch, NOW)
    monkeypatch.setattr(guard, 'budget_guard_runtime_available', lambda: True)
    monkeypatch.setenv('GOOGLE_ADS_AUTOMATION_EXECUTION_ENABLED', 'true')
    docs, native = case_documents(); db = DB(docs)
    await authorize(db, native)
    # Model an existing private policy claim predating shared allocation.
    db[guard.BUDGET_GUARDS].rows = []
    row = db[PROPOSALS].rows[0]
    if terminal == 'cancelled_before_dispatch':
        row.update(status='failed', submission_started=False,
                   safe_error='execution_cancelled_before_dispatch', provider_write_reached=False)
    else:
        row.update(status='completed', submission_started=True, verified=True,
                   verification={'verified': True, 'mismatches': [], 'coverage_gaps': []})
    private_before = deepcopy(db[AUTOMATION_POLICIES].rows[0])
    fence_before = deepcopy(db[FENCES].rows[0])
    with pytest.raises(HTTPException) as caught:
        await authority.settle_google_automation_claim(db, user_id='owner', account_id='123', claim_id='claim1', current=NOW)
    assert caught.value.detail['code'] == 'google_budget_guard_settlement_unproved'
    assert caught.value.detail['preserve_execution_fence'] is True
    assert caught.value.detail['automatic_retry_allowed'] is False
    assert db[AUTOMATION_POLICIES].rows[0] == private_before
    assert db[FENCES].rows[0] == fence_before
    assert db[guard.BUDGET_GUARDS].rows == []
