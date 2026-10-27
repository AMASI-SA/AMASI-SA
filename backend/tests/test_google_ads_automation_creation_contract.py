"""Policy-scoped creation binds reviewed copy and fresh canonical catalog facts."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone

import pytest

from integrations_control_center import google_ads_automation_creation_contract as contract

NOW = datetime(2026, 10, 26, 9, tzinfo=timezone.utc)


def prove_inventory(product):
    from product_inventory_proof import build_inventory_evidence
    product.update(details_loaded=True, details_synced_at=product["last_synced_at"])
    product.setdefault("variants", []); product.setdefault("variants_count", len(product["variants"]))
    product.setdefault("options_count", 0)
    raw = {key: product[key] for key in ("quantity", "unlimited_quantity", "variants")}
    raw["options"] = product.get("options", [])
    product["inventory_evidence_v1"] = build_inventory_evidence(raw, product)
    return product


def case():
    template = {"template_id": "winter-search", "version": 1, "approved_version": 1,
        "approved_by": "owner", "approved_at": NOW.isoformat(), "ad_format": "RSA",
        "campaign_name": "شتاء | {product_short_name}", "group_name": "مجموعة الشتاء",
        "headlines": ["{product_short_name}", "هدية شتوية بالاسم", "تفاصيل حسب اختيارك"],
        "descriptions": ["اختر تفاصيل منتجك قبل إتمام الطلب.", "تسوق من المتجر وراجع الخيارات المتاحة."],
        "keywords": [{"text": "هودي بالاسم", "match_type": "PHRASE"}]}
    policy = {"schema_version": 1, "user_id": "owner", "account_id": "123", "revision": 2,
        "enabled": True, "activation_version": 1, "activation_revision": 2,
        "activated_by": "owner", "activated_at": NOW.isoformat(), "owner_generation": 1,
        "currency_code": "SAR", "time_zone": "Asia/Riyadh", "campaign_type": "SEARCH",
        "allowed_domain": "amasi-sa.com", "product_scopes": [{"product_id": "1", "final_url": "https://amasi-sa.com/p1"}],
        "template_id": "winter-search", "template_version": 1, "template_digest": "pending",
        "budget_native": "15", "max_account_daily_budget_native": "55",
        "geo_target_ids": ["2682"], "bidding_strategy": "MAXIMIZE_CONVERSION_VALUE",
        "max_paused_campaigns": 2, "max_daily_creations": 1, "max_total_creations": 3,
        "catalog_max_age_seconds": 3600, "auto_enable_new": False,
        "expires_at": (NOW + timedelta(days=1)).isoformat()}
    product = {"user_id": "owner", "salla_product_id": "1", "mezan_product_id": "mpv2_1",
        "name": "هودي شتوي بناتي بطباعة الاسم والحرف حسب الطلب", "sku": "AMS11549",
        "status": "active", "archived": False, "quantity": 4, "unlimited_quantity": False,
        "canonical_url": "https://amasi-sa.com/p1", "source_revision": "a" * 64, "last_synced_at": NOW.isoformat()}
    prove_inventory(product)
    native_context = {"user_id": "owner", "account_id": "123", "currency_code": "SAR", "time_zone": "Asia/Riyadh",
        "source": "google_ads_api", "identity_verified": True, "complete": True, "observed_at": NOW.isoformat(),
        "supported_campaign_types": ["SEARCH"], "geo_target_ids": ["2682"],
        "purchase_value_bidding_verified": True,
        "campaigns": [{"account_id": "123", "campaign_id": "456", "status": "ENABLED", "budget_mode": "DAILY",
            "budget_native": "40", "source": "google_ads_api", "currency_code": "SAR", "time_zone": "Asia/Riyadh", "observed_at": NOW.isoformat()}]}
    state = {"user_id": "owner", "account_id": "123", "complete": True, "observed_at": NOW.isoformat(),
        "created_today": 0, "paused_created": 0, "total_created": 0, "pending_write": False,
        "reserved_enable_budget_native": "0"}
    return dict(policy=policy, template=template, product=product, native_context=native_context,
        creation_state=state, choice={"product_id": "1", "template_id": "winter-search", "template_version": 1}, current=NOW)


def prepared_case():
    values = case()
    values["policy"]["template_digest"] = contract.approved_template_digest(values["template"])
    return values


def test_reviewed_search_product_can_prepare_only_a_paused_native_draft():
    values = prepared_case()
    result = contract.prepare_google_creation_intent(**values)
    assert result["ready_for_paused_preview"] is True
    assert result["initial_status"] == "PAUSED" and result["provider_writes"] == 0
    assert result["executable"] is False and result["auto_enable_new"] is False


def assert_blocked(values, code):
    with pytest.raises(contract.CreationContractError) as error:
        contract.prepare_google_creation_intent(**values)
    assert error.value.code == code


@pytest.mark.parametrize("changes,code", [
    ({"status": "inactive"}, "creation_product_not_public"),
    ({"status": "sale"}, "creation_product_not_public"),
    ({"status": "hidden"}, "creation_product_not_public"),
    ({"status": "out_of_stock"}, "creation_product_not_public"),
    ({"archived": True}, "creation_product_not_public"),
    ({"archived": None}, "creation_product_not_public"),
    ({"quantity": 0}, "creation_product_unavailable"),
    ({"quantity": None}, "creation_product_unavailable"),
    ({"quantity": True}, "creation_product_unavailable"),
    ({"user_id": "other"}, "creation_product_scope_unproved"),
    ({"salla_product_id": "2"}, "creation_product_scope_unproved"),
    ({"last_synced_at": (NOW - timedelta(hours=1)).isoformat()}, "creation_catalog_unproved"),
    ({"last_synced_at": (NOW + timedelta(seconds=1)).isoformat()}, "creation_catalog_unproved"),
    ({"source_revision": None}, "creation_catalog_unproved"),
    ({"source_revision": "owner says current"}, "creation_catalog_unproved"),
    ({"canonical_url": "https://other.example/p1"}, "creation_product_url_changed"),
    ({"canonical_url": "https://amasi-sa.com/different"}, "creation_product_url_changed"),
    ({"name": "<b>هودي</b>"}, "creation_product_copy_unproved"),
    ({"sku": "{product_name}"}, "creation_product_copy_unproved"),
])
def test_catalog_must_be_public_in_stock_owned_fresh_and_revisioned(changes, code):
    values = prepared_case(); values["product"].update(changes)
    assert_blocked(values, code)


def test_canonical_product_v2_status_is_not_guessed_from_raw_salla():
    from product_v2_routes import _status
    assert _status("sale") == "active" and _status("hidden") == "inactive"
    values = prepared_case(); values["product"]["status"] = _status("sale")
    assert contract.prepare_google_creation_intent(**values)["ready_for_paused_preview"] is True


def test_explicit_unlimited_inventory_and_public_url_leaf_are_supported():
    values = prepared_case()
    values["product"].update(quantity=None, unlimited_quantity=True, canonical_url=None,
        raw_salla={"urls": {"product": "https://amasi-sa.com/p1"}, "private": "never return this"})
    prove_inventory(values["product"])
    result = contract.prepare_google_creation_intent(**values)
    assert result["ready_for_paused_preview"] is True and "never return this" not in str(result)


@pytest.mark.parametrize("placeholder", ["{product_name.__class__}", "{product_name[0]}", "{product_name!r}",
    "{product_name:>30}", "{product_name:{sku}}", "{price}", "{}", "{{product_name}}", "{__import__('os')}"])
def test_owner_template_cannot_execute_formatting_or_unknown_product_fields(placeholder):
    values = prepared_case(); values["template"]["headlines"][0] = placeholder
    assert_blocked(values, "creation_contract_invalid")


@pytest.mark.parametrize("value", ["http://amasi-sa.com/p1", "https://other.example/p1", "https://user@amasi-sa.com/p1",
    "https://amasi-sa.com/p1?token=x", "https://amasi-sa.com/p1#x", "https://amasi-sa.com:80/p1",
    "https://amasi-sa.com\\@other.example/p1"])
def test_policy_pins_exact_https_store_domain_and_url(value):
    values = prepared_case(); values["policy"]["product_scopes"][0]["final_url"] = value
    assert_blocked(values, "creation_contract_invalid")


@pytest.mark.parametrize("changes,code", [
    ({"enabled": False}, "creation_policy_disabled"),
    ({"activation_version": None}, "creation_owner_activation_required"),
    ({"activation_revision": 1}, "creation_owner_activation_required"),
    ({"activated_by": "other"}, "creation_owner_activation_required"),
    ({"activated_at": (NOW + timedelta(seconds=1)).isoformat()}, "creation_owner_activation_required"),
    ({"expires_at": NOW.isoformat()}, "creation_policy_expired"),
    ({"expires_at": (NOW + timedelta(days=31)).isoformat()}, "creation_policy_expired"),
    ({"budget_native": "0"}, "creation_contract_invalid"),
    ({"budget_native": "56"}, "creation_contract_invalid"),
    ({"campaign_type": "PERFORMANCE_MAX"}, "creation_contract_invalid"),
    ({"bidding_strategy": "MANUAL_CPC"}, "creation_contract_invalid"),
    ({"product_scopes": []}, "creation_contract_invalid"),
    ({"geo_target_ids": []}, "creation_contract_invalid"),
    ({"max_daily_creations": True}, "creation_contract_invalid"),
    ({"owner_generation": 0}, "creation_contract_invalid"),
])
def test_no_implicit_owner_permission_budget_products_or_family_fallback(changes, code):
    values = prepared_case(); values["policy"].update(changes)
    assert_blocked(values, code)


@pytest.mark.parametrize("extra", [{"headlines": ["invented claim"]}, {"budget_native": "999"}, {"final_url": "https://other.example"}])
def test_model_selection_surface_has_no_copy_budget_or_url_fields(extra):
    values = prepared_case(); values["choice"].update(extra)
    assert_blocked(values, "creation_contract_invalid")


def test_model_can_only_choose_the_owner_allowed_product_and_exact_template_version():
    values = prepared_case(); values["choice"]["product_id"] = "2"
    assert_blocked(values, "creation_product_not_allowed")
    values = prepared_case(); values["choice"]["template_version"] = 2
    assert_blocked(values, "creation_template_not_approved")


def test_catalog_refresh_and_policy_limit_revision_do_not_duplicate_creation_identity():
    values = prepared_case(); original = contract.prepare_google_creation_intent(**values)
    fresh = deepcopy(values)
    fresh["current"] += timedelta(seconds=20)
    fresh["product"]["last_synced_at"] = fresh["current"].isoformat()
    refreshed = contract.prepare_google_creation_intent(**fresh)
    assert refreshed["creation_identity"] == original["creation_identity"]
    assert refreshed["intent_digest"] == original["intent_digest"]
    fresh["policy"].update(revision=3, activation_revision=3, max_account_daily_budget_native="60")
    revised = contract.prepare_google_creation_intent(**fresh)
    assert revised["creation_identity"] == original["creation_identity"]
    assert revised["intent_digest"] != original["intent_digest"]


def test_catalog_revision_drift_is_detected_without_creating_a_new_identity():
    values = prepared_case(); original = contract.prepare_google_creation_intent(**values)
    values["product"]["source_revision"] = "b" * 64
    prove_inventory(values["product"])
    revised = contract.prepare_google_creation_intent(**values)
    assert revised["creation_identity"] == original["creation_identity"] and revised["intent_digest"] != original["intent_digest"]
    with pytest.raises(contract.CreationContractError, match="creation_intent_changed"):
        contract.revalidate_google_creation_intent(original, **values)


def test_only_an_explicit_owner_generation_or_template_version_starts_new_creative_identity():
    values = prepared_case(); original = contract.prepare_google_creation_intent(**values)
    values["policy"]["owner_generation"] = 2
    assert contract.prepare_google_creation_intent(**values)["creation_identity"] != original["creation_identity"]
    values = prepared_case(); values["template"].update(version=2, approved_version=2)
    values["policy"].update(template_version=2, template_digest=contract.approved_template_digest(values["template"]))
    values["choice"]["template_version"] = 2
    assert contract.prepare_google_creation_intent(**values)["creation_identity"] != original["creation_identity"]


def test_short_name_is_a_word_boundary_prefix_without_new_copy_or_ellipsis():
    values = prepared_case(); result = contract.prepare_google_creation_intent(**values)
    product = result["binding"]["product"]
    assert product["product_name"] == values["product"]["name"]
    assert len(product["product_short_name"]) <= 30 and product["product_name"].startswith(product["product_short_name"])
    assert not product["product_short_name"].endswith("…")
    assert result["draft"]["groups"][0]["headlines"][0] == product["product_short_name"]


def test_full_name_or_prefix_with_extra_copy_still_must_fit_rsa_limits():
    values = prepared_case(); values["template"]["headlines"][0] = "{product_name}"
    values["policy"]["template_digest"] = contract.approved_template_digest(values["template"])
    assert_blocked(values, "creation_native_draft_incomplete")


@pytest.mark.parametrize("field,value,code", [("created_today", 1, "creation_daily_limit"),
    ("paused_created", 2, "creation_paused_limit"), ("total_created", 3, "creation_total_limit"),
    ("pending_write", True, "creation_account_pending"), ("complete", False, "creation_state_unproved")])
def test_account_creation_quotas_and_pending_write_are_not_bypassed(field, value, code):
    values = prepared_case(); values["creation_state"][field] = value
    assert_blocked(values, code)


def test_paused_creation_can_prepare_without_funding_but_auto_enable_is_blocked_at_55():
    values = prepared_case(); values["policy"]["auto_enable_new"] = True
    values["native_context"]["campaigns"][0]["budget_native"] = "55"
    result = contract.prepare_google_creation_intent(**values)
    assert result["ready_for_paused_preview"] is True and result["initial_status"] == "PAUSED"
    assert result["enable_readiness"]["ready_for_enable_preview"] is False
    assert result["enable_readiness"]["blockers"] == ["creation_account_budget_limit"]
    assert result["auto_enable_new"] is False


def test_pending_enable_reservation_counts_against_commitment_and_never_frees_budget():
    values = prepared_case(); values["policy"]["auto_enable_new"] = True
    values["creation_state"]["reserved_enable_budget_native"] = "1"
    result = contract.prepare_google_creation_intent(**values)
    assert result["enable_readiness"]["blockers"] == ["creation_account_budget_limit"]
    values["creation_state"]["reserved_enable_budget_native"] = "0"
    assert contract.prepare_google_creation_intent(**values)["enable_readiness"]["ready_for_enable_preview"] is True


@pytest.mark.parametrize("changes", [{"status": None}, {"budget_native": None}, {"budget_mode": "TOTAL"},
    {"observed_at": (NOW - timedelta(seconds=90)).isoformat()}])
def test_unproved_budget_inventory_never_allows_enable(changes):
    values = prepared_case(); values["policy"]["auto_enable_new"] = True
    values["native_context"]["campaigns"][0].update(changes)
    result = contract.prepare_google_creation_intent(**values)
    assert result["enable_readiness"]["blockers"] == ["creation_account_budget_unproved"]


@pytest.mark.parametrize("changes,code", [({"account_id": "999"}, "creation_native_context_unproved"),
    ({"complete": False}, "creation_native_context_unproved"), ({"source": "cache"}, "creation_native_context_unproved"),
    ({"observed_at": (NOW - timedelta(seconds=90)).isoformat()}, "creation_native_context_unproved"),
    ({"purchase_value_bidding_verified": False}, "creation_native_capability_unproved"),
    ({"geo_target_ids": []}, "creation_native_capability_unproved"),
    ({"supported_campaign_types": ["PERFORMANCE_MAX"]}, "creation_native_capability_unproved")])
def test_native_account_geo_and_value_bidding_proof_is_required(changes, code):
    values = prepared_case(); values["native_context"].update(changes)
    assert_blocked(values, code)


def valid_ledger(intent, campaign_id="999"):
    return {"provider": "google_ads", "user_id": "owner", "account_id": "123", "created_campaign_id": campaign_id,
        "proposal_id": "proposal123", "correlation_label": "MEZAN-proposal123", "initial_status": "PAUSED",
        "verified": True, "native_correlation_verified": True, "origin": "google_automation_creation_v1", "creation_identity": intent["creation_identity"],
        "intent_digest": intent["intent_digest"], "draft_digest": intent["binding"]["draft_digest"]}


def valid_proposal(intent, campaign_id="999"):
    import re
    from integrations_control_center.google_ads_campaign_builder import build_campaign_operations
    from integrations_control_center.google_ads_management import digest, immutable
    draft = intent["draft"]
    operations = build_campaign_operations(draft, "123")
    campaign = next(operation["campaignOperation"]["create"]["resourceName"] for operation in operations if "campaignOperation" in operation)
    label = "customers/123/labels/-9000000000000"
    operations += [{"labelOperation": {"create": {"resourceName": label, "name": "MEZAN-proposal123"}}},
        {"campaignLabelOperation": {"create": {"campaign": campaign, "label": label}}}]
    names = []
    for index, operation in enumerate(operations):
        kind, body = next(iter(operation.items()))
        if kind == "campaignOperation": name = f"customers/123/campaigns/{campaign_id}"
        elif kind == "labelOperation": name = "customers/123/labels/700"
        elif kind == "campaignLabelOperation": name = f"customers/123/campaignLabels/{campaign_id}~700"
        else: name = re.sub(r"/-[0-9]+", f"/{index + 100}", (body.get("create") or body.get("update")).get("resourceName", f"customers/123/adGroupAds/104~{index + 100}"))
        names.append(name)
    proposal = {"user_id": "owner", "account_id": "123", "provider": "google_ads", "proposal_id": "proposal123",
        "actor_id": "mezan_google_automation_v1", "action": "create_campaign", "status": "completed",
        "verified": True, "submission_started": True, "created_campaign_id": campaign_id,
        "correlation_label": "MEZAN-proposal123", "operations": operations, "resource_names": names,
        "planned": {**deepcopy(draft), "initial_status": "PAUSED"},
        "automation_source": {"kind": "standing_creation", "creation_identity": intent["creation_identity"],
            "intent_digest": intent["intent_digest"], "policy_revision": intent["binding"]["policy_revision"],
            "policy_digest": intent["binding"]["policy_digest"], "draft_digest": intent["binding"]["draft_digest"]}}
    proposal["confirmation_digest"] = digest(immutable(proposal))
    return proposal


def test_campaign_ownership_needs_private_original_proposal_and_native_correlation_proof():
    intent = contract.prepare_google_creation_intent(**prepared_case())
    ledger = valid_ledger(intent)
    args = dict(intent=intent, proposal=valid_proposal(intent), user_id="owner", account_id="123", campaign_id="999")
    assert contract.creation_campaign_ownership_verified(ledger, **args) is True
    assert contract.creation_campaign_ownership_verified({"name": "MEZAN-proposal123", "correlation_label": "MEZAN-proposal123"}, **args) is False
    for key, value in [("provider", "snapchat"), ("user_id", "other"), ("verified", False), ("origin", "manual"), ("draft_digest", "bad")]:
        changed = {**ledger, key: value}
        assert contract.creation_campaign_ownership_verified(changed, **args) is False
    assert contract.creation_campaign_ownership_verified(ledger, **{**args, "proposal": None}) is False


def test_manual_test_or_matching_manual_name_label_and_product_can_never_be_adopted():
    from integrations_control_center.google_ads_management import digest, immutable
    intent = contract.prepare_google_creation_intent(**prepared_case())
    campaign_id = "24390429267"
    ledger = valid_ledger(intent, campaign_id)
    manual = valid_proposal(intent, campaign_id)
    manual.update(actor_id="owner", automation_source=None)
    manual["confirmation_digest"] = digest(immutable(manual))
    # A reconstructed auto-looking ledger is insufficient: the original
    # server-owned proposal still proves this was a manual owner's creation.
    assert contract.creation_campaign_ownership_verified(ledger, intent=intent, proposal=manual,
        user_id="owner", account_id="123", campaign_id=campaign_id) is False
    manual["actor_id"] = "mezan_google_automation_v1"
    manual["confirmation_digest"] = digest(immutable(manual))
    assert contract.creation_campaign_ownership_verified(ledger, intent=intent, proposal=manual,
        user_id="owner", account_id="123", campaign_id=campaign_id) is False


@pytest.mark.parametrize("mutation", [
    lambda proposal: proposal.update(actor_id="owner"),
    lambda proposal: proposal.update(status="uncertain"),
    lambda proposal: proposal.update(submission_started=False),
    lambda proposal: proposal.update(verified=False),
    lambda proposal: proposal["automation_source"].update(kind="standing_policy"),
    lambda proposal: proposal["resource_names"].__setitem__(-1, "customers/123/campaignLabels/998~700"),
    lambda proposal: proposal["operations"][-2]["labelOperation"]["create"].update(name="MEZAN-manual999"),
])
def test_original_creation_proposal_cannot_be_reconstructed_or_changed_to_adopt_campaign(mutation):
    intent = contract.prepare_google_creation_intent(**prepared_case())
    proposal = valid_proposal(intent); mutation(proposal)
    assert contract.creation_campaign_ownership_verified(valid_ledger(intent), intent=intent, proposal=proposal,
        user_id="owner", account_id="123", campaign_id="999") is False


def test_native_correlation_must_be_independently_verified_in_private_ledger():
    intent = contract.prepare_google_creation_intent(**prepared_case())
    ledger = valid_ledger(intent); ledger["native_correlation_verified"] = False
    assert contract.creation_campaign_ownership_verified(ledger, intent=intent, proposal=valid_proposal(intent),
        user_id="owner", account_id="123", campaign_id="999") is False


def test_preparation_and_revalidation_never_mutate_inputs_or_accept_changed_draft():
    values = prepared_case(); before = deepcopy(values)
    intent = contract.prepare_google_creation_intent(**values)
    assert contract.revalidate_google_creation_intent(intent, **values) == intent and values == before
    intent["draft"]["budget_native"] = "50"
    with pytest.raises(contract.CreationContractError, match="creation_intent_changed"):
        contract.revalidate_google_creation_intent(intent, **values)


def test_policy_defaults_are_off_without_inventing_a_budget_or_product():
    from pydantic import ValidationError
    values = prepared_case(); values["policy"].pop("enabled"); values["policy"].pop("auto_enable_new")
    model = contract.GoogleAutomationCreationPolicy.model_validate(values["policy"])
    assert model.enabled is model.auto_enable_new is False
    for key in ("budget_native", "product_scopes", "owner_generation"):
        missing = deepcopy(values["policy"]); missing.pop(key)
        with pytest.raises(ValidationError): contract.GoogleAutomationCreationPolicy.model_validate(missing)


@pytest.mark.parametrize("change,code", [
    (lambda values: values["policy"].update(schema_version=True), "creation_contract_invalid"),
    (lambda values: values["native_context"].update(supported_campaign_types="SEARCH"), "creation_native_capability_unproved"),
    (lambda values: values["native_context"].update(geo_target_ids=[["2682"]]), "creation_native_capability_unproved"),
    (lambda values: values["creation_state"].update(reserved_enable_budget_native=0.1), "creation_state_unproved"),
])
def test_malformed_typed_boundaries_fail_closed_with_safe_codes(change, code):
    values = prepared_case(); change(values)
    assert_blocked(values, code)


def test_malformed_ownership_inputs_are_false_without_accessing_untrusted_shapes():
    intent = contract.prepare_google_creation_intent(**prepared_case())
    args = dict(intent=intent, user_id="owner", account_id="123", campaign_id=["999"])
    assert contract.creation_campaign_ownership_verified(valid_ledger(intent), **args) is False
    args["campaign_id"] = "999"; args["intent"] = {"binding": []}
    assert contract.creation_campaign_ownership_verified(valid_ledger(intent), **args) is False


def test_ownership_cannot_match_missing_digests_or_a_tampered_intent():
    intent = contract.prepare_google_creation_intent(**prepared_case())
    ledger = valid_ledger(intent)
    for key in ("creation_identity", "intent_digest"):
        changed = deepcopy(intent); changed[key] = None
        changed_ledger = {**ledger, key: None}
        assert contract.creation_campaign_ownership_verified(changed_ledger, intent=changed, user_id="owner", account_id="123", campaign_id="999") is False
    changed = deepcopy(intent); changed["draft"]["budget_native"] = "50"
    assert contract.creation_campaign_ownership_verified(ledger, intent=changed, user_id="owner", account_id="123", campaign_id="999") is False
