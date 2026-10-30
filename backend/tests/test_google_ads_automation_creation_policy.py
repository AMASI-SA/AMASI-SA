"""Creation policy approval, catalog review and activation stay owner scoped."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import asyncio

import httpx
import pytest
from fastapi import APIRouter, FastAPI
from pymongo.errors import DuplicateKeyError

from integrations_control_center import google_ads_automation_creation_policy as policy
from integrations_control_center import google_ads_automation_policy as base
from tests.test_google_ads_automation_creation_contract import case

NOW = datetime(2026, 10, 26, 9, tzinfo=timezone.utc)


def matches(row, scope):
    return all(any(matches(row, item) for item in value) if key == "$or" else row.get(key) == value for key, value in scope.items())


class Collection:
    def __init__(self, rows=()): self.rows, self.indexes = deepcopy(list(rows)), []
    async def find_one(self, scope, projection=None):
        return next((deepcopy(row) for row in self.rows if matches(row, scope)), None)
    async def create_index(self, keys, **kwargs): self.indexes.append((keys, kwargs))
    def find(self, scope, projection=None):
        rows = [deepcopy(row) for row in self.rows if matches(row, scope)]
        class Cursor:
            async def to_list(self, length): return rows[:length]
        return Cursor()
    async def insert_one(self, row):
        if any(all(prior.get(key) == row.get(key) for key in ("user_id", "account_id")) for prior in self.rows):
            raise DuplicateKeyError("duplicate")
        self.rows.append(deepcopy(row))
    async def update_one(self, scope, update):
        for row in self.rows:
            if matches(row, scope):
                row.update(deepcopy(update["$set"]))
                for key, item in update.get("$push", {}).items():
                    row[key] = (row.get(key, []) + deepcopy(item["$each"]))[item["$slice"]:]
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


from tests.google_ads_transactional_fake import TransactionalDBMixin


class DB(TransactionalDBMixin):
    def __init__(self):
        self._tx_setup({'users':Collection([{"id":"owner","role":"owner"}]),
            policy.CREATION_POLICIES:Collection(), 'mezan_products_v2':Collection([case()["product"]])})
        self.users=self._tx_get('users');self.policies=self._tx_get(policy.CREATION_POLICIES)
        self.products=self._tx_get('mezan_products_v2')
    def __getitem__(self,name):return self._tx_get(name)


def body(**changes):
    sample = case()
    template = {key: value for key, value in sample["template"].items() if key not in {"approved_by", "approved_at", "approved_version"}}
    return {"expected_revision": 0, "owner_generation": 1, "enabled": False, "product_ids": ["1"],
        "template": template, "acknowledge_template": True, "budget_native": "15",
        "max_account_daily_budget_native": "55", "geo_target_ids": ["2682"], "bidding_strategy": "MAXIMIZE_CONVERSION_VALUE",
        "max_paused_campaigns": 2, "max_daily_creations": 1, "max_total_creations": 3,
        "catalog_max_age_seconds": 3600, "auto_enable_new": False,
        "expires_at": (NOW + timedelta(days=1)).isoformat(), **changes}


def app(monkeypatch, *, db=None, user=None, connected=True):
    db = db or DB(); calls = {"metadata": 0, "geos": 0, "mutations": 0, "closed": 0}
    async def current_user(): return user or {"id": "owner"}
    async def accounts(database, user_id): return [{"account_id": "123"}] if connected and user_id == "owner" else []
    async def account_client(*args, **kwargs):
        async def metadata():
            calls["metadata"] += 1
            return {"account_id": "123", "identity_verified": True, "currency_code": "SAR", "time_zone": "Asia/Riyadh"}
        async def close(): calls["closed"] += 1
        async def mutate(*args, **kwargs):
            calls["mutations"] += 1
            raise AssertionError("configuration never writes Google")
        return SimpleNamespace(account_metadata=metadata, close=close, mutate=mutate), "private-epoch"
    async def compatible_read(*args, **kwargs):
        calls["geos"] += 1
        return [{"geoTargetConstant": {"id": "2682", "status": "ENABLED", "name": "Saudi Arabia"}}], True, []
    monkeypatch.setattr(base, "available_accounts", accounts)
    monkeypatch.setattr(policy, "account_client", account_client, raising=False)
    monkeypatch.setattr(policy, "compatible_read", compatible_read, raising=False)
    monkeypatch.setattr(policy, "now", lambda: NOW, raising=False)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: False, raising=False)
    target, router = FastAPI(), APIRouter()
    policy.attach_google_ads_automation_creation_policy_routes(router, db, current_user)
    target.include_router(router)
    return target, db, calls


@pytest.mark.asyncio
async def test_get_default_does_not_create_configuration_or_choose_budget_products(monkeypatch):
    target, db, calls = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        response = await client.get("/google_ads/automation/creation-policy/123")
    assert response.status_code == 200
    result = response.json()
    assert result["configured"] is False and result["enabled"] is result["effective_enabled"] is False
    assert result["product_scopes"] == [] and "budget_native" not in result
    assert db.policies.rows == [] and calls["metadata"] == calls["mutations"] == 0


@pytest.mark.asyncio
async def test_save_derives_identity_urls_and_approval_atomically_and_always_disables(monkeypatch):
    target, db, calls = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/creation-policy/123", json=body(enabled=True))
    assert response.status_code == 200, response.text
    result, row = response.json(), db.policies.rows[0]
    assert result["enabled"] is result["effective_enabled"] is result["auto_enable_new"] is False
    assert row["user_id"] == row["template"]["approved_by"] == "owner"
    assert row["revision"] == 1 and row["template"]["approved_version"] == row["template"]["version"] == 1
    assert row["allowed_domain"] == "amasi-sa.com" and row["currency_code"] == "SAR" and row["time_zone"] == "Asia/Riyadh"
    assert row["product_scopes"] == [{"product_id": "1", "final_url": "https://amasi-sa.com/p1"}]
    assert calls["metadata"] == calls["closed"] == calls["geos"] == 1 and calls["mutations"] == 0
    assert "user_id" not in result and "_creation_claim" not in result


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"acknowledge_template": False}, {"acknowledge_template": 1}, {"acknowledge_template": "true"},
    {"user_id": "other"}, {"currency_code": "USD"}, {"allowed_domain": "other.example"},
    {"budget_native": "0"}, {"budget_native": "56"}, {"product_ids": []},
    {"product_ids": ["1", "1"]}, {"product_ids": [str(i) for i in range(9)]},
    {"geo_target_ids": []}, {"geo_target_ids": ["2682", "2682"]}, {"geo_target_ids": ["2682) OR 1=1"]},
    {"max_daily_creations": True}, {"max_total_creations": 101}, {"expected_revision": 2**63 - 1},
])
async def test_invalid_or_forged_configuration_does_not_reach_provider(monkeypatch, changes):
    target, db, calls = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/creation-policy/123", json=body(**changes))
    assert response.status_code == 422 and db.policies.rows == [] and calls["metadata"] == 0


@pytest.mark.asyncio
async def test_caller_cannot_forge_approver_or_insert_unapproved_placeholders(monkeypatch):
    target, db, calls = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        forged = body(); forged["template"]["approved_by"] = "owner"
        assert (await client.put("/google_ads/automation/creation-policy/123", json=forged)).status_code == 422
        malicious = body(); malicious["template"]["headlines"][0] = "{product_name.__class__}"
        assert (await client.put("/google_ads/automation/creation-policy/123", json=malicious)).status_code == 422
    assert db.policies.rows == [] and calls["metadata"] == 0


@pytest.mark.asyncio
async def test_foreign_or_unknown_product_and_disconnected_account_are_rejected(monkeypatch):
    target, db, calls = app(monkeypatch)
    db.products.rows[0]["user_id"] = "other"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/creation-policy/123", json=body())).status_code == 404
    target, db, calls = app(monkeypatch, connected=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.get("/google_ads/automation/creation-policy/123")).status_code == 404
    assert calls["metadata"] == 0


@pytest.mark.asyncio
async def test_persisted_owner_required_even_if_session_claims_owner(monkeypatch):
    target, db, calls = app(monkeypatch, user={"id": "owner", "role": "owner"})
    db.users.rows[0]["disabled"] = True
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/creation-policy/123", json=body())).status_code == 403
    assert db.policies.rows == [] and calls["metadata"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"status": "inactive"}, {"quantity": 0}, {"source_revision": None},
    {"last_synced_at": (NOW - timedelta(hours=1)).isoformat()}])
async def test_blocked_catalog_can_be_reviewed_in_disabled_configuration_but_not_activated(monkeypatch, changes):
    target, db, calls = app(monkeypatch)
    db.products.rows[0].update(changes)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        saved = await client.put("/google_ads/automation/creation-policy/123", json=body())
        assert saved.status_code == 200 and saved.json()["configuration_blockers"]
        activated = await client.post("/google_ads/automation/creation-policy/123/activate", json={"expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True})
    assert activated.status_code == 409 and db.policies.rows[0]["enabled"] is False and calls["mutations"] == 0


@pytest.mark.asyncio
async def test_activation_is_separate_requires_all_acknowledgements_and_preserves_private_state(monkeypatch):
    target, db, calls = app(monkeypatch)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/creation-policy/123", json=body())).status_code == 200
        db.policies.rows[0].update(_creation_claim={"private": "claim"}, _creation_days=["private quota"])
        payload = {"expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True}
        assert (await client.post("/google_ads/automation/creation-policy/123/activate", json={**payload, "acknowledge_creative": False})).status_code == 422
        response = await client.post("/google_ads/automation/creation-policy/123/activate", json=payload)
    assert response.status_code == 200, response.text
    row = db.policies.rows[0]
    assert row["enabled"] is True and row["revision"] == row["activation_revision"] == 2
    assert row["activated_by"] == "owner" and row["activation_version"] == 1
    assert response.json()["effective_enabled"] is True and response.json()["in_flight_authorized"] is True
    assert "private quota" not in response.text and row["_creation_days"] == ["private quota"]
    assert calls["mutations"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("key,value", [("acknowledge_creation", False), ("acknowledge_creative", 1), ("acknowledge_creative", "true"), ("acknowledge_auto_enable", 1)])
async def test_activation_acknowledgements_are_strict(monkeypatch, key, value):
    target, db, _ = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        payload = {"expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True, key: value}
        response = await client.post("/google_ads/automation/creation-policy/123/activate", json=payload)
    assert response.status_code == 422 and db.policies.rows == []


@pytest.mark.asyncio
async def test_flags_off_never_activate_and_generic_save_invalidates_old_proof(monkeypatch):
    target, db, calls = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/creation-policy/123", json=body())).status_code == 200
        count = calls["metadata"]
        denied = await client.post("/google_ads/automation/creation-policy/123/activate", json={"expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True})
        assert denied.status_code == 409 and calls["metadata"] == count
        db.policies.rows[0].update(enabled=True, activation_version=1, activation_revision=1, activated_by="owner", activated_at=NOW.isoformat())
        saved = await client.put("/google_ads/automation/creation-policy/123", json=body(expected_revision=1, enabled=True))
    assert saved.status_code == 200 and saved.json()["enabled"] is False
    assert db.policies.rows[0]["activation_version"] is None


@pytest.mark.asyncio
async def test_revision_cas_conflict_and_offline_revoke_never_remove_claims_or_quota(monkeypatch):
    target, db, _ = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/creation-policy/123", json=body())).status_code == 200
        db.policies.rows[0].update(_creation_claim={"claim_id": "private"}, _creation_generation=3)
        responses = await asyncio.gather(*(client.put("/google_ads/automation/creation-policy/123", json=body(expected_revision=1)) for _ in range(2)))
        assert sorted(response.status_code for response in responses) == [200, 409]
    offline, _, calls = app(monkeypatch, db=db, connected=False)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(offline), base_url="http://test") as client:
        revoked = await client.post("/google_ads/automation/creation-policy/123/revoke", json={"expected_revision": 2})
        stale = await client.post("/google_ads/automation/creation-policy/123/revoke", json={"expected_revision": 2})
    assert revoked.status_code == 200 and revoked.json()["enabled"] is False and revoked.json()["in_flight_authorized"] is True
    assert stale.status_code == 409 and calls["metadata"] == calls["geos"] == 0
    assert db.policies.rows[0]["_creation_generation"] == 3 and db.policies.rows[0]["_creation_claim"] == {"claim_id": "private"}


@pytest.mark.asyncio
async def test_changed_product_or_template_revision_requires_owner_review_again(monkeypatch):
    target, db, _ = app(monkeypatch)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/creation-policy/123", json=body())).status_code == 200
        db.products.rows[0]["source_revision"] = "b" * 64
        changed = await client.post("/google_ads/automation/creation-policy/123/activate", json={"expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True})
        assert changed.status_code == 409
        updated = body(expected_revision=1); updated["template"]["headlines"][1] = "اختر تفاصيل هديتك"
        assert (await client.put("/google_ads/automation/creation-policy/123", json=updated)).status_code == 422
        updated["template"]["version"] = 2
        assert (await client.put("/google_ads/automation/creation-policy/123", json=updated)).status_code == 200


@pytest.mark.asyncio
async def test_unknown_or_disabled_geo_and_currency_identity_are_not_substituted(monkeypatch):
    target, db, calls = app(monkeypatch)
    async def incompatible(*args, **kwargs): return [{"geoTargetConstant": {"id": "999", "status": "ENABLED"}}], True, []
    monkeypatch.setattr(policy, "compatible_read", incompatible)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/creation-policy/123", json=body())
    assert response.status_code == 409 and db.policies.rows == [] and calls["closed"] == 1


@pytest.mark.asyncio
async def test_owner_revocation_during_native_activation_read_defeats_activation_cas(monkeypatch):
    target, db, _ = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/creation-policy/123", json=body())).status_code == 200
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    async def during_native(*args, **kwargs):
        db.policies.rows[0].update(revision=2, enabled=False)
        return [{"geoTargetConstant": {"id": "2682", "status": "ENABLED", "name": "Saudi Arabia"}}], True, []
    monkeypatch.setattr(policy, "compatible_read", during_native)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        response = await client.post("/google_ads/automation/creation-policy/123/activate", json={"expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True})
    assert response.status_code == 409 and db.policies.rows[0]["enabled"] is False


@pytest.mark.asyncio
async def test_rendered_rsa_limits_must_be_reviewed_before_activation(monkeypatch):
    target, db, _ = app(monkeypatch)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    payload = body(); payload["template"]["headlines"][0] = "{product_name}"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        saved = await client.put("/google_ads/automation/creation-policy/123", json=payload)
        assert saved.status_code == 200 and "creative_asset_limit:1" in saved.json()["configuration_blockers"]
        activated = await client.post("/google_ads/automation/creation-policy/123/activate", json={"expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True})
    assert activated.status_code == 409 and db.policies.rows[0]["enabled"] is False


@pytest.mark.asyncio
async def test_product_changed_to_hidden_during_metadata_read_cannot_activate(monkeypatch):
    target, db, _ = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/creation-policy/123", json=body())).status_code == 200
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    async def hide_during_native(*args, **kwargs):
        db.products.rows[0]["status"] = "inactive"
        return [{"geoTargetConstant": {"id": "2682", "status": "ENABLED"}}], True, []
    monkeypatch.setattr(policy, "compatible_read", hide_during_native)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        response = await client.post("/google_ads/automation/creation-policy/123/activate", json={"expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True})
    assert response.status_code == 409 and db.policies.rows[0]["enabled"] is False


@pytest.mark.asyncio
async def test_unimplemented_launch_permission_is_rejected_and_legacy_rows_are_not_effective(monkeypatch):
    target, db, _ = app(monkeypatch)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        unsupported = await client.put("/google_ads/automation/creation-policy/123", json=body(auto_enable_new=True))
        assert unsupported.status_code == 422 and unsupported.json()["detail"]["code"] == "google_creation_auto_enable_unavailable"
        assert db.policies.rows == []
        assert (await client.put("/google_ads/automation/creation-policy/123", json=body())).status_code == 200
        db.policies.rows[0].update(auto_enable_new=True, enabled=True, activation_version=1,
            activation_revision=1, activated_by="owner", activated_at=NOW.isoformat())
        read = await client.get("/google_ads/automation/creation-policy/123")
        assert read.json()["effective_enabled"] is False and "auto_enable_not_implemented" in read.json()["configuration_blockers"]
        activated = await client.post("/google_ads/automation/creation-policy/123/activate", json={"expected_revision": 1,
            "acknowledge_creation": True, "acknowledge_creative": True, "acknowledge_auto_enable": True})
    assert activated.status_code == 409


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [{"name": "هدية a@b.com"}, {"name": "هودي " * 60}, {"sku": "AMS / private"}])
async def test_owner_approved_expansion_cannot_differ_from_raw_creator_fields(monkeypatch, changes):
    target, db, _ = app(monkeypatch)
    db.products.rows[0].update(changes)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        saved = await client.put("/google_ads/automation/creation-policy/123", json=body())
        assert saved.status_code == 200 and "product_copy_unproved:1" in saved.json()["configuration_blockers"]
        activated = await client.post("/google_ads/automation/creation-policy/123/activate", json={"expected_revision": 1,
            "acknowledge_creation": True, "acknowledge_creative": True})
    assert activated.status_code == 409


@pytest.mark.asyncio
async def test_product_alias_ambiguity_cannot_select_an_arbitrary_owned_row(monkeypatch):
    target, db, calls = app(monkeypatch)
    duplicate = deepcopy(db.products.rows[0]); duplicate.update(salla_product_id="2", mezan_product_id="1")
    db.products.rows.append(duplicate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/creation-policy/123", json=body())
    assert response.status_code == 409 and response.json()["detail"]["code"] == "google_creation_product_ambiguous"
    assert db.policies.rows == [] and calls["metadata"] == 0


@pytest.mark.asyncio
async def test_owner_permission_revoked_during_final_catalog_read_denies_activation(monkeypatch):
    target, db, _ = app(monkeypatch)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        assert (await client.put("/google_ads/automation/creation-policy/123", json=body())).status_code == 200
    original, reads = policy._catalog, 0
    async def revoke_after_final_read(*args, **kwargs):
        nonlocal reads
        result = await original(*args, **kwargs); reads += 1
        if reads == 2: db.users.rows[0]["disabled"] = True
        return result
    monkeypatch.setattr(policy, "_catalog", revoke_after_final_read)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        response = await client.post("/google_ads/automation/creation-policy/123/activate", json={"expected_revision": 1,
            "acknowledge_creation": True, "acknowledge_creative": True})
    assert response.status_code == 403 and db.policies.rows[0]["enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("field,text", [("headlines", "冬" * 16), ("descriptions", "冬" * 46)])
async def test_rsa_review_uses_google_display_width_not_python_character_count(monkeypatch, field, text):
    target, db, _ = app(monkeypatch)
    payload = body(); payload["template"][field][0] = text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        response = await client.put("/google_ads/automation/creation-policy/123", json=payload)
    assert response.status_code == 200 and "creative_asset_limit:1" in response.json()["configuration_blockers"]
