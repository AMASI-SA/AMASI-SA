"""Explicit tenant-scoped MZ2 advertising account bindings.

No legacy lookups and no external_ref inference. Binding mutations use the
unchanged financial pause/transaction gate; GET metadata never writes indexes.
"""
from datetime import date, datetime, timezone
import hashlib
import json
from typing import Literal
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import ReturnDocument
from accounting_atomic import atomic_owner

COLLECTION = "mz2_ad_account_bindings"
INTEGRATIONS = "mezan_integration_accounts_v2"
PROVIDERS = ("snapchat_ads", "tiktok_ads", "meta_ads", "google_ads")
FIELDS = (("prepaid_wallet_account_id", "ad_prepaid_wallet"), ("payable_account_id", "ad_payable"))


class AdBindingSave(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    version: int = Field(ge=0, strict=True)
    idempotency_key: str = Field(min_length=8, max_length=160)
    provider: Literal["snapchat_ads", "tiktok_ads", "meta_ads", "google_ads"]
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    effective_from: date
    status: Literal["active", "inactive"] = "active"
    prepaid_wallet_account_id: str | None = Field(default=None, min_length=1, max_length=160)
    payable_account_id: str | None = Field(default=None, min_length=1, max_length=160)

    @model_validator(mode="after")
    def meaningful_binding(self):
        if self.status == "active" and not (self.prepaid_wallet_account_id or self.payable_account_id):
            raise ValueError("ad_binding_account_required")
        if self.prepaid_wallet_account_id and self.prepaid_wallet_account_id == self.payable_account_id:
            raise ValueError("ad_binding_distinct_accounts_required")
        return self


def _fail(code, **details):
    raise HTTPException(409, detail={"code": code, "stage": "12", **details})


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def _active(row):
    return row.get("status") == "active" and row.get("is_active") is not False and not any(row.get(k) is True for k in ("archived", "is_archived", "deleted", "is_deleted"))


async def _integration(db, owner, integration_id):
    rows = await db[INTEGRATIONS].find({"user_id": owner, "mezan_integration_account_id": integration_id, "connection_provenance": "api_connection"}, {"_id": 0}).to_list(2)
    if len(rows) != 1 or rows[0].get("provider") not in PROVIDERS or not str(rows[0].get("external_account_id") or "").strip():
        _fail("ad_binding_integration_identity_invalid", integration_account_id=integration_id)
    return rows[0]


async def _binding_rows(db, owner, integration_id):
    return await db[COLLECTION].find({"user_id": owner, "integration_account_id": integration_id}).to_list(2)


async def _validate_accounts(db, owner, integration_id, content):
    gaps = []
    selected = []
    for field, kind in FIELDS:
        identity = content.get(field)
        if not identity:
            gaps.append(kind + "_missing")
            continue
        selected.append(identity)
        rows = await db.mz2_financial_accounts.find({"user_id": owner, "id": identity}, {"_id": 0}).to_list(2)
        if len(rows) != 1:
            _fail("ad_binding_financial_identity_invalid", field=field)
        account = rows[0]
        if not _active(account) or account.get("account_type") != kind:
            _fail("ad_binding_account_type_or_status_invalid", field=field)
        if account.get("currency") != content.get("currency"):
            _fail("ad_binding_currency_mismatch", field=field)
    if len(selected) != len(set(selected)):
        _fail("ad_binding_distinct_accounts_required")
    if selected and content.get("status") == "active":
        clash = await db[COLLECTION].find_one({"user_id": owner, "integration_account_id": {"$ne": integration_id}, "status": "active", "$or": [{field: {"$in": selected}} for field, _ in FIELDS]})
        if clash:
            _fail("ad_binding_financial_account_already_bound")
    return gaps


def _public(row, existing=False):
    return {**{k: v for k, v in row.items() if k not in {"_id", "user_id", "requests"}}, "existing": existing}


async def ad_binding_metadata(db, owner, profile, *, as_of=None):
    """Return explicit gaps, never substitute external_ref or legacy accounts."""
    identity = str(profile.get("mezan_integration_account_id") or profile.get("id") or "")
    result = {"prepaid_wallet_account_id": None, "payable_account_id": None, "currency": profile.get("currency"), "provider": profile.get("provider"), "binding_status": "missing", "binding_gaps": [], "version": 0, "effective_from": None, "binding_source": COLLECTION}
    try:
        integration = await _integration(db, owner, identity)
        rows = await _binding_rows(db, owner, identity)
        if not rows:
            result["binding_gaps"] = ["ad_binding_missing", "ad_prepaid_wallet_missing", "ad_payable_missing"]
            return result
        if len(rows) != 1:
            _fail("ad_binding_identity_ambiguous")
        binding = rows[0]
        result.update(version=binding.get("version", 0), effective_from=binding.get("effective_from"))
        if binding.get("provider") != integration["provider"]:
            _fail("ad_binding_provider_mismatch")
        if integration.get("currency") and binding.get("currency") != integration["currency"]:
            _fail("ad_binding_currency_mismatch")
        if binding.get("status") != "active":
            _fail("ad_binding_inactive")
        effective = date.fromisoformat(str(binding.get("effective_from")))
        if as_of is not None and effective > date.fromisoformat(str(as_of)[:10]):
            _fail("ad_binding_not_effective_at_cutover")
        gaps = await _validate_accounts(db, owner, identity, binding)
        result.update({field: binding.get(field) for field, _ in FIELDS})
        result.update(currency=binding["currency"], binding_status="incomplete" if gaps else "valid", binding_gaps=gaps)
    except HTTPException as exc:
        result.update(binding_status="invalid", binding_gaps=[exc.detail["code"]])
    except (ValueError, TypeError, KeyError):
        result.update(binding_status="invalid", binding_gaps=["ad_binding_contract_invalid"])
    return result


async def list_ad_bindings(db, owner):
    rows = await db[COLLECTION].find({"user_id": owner}, {"_id": 0, "user_id": 0, "requests": 0}).to_list(1001)
    if len(rows) > 1000:
        _fail("ad_binding_scope_too_large")
    return rows


async def save_ad_binding(db, owner, integration_id, payload: AdBindingSave, actor_id):
    """CAS/idempotent metadata write inside the existing paused financial gate."""
    request_hash = _hash(payload.model_dump(mode="json"))
    request_key = _hash(payload.idempotency_key)
    identity = "ad-binding-" + _hash([owner, integration_id])
    async def save(scoped):
        integration = await _integration(scoped, owner, integration_id)
        rows = await _binding_rows(scoped, owner, integration_id)
        if len(rows) > 1 or rows and rows[0].get("_id") != identity:
            _fail("ad_binding_identity_ambiguous")
        previous = rows[0] if rows else None
        if previous and request_key in previous.get("requests", {}):
            if previous["requests"][request_key]["hash"] != request_hash:
                _fail("ad_binding_idempotency_conflict")
            return _public(previous, True)
        if payload.version != (previous or {}).get("version", 0):
            _fail("ad_binding_version_conflict")
        if len((previous or {}).get("requests", {})) >= 1000:
            _fail("ad_binding_revision_limit")
        content = payload.model_dump(mode="json", exclude={"version", "idempotency_key"})
        if content["provider"] != integration["provider"]:
            _fail("ad_binding_provider_mismatch")
        if integration.get("currency") and content["currency"] != integration["currency"]:
            _fail("ad_binding_currency_mismatch")
        await _validate_accounts(scoped, owner, integration_id, content)
        now = datetime.now(timezone.utc).isoformat()
        version = payload.version + 1
        audit = {"version": version, "at": now, "actor_id": actor_id, "action": "ad_binding_save", "content": content}
        changes = {**content, "updated_at": now, "updated_by": actor_id, "version": version}
        record = {"hash": request_hash, "version": version}
        if previous:
            saved = await scoped[COLLECTION].find_one_and_update({"_id": identity, "user_id": owner, "version": payload.version}, {"$set": {**changes, f"requests.{request_key}": record}, "$push": {"audit": audit}}, return_document=ReturnDocument.AFTER)
            if not saved:
                _fail("ad_binding_version_conflict")
        else:
            saved = {"_id": identity, "id": identity, "user_id": owner, "integration_account_id": integration_id, **changes, "created_at": now, "created_by": actor_id, "audit": [audit], "requests": {request_key: record}}
            await scoped[COLLECTION].insert_one(saved)
        return _public(saved)
    return await atomic_owner(db, owner, save)
