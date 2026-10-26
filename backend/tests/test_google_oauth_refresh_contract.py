"""Synthetic adapter contract tests. No credentials, Mongo, or HTTP."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from integrations_control_center import google_oauth_refresh_contract as contract
from integrations_control_center.google_oauth_generation import RefreshConflict

NOW = datetime(2026, 10, 27, tzinfo=timezone.utc)


@pytest.fixture
def harness(monkeypatch):
    register = AsyncMock(return_value=object())
    claim = AsyncMock()
    commit = AsyncMock(return_value=2)
    send = AsyncMock(return_value=contract.EncryptedRefresh("encrypted-synthetic", NOW))
    monkeypatch.setattr(contract, "register_refresh_operation", register)
    monkeypatch.setattr(contract, "claim_refresh_operation", claim)
    monkeypatch.setattr(contract, "commit_refreshed_credential", commit)
    args = dict(operations=object(), credentials=object(), user_id="owner",
                grant_generation="generation-a", token_revision=1,
                operation_id="operation-a", attempt_id="attempt-a", now=NOW, send=send)
    return args, register, claim, commit, send


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["ads", "ga4", "merchant"])
async def test_each_adapter_returns_only_after_acknowledged_fenced_commit(harness, source):
    args, register, claim, commit, send = harness
    result = await contract.refresh_once(source=source, **args)
    assert result.ciphertext == "encrypted-synthetic"
    assert register.await_count == claim.await_count == send.await_count == commit.await_count == 1
    assert commit.await_args.kwargs["grant_generation"] == "generation-a"
    assert commit.await_args.kwargs["token_revision"] == 1
    assert claim.await_args.kwargs["ticket"] is register.return_value


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["ads", "ga4", "merchant"])
@pytest.mark.parametrize("stage", ["register", "claim", "dispatch", "commit"])
async def test_ambiguous_ack_or_dispatch_is_not_retried_or_returned(harness, source, stage):
    args, register, claim, commit, send = harness
    failing = {"register": register, "claim": claim, "dispatch": send, "commit": commit}[stage]
    failing.side_effect = TimeoutError("synthetic uncertain outcome")
    with pytest.raises(TimeoutError):
        await contract.refresh_once(source=source, **args)
    assert failing.await_count == 1
    assert send.await_count == (1 if stage in ("dispatch", "commit") else 0)
    assert commit.await_count == (1 if stage == "commit" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["ads", "ga4", "merchant"])
async def test_relink_wins_and_stale_provider_result_is_not_returned(harness, source):
    args, _, _, commit, send = harness
    commit.side_effect = RefreshConflict("generation changed")
    with pytest.raises(RefreshConflict):
        await contract.refresh_once(source=source, **args)
    assert send.await_count == commit.await_count == 1


@pytest.mark.asyncio
async def test_unknown_source_never_registers_or_sends(harness):
    args, register, claim, commit, send = harness
    with pytest.raises(ValueError):
        await contract.refresh_once(source="other", **args)
    assert all(call.await_count == 0 for call in (register, claim, commit, send))
