"""Reviewed rich terms in Native setup CAS; no financial writer or control edit."""
from copy import deepcopy
from typing import Literal

from pydantic import Field
from pymongo.errors import DuplicateKeyError

from accounting_module_contract import accounting_owner_id, require_accounting_permission, SHIPPING_CONTRACT_PERMISSIONS
from accounting_write_control import fresh_actor
from accounting_shipping_contracts import (
    ShippingContractInput, ShippingContractVersion, ShippingContractError, require_postable_contract,
)
from accounting_shipping_evidence import approval_snapshot, validate_bundle, verify_bundle
from accounting_shipping_native_contract import SetupInput, SETUP, MAX_ROWS, digest, fail, instant, now
from accounting_shipping_native_setup import read_setup, require_party
from accounting_shipping_native_contract_evidence import build_review, NativeEvidenceAuthority


class RichDraftInput(SetupInput):
    context: str = Field(min_length=1, max_length=120)
    terms: ShippingContractInput
    replaces_draft_id: str | None = Field(default=None, min_length=1, max_length=160)


class EvidenceReviewInput(SetupInput):
    courier_id: str = Field(min_length=1, max_length=120)
    file_id: str = Field(min_length=1, max_length=160)
    purpose: Literal["contract", "shipping_tax", "commission_tax"]
    confirmation: Literal["APPROVE_MZ2_SHIPPING_EVIDENCE"]


class EvidenceRevokeInput(SetupInput):
    evidence_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1, strict=True)
    confirmation: Literal["REVOKE_MZ2_SHIPPING_EVIDENCE"]


class RichApproveInput(SetupInput):
    draft_id: str = Field(min_length=1, max_length=160)
    draft_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    contract_evidence_id: str = Field(min_length=1, max_length=160)
    shipping_tax_evidence_id: str | None = Field(default=None, min_length=1, max_length=160)
    commission_tax_evidence_id: str | None = Field(default=None, min_length=1, max_length=160)
    confirmation: Literal["APPROVE_MZ2_SHIPPING_CONTRACT"]


async def _actor(db, owner, actor_id, action):
    actor = await fresh_actor(db, {"id": actor_id})
    require_accounting_permission(actor, SHIPPING_CONTRACT_PERMISSIONS[action])
    if accounting_owner_id(actor) != owner:
        fail("shipping_owner_scope_mismatch", 403)


async def list_rich_contracts(db, owner, actor_id):
    await _actor(db, owner, actor_id, "view")
    setup = await read_setup(db, owner)
    return {"version": setup["version"], "couriers": setup["couriers"],
            "drafts": setup.get("rich_drafts", []),
            "contract_evidence": setup.get("contract_evidence", []),
            "contracts": [r for r in setup["contracts"] if r.get("kind") == "rich"]}


async def require_current_rich_contract(db, owner, rate, at):
    """Readiness and the financial consumer use the same current proof."""
    try:
        version = require_postable_contract(rate["contract_version"], owner=owner,
                                            courier_id=rate["party_id"], accounting_at=at)
        if (rate["party_type"] != "courier" or version.id != rate["id"]
                or version.approved_by != rate["confirmed_by"]
                or version.approved_at != instant(rate["confirmed_at"])
                or version.effective_from != instant(rate["effective_from"])
                or version.effective_to != (instant(rate["effective_to"]) if rate["effective_to"] else None)):
            fail("shipping_contract_record_invalid")
    except (KeyError, ValueError) as exc:
        fail(str(exc) if isinstance(exc, ShippingContractError) else "shipping_contract_record_invalid")
    required = {"contract"}
    if version.shipping_cost > 0 and version.shipping_vat_percent > 0:
        required.add("shipping_tax")
    if version.commission_vat_percent > 0 and any(t.commission_percent > 0 or t.fixed_fee > 0 for t in version.cod_fee_tiers):
        required.add("commission_tax")
    bundle = rate.get("evidence_snapshot")
    items = validate_bundle(bundle, owner=owner, courier_id=version.courier_id)
    if {item["purpose"] for item in items} != required:
        fail("shipping_contract_evidence_binding_conflict")
    await verify_bundle(db, owner=owner, courier_id=version.courier_id, bundle=bundle,
                        authority=NativeEvidenceAuthority())
    return version


async def save_rich_setup(db, owner, actor_id, payload):
    """Only four bounded metadata actions, authorized before replay or CAS.

    Setup remains available while financial writes are paused, exactly as the
    existing setup path. This function never invokes a callback or journal.
    Evidence changes and approved terms replace the same owner document pinned
    by Native fee transactions, so approval/revocation cannot race a posting.
    """
    action = "manage" if isinstance(payload, RichDraftInput) else "review"
    await _actor(db, owner, actor_id, action)
    if payload.confirmed is not True:
        fail("shipping_owner_confirmation_required")
    old = await read_setup(db, owner)
    key = digest([type(payload).__name__, payload.request_id])
    fingerprint = digest(payload.model_dump(mode="json"))
    prior = old.get("rich_requests", {}).get(key)
    if prior:
        if prior["hash"] != fingerprint:
            fail("shipping_setup_idempotency_conflict")
        return {**prior["result"], "state": "already_saved", "version": old["version"]}
    if payload.version != old["version"]:
        fail("shipping_setup_version_conflict")
    if old["version"] >= MAX_ROWS:
        fail("shipping_setup_history_limit")
    doc, at = deepcopy(old), now()
    drafts = doc.setdefault("rich_drafts", [])
    records = doc.setdefault("contract_evidence", [])
    data = payload.model_dump(mode="json", exclude={"request_id", "version", "confirmed", "reason"})
    result = {}
    if isinstance(payload, RichDraftInput):
        await require_party(db, owner, old, "courier", payload.terms.courier_id)
        if payload.replaces_draft_id:
            prior_drafts = [d for d in drafts if d["id"] == payload.replaces_draft_id and d["status"] == "draft"]
            if len(prior_drafts) != 1:
                fail("shipping_contract_draft_unavailable")
            prior_drafts[0]["status"] = "superseded"
        draft = {"id": digest([owner, key]), "terms": data["terms"], "context": payload.context,
                 "status": "draft", "created_by": actor_id, "created_at": at}
        draft["hash"] = digest({k: draft[k] for k in ("id", "terms", "context")})
        drafts.append(draft)
        result, action = {"draft": deepcopy(draft)}, "rich_contract_drafted"
    elif isinstance(payload, EvidenceReviewInput):
        await require_party(db, owner, old, "courier", payload.courier_id)
        record = await build_review(db, owner=owner, actor_id=actor_id,
            courier_id=payload.courier_id, file_id=payload.file_id, purpose=payload.purpose,
            evidence_id=digest([owner, key]), approved_at=at)
        records.append(record)
        result, action = {"evidence": deepcopy(record)}, "shipping_source_reviewed"
    elif isinstance(payload, EvidenceRevokeInput):
        matches = [r for r in records if r["evidence_id"] == payload.evidence_id]
        if len(matches) != 1 or matches[0]["revision"] != payload.revision or matches[0]["state"] != "approved":
            fail("shipping_evidence_not_approved")
        matches[0].update(state="revoked", revision=payload.revision + 1,
                          revoked_by=actor_id, revoked_at=at, revocation_reason=payload.reason)
        result, action = {"evidence": deepcopy(matches[0])}, "shipping_source_revoked"
    elif isinstance(payload, RichApproveInput):
        matches = [d for d in drafts if d["id"] == payload.draft_id and d["status"] == "draft"]
        if len(matches) != 1:
            fail("shipping_contract_draft_unavailable")
        draft = matches[0]
        expected_hash = digest({k: draft[k] for k in ("id", "terms", "context")})
        if draft["hash"] != payload.draft_hash or draft["hash"] != expected_hash:
            fail("shipping_contract_draft_hash_mismatch")
        if draft["terms"].get("source_kind") == "legacy_copy":
            fail("legacy_copy_requires_confirmed_new_version")
        version = ShippingContractVersion(**draft["terms"], id=digest([owner, key]),
            user_id=owner, revision=old["version"] + 1, verification_status="approved",
            approved_by=actor_id, approved_at=at)
        await require_party(db, owner, old, "courier", version.courier_id)
        for rate in old["contracts"]:
            if (rate["party_type"], rate["party_id"], rate["context"]) != ("courier", version.courier_id, draft["context"]):
                continue
            if ((version.effective_to is None or instant(rate["effective_from"]) < version.effective_to)
                    and (rate["effective_to"] is None or version.effective_from < instant(rate["effective_to"]))):
                fail("shipping_rate_policy_overlap")
        evidence = await approval_snapshot(db, owner=owner, version=version, payload=payload,
                                           authority=NativeEvidenceAuthority())
        contract = {"id": version.id, "kind": "rich", "party_type": "courier",
            "party_id": version.courier_id, "context": draft["context"], "currency": "SAR",
            "effective_from": version.effective_from.isoformat(),
            "effective_to": version.effective_to.isoformat() if version.effective_to else None,
            "status": "approved", "confirmed_by": actor_id, "confirmed_at": at,
            "contract_version": version.model_dump(mode="json"), "evidence_snapshot": evidence,
            "draft_id": draft["id"], "draft_hash": draft["hash"]}
        doc["contracts"].append(contract)
        draft.update(status="approved", approved_contract_id=version.id)
        result, action = {"contract": contract}, "rich_contract_approved"
    else:
        fail("shipping_setup_payload_invalid", 422)
    doc["version"] += 1
    result = {**result, "state": "saved", "version": doc["version"]}
    doc.setdefault("rich_requests", {})[key] = {"hash": fingerprint, "result": result}
    doc["audit"].append({"action": action, "actor_id": actor_id, "at": at,
        "version": doc["version"], "reason": payload.reason, "request_hash": fingerprint, "data": data})
    try:
        if old["version"] == 0:
            await db[SETUP].insert_one(doc)
        else:
            changed = await db[SETUP].replace_one({"_id": owner, "user_id": owner,
                                                   "version": old["version"]}, doc)
            if changed.matched_count != 1:
                fail("shipping_setup_version_conflict")
    except DuplicateKeyError:
        fail("shipping_setup_version_conflict")
    return result
