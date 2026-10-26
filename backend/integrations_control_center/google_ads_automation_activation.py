"""Explicit owner activation of one reviewed standing-policy revision.

ADR-001 1/3/7/8/10/11/14: default-off, tenant scoped, revision CAS, audited,
and no provider mutation. Installing code or saving an enabled legacy setting
never manufactures the activation proof required by the dispatch authority.
"""
from __future__ import annotations

from typing import Literal

from fastapi import Depends
from pydantic import Field, ValidationError, field_validator

from . import google_ads_automation_policy as policies
from .google_ads_client import GoogleAdsClient, problem
from .google_ads_automation_policy_mutation_adapter import mutate_google_automation_policy


class AutomationActivationInput(policies.StrictInput):
    expected_revision: int = Field(ge=1, le=2**63 - 2)
    acknowledge_execution: Literal[True]

    @field_validator("acknowledge_execution", mode="before")
    @classmethod
    def explicit_boolean(cls, value):
        if type(value) is not bool or value is not True:
            raise ValueError("explicit execution acknowledgement is required")
        return value


def _unavailable():
    if not policies.automation_runtime_available():
        raise problem("google_automation_execution_unavailable", "التنفيذ الآلي غير متاح حاليًا؛ تبقى السياسة دون تفعيل.", 409)


def _validate_configuration(row, revision, current):
    if (not row or type(row.get("schema_version")) is not int or row["schema_version"] != 1
        or type(row.get("revision")) is not int or row["revision"] != revision):
        raise problem("google_automation_policy_changed", "تغيرت السياسة؛ أعد تحميل الحدود ومراجعتها قبل التفعيل.", 409)
    try:
        fields = set(policies.AutomationPolicyInput.model_fields) - {"expected_revision", "enabled"}
        if not fields <= set(row):
            raise ValueError()
        values = {key: row[key] for key in policies.AutomationPolicyInput.model_fields
            if key not in {"expected_revision", "enabled"} and key in row}
        validated = policies.AutomationPolicyInput.model_validate({**values, "expected_revision": revision, "enabled": True})
    except (ValidationError, ValueError):
        raise problem("google_automation_configuration_invalid", "أكمل الحملات والإجراءات والحدود قبل تفعيل السياسة.", 422) from None
    policies._expiry(validated.expires_at, current)


def attach_google_ads_automation_activation_routes(router, db, current_user, *, client_factory=GoogleAdsClient, policy_mutation_writer=None):
    """Optional guarded storage DI; None remains explicitly unmigrated legacy."""
    @router.post("/google_ads/automation/policy/{account_id}/activate")
    async def activate_policy(account_id: str, body: AutomationActivationInput, user=Depends(current_user)):
        actor = await policies._owner(db, user)
        _unavailable()
        scope = await policies._account_scope(db, actor, account_id)
        row = await db[policies.AUTOMATION_POLICIES].find_one(scope, {"_id": 0})
        _validate_configuration(row, body.expected_revision, policies.now())
        client, _ = await policies.account_client(db, actor["user_id"], scope["account_id"], client_factory=client_factory)
        try:
            metadata = await client.account_metadata()
        finally:
            await client.close()
        if (metadata.get("identity_verified") is not True or metadata.get("account_id") != scope["account_id"]
            or any(not row.get(key) or metadata.get(key) != row[key] for key in ("currency_code", "time_zone"))):
            raise problem("google_automation_account_changed", "تغيرت هوية حساب جوجل أو عملته أو توقيته؛ احفظ الحدود وراجعها مجددًا.", 409)
        # Revalidate persisted owner, linked account, flags and expiry after the
        # native read; a stale acknowledgement cannot survive a revocation CAS.
        fresh_actor = await policies._owner(db, user)
        if fresh_actor["user_id"] != actor["user_id"]:
            raise problem("google_ads_actor_scope_changed", "تغيرت صلاحية المالك قبل التفعيل.", 403)
        await policies._account_scope(db, fresh_actor, account_id)
        _unavailable()
        current = policies.now()
        _validate_configuration(row, body.expected_revision, current)
        revision = body.expected_revision + 1
        from .google_ads_automation_authority import ACTIVATION_VERSION
        proof = {"enabled": True, "revision": revision, "activation_version": ACTIVATION_VERSION,
            "activation_revision": revision, "activated_by": actor["user_id"], "activated_at": current.isoformat(),
            "updated_by": actor["actor_id"], "updated_at": current.isoformat()}
        event = {**policies._event(actor, revision, True, current), "kind": "owner_activation"}
        await mutate_google_automation_policy(db, policy_kind="standing", scope=scope,
            expected_revision=body.expected_revision, values=proof, event=event, writer=policy_mutation_writer)
        result = await db[policies.AUTOMATION_POLICIES].find_one(scope, {"_id": 0})
        return policies.public_policy(result, account_id=scope["account_id"], current=policies.now())
