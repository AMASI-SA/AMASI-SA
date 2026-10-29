"""Atomic owner policy and creative approval for the future creation gateway.

ADR-001 1/3/7/8/10/11/14: additive, default-off, tenant scoped, revision CAS,
bounded audit and no provider mutations. Catalog URLs, account identity and
approval identities are server derived. Saving never activates creation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import os
import re
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import Depends
from pydantic import Field, ValidationError, field_validator, model_validator
from pymongo.errors import DuplicateKeyError

from . import google_ads_automation_creation_contract as contract
from . import google_ads_automation_policy as base
from .google_ads_ai import PRODUCT_COLLECTION, _public_product
from .google_ads_creation_inventory import PRODUCT_PROJECTION, inventory_readiness
from .google_ads_client import GoogleAdsClient, identifier, problem
from .google_ads_campaign_builder import _display_width
from .google_ads_management import account_client
from .google_ads_workspace import compatible_read

CREATION_POLICIES = "mezan_google_ads_automation_creation_policies_v1"
POLICY_FIELDS = tuple(contract.GoogleAutomationCreationPolicy.model_fields)
PUBLIC_FIELDS = tuple(key for key in POLICY_FIELDS if key != "user_id") + ("template", "product_review", "geo_review", "configuration_blockers", "updated_by", "updated_at", "events")


def now():
    return datetime.now(timezone.utc)


def creation_runtime_available():
    if os.getenv("GOOGLE_ADS_AUTOMATION_CREATION_ENABLED", "false").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    try:
        from .google_ads_automation_creation_gateway import execute_google_automation_creation
        return callable(execute_google_automation_creation) and base.automation_runtime_available()
    except ImportError:
        return False


class OwnerSearchTemplate(base.StrictInput):
    template_id: str = Field(pattern=contract._ID)
    version: int = Field(ge=1, le=2**63 - 2)
    ad_format: Literal["RSA"] = "RSA"
    campaign_name: str = Field(min_length=1, max_length=300)
    group_name: str = Field(min_length=1, max_length=300)
    headlines: list[str] = Field(min_length=3, max_length=15)
    descriptions: list[str] = Field(min_length=2, max_length=4)
    keywords: list[contract.ApprovedKeyword] = Field(min_length=1, max_length=20)
    _names = field_validator("campaign_name", "group_name")(contract._template_text)

    @field_validator("headlines", "descriptions")
    @classmethod
    def copy(cls, values):
        if any(len(value) > 300 for value in values): raise ValueError("template is too long")
        return [contract._template_text(value) for value in values]


class CreationPolicySave(base.StrictInput):
    expected_revision: int = Field(ge=0, le=2**63 - 2)
    enabled: bool = False
    owner_generation: int = Field(ge=1, le=2**63 - 2)
    product_ids: list[str] = Field(min_length=1, max_length=8)
    template: OwnerSearchTemplate
    acknowledge_template: Literal[True]
    budget_native: str = Field(pattern=contract._MONEY)
    max_account_daily_budget_native: str = Field(pattern=contract._MONEY)
    geo_target_ids: list[str] = Field(min_length=1, max_length=20)
    bidding_strategy: Literal["MAXIMIZE_CONVERSION_VALUE"]
    max_paused_campaigns: int = Field(ge=1, le=20)
    max_daily_creations: int = Field(ge=1, le=20)
    max_total_creations: int = Field(ge=1, le=100)
    catalog_max_age_seconds: int = Field(ge=60, le=86400)
    auto_enable_new: bool = False
    expires_at: str = Field(max_length=64)

    @field_validator("acknowledge_template", mode="before")
    @classmethod
    def acknowledgement(cls, value):
        if type(value) is not bool or value is not True: raise ValueError("explicit creative approval is required")
        return value

    @model_validator(mode="after")
    def bounds(self):
        if len(set(self.product_ids)) != len(self.product_ids) or any(not re.fullmatch(contract._ID, value) for value in self.product_ids):
            raise ValueError("explicit unique product identifiers required")
        if len(set(self.geo_target_ids)) != len(self.geo_target_ids) or any(not re.fullmatch(r"[0-9]{1,20}", value) for value in self.geo_target_ids):
            raise ValueError("explicit unique geographic identifiers required")
        if Decimal(self.budget_native) <= 0 or Decimal(self.budget_native) > Decimal(self.max_account_daily_budget_native):
            raise ValueError("positive explicit budget must fit account limit")
        return self


class CreationPolicyActivation(base.StrictInput):
    expected_revision: int = Field(ge=1, le=2**63 - 2)
    acknowledge_creation: Literal[True]
    acknowledge_creative: Literal[True]
    acknowledge_auto_enable: bool = False
    @field_validator("acknowledge_creation", "acknowledge_creative", mode="before")
    @classmethod
    def explicit_acknowledgement(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("explicit owner acknowledgement is required")
        return value


def creation_policy_snapshot(row):
    """Project only the pure contract; no operational claims or audit history."""
    return {key: row[key] for key in POLICY_FIELDS if key in row}


def public_creation_policy(row, account_id, current):
    if not row:
        return {"account_id": account_id, "configured": False, "revision": 0, "enabled": False,
            "effective_enabled": False, "executor_available": creation_runtime_available(), "auto_enable_new": False,
            "product_scopes": [], "product_review": [], "configuration_blockers": [], "provider_writes": 0}
    available = creation_runtime_available()
    expiry = contract._instant(row.get("expires_at"))
    expired = expiry is None or expiry <= current
    stamp = contract._instant(row.get("activated_at"))
    proof = (type(row.get("revision")) is int and row["revision"] >= 1
        and type(row.get("activation_version")) is int and row["activation_version"] == 1
        and type(row.get("activation_revision")) is int and row["activation_revision"] == row.get("revision")
        and row.get("activated_by") == row.get("user_id") and stamp is not None and stamp <= current)
    in_flight = row.get("_dispatch_claim") is not None or row.get("_creation_claim") is not None
    blockers = list(row.get("configuration_blockers") or [])
    if row.get("auto_enable_new") is True and "auto_enable_not_implemented" not in blockers:
        blockers.append("auto_enable_not_implemented")
    return {**{key: row[key] for key in PUBLIC_FIELDS if key in row}, "configured": True,
        "executor_available": available, "expired": expired,
        "configuration_blockers": blockers,
        "effective_enabled": bool(available and row.get("enabled") is True and proof and not expired and not blockers),
        "activation_required": not proof, "in_flight_authorized": in_flight,
        "provider_writes": 0, "initial_status": "PAUSED", "budget_semantics": "average_daily_not_hard_spend_cap"}


def _expiry(value, current):
    expiry = contract._instant(value)
    if expiry is None or not current < expiry <= current + timedelta(days=30):
        raise problem("google_creation_expiry_invalid", "حدد انتهاءً خلال الثلاثين يومًا القادمة مع المنطقة الزمنية.", 422)
    return expiry.isoformat()


async def _catalog(db, user_id, ids, maximum_age, current):
    scopes, review, blockers, domains = [], [], [], set()
    for selected in ids:
        matches = await db[PRODUCT_COLLECTION].find({"user_id": user_id,
            "$or": [{"salla_product_id": selected}, {"mezan_product_id": selected}]}, PRODUCT_PROJECTION).to_list(length=2)
        if len(matches) > 1:
            raise problem("google_creation_product_ambiguous", "معرف المنتج يطابق أكثر من سجل؛ صحح هوية الكتالوج قبل التفويض.", 409)
        row = matches[0] if matches else None
        if not row or row.get("user_id") != user_id:
            raise problem("google_creation_product_not_owned", "أحد المنتجات غير متاح ضمن كتالوج متجرك.", 404)
        product = _public_product(row)
        if selected not in {product["product_id"], product["mezan_product_id"]}:
            raise problem("google_creation_product_not_owned", "تعذر إثبات هوية المنتج.", 404)
        url = contract._canonical_url(row)
        try:
            scope = contract.CreationProductScope(product_id=product["product_id"], final_url=url)
        except ValidationError:
            raise problem("google_creation_product_url_unproved", "يحتاج المنتج رابط متجر HTTPS أصليًا مثبتًا قبل حفظ التفويض.", 422) from None
        domains.add(urlsplit(url).hostname)
        if scope.product_id in {item["product_id"] for item in scopes}:
            raise problem("google_creation_product_duplicate", "اختر كل منتج مرة واحدة فقط.", 422)
        available = row.get("unlimited_quantity") is True or contract._amount(row.get("quantity")) is not None
        inventory_state, inventory_digest = inventory_readiness(row, current, maximum_age)
        codes = []
        if row.get("status") != "active" or row.get("archived") is not False: codes.append("product_not_public")
        if not available: codes.append("product_out_of_stock")
        elif inventory_state == "unavailable": codes.append("product_out_of_stock")
        elif inventory_state != "available": codes.append("product_inventory_unproved")
        available = available and inventory_state == "available"
        if (not isinstance(row.get("source_revision"), str) or not re.fullmatch(r"[a-f0-9]{64}", row["source_revision"])
            or not contract._fresh(row.get("last_synced_at"), current, maximum_age)): codes.append("product_facts_stale")
        if (not contract._plain(row.get("name")) or len(row["name"]) > 500
            or product["name"] != row.get("name") or (product["sku"] or None) != (row.get("sku") or None)
            or (row.get("sku") and (not contract._plain(row["sku"]) or len(row["sku"]) > 160))):
            codes.append("product_copy_unproved")
        blockers.extend(f"{code}:{scope.product_id}" for code in codes)
        scopes.append(scope.model_dump())
        review.append({"product_id": scope.product_id, "name": product["name"], "final_url": url,
            "product_short_name": contract._short_name(product["name"]), "sku": product["sku"],
            "status": product["status"], "available": available, "source_revision": row.get("source_revision"),
            "inventory_digest": inventory_digest,
            "last_synced_at": product["last_synced_at"], "blockers": codes})
    if len(domains) != 1:
        raise problem("google_creation_store_domain_ambiguous", "يجب أن تكون روابط المنتجات في نطاق متجر واحد مثبت.", 422)
    return scopes, review, blockers, next(iter(domains))


async def _native(db, user_id, account_id, geos, client_factory):
    client, _ = await account_client(db, user_id, account_id, client_factory=client_factory)
    try:
        metadata = await client.account_metadata()
        currency, zone = metadata.get("currency_code"), metadata.get("time_zone")
        try:
            if metadata.get("identity_verified") is not True or metadata.get("account_id") != account_id or not isinstance(currency, str) or not re.fullmatch(r"[A-Z]{3}", currency):
                raise ValueError()
            ZoneInfo(zone)
        except (ValueError, TypeError, ZoneInfoNotFoundError):
            raise problem("google_creation_native_identity_unproved", "تعذر إثبات هوية حساب جوجل وعملته وتوقيته.", 409) from None
        rows, complete, _ = await compatible_read(client,
            ["geo_target_constant.id", "geo_target_constant.name", "geo_target_constant.status"], "geo_target_constant",
            "geo_target_constant.status = 'ENABLED' AND geo_target_constant.id IN (" + ",".join(geos) + ")",
            required=("geo_target_constant.id", "geo_target_constant.status"), limit=21)
        native = [row.get("geoTargetConstant") or {} for row in rows]
        if (complete is not True or len(native) != len(geos)
            or {str(row.get("id")) for row in native} != set(geos) or any(row.get("status") != "ENABLED" for row in native)):
            raise problem("google_creation_geography_unproved", "أحد نطاقات الاستهداف غير مثبت أو غير متاح لدى جوجل.", 409)
        return metadata, [{"id": str(row["id"]), "name": str(row.get("name") or row["id"])[:160]} for row in native]
    finally:
        await client.close()


def _creative_blockers(template, products):
    """Check the concrete reviewed expansion without fabricating native proof."""
    blocked = []
    for product in products:
        fields = {"product_name": product["name"], "product_short_name": product["product_short_name"], "sku": product["sku"]}
        try:
            headlines = [contract._render(text, fields) for text in template.headlines]
            descriptions = [contract._render(text, fields) for text in template.descriptions]
            keywords = [contract._render(item.text, fields) for item in template.keywords]
            names = [contract._render(text, fields) for text in (template.campaign_name, template.group_name)]
            if (any(not 1 <= _display_width(text) <= 30 for text in headlines) or any(not 1 <= _display_width(text) <= 90 for text in descriptions)
                or any(not 1 <= len(text) <= 80 or len(text.split()) > 10 for text in keywords)
                or any(not 1 <= len(text) <= 255 for text in names)):
                raise ValueError()
        except (contract.CreationContractError, ValueError):
            blocked.append("creative_asset_limit:" + product["product_id"])
    return blocked


def _changed():
    raise problem("google_creation_policy_changed", "تغيرت سياسة الإنشاء؛ أعد تحميل النسخة المحفوظة قبل المتابعة.", 409)


def _require_runtime():
    if not creation_runtime_available():
        raise problem("google_creation_execution_unavailable", "إنشاء الحملات الآلي غير متاح حاليًا؛ تبقى السياسة دون تفعيل.", 409)


async def _index(db):
    await db[CREATION_POLICIES].create_index([("user_id", 1), ("account_id", 1)], unique=True, name="google_creation_tenant_account")


def attach_google_ads_automation_creation_policy_routes(router, db, current_user, *, client_factory=GoogleAdsClient):
    root = "/google_ads/automation/creation-policy/{account_id}"

    @router.get(root)
    async def read(account_id: str, user=Depends(current_user)):
        actor = await base._owner(db, user)
        scope = await base._account_scope(db, actor, account_id)
        row = await db[CREATION_POLICIES].find_one(scope, {"_id": 0})
        return public_creation_policy(row, scope["account_id"], now())

    @router.put(root)
    async def save(account_id: str, body: CreationPolicySave, user=Depends(current_user)):
        actor = await base._owner(db, user)
        if body.auto_enable_new:
            raise problem("google_creation_auto_enable_unavailable", "التفعيل اللاحق غير مكتمل حاليًا. احفظ التفويض لإنشاء حملات موقوفة فقط.", 422)
        scope = await base._account_scope(db, actor, account_id)
        prior = await db[CREATION_POLICIES].find_one(scope, {"_id": 0})
        if (prior or {}).get("revision", 0) != body.expected_revision: _changed()
        if prior and body.owner_generation < prior["owner_generation"]:
            raise problem("google_creation_generation_decreased", "لا يمكن الرجوع إلى إصدار إنشاء أقدم.", 422)
        template_input = body.template.model_dump(mode="json")
        if prior and prior.get("template_id") == body.template.template_id:
            previous = {key: prior["template"][key] for key in OwnerSearchTemplate.model_fields if key in prior["template"]}
            if body.template.version < prior["template_version"] or (body.template.version == prior["template_version"] and previous != template_input):
                raise problem("google_creation_template_version_required", "ارفع إصدار القالب عند تعديل محتواه المعتمد.", 422)
        _expiry(body.expires_at, now())
        scopes, products, blockers, domain = await _catalog(db, actor["user_id"], body.product_ids, body.catalog_max_age_seconds, now())
        metadata, geos = await _native(db, actor["user_id"], scope["account_id"], body.geo_target_ids, client_factory)
        await base._owner(db, user)
        await base._account_scope(db, actor, account_id)
        current, revision = now(), body.expected_revision + 1
        template = contract.ApprovedSearchRSATemplate.model_validate({**template_input,
            "approved_version": body.template.version, "approved_by": actor["user_id"], "approved_at": current.isoformat()})
        blockers += _creative_blockers(template, products)
        values = body.model_dump(exclude={"expected_revision", "product_ids", "template", "acknowledge_template", "enabled"})
        try:
            approved = contract.GoogleAutomationCreationPolicy.model_validate({**values, **scope, "schema_version": 1,
                "revision": revision, "enabled": False, "currency_code": metadata["currency_code"], "time_zone": metadata["time_zone"],
                "allowed_domain": domain, "product_scopes": scopes, "template_id": template.template_id, "template_version": template.version,
                "template_digest": contract.approved_template_digest(template), "expires_at": _expiry(body.expires_at, current)})
        except ValidationError:
            raise problem("google_creation_configuration_invalid", "راجع حدود الإنشاء والمنتجات والقالب.", 422) from None
        payload = {**approved.model_dump(mode="json"), "template": template.model_dump(mode="json"),
            "product_review": products, "geo_review": geos, "configuration_blockers": blockers,
            "updated_by": actor["actor_id"], "updated_at": current.isoformat()}
        event = {"kind": "configuration_saved_disabled", "revision": revision, "actor_id": actor["actor_id"], "occurred_at": current.isoformat()}
        await _index(db)
        if body.expected_revision == 0:
            try: await db[CREATION_POLICIES].insert_one({**payload, "events": [event]})
            except DuplicateKeyError: _changed()
        else:
            changed = await db[CREATION_POLICIES].update_one({**scope, "revision": body.expected_revision},
                {"$set": payload, "$push": {"events": {"$each": [event], "$slice": -50}}})
            if not changed.matched_count: _changed()
        row = await db[CREATION_POLICIES].find_one(scope, {"_id": 0})
        return public_creation_policy(row, scope["account_id"], now())

    @router.post(root + "/revoke")
    async def revoke(account_id: str, body: base.AutomationPolicyRevoke, user=Depends(current_user)):
        actor = await base._owner(db, user)
        scope = {"user_id": actor["user_id"], "account_id": identifier(account_id)}
        current, revision = now(), body.expected_revision + 1
        changed = await db[CREATION_POLICIES].update_one({**scope, "revision": body.expected_revision},
            {"$set": {"enabled": False, "revision": revision, "updated_by": actor["actor_id"], "updated_at": current.isoformat()},
             "$push": {"events": {"$each": [{"kind": "revoked", "revision": revision, "actor_id": actor["actor_id"], "occurred_at": current.isoformat()}], "$slice": -50}}})
        if not changed.matched_count: _changed()
        return public_creation_policy(await db[CREATION_POLICIES].find_one(scope, {"_id": 0}), scope["account_id"], now())

    @router.post(root + "/activate")
    async def activate(account_id: str, body: CreationPolicyActivation, user=Depends(current_user)):
        actor = await base._owner(db, user)
        _require_runtime()
        scope = await base._account_scope(db, actor, account_id)
        row = await db[CREATION_POLICIES].find_one(scope, {"_id": 0})
        if not row or row.get("revision") != body.expected_revision: _changed()
        try:
            settings = contract.GoogleAutomationCreationPolicy.model_validate(creation_policy_snapshot(row))
            template = contract.ApprovedSearchRSATemplate.model_validate(row["template"])
        except (ValidationError, KeyError):
            raise problem("google_creation_configuration_invalid", "احفظ سياسة الإنشاء وقالبها مجددًا قبل التفعيل.", 422) from None
        if (template.approved_by != actor["user_id"] or contract.approved_template_digest(template) != settings.template_digest
            or template.version != settings.template_version or template.template_id != settings.template_id
            or contract._instant(template.approved_at) is None or contract._instant(template.approved_at) > now()):
            raise problem("google_creation_template_unapproved", "القالب الحالي يحتاج اعتماد المالك مجددًا.", 409)
        if settings.auto_enable_new:
            raise problem("google_creation_auto_enable_unavailable", "التفعيل اللاحق غير مكتمل حاليًا؛ أعد حفظ السياسة بإنشاء حملات موقوفة فقط.", 409)
        scopes, products, blockers, domain = await _catalog(db, actor["user_id"], [p.product_id for p in settings.product_scopes], settings.catalog_max_age_seconds, now())
        if blockers or _creative_blockers(template, products):
            raise problem("google_creation_products_not_ready", "أحد المنتجات مخفي أو غير متاح أو يحتاج تحديث بياناته؛ احفظ السياسة وراجعها بعد التحديث.", 409)
        prior_products = {p["product_id"]: (p.get("source_revision"), p.get("name"), p.get("final_url"), p.get("inventory_digest")) for p in row.get("product_review", [])}
        if (domain != settings.allowed_domain or scopes != [p.model_dump() for p in settings.product_scopes]
            or any(prior_products.get(p["product_id"]) != (p["source_revision"], p["name"], p["final_url"], p["inventory_digest"]) for p in products)):
            raise problem("google_creation_product_review_changed", "تغيرت حقائق المنتج أو رابطه؛ أعد حفظ السياسة ومراجعتها قبل التفعيل.", 409)
        metadata, _ = await _native(db, actor["user_id"], scope["account_id"], settings.geo_target_ids, client_factory)
        if any(metadata[key] != getattr(settings, key) for key in ("currency_code", "time_zone")):
            raise problem("google_creation_account_changed", "تغيرت عملة الحساب أو توقيته؛ احفظ السياسة وراجعها مجددًا.", 409)
        await base._owner(db, user)
        await base._account_scope(db, actor, account_id)
        _require_runtime()
        current, revision = now(), body.expected_revision + 1
        _expiry(settings.expires_at, current)
        _, final_products, final_blockers, _ = await _catalog(db, actor["user_id"], [p.product_id for p in settings.product_scopes], settings.catalog_max_age_seconds, current)
        if (final_blockers or _creative_blockers(template, final_products)
            or any(prior_products.get(p["product_id"]) != (p["source_revision"], p["name"], p["final_url"], p["inventory_digest"]) for p in final_products)):
            raise problem("google_creation_product_review_changed", "تغيرت جاهزية المنتج أثناء التحقق؛ راجع السياسة مجددًا.", 409)
        # The final catalog read is awaited: authority and runtime may have
        # changed while it was in flight. Check again after that boundary.
        await base._owner(db, user)
        await base._account_scope(db, actor, account_id)
        _require_runtime()
        current = now()
        _expiry(settings.expires_at, current)
        if any(not contract._fresh(p.get("last_synced_at"), current, settings.catalog_max_age_seconds) for p in final_products):
            raise problem("google_creation_products_not_ready", "تحتاج حقائق المنتج تحديثًا قبل تفعيل الإنشاء.", 409)
        changed = await db[CREATION_POLICIES].update_one({**scope, "revision": body.expected_revision},
            {"$set": {"enabled": True, "revision": revision, "activation_version": 1, "activation_revision": revision,
                "activated_by": actor["user_id"], "activated_at": current.isoformat(), "configuration_blockers": [],
                "updated_by": actor["actor_id"], "updated_at": current.isoformat()},
             "$push": {"events": {"$each": [{"kind": "owner_activation", "revision": revision,
                "actor_id": actor["actor_id"], "auto_enable_acknowledged": body.acknowledge_auto_enable, "occurred_at": current.isoformat()}], "$slice": -50}}})
        if not changed.matched_count: _changed()
        return public_creation_policy(await db[CREATION_POLICIES].find_one(scope, {"_id": 0}), scope["account_id"], now())
