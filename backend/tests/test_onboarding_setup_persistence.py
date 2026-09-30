"""Real isolated Mongo: partial setup data persists, no financial write surface."""
from copy import deepcopy

import pytest
from tests.test_accounting_onboarding import api, request, pause, fingerprint, CREATE
from tests.test_financial_accounts_real_mongo import mongo_db


@pytest.mark.asyncio
async def test_setup_roundtrip_cas_replay_and_financial_fingerprints(api):
    await pause(api)
    row = await request(api, "POST", "/sessions", CREATE)
    before = await fingerprint(api)
    draft = {"schema_version": 1, "active_stage": "inventory", "sections": {
        "inventory": {"rows": []},
        "advertising": {"rows": [{"funding_reference": "receipt-note", "currency": "USD"}]},
        "obligations": {"rows": [{"name": "incomplete saved note"}]},
    }, "couriers": {}, "section_metadata": {}, "note": "Draft only"}
    payload = {"version": 1, "idempotency_key": "setup-snapshot-0001", "setup_draft": draft}
    path = f"/sessions/{row['id']}/setup-draft"
    saved = await request(api, "PUT", path, payload)
    assert saved["version"] == 2 and saved["status"] == "draft"
    assert saved["setup_draft"] == draft
    resumed = await request(api, "GET", f"/sessions/{row['id']}")
    assert resumed["setup_draft"] == draft
    replay = await request(api, "PUT", path, payload)
    assert replay["existing"] and replay["version"] == 2
    conflict = await request(api, "PUT", path, {**payload, "idempotency_key": "stale-snapshot-0002"}, status=409)
    assert conflict["detail"]["code"] == "onboarding_version_conflict"
    assert await fingerprint(api) == before
    assert all(not s["data"].get("lines") for s in resumed["sections"].values())


@pytest.mark.asyncio
async def test_setup_unknown_stages_permission_and_reviewed_lock(api):
    row = await request(api, "POST", "/sessions", CREATE)
    path = f"/sessions/{row['id']}/setup-draft"
    payload = {"version": 1, "idempotency_key": "setup-invalid-0001", "setup_draft": {"sections": {"activate_writer": {}}}}
    await request(api, "PUT", path, payload, status=422)
    payload["setup_draft"] = {"sections": {"banks": {"rows": []}}}
    await request(api, "PUT", path, payload, user="viewer", status=403)
    await api.db.mz2_onboarding_sessions.update_one({"id": row["id"]}, {"$set": {"status": "reviewed"}})
    result = await request(api, "PUT", path, payload, status=409)
    assert result["detail"]["code"] == "onboarding_session_locked"


@pytest.mark.asyncio
async def test_setup_edits_invalidate_previous_financial_completion(api):
    row = await request(api, "POST", "/sessions", CREATE)
    await api.db.mz2_onboarding_sessions.update_one({"id": row["id"]}, {"$set": {
        "sections.providers.status": "complete", "status": "previewed", "preview": {"hash": "old"}}})
    updated = await request(api, "PUT", f"/sessions/{row['id']}/setup-draft", {
        "version": 1, "idempotency_key": "invalidate-snapshot-0001",
        "setup_draft": {"sections": {"advertising": {"rows": []}}},
    })
    assert updated["sections"]["providers"]["status"] == "incomplete"
    assert updated["preview"] is None and updated["status"] == "draft"
