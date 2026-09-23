"""Review candidate: persisted dated carrier contracts and delivered accrual.

No activation, bank movement, sales recognition, legacy financial reads, or
background task. All monetary facts come from accounting_shipping_contracts.
The existing MZ2 owner transaction and ledger core remain the only writers.

V4 isolation: this new module is not wired into any existing route. Its native
writers and route installer are closed. In particular, the current ledger does
not yet accept shipping_cod_reclass and its report producer is not amended by
this isolated patch. The implementation below is retained review work, not an
operational replacement or proof that its integration tests can pass today.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Context, Decimal, DecimalException, InvalidOperation, ROUND_HALF_UP
from typing import Any, Literal

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from pymongo.errors import DuplicateKeyError

from accounting_atomic import atomic_owner
from accounting_shipping_contract_gate import (
    native_contract_readiness, require_native_contract_runtime,
)
from accounting_courier_bank_routes import external_courier_catalog_from_rows
from accounting_module_contract import (
    OPERATION_ID, SHIPPING_CONTRACT_PERMISSIONS, ACCOUNTING_PERMISSION_KEYS,
    accounting_owner_id, require_accounting_permission,
)
from accounting_mz2_balances import read_mz2_write_balances
from accounting_mz2_reports import read_mz2_ledger
from accounting_periods import assert_open_journal_periods
from accounting_shipping_contracts import (
    ShippingContractError, ShippingContractInput, ShippingContractVersion,
    quote_shipping_contract, select_shipping_contract,
)
from accounting_shipping_p02 import (
    MAX_POLICY_VERSIONS, SOURCE, ShippingAccountingError, _event_date_from_salla,
    _event_record, _hash, _insert_event_posting, _money, _positive,
    read_shipping_policy,
)
from accounting_write_control import fresh_actor
import accounting_shipping_evidence as shipping_evidence
from accounting_shipping_payment_evidence import shipping_order_provider
from ledger_core import post_txn_group
from shipping_companies import normalize_shipping_company
from store_delivery_accounting import require_p02_shipping_financial_writes

SCHEMA = "mz2.shipping.contract.v1"
POLICY_COLLECTION = "mz2_shipping_rate_policies"
AUDIT_COLLECTION = "mz2_shipping_contract_audit"



def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _terms(raw: dict) -> ShippingContractVersion:
    try:
        if raw.get("contract_schema") != SCHEMA:
            raise ShippingContractError("dated_shipping_contract_required")
        version = ShippingContractVersion.model_validate({
            key: raw[key] for key in ShippingContractVersion.model_fields if key in raw
        })
        if raw.get("terms_hash") != _hash(version.model_dump(mode="json")):
            raise ShippingContractError("shipping_contract_integrity_conflict")
        if version.verification_status == "approved":
            shipping_evidence.validate_bundle(raw.get("evidence_snapshot"), owner=version.user_id,
                                              courier_id=version.courier_id)
            proof_ids = {item["purpose"]: item["evidence_id"] for item in raw["evidence_snapshot"]["items"]}
            expected_ids = {"contract": raw.get("contract_evidence_id")}
            if version.shipping_cost > 0 and version.shipping_vat_percent > 0:
                expected_ids["shipping_tax"] = raw.get("shipping_tax_evidence_id")
            if (version.commission_vat_percent > 0
                    and any(t.commission_percent > 0 or t.fixed_fee > 0 for t in version.cod_fee_tiers)):
                expected_ids["commission_tax"] = raw.get("commission_tax_evidence_id")
            if proof_ids != expected_ids or any(not value for value in expected_ids.values()):
                raise ShippingContractError("shipping_contract_evidence_binding_conflict")
            if raw.get("evidence_snapshot_sha256") != raw["evidence_snapshot"]["sha256"]:
                raise ShippingContractError("shipping_contract_evidence_integrity_conflict")
        return version
    except (ValidationError, ShippingContractError) as exc:
        raise ShippingAccountingError("shipping_contract_record_invalid") from exc


def _public(row: dict) -> dict:
    return {key: value for key, value in row.items() if key != "_id"}


async def _actor(db, *, owner: str, actor: dict, action: str):
    permission = SHIPPING_CONTRACT_PERMISSIONS[action]
    if permission not in ACCOUNTING_PERMISSION_KEYS:
        raise HTTPException(503, detail={"code": "shipping_permission_registry_not_ready"})
    fresh = await fresh_actor(db, {"id": actor.get("id")})
    require_accounting_permission(fresh, permission)
    if accounting_owner_id(fresh) != owner:
        raise HTTPException(403, "shipping_contract_owner_scope_mismatch")
    return fresh


async def configured_couriers(db, owner: str) -> list[dict]:
    """Identity only from actual persisted settings; NEVER default catalog/rates.

    courier_key is the same canonical identifier used by P01 bank bindings.
    A raw request string is never normalized into a new carrier here.
    Duplicate canonical keys are rejected, rather than silently merged.
    """
    settings = await db.settings.find_one(
        {"user_id": owner}, {"_id": 0, "shipping_companies": 1}
    ) or {}
    rows = settings.get("shipping_companies")
    if rows is None:
        return []
    if not isinstance(rows, list):
        raise HTTPException(409, "shipping_courier_catalog_invalid")
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise HTTPException(409, "shipping_courier_catalog_invalid")
        identity = external_courier_catalog_from_rows([row])
        if not identity:
            continue
        key, display = identity[0]["courier_key"], identity[0]["display_name"]
        if key in seen:
            raise HTTPException(409, "shipping_courier_identity_ambiguous")
        seen.add(key)
        result.append({
            "courier_id": key, "provider_id": "shipping:" + key,
            "display_name": display,
            "active": all(row.get(field) is not False for field in ("is_active", "active"))
                and row.get("status") not in {"inactive", "disabled", "deleted"}
                and row.get("archived") is not True and row.get("deleted") is not True,
        })
    return result


async def _courier(db, owner: str, courier_id: str) -> dict:
    found = [c for c in await configured_couriers(db, owner) if c["courier_id"] == courier_id]
    if len(found) != 1:
        raise HTTPException(404, "shipping_courier_not_configured")
    if not found[0]["active"]:
        raise HTTPException(409, "shipping_courier_inactive")
    return found[0]


async def _lock_courier_identity(db, owner: str, courier_id: str):
    """Serialize against non-accounting edits to the actual settings catalogue.

    A snapshot read alone could accept a concurrently removed carrier. The
    conditional write locks that same settings document until commit/abort.
    Only this bookkeeping revision changes; no legacy price/balance is copied.
    """
    settings = await db.settings.find_one({"user_id": owner}, {"_id": 0, "shipping_companies": 1}) or {}
    rows = settings.get("shipping_companies")
    if not isinstance(rows, list):
        raise HTTPException(409, "shipping_courier_catalog_invalid")
    changed = await db.settings.update_one(
        {"user_id": owner, "shipping_companies": {"$eq": rows}},
        {"$inc": {"mz2_shipping_identity_revision": 1}},
    )
    if changed.matched_count != 1:
        raise HTTPException(409, "shipping_courier_catalog_changed")
    return await _courier(db, owner, courier_id)


class ContractDraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=120)
    revision: int = Field(ge=0, strict=True)
    terms: ShippingContractInput
    replaces_draft_id: str | None = Field(default=None, min_length=1, max_length=200)
    reason: str = Field(min_length=3, max_length=500)

    @field_validator("request_id", "reason")
    @classmethod
    def text(cls, value, info):
        value = value.strip()
        if len(value) < (8 if info.field_name == "request_id" else 3):
            raise ValueError("shipping_contract_text_required")
        return value


class ContractApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=8, max_length=120)
    revision: int = Field(ge=0, strict=True)
    draft_id: str = Field(min_length=1, max_length=200)
    draft_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    contract_evidence_id: str = Field(min_length=1, max_length=200)
    confirmation: Literal["APPROVE_MZ2_SHIPPING_CONTRACT"]
    reason: str = Field(min_length=3, max_length=500)
    shipping_tax_evidence_id: str | None = Field(default=None, min_length=3, max_length=500)
    commission_tax_evidence_id: str | None = Field(default=None, min_length=3, max_length=500)

    @field_validator("request_id", "reason", "contract_evidence_id", "shipping_tax_evidence_id", "commission_tax_evidence_id")
    @classmethod
    def text(cls, value, info):
        if value is None:
            return None
        value = value.strip()
        if len(value) < (8 if info.field_name == "request_id" else 3):
            raise ValueError("shipping_contract_text_required")
        return value


def _new_rows(policy):
    rows = []
    for row in policy.get("versions") or []:
        if row.get("contract_schema") == SCHEMA:
            _terms(row)  # Do not ignore corrupt native contracts.
            if row.get("user_id") != policy.get("user_id"):
                raise ShippingAccountingError("shipping_contract_policy_owner_conflict")
            rows.append(row)
    return rows


def _superseded(rows):
    return {
        row[key] for row in rows for key in ("replaces_draft_id", "approved_from_draft_id")
        if row.get(key)
    }


def _check_interval(policy, candidate: ShippingContractInput, *, excluding=None):
    """Invoked only inside owner transaction, before a CAS on ONE policy row.

    A date/unique index cannot exclude arbitrary interval intersections. Mongo's
    owner serialization row + revision predicate prevent concurrent approvals
    from independently accepting intersecting intervals.
    """
    rows = _new_rows(policy)
    ignored = _superseded(rows) | ({excluding} if excluding else set())
    for row in rows:
        if row["id"] in ignored or row["courier_id"] != candidate.courier_id:
            continue
        old = _terms(row)
        if ((candidate.effective_to is None or old.effective_from < candidate.effective_to)
                and (old.effective_to is None or candidate.effective_from < old.effective_to)):
            raise HTTPException(409, "shipping_contract_interval_overlap")


def _revision(policy, expected):
    if policy.get("revision", 0) != expected:
        raise HTTPException(409, "shipping_rate_policy_changed_refresh_required")
    if len(policy.get("versions") or []) >= MAX_POLICY_VERSIONS:
        raise HTTPException(409, "shipping_rate_policy_history_limit")


async def _append_version(db, *, owner, policy, row, audit):
    """Append only; persist policy and audit in the caller's owner transaction."""
    updated = {
        **policy, "_id": owner, "user_id": owner, "revision": row["revision"],
        "versions": [*(policy.get("versions") or []), row],
        "audit": [*(policy.get("audit") or []), audit],
    }
    if policy.get("revision", 0) == 0:
        try:
            await db[POLICY_COLLECTION].insert_one(updated)
        except DuplicateKeyError:
            # Raise out of the callback so the entire transaction aborts.
            # Never continue using the aborted session or expose driver errors.
            raise HTTPException(409, "shipping_rate_policy_changed_refresh_required") from None
    else:
        result = await db[POLICY_COLLECTION].replace_one(
            {"_id": owner, "user_id": owner, "revision": policy["revision"]}, updated
        )
        if result.matched_count != 1:
            raise HTTPException(409, "shipping_rate_policy_changed_refresh_required")
    await db[AUDIT_COLLECTION].insert_one({"_id": audit["request_key"], **audit})


async def _retry(db, *, owner, action, request_id, digest):
    key = _hash([owner, "shipping_contract", action, request_id])
    prior = await db[AUDIT_COLLECTION].find_one({"_id": key, "user_id": owner})
    if not prior:
        return key, None
    if prior.get("request_hash") != digest:
        raise HTTPException(409, "shipping_contract_request_conflict")
    policy = await read_shipping_policy(db, owner)
    rows = [r for r in policy.get("versions", []) if r.get("id") == prior["version_id"]]
    if len(rows) != 1:
        raise HTTPException(409, "shipping_contract_retry_requires_recovery")
    return key, {"state": "already_saved", "revision": policy["revision"], "version": _public(rows[0])}


async def save_contract_draft(db, *, owner, actor, payload: ContractDraftIn):
    require_native_contract_runtime()
    # Reject impersonation/unlinked users before atomic_owner can create its
    # non-financial coordination row. Recheck persisted permissions in session.
    await _actor(db, owner=owner, actor=actor, action="manage")
    await _courier(db, owner, payload.terms.courier_id)
    digest = _hash({"actor_id": actor.get("id"), **payload.model_dump(mode="json", exclude={"revision"})})

    async def commit(scoped):
        fresh = await _actor(scoped, owner=owner, actor=actor, action="manage")
        courier = await _courier(scoped, owner, payload.terms.courier_id)
        key, prior = await _retry(scoped, owner=owner, action="draft", request_id=payload.request_id, digest=digest)
        if prior:
            return prior
        policy = await read_shipping_policy(scoped, owner)
        _revision(policy, payload.revision)
        rows = _new_rows(policy)
        if payload.replaces_draft_id:
            old = [r for r in rows if r["id"] == payload.replaces_draft_id]
            if (len(old) != 1 or old[0]["verification_status"] != "unverified"
                    or old[0]["courier_id"] != payload.terms.courier_id
                    or old[0]["id"] in _superseded(rows)):
                raise HTTPException(409, "shipping_contract_replace_requires_live_draft")
        _check_interval(policy, payload.terms, excluding=payload.replaces_draft_id)
        courier = await _lock_courier_identity(scoped, owner, payload.terms.courier_id)
        version = ShippingContractVersion(
            **payload.terms.model_dump(), id=key, user_id=owner,
            revision=payload.revision + 1, verification_status="unverified",
        )
        now = _now()
        row = {
            **version.model_dump(mode="json"), "contract_schema": SCHEMA,
            "name": courier["display_name"], "created_by": fresh["id"], "created_at": now,
            "replaces_draft_id": payload.replaces_draft_id,
            "terms_hash": _hash(version.model_dump(mode="json")),
        }
        audit = dict(request_key=key, user_id=owner, action="contract_draft_created", request_hash=digest,
                     actor_id=fresh["id"], at=now, version_id=key, reason=payload.reason,
                     previous_revision=payload.revision, operation_id=OPERATION_ID)
        await _append_version(scoped, owner=owner, policy=policy, row=row, audit=audit)
        return {"state": "draft_saved", "revision": row["revision"], "version": row}
    return await atomic_owner(db, owner, commit)


async def approve_contract(db, *, owner, actor, payload: ContractApproveIn):
    require_native_contract_runtime()
    await _actor(db, owner=owner, actor=actor, action="review")
    shipping_evidence._approved_service()  # Explicit dependency, no owner/management override.
    digest = _hash({"actor_id": actor.get("id"), **payload.model_dump(mode="json", exclude={"revision"})})

    async def commit(scoped):
        fresh = await _actor(scoped, owner=owner, actor=actor, action="review")
        key, prior = await _retry(scoped, owner=owner, action="approve", request_id=payload.request_id, digest=digest)
        if prior:
            return prior
        policy = await read_shipping_policy(scoped, owner)
        _revision(policy, payload.revision)
        rows = _new_rows(policy)
        matches = [row for row in rows if row["id"] == payload.draft_id]
        if len(matches) != 1 or payload.draft_id in _superseded(rows):
            raise HTTPException(409, "shipping_contract_live_draft_required")
        draft = matches[0]
        version = _terms(draft)
        if version.verification_status != "unverified":
            raise HTTPException(409, "shipping_contract_live_draft_required")
        if draft.get("terms_hash") != payload.draft_hash:
            raise HTTPException(409, "shipping_contract_preview_changed")
        if version.source_kind == "legacy_copy":
            raise HTTPException(409, "legacy_copy_requires_confirmed_new_version")
        courier = await _courier(scoped, owner, version.courier_id)
        evidence_snapshot = await shipping_evidence.approval_snapshot(
            scoped, owner=owner, version=version, payload=payload)
        _check_interval(policy, version, excluding=draft["id"])
        courier = await _lock_courier_identity(scoped, owner, version.courier_id)
        now = _now()
        approved = ShippingContractVersion.model_validate({
            **version.model_dump(), "id": key, "revision": payload.revision + 1,
            "verification_status": "approved", "approved_by": fresh["id"], "approved_at": now,
        })
        shipping = quote_shipping_contract(approved, owner=owner, courier_id=approved.courier_id,
                                           accounting_at=approved.effective_from, cod_amount=None)["calculation"]
        row = {
            **approved.model_dump(mode="json"), "contract_schema": SCHEMA,
            "name": courier["display_name"], "created_by": fresh["id"], "created_at": now,
            "approved_from_draft_id": draft["id"], "terms_hash": _hash(approved.model_dump(mode="json")),
            "evidence_snapshot": evidence_snapshot,
            "evidence_snapshot_sha256": evidence_snapshot["sha256"],
            "contract_evidence_id": payload.contract_evidence_id,
            "shipping_tax_evidence_id": payload.shipping_tax_evidence_id,
            "commission_tax_evidence_id": payload.commission_tax_evidence_id,
            # Compatibility metadata consumed by the shared opening-scope gate
            # and read-only workspace. It is NOT an alternative quote source.
            "effective_at": approved.effective_from.isoformat(),
            "total_fee": format(shipping["shipping_gross"], ".2f"),
            "tax_treatment": "contract_net_and_input_vat",
        }
        audit = dict(request_key=key, user_id=owner, action="contract_approved", request_hash=digest,
                     actor_id=fresh["id"], at=now, version_id=key, reason=payload.reason,
                     previous_revision=payload.revision, operation_id=OPERATION_ID)
        await shipping_evidence.pin_bundle(scoped, owner=owner, courier_id=version.courier_id,
                                           bundle=evidence_snapshot, link_kind="contract", link_id=key)
        await _append_version(scoped, owner=owner, policy=policy, row=row, audit=audit)
        return {"state": "approved", "revision": row["revision"], "version": row}
    return await atomic_owner(db, owner, commit)


async def list_contracts(db, *, owner, actor):
    await _actor(db, owner=owner, actor=actor, action="view")
    policy = await read_shipping_policy(db, owner)
    return {"revision": policy["revision"], "items": [_public(r) for r in _new_rows(policy)],
            "couriers": await configured_couriers(db, owner),
            "evidence_service": shipping_evidence.readiness(),
            "contract_ui_ready": False,
            "native_path": native_contract_readiness()}


def _account(row):
    return (row["entity_type"], row["entity_id"], row.get("sub_account") or "")


async def _cod_source(db, *, owner, evidence, courier_id, gross, accounting_at, source_entry_id=None, handover_ref=None):
    """Reclassify an existing eligible COD receivable, never make a sale.

    Currently accepted core COD recognition puts receivables on a courier or
    individual store driver. No generic customer/clearing or paid gateway
    account is invented. Missing/unallocated/partly settled source is review.
    """
    group_id = evidence.get("recognition_txn_group_id")
    if not group_id or evidence.get("recognized_provider") != "cod":
        raise ShippingAccountingError("cod_base_receivable_required")
    if evidence.get("recognized_gross_sar") is not None and _money(evidence["recognized_gross_sar"]) != gross:
        raise ShippingAccountingError("cod_recognized_amount_conflict")
    scope = await read_mz2_ledger(db, owner=owner)
    if scope["status"] != "available":
        raise HTTPException(409, detail={"code": "mz2_balance_not_ready", "reason": scope["reason"]})
    rows = scope["items"]
    group = [r for r in rows if r.get("txn_group_id") == group_id]
    debits = [r for r in group if r["side"] == "debit"]
    if (len(debits) != 1 or not group or any(r["entry_type"] != "cod_sale" for r in group)
            or any(str(r.get("metadata", {}).get("order_reference_id")) != str(evidence["order_number"]) for r in group)
            or _money(debits[0]["amount"]) != gross
            or debits[0]["entity_type"] not in {"courier", "store_driver"}
            or debits[0].get("sub_account") != "cod_receivable"):
        raise ShippingAccountingError("cod_original_receivable_not_proven")
    source = debits[0]
    if not source.get("id"):
        raise ShippingAccountingError("cod_source_entry_identity_required")
    source_at = datetime.fromisoformat(source["metadata"]["accounting_at"].replace("Z", "+00:00"))
    at = datetime.fromisoformat(accounting_at.replace("Z", "+00:00"))
    if source_at > at:
        raise ShippingAccountingError("cod_transfer_precedes_original_recognition")
    duplicates = [r for r in rows if r.get("entity_type") == "revenue"
                  and str(r.get("metadata", {}).get("order_reference_id")) == str(evidence["order_number"])
                  and r.get("txn_group_id") != group_id]
    if duplicates:
        raise ShippingAccountingError("cod_multiple_recognition_groups_require_review")
    account = _account(source)
    if account != ("courier", courier_id, "cod_receivable"):
        # A change of custody requires its own explicit evidence, not a
        # conjecture based on a renamed carrier or a positive account balance.
        if source_entry_id != source["id"] or not isinstance(handover_ref, str) or len(handover_ref.strip()) < 3:
            raise ShippingAccountingError("cod_handover_evidence_required")
    elif source_entry_id not in (None, source["id"]):
        raise ShippingAccountingError("cod_source_entry_conflict")
    for row in rows:
        if _account(row) != account or row["side"] != "credit":
            continue
        linked = (row.get("metadata") or {}).get("cod_source_entry_id")
        if linked == source["id"]:
            raise ShippingAccountingError("cod_source_receivable_already_transferred")
        # Historical aggregate settlements cannot prove which order remains
        # outstanding. Do not use another order's balance to hide that gap.
        if not linked:
            raise ShippingAccountingError("cod_source_allocation_requires_review")
    return {
        "entry_id": source["id"], "txn_group_id": group_id,
        "entity_type": source["entity_type"], "entity_id": source["entity_id"],
        "sub_account": "cod_receivable", "gross": format(gross, ".2f"),
        "already_on_target": account == ("courier", courier_id, "cod_receivable"),
        "handover_evidence_ref": handover_ref.strip() if handover_ref else None,
    }


def _leg(entity_type, entity_id, side, amount, component, *, sub_account=None, kind="shipping_fee_accrual"):
    """Validate an exact positive Decimal amount; never round a posted leg.

    The calculator must have performed its contractual rounding already.
    Quantize only verifies that the value is an exact multiple of one halala.
    Extra trailing zeroes are harmless; a nonzero sub-halala is rejected.

    An isolated context preserves large exact amounts without binary conversion
    or dependence on the caller's precision, rounding mode or rounding traps.
    This validates the in-memory leg, NOT the still-unconnected ledger's storage.
    """
    if not isinstance(amount, Decimal) or not amount.is_finite() or amount <= 0:
        raise ShippingAccountingError("shipping_leg_amount_invalid")
    try:
        # adjusted()+3 accommodates all integer digits plus two halala places.
        # Keeping the coefficient length also handles exact trailing zeroes.
        precision = max(3, len(amount.as_tuple().digits), amount.adjusted() + 3)
        context = Context(
            prec=precision, rounding=ROUND_HALF_UP, traps=[InvalidOperation],
        )
        quantized = amount.quantize(Decimal("0.01"), context=context)
    except (DecimalException, ValueError, OverflowError):
        raise ShippingAccountingError("shipping_amount_requires_exact_halalas") from None
    if not quantized.is_finite() or quantized != amount:
        raise ShippingAccountingError("shipping_amount_requires_exact_halalas")
    leg = dict(entity_type=entity_type, entity_id=entity_id, side=side,
               amount=format(quantized, ".2f"), entry_type=kind, metadata={"component": component})
    if sub_account is not None:
        leg["sub_account"] = sub_account
    return leg


def accrual_entries(facts):
    """Build one balanced group. No bank, sales or sales VAT legs allowed."""
    amount = {k: Decimal(v) for k, v in facts["amounts"].items()}
    cid, result = facts["courier_id"], []
    source = facts.get("cod_source")
    if source and not source["already_on_target"]:
        result += [
            _leg("courier", cid, "debit", amount["cod_gross"], "cod_custody_in",
                 sub_account="cod_receivable", kind="shipping_cod_reclass"),
            _leg(source["entity_type"], source["entity_id"], "credit", amount["cod_gross"], "cod_custody_out",
                 sub_account=source["sub_account"], kind="shipping_cod_reclass"),
        ]
    for key, entity_type, entity_id in (
        ("shipping_net", "expense", "shipping"),
        ("shipping_vat", "tax", "input_vat"),
        ("cod_commission", "expense", "courier_cod_commission"),
        ("cod_commission_vat", "tax", "input_vat"),
    ):
        if amount[key] > 0:
            result.append(_leg(entity_type, entity_id, "debit", amount[key], key))
    if amount["payable_total"] > 0:
        result.append(_leg("courier", cid, "credit", amount["payable_total"], "carrier_payable", sub_account="payable"))
    sides = {side: sum((Decimal(r["amount"]) for r in result if r["side"] == side), Decimal(0))
             for side in ("debit", "credit")}
    if not result or sides["debit"] <= 0 or sides["debit"] != sides["credit"]:
        raise ShippingAccountingError("shipping_accrual_zero_or_unbalanced")
    return result


async def prepare_contract_courier(db, *, owner, evidence_id, cod_source_entry_id=None, cod_handover_evidence_ref=None):
    require_native_contract_runtime()
    evidence = await db.mz2_salla_order_evidence.find_one(
        {"user_id": owner, "id": evidence_id}, {"_id": 0}
    )
    if not evidence or evidence.get("conflict"):
        raise ShippingAccountingError("order_evidence_missing_or_conflicting")
    if evidence.get("order_status") not in {"تم التوصيل", "delivered"}:
        raise ShippingAccountingError("courier_delivered_status_required")
    when = _event_date_from_salla(evidence.get("delivery_source_text"))
    company = str(evidence.get("shipping_company") or "").strip()
    waybill = str(evidence.get("waybill") or "").strip()
    order = str(evidence.get("order_number") or "").strip()
    if not company or not waybill or not order:
        raise ShippingAccountingError("courier_identity_or_waybill_missing")
    cid, _ = normalize_shipping_company(company)
    courier = await _courier(db, owner, cid)
    if evidence.get("shipping_courier_id") not in (None, "", cid):
        raise ShippingAccountingError("shipping_source_courier_identity_conflict")
    provider = shipping_order_provider(evidence)
    if provider != "cod" and (cod_source_entry_id or cod_handover_evidence_ref):
        raise ShippingAccountingError("prepaid_order_cannot_carry_cod_handover")
    if provider == "cod" and (not evidence.get("recognition_txn_group_id")
                              or evidence.get("recognized_provider") != "cod"):
        raise ShippingAccountingError("cod_base_receivable_required")
    gross = _positive(evidence.get("current_net_sar")) if provider == "cod" else Decimal(0)
    if provider == "cod" and _money(evidence.get("refunded_sar", "0")) != 0:
        raise ShippingAccountingError("cod_refund_requires_review")
    event_id = _hash([owner, "courier_fee", cid, waybill])
    input_facts = dict(order_evidence_id=evidence_id, order_number=order, courier_id=cid,
                       waybill=waybill, accounting_at=when, accounting_provider=provider,
                        payment_method_raw=evidence.get("payment_method_raw"),
                        payment_reference=evidence.get("payment_reference"),
                       cod_gross=format(gross, ".2f"),
                       cod_source_entry_id=cod_source_entry_id,
                       cod_handover_evidence_ref=cod_handover_evidence_ref.strip() if cod_handover_evidence_ref else None,
                       cod_recognition_group=evidence.get("recognition_txn_group_id") if provider == "cod" else None,
                       refunded_sar=format(_money(evidence.get("refunded_sar", "0")), ".2f"))
    input_hash = _hash(input_facts)
    prior = await _event_record(db, owner, event_id)
    if prior:
        if prior.get("contract_schema") != SCHEMA:
            raise ShippingAccountingError("legacy_shipping_event_requires_review")
        if prior.get("input_hash") != input_hash:
            raise ShippingAccountingError("shipping_event_source_conflict")
        if prior.get("status") != "posted":
            raise ShippingAccountingError("shipping_event_requires_recovery")
        return dict(state="already_posted", event_id=event_id, txn_group_id=prior["txn_group_id"], facts=prior["facts"])
    same_waybill = await db.mz2_salla_order_evidence.find(
        {"user_id": owner, "waybill": waybill}, {"_id": 0, "id": 1}
    ).limit(3).to_list(3)
    if len(same_waybill) != 1 or same_waybill[0].get("id") != evidence_id:
        raise ShippingAccountingError("courier_waybill_not_unique")
    prior_order = await db.mz2_shipping_accounting_events.find_one({
        "user_id": owner, "kind": "courier_fee", "facts.order_number": order,
    })
    if prior_order:
        raise ShippingAccountingError("order_has_prior_shipping_fee_event")
    policy = await read_shipping_policy(db, owner)
    try:
        version = select_shipping_contract([_terms(r) for r in _new_rows(policy)],
                                           owner=owner, courier_id=cid, accounting_at=when)
        selected = next(r for r in policy["versions"] if r["id"] == version.id)
        quote = quote_shipping_contract(version, owner=owner, courier_id=cid,
                                        accounting_at=when, cod_amount=gross if provider == "cod" else None)
    except (ShippingContractError, ValidationError) as exc:
        raise ShippingAccountingError(str(exc)) from exc
    if quote["state"] != "eligible":
        return {"state": "needs_review", "reasons": quote["reasons"], "event_id": event_id}
    calc = quote["calculation"]
    evidence_snapshot = selected.get("evidence_snapshot")
    await shipping_evidence.verify_bundle(db, owner=owner, courier_id=cid, bundle=evidence_snapshot)
    for amount_key, ref_key in (("shipping_vat", "shipping_tax_evidence_id"),
                                ("cod_commission_vat", "commission_tax_evidence_id")):
        if calc[amount_key] > 0 and not selected.get(ref_key):
            raise ShippingAccountingError("shipping_input_vat_evidence_required")
    source = (await _cod_source(db, owner=owner, evidence=evidence, courier_id=cid,
                               gross=gross, accounting_at=when,
                               source_entry_id=cod_source_entry_id,
                               handover_ref=cod_handover_evidence_ref)) if provider == "cod" else None
    if source and not source["already_on_target"]:
        handover = await shipping_evidence.snapshot_one(db, owner=owner, courier_id=cid,
            evidence_id=cod_handover_evidence_ref, purpose="cod_handover")
        evidence_snapshot = shipping_evidence.bundle_of([*evidence_snapshot["items"], handover])
    amount_keys = ("cod_gross", "shipping_net", "shipping_vat", "shipping_gross", "cod_commission",
                   "cod_commission_vat", "cod_commission_gross", "payable_total")
    facts = {
        **input_facts, "event_id": event_id, "courier_name": courier["display_name"],
        "contract_snapshot": quote["contract_snapshot"], "rate_version_id": version.id,
        "evidence_snapshot": evidence_snapshot,
        "evidence_snapshot_sha256": evidence_snapshot["sha256"],
        "rate_evidence_ref": version.evidence_ref, "tax_treatment": "contract_net_and_input_vat",
        "payment_mode": version.payment_mode, "cod_source": source,
        "amounts": {key: format(calc[key], ".2f") for key in amount_keys},
        "total_fee": format(calc["payable_total"], ".2f"),
        "shipping_tax_evidence_id": selected.get("shipping_tax_evidence_id"),
        "commission_tax_evidence_id": selected.get("commission_tax_evidence_id"),
        "salla_shipping_charge_for_review": evidence.get("shipping_cost_source"),
    }
    return dict(state="eligible", event_id=event_id, facts=facts, input_hash=input_hash,
                economic_hash=_hash(facts), entries=accrual_entries(facts))


async def _finish_event(scoped, *, owner, proposal, group_id, evidence_id):
    now, facts = _now(), proposal["facts"]
    updated = await scoped.mz2_shipping_accounting_events.update_one(
        {"_id": proposal["event_id"], "user_id": owner, "status": "posting"},
        {"$set": {"status": "posted", "txn_group_id": group_id, "posted_at": now,
                   "input_hash": proposal["input_hash"], "contract_schema": SCHEMA}},
    )
    if updated.matched_count != 1:
        raise ShippingAccountingError("shipping_event_finalize_conflict")
    updated = await scoped.mz2_salla_order_evidence.update_one(
        {"user_id": owner, "id": evidence_id},
        {"$set": {"shipping_fee_event_id": proposal["event_id"], "shipping_fee_txn_group_id": group_id,
                   "shipping_fee_courier_id": facts["courier_id"],
                   "shipping_fee_sar": facts["amounts"]["shipping_gross"],
                   "shipping_commission_sar": facts["amounts"]["cod_commission_gross"],
                   "shipping_contract_version_id": facts["rate_version_id"]}},
    )
    if updated.matched_count != 1:
        raise ShippingAccountingError("shipping_source_finalize_conflict")


async def post_contract_courier(db, *, owner, actor, evidence_id,
                                cod_source_entry_id=None, cod_handover_evidence_ref=None):
    require_native_contract_runtime()
    await _actor(db, owner=owner, actor=actor, action="post")
    preliminary = await prepare_contract_courier(db, owner=owner, evidence_id=evidence_id,
                                                cod_source_entry_id=cod_source_entry_id,
                                                cod_handover_evidence_ref=cod_handover_evidence_ref)
    if preliminary["state"] == "already_posted":
        return preliminary
    if preliminary["state"] != "eligible":
        raise ShippingAccountingError("cod_amount_not_covered_by_contract_needs_review")
    await require_p02_shipping_financial_writes(db, user_id=owner, event_at=preliminary["facts"]["accounting_at"])
    await assert_open_journal_periods(db, owner, [{"metadata": {"accounting_at": preliminary["facts"]["accounting_at"]}}])

    async def commit(scoped):
        fresh = await _actor(scoped, owner=owner, actor=actor, action="post")
        proposal = await prepare_contract_courier(scoped, owner=owner, evidence_id=evidence_id,
                                                 cod_source_entry_id=cod_source_entry_id,
                                                 cod_handover_evidence_ref=cod_handover_evidence_ref)
        if proposal["state"] == "already_posted":
            return proposal
        if proposal["state"] != "eligible" or proposal["economic_hash"] != preliminary["economic_hash"]:
            raise ShippingAccountingError("shipping_preview_changed_review_again")
        facts, source = proposal["facts"], proposal["facts"].get("cod_source")
        await require_p02_shipping_financial_writes(scoped, user_id=owner, event_at=facts["accounting_at"])
        await assert_open_journal_periods(scoped, owner, [{"metadata": {"accounting_at": facts["accounting_at"]}}])
        await _lock_courier_identity(scoped, owner, facts["courier_id"])
        required = [("courier", facts["courier_id"], "payable")]
        if source:
            required.append(("courier", facts["courier_id"], "cod_receivable"))
            required.append((source["entity_type"], source["entity_id"], source["sub_account"]))
        balances = await read_mz2_write_balances(scoped, owner=owner, required_accounts=required)
        if source and balances.net_balance(entity_type=source["entity_type"], entity_id=source["entity_id"],
                                           sub_account=source["sub_account"]) < Decimal(source["gross"]):
            raise ShippingAccountingError("cod_source_receivable_insufficient")
        await _insert_event_posting(scoped, owner=owner, event_id=proposal["event_id"], kind="courier_fee",
                                    economic_hash=proposal["economic_hash"], facts=facts, actor_id=fresh["id"])
        metadata = {
            "operation_id": OPERATION_ID, "source": SOURCE,
            "shipping_event_id": proposal["event_id"], "shipping_event_kind": "courier_fee",
            "accounting_at": facts["accounting_at"], "order_reference_id": facts["order_number"],
            "courier_id": facts["courier_id"], "waybill": facts["waybill"],
            "contract_schema": SCHEMA, "contract_snapshot": facts["contract_snapshot"],
            "evidence_snapshot": facts["evidence_snapshot"],
            "evidence_snapshot_sha256": facts["evidence_snapshot_sha256"],
            "rate_version_id": facts["rate_version_id"], "rate_evidence_ref": facts["rate_evidence_ref"],
            "tax_treatment": facts["tax_treatment"],
            "shipping_tax_evidence_id": facts["shipping_tax_evidence_id"],
            "commission_tax_evidence_id": facts["commission_tax_evidence_id"],
            "salla_shipping_charge_for_review": facts["salla_shipping_charge_for_review"],
            "cod_source_entry_id": source["entry_id"] if source else None,
            "cod_source_txn_group_id": source["txn_group_id"] if source else None,
            "cod_handover_evidence_ref": source["handover_evidence_ref"] if source else None,
            "revenue_recognized_by_this_event": False,
        }
        result = await post_txn_group(scoped, user_id=owner, actor_id=fresh["id"], actor_name=fresh["id"],
                                     txn_type="mz2_shipping_contract_accrual",
                                     notes="استحقاق شحن وعمولة وإعادة تصنيف COD موثقة",
                                     metadata=metadata, entries=proposal["entries"])
        await shipping_evidence.pin_bundle(scoped, owner=owner, courier_id=facts["courier_id"],
                                           bundle=facts["evidence_snapshot"], link_kind="journal",
                                           link_id=result["txn_group_id"])
        await _finish_event(scoped, owner=owner, proposal=proposal, group_id=result["txn_group_id"], evidence_id=evidence_id)
        return {**proposal, "state": "posted", "txn_group_id": result["txn_group_id"]}
    return await atomic_owner(db, owner, commit)


def install_shipping_contract_routes(router, db, current_user):
    # Not called by the current application. Even an explicit call must register
    # no native endpoint while integration is closed; no startup 503 exception.
    readiness = native_contract_readiness()
    if readiness["enabled"] is not True:
        return {**readiness, "registered": False}
    require_native_contract_runtime()
    base = "/accounting-module/shipping-p02/contracts"

    async def scope(user):
        fresh = await fresh_actor(db, user)
        return fresh, accounting_owner_id(fresh)

    @router.get(base)
    async def contracts(user: dict = Depends(current_user)):
        actor, owner = await scope(user)
        return await list_contracts(db, owner=owner, actor=actor)

    @router.post(base + "/drafts")
    async def draft(payload: ContractDraftIn, user: dict = Depends(current_user)):
        actor, owner = await scope(user)
        return await save_contract_draft(db, owner=owner, actor=actor, payload=payload)

    @router.post(base + "/approve")
    async def approve(payload: ContractApproveIn, user: dict = Depends(current_user)):
        actor, owner = await scope(user)
        return await approve_contract(db, owner=owner, actor=actor, payload=payload)
