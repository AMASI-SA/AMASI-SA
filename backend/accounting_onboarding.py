"""Versioned setup metadata; financial handoff uses the existing owner barrier.

Save/preview/review only ever mutate mz2_onboarding_sessions. They cannot call
the ledger, settings writer, inventory, provider APIs or write-control. A
single-document CAS includes the audit and idempotency record with each save.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import re

from fastapi import Depends, HTTPException
from pydantic import ValidationError
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from accounting_atomic import atomic_owner
from accounting_financial_accounts import (
    ACCOUNT_TYPES, OPENING_CATEGORY_CATALOG, FINANCIAL_ACCOUNT_RULES, PERMISSIONS,
    OpeningDraftCreate, OpeningAction, _canonical_hash, _compile_opening,
    _verified_evidence, _assert_financial_account_coverage, _now, _utc_iso,
)
from accounting_ledger_v2 import verify_active_opening_v2, AccountingLedgerV2Error
from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_onboarding_contract import (
    SCHEMA_VERSION, TARGET_CUTOVER, SECTION_IDS, SECTION_STATES,
    SessionCreate, SessionAction, CutoverSave, SectionSave,
)
from accounting_onboarding_identities import identities, verify_mappings
from accounting_write_control import AccountingDatabase, fresh_actor, write_state
from accounting_writer_transition import transition_state

BASE = "/accounting-module/onboarding"
FINANCIAL_BASE = "/api/financial-provider-apps/accounting-module/financial-accounts"
COLLECTION = "mz2_onboarding_sessions"
MAX_REVISIONS = 1000


def _fail(code, status=409, **details):
    raise HTTPException(status, detail={"code": code, **details})


def _public(row, existing=False):
    return {**{key: value for key, value in row.items()
               if key not in {"_id", "requests", "create_hash", "user_id"}}, "existing": existing}


async def _load(db, owner, session_id):
    row = await db[COLLECTION].find_one({"id": session_id, "user_id": owner})
    if not row:
        _fail("onboarding_session_not_found", 404)
    return row


def _request(row, action, payload):
    digest = _canonical_hash({"action": action, "payload": payload.model_dump(mode="json")})
    key = _canonical_hash(payload.idempotency_key)
    old = row.get("requests", {}).get(key)
    if old:
        if old["hash"] != digest:
            _fail("onboarding_idempotency_conflict")
        return key, digest, True
    if row["status"] in {"reviewed", "handed_off"} and action != "opening-draft":
        _fail("onboarding_session_locked")
    if row["version"] != payload.version:
        _fail("onboarding_version_conflict")
    if row["version"] >= MAX_REVISIONS:
        _fail("onboarding_revision_limit")
    return key, digest, False


async def _save(db, owner, row, payload, action, changes, actor):
    key, digest, replay = _request(row, action, payload)
    if replay:
        return _public(row, True)
    now = _now()
    result = await db[COLLECTION].find_one_and_update(
        {"_id": row["_id"], "user_id": owner, "version": payload.version, "status": row["status"]},
        {"$set": {**changes, "updated_at": now, "updated_by": actor["id"],
                  f"requests.{key}": {"hash": digest, "version": payload.version + 1}},
         "$inc": {"version": 1}, "$push": {"audit": {
             "action": action, "actor_id": actor["id"], "at": now,
             "version": payload.version + 1,
             "reason": getattr(payload, "note", getattr(payload, "reason", "save_setup")),
         }}}, return_document=ReturnDocument.AFTER)
    if not result:
        # Resolve only an identical concurrent retry. Never retry a stale edit.
        latest = await _load(db, owner, row["id"])
        prior = latest.get("requests", {}).get(key)
        if prior and prior["hash"] == digest:
            return _public(latest, True)
        _fail("onboarding_version_conflict")
    return _public(result)


def _payload(row):
    if any(row["sections"][key]["status"] not in {"complete", "not_applicable"} for key in SECTION_IDS):
        _fail("onboarding_sections_incomplete")
    cutover = row["cutover"]
    try:
        return OpeningDraftCreate(
            idempotency_key="onboarding:" + row["id"],
            cutover_at=cutover["cutover_at"], cutover_timezone=cutover["cutover_timezone"],
            cutover_evidence_file_id=cutover.get("cutover_evidence_file_id"),
            section_evidence_file_ids={key: row["sections"][key]["evidence_file_id"] for key in SECTION_IDS},
            lines=[line for key in SECTION_IDS for line in row["sections"][key]["data"].get("lines", [])],
        )
    except (ValidationError, KeyError, TypeError):
        _fail("onboarding_payload_invalid", 422)


def _amount(value):
    if not isinstance(value, str):
        _fail("onboarding_inventory_value_mismatch")
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount < 0 or amount != amount.quantize(Decimal("0.01")):
            _fail("onboarding_inventory_value_mismatch")
    except InvalidOperation:
        _fail("onboarding_inventory_value_mismatch")
    return amount


def _inventory(row, compiled):
    section = row["sections"]["inventory"]
    expected = {line["entity_id"]: Decimal(line["sar_amount"]) for line in compiled["lines"]
                if line["category"] == "inventory_asset"}
    value = section["data"].get("inventory_valuation")
    if section["status"] == "not_applicable" and not expected:
        return {"verified": True, "kind": "not_applicable", "physical_inventory_verified": False}
    if not value or not isinstance(value.get("account_totals"), dict) or not expected:
        _fail("onboarding_inventory_value_mismatch")
    amounts = {key: _amount(amount) for key, amount in value["account_totals"].items()}
    if (amounts != expected or _amount(value.get("total_sar")) != sum(expected.values(), Decimal(0))
            or value.get("evidence_file_id") != section["evidence_file_id"]
            or not re.fullmatch(r"[a-f0-9]{64}", str(value.get("manifest_hash") or ""))):
        _fail("onboarding_inventory_value_mismatch")
    return {"verified": True, "kind": "planned_valuation", **value, "physical_inventory_verified": False}


async def compile_session(db, owner, row):
    payload = _payload(row)
    compiled = await _compile_opening(db, owner=owner, payload=payload)
    # No section may conceal another section's lines or accept domain fields
    # it does not own. Mapping checks are repeated at preview/review/handoff.
    for key in SECTION_IDS:
        section = row["sections"][key]
        data = section["data"]
        if key != "providers" and data.get("provider_bindings"):
            _fail("onboarding_payload_invalid", 422)
        if key != "inventory" and data.get("inventory_valuation") is not None:
            _fail("onboarding_payload_invalid", 422)
        for line in data.get("lines", []):
            category = line.get("category")
            if category == "financial_account":
                target = next((item for item in compiled["lines"] if item["financial_account_id"] == line.get("financial_account_id")), None)
                expected = target["evidence_section_id"] if target else None
            else:
                expected = OPENING_CATEGORY_CATALOG.get(category, {}).get("section")
            if expected != key:
                _fail("opening_line_evidence_section_mismatch")
    await _assert_financial_account_coverage(db, owner, compiled)
    evidence = await _verified_evidence(db, owner=owner, requirements=compiled["evidence_requirements"])
    mappings = await verify_mappings(db, owner, compiled, row["sections"]["providers"]["data"].get("provider_bindings", []))
    inventory = _inventory(row, compiled)
    result = {"payload": payload.model_dump(mode="json"), "lines": compiled["lines"],
              "entries": compiled["preview_entries"], "zero_accounts": compiled["zero_accounts"],
              "debit_total": compiled["debit_total"], "credit_total": compiled["credit_total"],
              "balanced": compiled["debit_total"] == compiled["credit_total"],
              "mappings": mappings, "evidence": evidence, "inventory_reconciliation": inventory,
              "sections": row["sections"]}
    return {**result, "hash": _canonical_hash(result)}


async def readiness(db, owner, row):
    blockers = []
    preview = None
    try:
        preview = await compile_session(db, owner, row)
        if row.get("preview") and row["preview"]["hash"] != preview["hash"]:
            _fail("onboarding_snapshot_changed")
    except HTTPException as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else "onboarding_payload_invalid"
        blockers.append({"code": code})
    writer = None
    try:
        writer = await transition_state(db, owner)
    except HTTPException:
        blockers.append({"code": "accounting_transition_contract_invalid"})
    control = await write_state(db, owner)
    settings = await db.settings.find_one({"user_id": owner}) or {}
    cutover = settings.get("mezan2_financial_cutover") or {}
    try:
        opening_verified = await verify_active_opening_v2(db, user_id=owner, cutover=cutover)
    except AccountingLedgerV2Error:
        opening_verified = False
    source_ready = preview is not None and not blockers
    if control["paused"]:
        blockers.append({"code": "mz2_writes_paused"})
    if not writer or writer["state"] != "v2_active":
        blockers.append({"code": "accounting_v2_not_active"})
    if not opening_verified:
        blockers.append({"code": "opening_balance_not_verified"})
    if cutover.get("p02_shipping_cod_enabled") is True or cutover.get("p03_inventory_enabled") is True:
        blockers.append({"code": "later_phases_must_remain_locked"})
    blockers.extend([{"code": "smoke_b_production_proof_required"}, {"code": "live_owner_authorization_required"}])
    return {"schema_version": SCHEMA_VERSION, "session_id": row["id"], "version": row["version"],
            "status": row["status"], "source_ready": source_ready, "opening_verified": opening_verified,
            "inventory_reconciled": bool(preview and preview["inventory_reconciliation"]["verified"]),
            "inventory_physical_approval_verified": False,
            "writer_transition": writer, "financial_writes_paused": control["paused"],
            "ready_for_live_post": False, "blockers": blockers,
            "live_gates": {"smoke_b": "BLOCKED_BY_ENVIRONMENT", "owner_authorization": "REQUIRED"},
            "p02_activation_allowed": False, "g47_activation_allowed": False}


def install_onboarding_routes(router, db, current_user, canonical_handlers):
    # Registered after the financial router wrapper. Each metadata handler has
    # fresh auth and a bounded write surface. Only handoff enters atomic_owner.
    raw = db.root if isinstance(db, AccountingDatabase) else db

    async def actor_for(user, *permissions):
        actor = await fresh_actor(raw, user)
        for permission in ("opening_view", *permissions):
            require_accounting_permission(actor, PERMISSIONS[permission])
        return actor, accounting_owner_id(actor)

    @router.get(BASE + "/definitions")
    async def definitions(user: dict = Depends(current_user)):
        await actor_for(user)
        return {"schema_version": SCHEMA_VERSION, "sections": list(SECTION_IDS),
                "section_statuses": list(SECTION_STATES), "account_types": list(ACCOUNT_TYPES),
                "opening_categories": OPENING_CATEGORY_CATALOG, "financial_account_rules": FINANCIAL_ACCOUNT_RULES,
                "target_cutover_at": TARGET_CUTOVER, "cutover_timezone": "Asia/Riyadh",
                "financial_base": FINANCIAL_BASE, "external_person_storage_kind": "general",
                "live_actions_enabled": False}

    @router.get(BASE + "/identities/{kind}")
    async def identity_list(kind: str, user: dict = Depends(current_user)):
        _, owner = await actor_for(user)
        return {"items": await identities(raw, owner, kind)}

    @router.post(BASE + "/sessions")
    async def create(payload: SessionCreate, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        session_id = "onboarding-" + _canonical_hash([owner, payload.idempotency_key])
        digest = _canonical_hash(payload.model_dump(mode="json"))
        now = _now()
        row = {"_id": session_id, "id": session_id, "user_id": owner, "schema_version": SCHEMA_VERSION,
               "version": 1, "status": "draft", "create_hash": digest, "created_at": now,
               "created_by": actor["id"], "updated_at": now, "requests": {},
               "cutover": {"cutover_at": payload.cutover_at.isoformat(), "cutover_timezone": payload.cutover_timezone,
                           "cutover_evidence_file_id": None},
               "sections": {key: {"status": "not_started", "reason": "", "evidence_file_id": None,
                                   "data": {"lines": []}} for key in SECTION_IDS},
               "audit": [{"action": "create", "actor_id": actor["id"], "at": now, "version": 1}]}
        try:
            await raw[COLLECTION].insert_one(row)
        except DuplicateKeyError:
            row = await _load(raw, owner, session_id)
            if row["create_hash"] != digest:
                _fail("onboarding_idempotency_conflict")
            return _public(row, True)
        return _public(row)

    @router.get(BASE + "/sessions")
    async def list_sessions(user: dict = Depends(current_user)):
        _, owner = await actor_for(user)
        rows = await raw[COLLECTION].find({"user_id": owner}, {
            "id": 1, "schema_version": 1, "version": 1, "status": 1, "cutover": 1, "updated_at": 1,
        }).sort("updated_at", -1).to_list(200)
        return {"items": [_public(row) for row in rows]}

    @router.get(BASE + "/sessions/{session_id}")
    async def get(session_id: str, user: dict = Depends(current_user)):
        _, owner = await actor_for(user)
        return _public(await _load(raw, owner, session_id))

    @router.put(BASE + "/sessions/{session_id}/cutover")
    async def save_cutover(session_id: str, payload: CutoverSave, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        row = await _load(raw, owner, session_id)
        changes = {"cutover": payload.model_dump(mode="json", exclude={"version", "idempotency_key"}),
                   "status": "draft", "preview": None}
        return await _save(raw, owner, row, payload, "cutover", changes, actor)

    @router.put(BASE + "/sessions/{session_id}/sections/{section_id}")
    async def save_section(session_id: str, section_id: str, payload: SectionSave, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        if section_id not in SECTION_IDS:
            _fail("onboarding_payload_invalid", 422)
        row = await _load(raw, owner, session_id)
        sections = deepcopy(row["sections"])
        sections[section_id] = payload.model_dump(mode="json", exclude={"version", "idempotency_key"})
        return await _save(raw, owner, row, payload, "section:" + section_id,
                           {"sections": sections, "status": "draft", "preview": None}, actor)

    @router.post(BASE + "/sessions/{session_id}/preview")
    async def preview(session_id: str, payload: SessionAction, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage")
        row = await _load(raw, owner, session_id)
        if _request(row, "preview", payload)[2]:
            return _public(row, True)
        result = await compile_session(raw, owner, row)
        return await _save(raw, owner, row, payload, "preview", {"preview": result, "status": "previewed"}, actor)

    @router.post(BASE + "/sessions/{session_id}/review")
    async def review(session_id: str, payload: SessionAction, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "review")
        row = await _load(raw, owner, session_id)
        if _request(row, "review", payload)[2]:
            return _public(row, True)
        if row["status"] != "previewed" or not row.get("preview"):
            _fail("onboarding_preview_required")
        result = await compile_session(raw, owner, row)
        if result["hash"] != row["preview"]["hash"]:
            _fail("onboarding_snapshot_changed")
        return await _save(raw, owner, row, payload, "review", {
            "status": "reviewed", "reviewed_by": actor["id"], "reviewed_at": _now(),
            "reviewed_hash": result["hash"],
        }, actor)

    @router.get(BASE + "/sessions/{session_id}/readiness")
    async def get_readiness(session_id: str, user: dict = Depends(current_user)):
        _, owner = await actor_for(user)
        return await readiness(raw, owner, await _load(raw, owner, session_id))

    @router.post(BASE + "/sessions/{session_id}/opening-draft")
    async def handoff(session_id: str, payload: SessionAction, user: dict = Depends(current_user)):
        actor, owner = await actor_for(user, "drafts_manage", "review")
        async def transfer(scoped):
            row = await _load(scoped, owner, session_id)
            if _request(row, "opening-draft", payload)[2]:
                return _public(row, True)
            if row["status"] != "reviewed":
                _fail("onboarding_session_review_required")
            result = await compile_session(scoped, owner, row)
            if result["hash"] != row.get("reviewed_hash"):
                _fail("onboarding_snapshot_changed")
            if not isinstance(db, AccountingDatabase):
                _fail("onboarding_transaction_binding_required", 503)
            token = db.scope.set(scoped)
            try:
                draft = await canonical_handlers["create"](OpeningDraftCreate.model_validate(result["payload"]), user=actor)
                for action in ("preview", "review"):
                    draft = await canonical_handlers[action](draft["id"], OpeningAction(
                        version=draft["version"], idempotency_key=f"{session_id}:{action}",
                        note=payload.note,
                    ), user=actor)
                return await _save(scoped, owner, row, payload, "opening-draft", {
                    "status": "handed_off", "opening_draft": {key: draft[key] for key in ("id", "version", "status")},
                }, actor)
            finally:
                db.scope.reset(token)
        return await atomic_owner(raw, owner, transfer)
