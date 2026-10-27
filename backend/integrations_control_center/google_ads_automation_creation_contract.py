"""Pure, default-off SEARCH/RSA creation contracts; no IO or dispatch authority.

ADR-001 1/3/4/7/10/11/14: additive, tenant scoped and deterministic. Native
context, creation state, catalog product and approved template MUST come from
server-owned reads, never model/client assertions. The future gateway must
prove transport provenance, reserve quotas, validate with Google and authorize
each dispatch again. Nothing here creates a worker, policy, draft or campaign.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from product_inventory_proof import inventory_readiness
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
from string import Formatter
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .google_ads_campaign_contract import GoogleCampaignDraft
from .google_ads_campaign_builder import validate_draft_readiness

_ID = r"^[A-Za-z0-9_.:-]{1,160}$"
_MONEY = r"^(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,6})?$"
_FIELDS = frozenset({"product_name", "product_short_name", "sku"})


class CreationContractError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise CreationContractError(code)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _instant(value):
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc) if value.tzinfo else None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.astimezone(timezone.utc) if result.tzinfo else None
    except (ValueError, TypeError):
        return None


def _fresh(value, current, seconds):
    stamp = _instant(value)
    return stamp is not None and timedelta(0) <= current - stamp < timedelta(seconds=seconds)


def _amount(value, *, zero=False):
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() and (number >= 0 if zero else number > 0) else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _plain(value):
    return isinstance(value, str) and bool(value.strip()) and not any(ord(c) < 32 or c in "<>{}" for c in value)


def _template_text(value):
    if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 or c in "<>" for c in value):
        raise ValueError("plain owner-approved template text required")
    remaining = value
    try:
        for _, field, spec, conversion in Formatter().parse(value):
            if field is not None:
                if field not in _FIELDS or spec or conversion:
                    raise ValueError("only simple verified product placeholders are allowed")
                remaining = remaining.replace("{" + field + "}", "")
    except ValueError:
        raise ValueError("invalid product placeholder") from None
    if "{" in remaining or "}" in remaining:
        raise ValueError("escaped or nested placeholders are not allowed")
    return value


def _https_url(value):
    if not isinstance(value, str) or len(value) > 2000 or any(c.isspace() or ord(c) < 32 or c == "\\" for c in value):
        raise ValueError("exact public HTTPS URL required")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in {None, 443}):
            raise ValueError()
    except ValueError:
        raise ValueError("exact public HTTPS URL without query or credentials required") from None
    return value


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, revalidate_instances="always")


class CreationProductScope(Strict):
    product_id: str = Field(pattern=_ID)
    final_url: str = Field(max_length=2000)
    _url = field_validator("final_url")(_https_url)


class ApprovedKeyword(Strict):
    text: str = Field(min_length=1, max_length=160)
    match_type: Literal["EXACT", "PHRASE"]
    _text = field_validator("text")(_template_text)


class ApprovedSearchRSATemplate(Strict):
    template_id: str = Field(pattern=_ID)
    version: int = Field(ge=1, le=2**63 - 2)
    approved_version: int = Field(ge=1, le=2**63 - 2)
    approved_by: str = Field(min_length=1, max_length=128)
    approved_at: str = Field(max_length=64)
    ad_format: Literal["RSA"] = "RSA"
    campaign_name: str = Field(min_length=1, max_length=300)
    group_name: str = Field(min_length=1, max_length=300)
    headlines: list[str] = Field(min_length=3, max_length=15)
    descriptions: list[str] = Field(min_length=2, max_length=4)
    keywords: list[ApprovedKeyword] = Field(min_length=1, max_length=20)

    _names = field_validator("campaign_name", "group_name")(_template_text)

    @field_validator("headlines", "descriptions")
    @classmethod
    def copy_templates(cls, values):
        if any(len(value) > 300 for value in values):
            raise ValueError("template text is too long")
        return [_template_text(value) for value in values]

    @model_validator(mode="after")
    def reviewed_version(self):
        if self.version != self.approved_version:
            raise ValueError("the exact creative version must be owner approved")
        return self


class GoogleAutomationCreationPolicy(Strict):
    schema_version: Literal[1] = 1
    user_id: str = Field(min_length=1, max_length=128)
    account_id: str = Field(pattern=r"^[0-9]{1,20}$")
    revision: int = Field(ge=1, le=2**63 - 2)
    enabled: bool = False
    activation_version: int | None = None
    activation_revision: int | None = None
    activated_by: str | None = Field(default=None, max_length=128)
    activated_at: str | None = Field(default=None, max_length=64)
    owner_generation: int = Field(ge=1, le=2**63 - 2)
    campaign_type: Literal["SEARCH"] = "SEARCH"
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")
    time_zone: str = Field(min_length=1, max_length=100)
    allowed_domain: str = Field(min_length=3, max_length=253)
    product_scopes: list[CreationProductScope] = Field(min_length=1, max_length=8)
    template_id: str = Field(pattern=_ID)
    template_version: int = Field(ge=1, le=2**63 - 2)
    template_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    budget_native: str = Field(pattern=_MONEY)
    max_account_daily_budget_native: str = Field(pattern=_MONEY)
    geo_target_ids: list[str] = Field(min_length=1, max_length=20)
    bidding_strategy: Literal["MAXIMIZE_CONVERSION_VALUE"]
    max_paused_campaigns: int = Field(ge=1, le=20)
    max_daily_creations: int = Field(ge=1, le=20)
    max_total_creations: int = Field(ge=1, le=100)
    catalog_max_age_seconds: int = Field(ge=60, le=86400)
    auto_enable_new: bool = False
    expires_at: str = Field(max_length=64)

    @field_validator("schema_version", mode="before")
    @classmethod
    def exact_schema_version(cls, value):
        if type(value) is not int or value != 1:
            raise ValueError("schema_version must be integer one")
        return value

    @model_validator(mode="after")
    def scope(self):
        domain = self.allowed_domain
        if (domain != domain.lower() or not domain.isascii() or "." not in domain
            or not all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in domain.split("."))):
            raise ValueError("an exact lowercase store domain is required")
        if len({p.product_id for p in self.product_scopes}) != len(self.product_scopes):
            raise ValueError("product allowlist must be unique")
        if any(urlsplit(p.final_url).hostname != domain for p in self.product_scopes):
            raise ValueError("every product URL must belong to the pinned store domain")
        if (len(set(self.geo_target_ids)) != len(self.geo_target_ids)
            or any(not re.fullmatch(r"[0-9]{1,20}", geo) for geo in self.geo_target_ids)):
            raise ValueError("unique native geographic identifiers are required")
        if _amount(self.budget_native) is None or _amount(self.max_account_daily_budget_native) is None or Decimal(self.budget_native) > Decimal(self.max_account_daily_budget_native):
            raise ValueError("explicit positive budget cannot exceed account commitment limit")
        try:
            ZoneInfo(self.time_zone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("native account timezone is required") from None
        return self


class GoogleCreationChoice(Strict):
    """The whole model selection surface; no free copy, URL or budget fields."""
    product_id: str = Field(pattern=_ID)
    template_id: str = Field(pattern=_ID)
    template_version: int = Field(ge=1, le=2**63 - 2)


def approved_template_digest(template):
    value = ApprovedSearchRSATemplate.model_validate(template)
    return _digest(value.model_dump(mode="json"))


def _canonical_url(product):
    values = [product.get("canonical_url"), product.get("url")]
    for key in ("raw_salla_details", "raw_salla"):
        raw = product.get(key) if isinstance(product.get(key), dict) else {}
        urls = raw.get("urls") if isinstance(raw.get("urls"), dict) else {}
        values.extend([urls.get("product"), raw.get("url")])
    return next((value for value in values if isinstance(value, str) and value), None)


def _short_name(name):
    selected = []
    for word in name.split():
        if len(" ".join([*selected, word])) > 30:
            break
        selected.append(word)
    return " ".join(selected)


def _render(value, fields):
    for _, name, _, _ in Formatter().parse(value):
        if name is not None and not fields.get(name):
            _fail("creation_template_field_unproved")
    result = value
    for name in _FIELDS:
        result = result.replace("{" + name + "}", fields.get(name) or "")
    return result


def _native_gate(policy, native, current):
    if (not isinstance(native, dict) or native.get("complete") is not True or native.get("identity_verified") is not True
        or native.get("source") != "google_ads_api" or native.get("user_id") != policy.user_id
        or native.get("account_id") != policy.account_id
        or any(native.get(key) != getattr(policy, key) for key in ("currency_code", "time_zone"))
        or not _fresh(native.get("observed_at"), current, 90)):
        _fail("creation_native_context_unproved")
    types, geos = native.get("supported_campaign_types"), native.get("geo_target_ids")
    if (not isinstance(types, list) or len(types) > 30 or any(not isinstance(item, str) for item in types)
        or not isinstance(geos, list) or len(geos) > 1000
        or any(not isinstance(item, str) or not re.fullmatch(r"[0-9]{1,20}", item) for item in geos)
        or "SEARCH" not in types
        or native.get("purchase_value_bidding_verified") is not True
        or not set(policy.geo_target_ids) <= set(geos)):
        _fail("creation_native_capability_unproved")


def _state_gate(policy, state, current):
    if (not isinstance(state, dict) or state.get("complete") is not True
        or state.get("user_id") != policy.user_id or state.get("account_id") != policy.account_id
        or not _fresh(state.get("observed_at"), current, 90)
        or any(type(state.get(key)) is not int or state[key] < 0 for key in ("created_today", "paused_created", "total_created"))
        or type(state.get("pending_write")) is not bool
        or not isinstance(state.get("reserved_enable_budget_native"), str)
        or not re.fullmatch(_MONEY, state["reserved_enable_budget_native"])
        or _amount(state.get("reserved_enable_budget_native"), zero=True) is None):
        _fail("creation_state_unproved")
    if state["pending_write"]:
        _fail("creation_account_pending")
    if state["created_today"] >= policy.max_daily_creations:
        _fail("creation_daily_limit")
    if state["paused_created"] >= policy.max_paused_campaigns:
        _fail("creation_paused_limit")
    if state["total_created"] >= policy.max_total_creations:
        _fail("creation_total_limit")


def _funding(policy, native, state, current):
    campaigns = native.get("campaigns")
    if not isinstance(campaigns, list) or len(campaigns) > 500:
        return {"ready_for_enable_preview": False, "blockers": ["creation_account_budget_unproved"]}
    total = Decimal(0)
    ids = set()
    for row in campaigns:
        if (not isinstance(row, dict) or not isinstance(row.get("campaign_id"), str)
            or not re.fullmatch(r"[0-9]{1,30}", row["campaign_id"]) or row["campaign_id"] in ids
            or row.get("status") not in {"ENABLED", "PAUSED", "REMOVED"}):
            return {"ready_for_enable_preview": False, "blockers": ["creation_account_budget_unproved"]}
        ids.add(row["campaign_id"])
        if row["status"] != "ENABLED":
            continue
        amount = _amount(row.get("budget_native"))
        if (amount is None or row.get("budget_mode") != "DAILY" or row.get("account_id") != policy.account_id
            or row.get("source") != "google_ads_api" or not _fresh(row.get("observed_at"), current, 90)
            or any(row.get(key) != getattr(policy, key) for key in ("currency_code", "time_zone"))):
            return {"ready_for_enable_preview": False, "blockers": ["creation_account_budget_unproved"]}
        total += amount
    total += _amount(state["reserved_enable_budget_native"], zero=True)
    funded = total + Decimal(policy.budget_native) <= Decimal(policy.max_account_daily_budget_native)
    blockers = [] if funded else ["creation_account_budget_limit"]
    if not policy.auto_enable_new:
        blockers.append("creation_auto_enable_not_authorized")
    return {"ready_for_enable_preview": not blockers, "blockers": blockers,
        "enabled_and_reserved_average_budget_native": str(total), "account_commitment_limit_native": policy.max_account_daily_budget_native}


def prepare_google_creation_intent(*, policy, template, product, native_context, creation_state, choice, current):
    """Prepare an immutable PAUSED draft; this never reserves or sends it."""
    if not isinstance(current, datetime) or current.tzinfo is None:
        _fail("creation_clock_unproved")
    current = current.astimezone(timezone.utc)
    try:
        policy = GoogleAutomationCreationPolicy.model_validate(policy)
        template = ApprovedSearchRSATemplate.model_validate(template)
        choice = GoogleCreationChoice.model_validate(choice)
    except ValidationError:
        _fail("creation_contract_invalid")
    if not policy.enabled:
        _fail("creation_policy_disabled")
    activated = _instant(policy.activated_at)
    if (type(policy.activation_version) is not int or policy.activation_version != 1
        or type(policy.activation_revision) is not int or policy.activation_revision != policy.revision
        or policy.activated_by != policy.user_id or activated is None or activated > current):
        _fail("creation_owner_activation_required")
    expiry = _instant(policy.expires_at)
    if expiry is None or not current < expiry <= current + timedelta(days=30):
        _fail("creation_policy_expired")
    approved = _instant(template.approved_at)
    if (template.approved_by != policy.user_id or approved is None or approved > current
        or template.template_id != policy.template_id or template.version != policy.template_version
        or approved_template_digest(template) != policy.template_digest
        or choice.template_id != template.template_id or choice.template_version != template.version):
        _fail("creation_template_not_approved")
    scopes = [scope for scope in policy.product_scopes if scope.product_id == choice.product_id]
    if len(scopes) != 1:
        _fail("creation_product_not_allowed")
    scope = scopes[0]
    if (not isinstance(product, dict) or product.get("user_id") != policy.user_id
        or str(product.get("salla_product_id") or product.get("mezan_product_id") or "") != choice.product_id):
        _fail("creation_product_scope_unproved")
    # Product V2 normalizes sale/published/available to active and hidden/draft
    # to inactive. Raw provider status strings are not accepted here.
    if product.get("status") != "active" or product.get("archived") is not False:
        _fail("creation_product_not_public")
    if product.get("unlimited_quantity") is not True and _amount(product.get("quantity")) is None:
        _fail("creation_product_unavailable")
    if (not isinstance(product.get("source_revision"), str) or not re.fullmatch(r"[a-f0-9]{64}", product["source_revision"])
        or not _fresh(product.get("last_synced_at"), current, policy.catalog_max_age_seconds)):
        _fail("creation_catalog_unproved")
    inventory_state, inventory_digest = inventory_readiness(product, current, policy.catalog_max_age_seconds)
    if inventory_state != "available":
        _fail("creation_product_unavailable" if inventory_state == "unavailable" else "creation_product_inventory_unproved")
    if _canonical_url(product) != scope.final_url:
        _fail("creation_product_url_changed")
    name, sku = product.get("name"), product.get("sku") or None
    if not _plain(name) or len(name) > 500 or (sku is not None and (not _plain(sku) or len(sku) > 160)):
        _fail("creation_product_copy_unproved")
    fields = {"product_name": name, "product_short_name": _short_name(name), "sku": sku}
    _native_gate(policy, native_context, current)
    _state_gate(policy, creation_state, current)
    try:
        draft = GoogleCampaignDraft.model_validate({"account_id": policy.account_id, "product_ids": [choice.product_id],
            "campaign_type": "SEARCH", "name": _render(template.campaign_name, fields), "budget_native": policy.budget_native,
            "budget_mode": "DAILY", "geo_target_ids": policy.geo_target_ids, "language_ids": [],
            "bidding": {"strategy": policy.bidding_strategy},
            "search": {"target_search_network": False, "target_content_network": False, "target_partner_search_network": False},
            "ai_max": {"enabled": False, "text_customization": False, "final_url_expansion": False},
            "conversion_goals": [{"category": "PURCHASE", "origin": "WEBSITE", "biddable": True}],
            "groups": [{"name": _render(template.group_name, fields), "ad_format": "RSA", "status": "ENABLED", "ad_status": "ENABLED",
                "headlines": [_render(value, fields) for value in template.headlines],
                "descriptions": [_render(value, fields) for value in template.descriptions], "final_urls": [scope.final_url],
                "keywords": [{"text": _render(keyword.text, fields), "match_type": keyword.match_type} for keyword in template.keywords]}]})
        if validate_draft_readiness(draft, customer_id=policy.account_id):
            _fail("creation_native_draft_incomplete")
    except ValidationError:
        _fail("creation_native_draft_invalid")
    draft_value = draft.model_dump(mode="json")
    identity = {"user_id": policy.user_id, "account_id": policy.account_id, "product_id": choice.product_id,
        "template_id": template.template_id, "template_version": template.version, "owner_generation": policy.owner_generation}
    product_binding = {"catalog_collection": "mezan_products_v2", "product_id": choice.product_id,
        "source_revision": product["source_revision"], "product_name": name, "product_short_name": fields["product_short_name"],
        "sku": sku, "final_url": scope.final_url, "inventory_digest": inventory_digest}
    binding = {"schema_version": 1, "identity": identity, "policy_digest": _digest(policy.model_dump(mode="json")),
        "policy_revision": policy.revision, "template_digest": approved_template_digest(template),
        "product": product_binding, "draft_digest": _digest(draft_value), "initial_status": "PAUSED"}
    return {"provider": "google_ads", "provider_writes": 0, "executable": False,
        "ready_for_paused_preview": True, "initial_status": "PAUSED", "auto_enable_new": False,
        "provider_validation_required": True, "creation_authorization_required": True,
        "ownership_proof_required_before_enable": True,
        "creation_identity": "google_create_" + _digest(identity), "intent_digest": _digest(binding),
        "binding": binding, "draft": draft_value,
        "enable_readiness": _funding(policy, native_context, creation_state, current)}


def revalidate_google_creation_intent(intent, **fresh_inputs):
    fresh = prepare_google_creation_intent(**fresh_inputs)
    if (not isinstance(intent, dict) or intent.get("creation_identity") != fresh["creation_identity"]
        or intent.get("intent_digest") != fresh["intent_digest"] or intent.get("draft") != fresh["draft"]
        or intent.get("binding") != fresh["binding"] or intent.get("initial_status") != "PAUSED"):
        _fail("creation_intent_changed")
    return fresh


def _original_creation_proposal_verified(proposal, intent, *, user_id, account_id, campaign_id):
    """Inspect the original server-owned proposal, not a reconstructed ledger.

    The gateway must load this document itself. No public request or model may
    supply a substitute proposal. Core verification owns the native readback;
    this pure check binds its receipt to the original paused draft and label.
    """
    if (not isinstance(proposal, dict) or proposal.get("user_id") != user_id or proposal.get("account_id") != account_id
        or proposal.get("provider") != "google_ads" or proposal.get("actor_id") != "mezan_google_automation_v1"
        or proposal.get("action") != "create_campaign" or proposal.get("status") != "completed"
        or proposal.get("verified") is not True or proposal.get("submission_started") is not True
        or proposal.get("created_campaign_id") != campaign_id):
        return False
    from .google_ads_management import immutable
    from .google_ads_campaign_builder import build_campaign_operations, GoogleCampaignBuildError
    try:
        source = {"kind": "standing_creation", "creation_identity": intent["creation_identity"],
            "intent_digest": intent["intent_digest"], "policy_revision": intent["binding"]["policy_revision"],
            "policy_digest": intent["binding"]["policy_digest"], "draft_digest": intent["binding"]["draft_digest"]}
        if proposal.get("automation_source") != source or proposal.get("confirmation_digest") != _digest(immutable(proposal)):
            return False
        proposal_id = proposal.get("proposal_id")
        if not isinstance(proposal_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", proposal_id):
            return False
        correlation = "MEZAN-" + proposal_id
        if proposal.get("correlation_label") != correlation:
            return False
        operations = build_campaign_operations(intent["draft"], account_id)
        campaigns = [(i, operation["campaignOperation"]["create"]) for i, operation in enumerate(operations) if "campaignOperation" in operation]
        if len(campaigns) != 1 or campaigns[0][1].get("status") != "PAUSED":
            return False
        campaign_index, campaign = campaigns[0]
        temporary_label = f"customers/{account_id}/labels/-9000000000000"
        operations += [{"labelOperation": {"create": {"resourceName": temporary_label, "name": correlation}}},
            {"campaignLabelOperation": {"create": {"campaign": campaign["resourceName"], "label": temporary_label}}}]
        if proposal.get("operations") != operations:
            return False
        planned = proposal.get("planned")
        if (not isinstance(planned, dict) or planned.get("initial_status") != "PAUSED"
            or any(planned.get(key) != value for key, value in intent["draft"].items())):
            return False
        names = proposal.get("resource_names")
        if (not isinstance(names, list) or len(names) != len(operations)
            or any(not isinstance(name, str) or not name.startswith(f"customers/{account_id}/") for name in names)
            or names[campaign_index] != f"customers/{account_id}/campaigns/{campaign_id}"
            or not re.fullmatch(rf"customers/{re.escape(account_id)}/labels/[1-9][0-9]*", names[-2])):
            return False
        return names[-1] == f"customers/{account_id}/campaignLabels/{campaign_id}~{names[-2].rsplit('/', 1)[-1]}"
    except (KeyError, TypeError, ValueError, GoogleCampaignBuildError):
        return False


def creation_campaign_ownership_verified(ledger, *, intent, user_id, account_id, campaign_id, proposal=None):
    """Require private ledger AND original service proposal/native correlation.

    Campaign IDs are identity agnostic. Every manual creation is rejected even
    if its name, label and products resemble an automated campaign. The caller
    must load the original proposal and ledger from tenant-owned server storage.
    """
    if not isinstance(campaign_id, str):
        return False
    if (not isinstance(ledger, dict) or not isinstance(intent, dict) or not isinstance(campaign_id, str)
        or not re.fullmatch(r"[0-9]{1,30}", campaign_id) or not isinstance(ledger.get("proposal_id"), str)
        or not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", ledger["proposal_id"])):
        return False
    binding = intent.get("binding")
    if not isinstance(binding, dict) or not isinstance(binding.get("identity"), dict):
        return False
    try:
        if (intent.get("provider") != "google_ads" or intent.get("initial_status") != "PAUSED"
            or not isinstance(intent.get("draft"), dict)
            or intent.get("creation_identity") != "google_create_" + _digest(binding["identity"])
            or intent.get("intent_digest") != _digest(binding)
            or binding.get("draft_digest") != _digest(intent["draft"])):
            return False
    except (TypeError, ValueError):
        return False
    return (_original_creation_proposal_verified(proposal, intent, user_id=user_id, account_id=account_id, campaign_id=campaign_id)
        and ledger.get("proposal_id") == proposal["proposal_id"]
        and ledger.get("provider") == "google_ads" and ledger.get("user_id") == user_id and ledger.get("account_id") == account_id
        and ledger.get("created_campaign_id") == campaign_id and ledger.get("origin") == "google_automation_creation_v1"
        and ledger.get("verified") is True and ledger.get("native_correlation_verified") is True and ledger.get("initial_status") == "PAUSED"
        and ledger.get("correlation_label") == "MEZAN-" + ledger["proposal_id"]
        and ledger.get("creation_identity") == intent.get("creation_identity")
        and ledger.get("intent_digest") == intent.get("intent_digest")
        and ledger.get("draft_digest") == binding.get("draft_digest")
        and binding["identity"].get("user_id") == user_id
        and binding["identity"].get("account_id") == account_id)
