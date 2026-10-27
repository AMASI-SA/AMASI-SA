"""A requested OAuth scope is not proof of a provider-granted scope."""
from copy import deepcopy

import pytest

from integrations_control_center.catalog import PROVIDER_BY_ID, build_capability_matrix
from integrations_control_center.service import _actions, _ai_actions, _card_from_snapshot
from integrations_control_center.snapchat_catalog_native import install_snapchat_native_catalog
from integrations_control_center import snapchat_projection
from tests.test_snapchat_native_integrations_v2 import FakeDB


def test_snapchat_card_does_not_present_unproven_persisted_scopes_as_granted(monkeypatch):
    install_snapchat_native_catalog()
    monkeypatch.setenv("SNAPCHAT_MARKETING_SCOPES", "snapchat-marketing-api snapchat-profile-api")
    snapshot = {
        "provider": "snapchat_ads", "connection_status": "connected",
        "connection_provenance": "api_connection", "source_mode": "snapchat_marketing_oauth_v2",
        "has_data": True, "permissions_observed": True,
        "current_permissions": ["snapchat-marketing-api"], "missing_permissions": [],
        "capability_evidence": ["campaigns.read"],
        "accounts": [{"mezan_integration_account_id": "account-1", "provider": "snapchat_ads",
                      "external_account_id": "account-1", "permissions_observed": True,
                      "permissions": ["snapchat-marketing-api"], "has_data": True}],
    }
    before = deepcopy(snapshot)
    card = _card_from_snapshot(PROVIDER_BY_ID["snapchat_ads"], snapshot)
    assert card["permissions"]["unknown"] is True
    assert card["permissions"]["current"] == []
    assert card["permissions"]["missing"] == []
    assert card["accounts"][0]["permissions"] == []
    assert card["connection_status"] == "connected"
    expected_capabilities = build_capability_matrix(
        PROVIDER_BY_ID["snapchat_ads"], connection_status="connected", has_data=True,
        current_permissions=["snapchat-marketing-api"], permissions_observed=True,
        evidence_capabilities=["campaigns.read"],
    )
    assert card["capabilities"] == expected_capabilities
    assert card["ai"] == _ai_actions(PROVIDER_BY_ID["snapchat_ads"], snapshot, expected_capabilities)
    assert card["actions"] == _actions(PROVIDER_BY_ID["snapchat_ads"], snapshot)
    assert snapshot == before


def test_non_snapchat_permission_evidence_is_unchanged():
    snapshot = {
        "provider": "meta_ads", "connection_status": "connected",
        "connection_provenance": "api_connection", "source_mode": "meta_oauth_v2",
        "has_data": True, "permissions_observed": True,
        "current_permissions": ["ads_read"], "missing_permissions": [], "accounts": [],
    }
    card = _card_from_snapshot(PROVIDER_BY_ID["meta_ads"], snapshot)
    assert card["permissions"]["current"] == ["ads_read"]
    assert card["permissions"]["unknown"] is False


@pytest.mark.asyncio
async def test_callback_fallback_and_explicit_scope_produce_indistinguishable_old_records(monkeypatch):
    install_snapchat_native_catalog()
    monkeypatch.setattr(snapchat_projection, "encrypt_snapchat_token", lambda _: b"opaque")
    monkeypatch.setenv("SNAPCHAT_MARKETING_SCOPES", "snapchat-marketing-api")
    outputs = []
    for supplied in (None, "snapchat-marketing-api"):
        db = FakeDB()
        payload = {"access_token": "fixture", "refresh_token": "fixture"}
        if supplied is not None:
            payload["scope"] = supplied
        await snapchat_projection.persist_snapchat_projection(db, user_id="u", token_payload=payload,
            discovery={"accounts": [{"external_account_id": "a"}]})
        credential = db.rows["mezan_snapchat_oauth_credentials_v2"][0]
        integration = db.rows["mezan_integrations_v2"][0]
        permission_rows = db.rows["mezan_integration_permissions_v2"]
        outputs.append((credential["scope"], integration["permissions_observed"],
                        [(p["permission_key"], p["permission_status"]) for p in permission_rows]))
    assert outputs[0] == outputs[1] == (["snapchat-marketing-api"], True, [("snapchat-marketing-api", "current")])
