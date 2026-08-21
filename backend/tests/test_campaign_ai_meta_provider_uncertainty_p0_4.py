import pytest
from fastapi import HTTPException

import campaign_ai_monitor_legacy as legacy


def test_p0_4_meta_state_match_is_exact_and_fail_closed():
    assert legacy._meta_state_matches_mutation({"status": "PAUSED"}, {"status": "PAUSED"}) is True
    assert legacy._meta_state_matches_mutation({"status": "PAUSED"}, {"effective_status": "ACTIVE"}) is False
    assert legacy._meta_state_matches_mutation({"daily_budget": "12500"}, {"daily_budget": "12500"}) is True
    assert legacy._meta_state_matches_mutation({"daily_budget": "12500"}, {"daily_budget": "12499"}) is False
    assert legacy._meta_state_matches_mutation({}, {}) is False


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Client:
    def __init__(self, gets, post):
        self.gets = list(gets)
        self.post_response = post
        self.posts = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        return self.gets.pop(0)

    async def post(self, *args, **kwargs):
        self.posts += 1
        if isinstance(self.post_response, Exception):
            raise self.post_response
        return self.post_response


async def _prepare_meta_test(monkeypatch, client):
    async def preflight(*args, **kwargs):
        return {}
    async def credential(*args, **kwargs):
        return "token"
    async def reconcile(*args, **kwargs):
        return None
    monkeypatch.setattr(legacy._execution_quality, "preflight_approved_execution", preflight)
    monkeypatch.setattr(legacy._execution_quality, "require_provider_state_unchanged", lambda *a, **k: None)
    monkeypatch.setattr(legacy, "_meta_credential", credential)
    monkeypatch.setattr(legacy, "meta_appsecret_proof", lambda token: "proof")
    monkeypatch.setattr(legacy, "meta_graph_base", lambda: "https://graph.test")
    monkeypatch.setattr(legacy, "_reconcile_meta_provider_uncertainty", reconcile)
    monkeypatch.setattr(legacy.httpx, "AsyncClient", lambda **kwargs: client)


@pytest.mark.asyncio
async def test_p0_4_successful_post_failed_verify_is_provider_state_uncertain(monkeypatch):
    client = _Client(
        gets=[_Response(200, {"status": "ACTIVE", "daily_budget": "10000"}), _Response(503, {})],
        post=_Response(200, {"success": True}),
    )
    await _prepare_meta_test(monkeypatch, client)
    result = await legacy._execute_meta_approval(
        object(), "u1",
        {"action": "scale", "entity_level": "campaign", "change_percent": 10},
        {"entity_id": "c1"},
        snapshot_id="s1", recommendation_id="r1", snapshot_digest="d1",
    )
    assert client.posts == 1
    assert result["status"] == "provider_state_uncertain"
    assert result["provider_write_reached"] is True
    assert result["requested_change"] == {"daily_budget": "11000"}
    assert result["uncertainty_reason"] == "meta_verification_read_failed"


@pytest.mark.asyncio
async def test_p0_4_post_transport_exception_is_never_classified_failed_safe_to_retry(monkeypatch):
    client = _Client(
        gets=[_Response(200, {"status": "ACTIVE", "daily_budget": "10000"})],
        post=TimeoutError("outcome unknown"),
    )
    await _prepare_meta_test(monkeypatch, client)
    result = await legacy._execute_meta_approval(
        object(), "u1",
        {"action": "scale", "entity_level": "campaign", "change_percent": 10},
        {"entity_id": "c1"},
        snapshot_id="s1", recommendation_id="r1", snapshot_digest="d1",
    )
    assert result["status"] == "provider_state_uncertain"
    assert result["provider_write_reached"] is None
    assert result["uncertainty_reason"] == "meta_write_transport_outcome_unknown"


class _Collection:
    def __init__(self, unresolved=None):
        self.unresolved = unresolved
        self.updates = []

    async def find_one(self, *args, **kwargs):
        return self.unresolved

    async def update_one(self, query, update, **kwargs):
        self.updates.append((query, update, kwargs))


class _DB(dict):
    def __getitem__(self, key):
        return dict.__getitem__(self, key)


@pytest.mark.asyncio
async def test_p0_4_unresolved_prior_write_blocks_new_meta_mutation():
    executions = _Collection({
        "execution_id": "e1", "snapshot_id": "s0", "recommendation_id": "r0",
        "result": {"requested_change": {"daily_budget": "11000"}},
    })
    db = _DB({legacy.EXECUTION_COLLECTION: executions, legacy.RECOMMENDATION_COLLECTION: _Collection()})
    with pytest.raises(HTTPException) as caught:
        await legacy._reconcile_meta_provider_uncertainty(
            db, "u1", "c1", {"status": "ACTIVE", "daily_budget": "10000"}
        )
    assert caught.value.status_code == 409
    assert caught.value.detail["code"] == "meta_provider_state_uncertain"
    assert executions.updates == []


@pytest.mark.asyncio
async def test_p0_4_provider_confirmation_reconciles_old_execution():
    executions = _Collection({
        "execution_id": "e1", "snapshot_id": "s0", "recommendation_id": "r0",
        "result": {"requested_change": {"daily_budget": "11000"}},
    })
    recommendations = _Collection()
    db = _DB({legacy.EXECUTION_COLLECTION: executions, legacy.RECOMMENDATION_COLLECTION: recommendations})
    resolved = await legacy._reconcile_meta_provider_uncertainty(
        db, "u1", "c1", {"status": "ACTIVE", "daily_budget": "11000"}
    )
    assert resolved["resolved"] is True
    assert executions.updates[0][1]["$set"]["status"] == "completed"
    assert recommendations.updates[0][1]["$set"]["recommendations.$[item].execution_status"] == "completed"
