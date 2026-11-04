"""Explicit legacy-mode dependency for older isolated fee-calculator fixtures."""
import pytest


@pytest.fixture
def legacy_fee_mode(monkeypatch):
    async def policies(_db, _owner):
        return []

    async def mode(_db, _owner):
        return 'legacy_settings'

    monkeypatch.setattr('accounting_onboarding_ssot.list_fee_policies', policies)
    monkeypatch.setattr('accounting_onboarding_ssot.get_fee_source_mode', mode)
