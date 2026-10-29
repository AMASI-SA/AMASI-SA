"""Details-writer provenance must survive policy, planning and claim boundaries."""
from copy import deepcopy
from datetime import timedelta
from unittest.mock import patch

import httpx
import pytest
from fastapi import HTTPException

from product_inventory_proof import inventory_readiness
from product_v2_details_routes import _details_patch
from product_v2_routes import normalize_salla_product
from integrations_control_center import google_ads_automation_creation_contract as contract
from integrations_control_center import google_ads_automation_creation_policy as policy
from integrations_control_center.google_ads_creation_inventory import PRODUCT_PROJECTION
from tests.test_google_ads_automation_creation_contract import NOW, prepared_case
from tests.test_google_ads_automation_creation_policy import DB, app, body
from tests import test_google_ads_automation_creation_authority as claim_fixture


def raw_product(**changes):
    return {"id": "1", "name": "هودي شتوي", "sku": "AMS11549", "status": "sale", "quantity": 4,
            "unlimited_quantity": False, "urls": {"product": "https://amasi-sa.com/p1"},
            "options": [], "variants": [], **changes}


def detailed(raw=None):
    with patch("product_v2_details_routes._now", return_value=NOW):
        return _details_patch(raw if raw is not None else raw_product(), user_id="owner")


def variant(identity=1936825372, quantity=2, unlimited=False):
    return {"id": identity, "quantity": quantity, "unlimited_quantity": unlimited}


def prepare(product):
    values = prepared_case(); values["product"] = product
    return contract.prepare_google_creation_intent(**values)


@pytest.mark.parametrize("raw", [raw_product(), raw_product(quantity="4", stock_quantity=4), raw_product(variants=[variant(), variant(1936825373, 0)]),
    raw_product(quantity=None, unlimited_quantity=True), raw_product(variants=[variant(quantity=None, unlimited=True)])])
def test_actual_details_writer_proves_positive_simple_or_variant_inventory(raw):
    product = detailed(raw)
    assert inventory_readiness(product, NOW, 3600)[0] == "available"
    result = prepare(product)
    assert result["ready_for_paused_preview"] and result["provider_writes"] == 0
    assert result["binding"]["product"]["inventory_digest"] == product["inventory_evidence_v1"]["stock_digest"]


@pytest.mark.asyncio
@pytest.mark.parametrize("quantities,enabled,blocker", [([0, 0], False, "product_out_of_stock:1"), ([0, 2], True, None)])
async def test_canonical_variants_control_saved_blockers_activation_and_prepare(monkeypatch, quantities, enabled, blocker):
    product = detailed(raw_product(variants=[variant(1936825372 + index, quantity) for index, quantity in enumerate(quantities)]))
    db = DB(); db.products.rows = [product]
    target, db, calls = app(monkeypatch, db=db)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        saved = await client.put("/google_ads/automation/creation-policy/123", json=body())
        activated = await client.post("/google_ads/automation/creation-policy/123/activate", json={
            "expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True})
    assert saved.status_code == 200 and saved.json()["enabled"] is False
    assert saved.json()["configuration_blockers"] == ([blocker] if blocker else [])
    assert activated.status_code == (200 if enabled else 409)
    assert db.policies.rows[0]["enabled"] is enabled and calls["mutations"] == 0
    if enabled:
        assert prepare(product)["ready_for_paused_preview"]
    else:
        with pytest.raises(contract.CreationContractError, match="creation_product_unavailable"):
            prepare(product)


@pytest.mark.parametrize("mutate", [
    lambda raw: raw.pop("variants"), lambda raw: raw.update(variants=None),
    lambda raw: raw.update(variants="none"), lambda raw: raw.update(skus=[variant()]),
    lambda raw: raw.pop("options"), lambda raw: raw.update(options=None),
    lambda raw: raw.update(product_options=[{"id": "size"}]),
    lambda raw: raw.update(variants=[{"quantity": 1, "unlimited_quantity": False}]),
    lambda raw: raw.update(variants=[variant(), variant()]),
    lambda raw: raw.update(variants=[variant(1936825372 + index) for index in range(51)]),
    lambda raw: raw.update(variants=[variant(quantity=None)]),
    lambda raw: raw.update(options=[{"id": "size"}], variants=[]),
])
def test_missing_ambiguous_partial_or_unproved_detail_inventory_never_grants_eligibility(mutate):
    raw = raw_product(); mutate(raw)
    product = detailed(raw)
    assert inventory_readiness(product, NOW, 3600) == ("unproved", None)
    with pytest.raises(contract.CreationContractError): prepare(product)


@pytest.mark.parametrize("bad", [True, False, " 2", "2 ", "1e2", "+2", "NaN", float("nan"), float("inf"), -1])
@pytest.mark.parametrize("level", ["parent", "variant"])
def test_invalid_raw_quantities_do_not_gain_authority_through_normalization(bad, level):
    raw = raw_product(quantity=bad) if level == "parent" else raw_product(variants=[variant(quantity=bad)])
    assert inventory_readiness(detailed(raw), NOW, 3600)[0] == "unproved"


@pytest.mark.parametrize("level", ["parent", "variant"])
@pytest.mark.parametrize("bad", ["false", "true", 0, 1])
def test_non_boolean_unlimited_source_is_not_proof(level, bad):
    raw = raw_product(quantity=0, unlimited_quantity=bad) if level == "parent" else raw_product(variants=[variant(quantity=0, unlimited=bad)])
    assert inventory_readiness(detailed(raw), NOW, 3600)[0] == "unproved"


def test_documented_salla_sku_numeric_stock_string_and_explicit_unlimited():
    raw = raw_product(quantity=0, unlimited_quantity=True)
    del raw["variants"]
    raw["skus"] = [{"id": 17, "stock_quantity": "0", "unlimited_quantity": True}]
    product = detailed(raw)
    assert inventory_readiness(product, NOW, 3600)[0] == "available"
    assert prepare(product)["ready_for_paused_preview"]


@pytest.mark.parametrize("identity", [None, True, False, 0, -1, 1.5, "size-s", " 1936825372", "01"])
def test_variant_identity_must_be_explicit_positive_provider_numeric_id(identity):
    product = detailed(raw_product(variants=[variant(identity)]))
    assert inventory_readiness(product, NOW, 3600)[0] == "unproved"


def test_later_recent_light_sync_cannot_keep_detail_authority_even_at_identical_timestamp():
    raw = raw_product(variants=[variant()]); product = detailed(raw)
    old_proof = deepcopy(product["inventory_evidence_v1"])
    light = {key: value for key, value in raw.items() if key not in {"variants", "options"}}
    # Mirrors recent-sync's actual normalizer plus $set, retaining old metadata.
    product.update(normalize_salla_product(light, user_id="owner", synced_at=NOW))
    assert product["variants_count"] == 0 and product["details_loaded"] is True
    assert product["inventory_evidence_v1"] == old_proof
    assert product["source_revision"] != old_proof["source_revision"]
    assert inventory_readiness(product, NOW, 3600) == ("unproved", None)
    with pytest.raises(contract.CreationContractError, match="creation_product_inventory_unproved"): prepare(product)


@pytest.mark.parametrize("mutate", [
    lambda p: p.pop("inventory_evidence_v1"), lambda p: p["inventory_evidence_v1"].pop("version"),
    lambda p: p["inventory_evidence_v1"].update(version=True), lambda p: p["inventory_evidence_v1"].update(variant_count=True),
    lambda p: p.update(variants_count=2), lambda p: p.update(variants=[]),
    lambda p: p["variants"][0].update(quantity=0), lambda p: p.update(quantity=9),
    lambda p: p.update(details_loaded=False), lambda p: p.update(details_synced_at=NOW-timedelta(seconds=1)),
])
def test_legacy_or_changed_canonical_stock_without_revision_change_invalidates_evidence(mutate):
    product = detailed(raw_product(variants=[variant()])); revision = product["source_revision"]
    mutate(product)
    assert product["source_revision"] == revision
    assert inventory_readiness(product, NOW, 3600) == ("unproved", None)


def test_fresh_parent_sync_does_not_refresh_old_details_proof():
    product = detailed(raw_product(variants=[variant()]))
    product["last_synced_at"] = NOW + timedelta(hours=1)
    assert inventory_readiness(product, NOW + timedelta(hours=1), 3600)[0] == "unproved"


def test_variant_change_is_bound_even_if_new_valid_evidence_keeps_parent_source_revision():
    original = detailed(raw_product(variants=[variant()]))
    values = prepared_case(); values["product"] = original
    intent = contract.prepare_google_creation_intent(**values)
    changed = detailed(raw_product(variants=[variant(quantity=3)]))
    # Simulates a separately refreshed stock proof: old intents still bind content.
    changed["source_revision"] = original["source_revision"]
    changed["inventory_evidence_v1"]["source_revision"] = original["source_revision"]
    values["product"] = changed
    assert prepare(changed)["ready_for_paused_preview"]
    with pytest.raises(contract.CreationContractError): contract.revalidate_google_creation_intent(intent, **values)


@pytest.mark.asyncio
async def test_owner_activation_rejects_changed_positive_variant_even_with_same_source_revision(monkeypatch):
    original = detailed(raw_product(variants=[variant()]))
    db = DB(); db.products.rows = [original]
    target, db, calls = app(monkeypatch, db=db)
    monkeypatch.setattr(policy, "creation_runtime_available", lambda: True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(target), base_url="http://test") as client:
        saved = await client.put("/google_ads/automation/creation-policy/123", json=body())
        changed = detailed(raw_product(variants=[variant(quantity=3)]))
        changed["source_revision"] = original["source_revision"]
        changed["inventory_evidence_v1"]["source_revision"] = original["source_revision"]
        db.products.rows[0] = changed
        assert inventory_readiness(changed, NOW, 3600)[0] == "available"
        activated = await client.post("/google_ads/automation/creation-policy/123/activate", json={
            "expected_revision": 1, "acknowledge_creation": True, "acknowledge_creative": True})
    assert saved.status_code == 200 and activated.status_code == 409
    assert activated.json()["detail"]["code"] == "google_creation_product_review_changed"
    assert db.policies.rows[0]["enabled"] is False and calls["mutations"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("changed", [False, True])
async def test_final_claim_rechecks_variant_fingerprint_before_reserving(monkeypatch, changed):
    # Explicit test-only capability so this still reaches inventory validation
    # after the mandatory guard migration; production remains literal false.
    from integrations_control_center import google_ads_automation_budget_guard as guard
    monkeypatch.setattr(guard, "budget_guard_runtime_available", lambda: True)
    values = prepared_case()
    values["product"] = detailed(raw_product(variants=[variant()]))
    # The fixture projection supports top-level canonical URLs as in real rows.
    values["product"]["canonical_url"] = "https://amasi-sa.com/p1"
    monkeypatch.setattr(claim_fixture, "prepared_case", lambda: deepcopy(values))
    claim_fixture.enabled.__wrapped__(monkeypatch)
    docs, native, _ = claim_fixture.documents(); db = claim_fixture.DB(docs)
    if changed:
        db[claim_fixture.PRODUCT_COLLECTION].rows[0]["variants"][0]["quantity"] = 0
        with pytest.raises(HTTPException) as error:
            await claim_fixture.authorize(db, native)
        assert error.value.detail["code"] != "google_creation_execution_disabled"
        assert not db[claim_fixture.CREATION_POLICIES].updates
    else:
        reply = await claim_fixture.authorize(db, native)
        assert reply["authorized"] and reply["provider_writes"] == 0


def test_projection_is_creation_only_bounded_and_excludes_raw_inventory():
    from integrations_control_center.google_ads_ai import PRODUCT_PROJECTION as shared
    assert PRODUCT_PROJECTION["variants"] == {"$slice": 50}
    assert PRODUCT_PROJECTION["inventory_evidence_v1"] == PRODUCT_PROJECTION["variants_count"] == 1
    assert "inventory_evidence_v1" not in shared
    assert not any(key.startswith("raw_salla") and not key.endswith((".url", ".urls.product")) for key in PRODUCT_PROJECTION)
