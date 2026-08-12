"""Focused safety checks for preview retries and the provider-write boundary."""

from __future__ import annotations

import hashlib

import pytest

from integrations_control_center import snapchat_campaign_management as management
from tests.test_snapchat_campaign_management_v1 import (
    DB,
    Provider,
    campaign_create,
)


async def _selected_account(*args, **kwargs):
    return {"ad_account_id": "account-1", "display_name": "AMASI"}


@pytest.mark.asyncio
async def test_transport_retry_reuses_one_preview_without_provider_write(monkeypatch):
    """A lost preview response may be retried without a second proposal/write."""
    db = DB()
    provider = Provider()
    monkeypatch.setattr(management, "_selected_account", _selected_account)

    payload = campaign_create(idempotency_key="preview-transport-retry-001")
    first = await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        payload,
        provider=provider,
    )
    retry = await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        payload,
        provider=provider,
    )

    proposals = db[management.PROPOSAL_COLLECTION].rows
    assert first["proposal_id"] == retry["proposal_id"]
    assert first["status"] == retry["status"] == "previewed"
    assert len(proposals) == 1
    assert proposals[0]["idempotency_key"] == "preview-transport-retry-001"
    assert first["confirm_token"] != retry["confirm_token"]
    assert proposals[0]["confirm_token_hash"] == hashlib.sha256(
        retry["confirm_token"].encode()
    ).hexdigest()
    assert provider.executions == []


@pytest.mark.asyncio
async def test_approval_is_local_and_execute_retry_does_not_duplicate_write(monkeypatch):
    """Preview/approval stay local; only execute writes, exactly once on retry."""
    db = DB()
    provider = Provider()

    async def upsert(*args, **kwargs):
        return True

    monkeypatch.setattr(management, "_selected_account", _selected_account)
    monkeypatch.setattr(management, "_upsert_entity", upsert)
    monkeypatch.setenv(management.MUTATIONS_ENABLED_ENV, "true")

    preview = await management.create_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        campaign_create(idempotency_key="preview-approve-execute-retry-001"),
        provider=provider,
    )
    assert preview["provider_write_reached"] is False
    assert provider.executions == []

    approved = await management.approve_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        preview["proposal_id"],
        management.SnapchatManagementApprovalInput(
            confirm_token=preview["confirm_token"],
            expected_revision=preview["revision"],
        ),
    )
    assert approved["status"] == "approved"
    assert approved["provider_write_reached"] is False
    assert provider.executions == []

    completed = await management.execute_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        preview["proposal_id"],
        provider=provider,
    )
    assert completed["status"] == "completed"
    assert completed["provider_write_reached"] is True
    assert len(provider.executions) == 1

    replay = await management.execute_snapchat_management_proposal(
        db,
        "owner-1",
        "owner-1",
        preview["proposal_id"],
        provider=provider,
    )
    assert replay["status"] == "completed"
    assert len(provider.executions) == 1
