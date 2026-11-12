"""Owner-selected Google reporting scope, shared by HTTP, CLI and saved readers."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Any
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern


ACCOUNTS = "mezan_integration_accounts_v2"
INTEGRATIONS = "mezan_integrations_v2"
AUDIT = "mezan_integration_sync_runs_v2"
MAX_ACCOUNTS = 50
SOURCE = "google_ads_reporting_account_selection_v1"
SELECTION_FIELDS = ("mezan_selected", "selection_status", "selected_at", "selection_updated_at")
POLICY_FIELDS = ("account_selection_configured", "selected_account_ids", "selected_account_count",
                 "account_selection_required", "account_selection_updated_at")
INTEGRATION_FIELDS = {key: 1 for key in (
    "_id", "user_id", "provider", "connection_status", "connection_provenance", *POLICY_FIELDS,
)}
ACCOUNT_FIELDS = {
    key: 1 for key in (
        "_id", "user_id", "provider", "connection_status", "connection_provenance",
        "ad_account_id", "external_account_id", "display_name", "currency", "timezone", *SELECTION_FIELDS,
    )
}
NO_EXTERNAL_WRITES = {
    "provider_write_reached": False, "campaign_write_reached": False,
    "accounting_write_reached": False, "qoyod_write_reached": False,
    "credential_write_reached": False, "report_write_reached": False,
}


class SelectionError(ValueError):
    """A static, safe-to-print reason; never includes database exception text."""

    def __init__(self, code: str, *, outcome_unknown: bool = False):
        super().__init__(code)
        self.outcome_unknown = outcome_unknown


def _account_id(value: Any) -> str:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SelectionError("invalid_google_account_id")
    result = str(value).replace("-", "").strip()
    if not re.fullmatch(r"[0-9]{10}", result):
        raise SelectionError("invalid_google_account_id")
    return result


def _ids(values: Any, label: str) -> tuple[str, ...]:
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= MAX_ACCOUNTS:
        raise SelectionError(f"{label}_account_ids_required_and_bounded")
    normalized = [_account_id(value) for value in values]
    if len(set(normalized)) != len(normalized):
        raise SelectionError(f"duplicate_{label}_account_ids")
    return tuple(sorted(normalized))


def _scope(owner_id: str) -> dict[str, Any]:
    return {
        "user_id": owner_id, "provider": "google_ads",
        "connection_status": "connected", "connection_provenance": "api_connection",
    }


def google_reporting_scope(accounts, integration=None):
    """Resolve the same saved scope for every Google reporting consumer.

    The parent policy is authoritative after an owner saves. Account flags are
    retained for older records, but cannot override a later owner choice when
    a disconnected account reappears. Only wholly unconfigured legacy records
    retain the old all-accounts behavior.
    """
    integration = integration or {}
    explicit_policy = ("account_selection_configured" in integration
                       or "selected_account_ids" in integration)
    identity = lambda row: str(row.get("ad_account_id") or row.get("external_account_id") or "").replace("-", "").strip()
    invalid = False
    if explicit_policy:
        try:
            expected = set(_ids(integration.get("selected_account_ids"), "selected"))
            invalid = integration.get("account_selection_configured") is not True
        except SelectionError:
            expected, invalid = set(), True
        if invalid:
            expected = set()
        selected = [row for row in accounts if identity(row) in expected]
    else:
        configured_flags = any("mezan_selected" in row for row in accounts)
        selected = [row for row in accounts if row.get("mezan_selected") is True] if configured_flags else list(accounts)
        expected = {identity(row) for row in selected if identity(row)}
        invalid = any(row.get("mezan_selected") is not None and not isinstance(row["mezan_selected"], bool)
                      for row in accounts)
    configured = explicit_policy or any("mezan_selected" in row for row in accounts)
    found = {identity(row) for row in selected if identity(row)}
    missing = expected - found
    complete = (not invalid and not missing and len(found) == len(selected)
                and (bool(selected) or not configured))
    return {
        "accounts": selected, "configured": configured, "policy_configured": explicit_policy,
        "expected_account_ids": sorted(expected), "missing_account_ids": sorted(missing),
        "complete": complete,
        "reason": ("google_reporting_selection_invalid" if invalid
                   else "google_reporting_selected_account_missing" if missing
                   else "google_reporting_selection_required" if configured and not selected else None),
    }


async def _read_scope(db, owner_id, expected_ids=None, *, session=None, require_integration=True):
    owners = await db["users"].find(
        {"id": owner_id},
        {"_id": 1, "id": 1, "role": 1, "is_active": 1, "disabled": 1, "deleted_at": 1},
        session=session,
    ).max_time_ms(2000).limit(2).to_list(length=2)
    if len(owners) != 1:
        raise SelectionError("unique_active_owner_required")
    owner = owners[0]
    if (owner.get("role") != "owner"
            or owner.get("is_active") is not None and owner["is_active"] is not True
            or owner.get("disabled") is not None and owner["disabled"] is not False
            or owner.get("deleted_at") not in (None, "")):
        raise SelectionError("unique_active_owner_required")
    integrations = await db[INTEGRATIONS].find(
        {"user_id": owner_id, "provider": "google_ads"}, INTEGRATION_FIELDS, session=session,
    ).max_time_ms(2000).limit(2).to_list(length=2)
    if len(integrations) > 1 or require_integration and not integrations:
        raise SelectionError("unique_existing_google_integration_required")
    integration = integrations[0] if integrations else None
    rows = await db[ACCOUNTS].find(
        _scope(owner_id), ACCOUNT_FIELDS, session=session,
    ).max_time_ms(2000).limit(MAX_ACCOUNTS + 1).to_list(length=MAX_ACCOUNTS + 1)
    if len(rows) > MAX_ACCOUNTS:
        raise SelectionError("connected_google_account_limit_exceeded")
    by_id = {}
    for row in rows:
        identities = {
            _account_id(row[field]) for field in ("ad_account_id", "external_account_id")
            if row.get(field) not in (None, "")
        }
        if len(identities) != 1 or "_id" not in row:
            raise SelectionError("google_account_identity_unproved")
        account_id = identities.pop()
        if account_id in by_id:
            raise SelectionError("duplicate_connected_google_account")
        flag = row.get("mezan_selected")
        if flag is not None and not isinstance(flag, bool):
            raise SelectionError("invalid_existing_google_selection")
        by_id[account_id] = row
    if expected_ids is not None and set(by_id) != set(expected_ids):
        raise SelectionError("expected_google_account_scope_mismatch")
    return owner, integration, dict(sorted(by_id.items()))


def _cas(row, fields=ACCOUNT_FIELDS):
    # Distinguish absent, explicit null, and false; do not widen a write filter.
    return {
        key: ({"$eq": row[key], "$exists": True} if key in row else {"$exists": False})
        for key in fields if key != "_id"
    } | {"_id": row["_id"]}


async def set_reporting_accounts(
    db, *, owner_id, selected_account_ids, expected_account_ids, apply=False,
):
    """Plan or atomically apply an explicitly bounded selection of existing rows."""
    if (not isinstance(owner_id, str) or not owner_id.strip()
            or len(owner_id) > 200 or any(ord(char) < 32 for char in owner_id)):
        raise SelectionError("explicit_owner_id_required")
    owner_id = owner_id.strip()
    selected_ids = _ids(selected_account_ids, "selected")
    expected_ids = _ids(expected_account_ids, "expected")
    if not set(selected_ids).issubset(expected_ids):
        raise SelectionError("selected_accounts_outside_expected_scope")
    owner, policy, before = await _read_scope(db, owner_id, expected_ids)
    changed_ids = [
        account_id for account_id, row in before.items()
        if row.get("mezan_selected") is not (account_id in selected_ids)
    ]
    previous = [{
        "account_id": account_id,
        "mezan_selected_present": "mezan_selected" in row,
        "mezan_selected": row.get("mezan_selected"),
    } for account_id, row in before.items()]
    prior_scope = google_reporting_scope(list(before.values()), policy)
    policy_changed = (policy.get("account_selection_configured") is not True
                      or policy.get("selected_account_ids") != list(selected_ids))
    result = {
        "ok": True, "provider": "google_ads", "owner_id": owner_id,
        "mode": "apply" if apply else "dry_run", "status": "planned", "changed": False,
        "expected_account_ids": list(expected_ids), "selected_account_ids": list(selected_ids),
        "previous_selection": previous,
        "previous_selected_account_ids": (prior_scope["expected_account_ids"] if prior_scope["configured"]
                                          else [item["account_id"] for item in previous if item["mezan_selected"] is True]),
        "planned_change_count": len(changed_ids), "accounts_to_change": changed_ids,
        "reporting_policy_change": policy_changed,
        "previous_reporting_policy": {key: deepcopy(policy[key]) for key in POLICY_FIELDS if key in policy},
        **NO_EXTERNAL_WRITES,
    }
    if not apply:
        return result

    after_expected = deepcopy(before)
    policy_expected = deepcopy(policy)
    run_id = None
    # A changed scope/selection between planning and this fresh snapshot refuses
    # the operation. Mongo write conflicts or any failed CAS abort the full set.
    async with await db.client.start_session() as session:
        async with session.start_transaction(
            read_concern=ReadConcern("snapshot"), read_preference=ReadPreference.PRIMARY,
            write_concern=WriteConcern(w="majority", wtimeout=5000), max_commit_time_ms=5000,
        ):
            fresh_owner, fresh_policy, fresh = await _read_scope(db, owner_id, expected_ids, session=session)
            if (fresh_owner, fresh_policy, fresh) != (owner, policy, before):
                raise SelectionError("google_selection_changed_since_plan")
            if changed_ids or policy_changed:
                timestamp = datetime.now(timezone.utc).isoformat()
                for account_id in changed_ids:
                    selected = account_id in selected_ids
                    fields = {
                        "mezan_selected": selected,
                        "selection_status": "selected" if selected else "discovered",
                        "selected_at": timestamp if selected else None,
                        "selection_updated_at": timestamp,
                    }
                    update = await db[ACCOUNTS].update_one(
                        _cas(fresh[account_id]), {"$set": fields}, upsert=False, session=session,
                    )
                    if update.matched_count != 1 or update.modified_count != 1:
                        raise SelectionError("google_selection_changed_during_apply")
                    after_expected[account_id].update(fields)
                policy_fields = {
                    "account_selection_configured": True, "selected_account_ids": list(selected_ids),
                    "selected_account_count": len(selected_ids), "account_selection_required": False,
                    "account_selection_updated_at": timestamp,
                }
                policy_update = await db[INTEGRATIONS].update_one(
                    _cas(fresh_policy, INTEGRATION_FIELDS), {"$set": policy_fields},
                    upsert=False, session=session,
                )
                if policy_update.matched_count != 1 or policy_update.modified_count != 1:
                    raise SelectionError("google_selection_changed_during_apply")
                policy_expected.update(policy_fields)
                run_id = str(uuid.uuid4())
                await db[AUDIT].insert_one({
                    "run_id": run_id, "user_id": owner_id, "provider": "google_ads",
                    "run_type": "google_ads_account_selection", "status": "complete",
                    "started_at": timestamp, "finished_at": timestamp, "source_mode": SOURCE,
                    "summary": {
                        "discovered_count": len(before), "selected_count": len(selected_ids),
                        "expected_account_ids": list(expected_ids),
                        "selected_account_ids": list(selected_ids), "previous_selection": previous,
                        "previous_reporting_policy": result["previous_reporting_policy"],
                        "previous_effective_account_ids": prior_scope["expected_account_ids"],
                        "changed_account_ids": changed_ids, "legacy_collection_read": False,
                        "legacy_collection_write": False, **NO_EXTERNAL_WRITES,
                    },
                    "error": None,
                }, session=session)

    # Use a fresh primary read after commit. A readback failure must never claim
    # the apply failed without writes: the transaction may already be committed.
    try:
        readback_owner, readback_policy, readback = await _read_scope(db, owner_id, expected_ids)
        if (readback_owner, readback_policy, readback) != (owner, policy_expected, after_expected):
            raise SelectionError("google_selection_readback_unverified", outcome_unknown=True)
    except Exception:
        raise SelectionError("google_selection_readback_unverified", outcome_unknown=True) from None
    result.update(status="applied" if changed_ids or policy_changed else "unchanged",
                  changed=bool(changed_ids or policy_changed))
    if run_id:
        result["audit_run_id"] = run_id
    result["selection"] = _public_selection(policy_expected, after_expected)
    return result


def _public_selection(integration, accounts):
    scope = google_reporting_scope(list(accounts.values()), integration)
    chosen = set(scope["expected_account_ids"]) if scope["policy_configured"] else {
        account_id for account_id, row in accounts.items() if row.get("mezan_selected") is True
    }
    rows = [{
        "account_id": account_id, "display_name": row.get("display_name"),
        "currency": row.get("currency"), "timezone": row.get("timezone"),
        "selected": account_id in chosen,
        "selection_status": "selected" if account_id in chosen else "discovered",
        "selected_at": row.get("selected_at") if account_id in chosen else None,
    } for account_id, row in accounts.items()]
    count = sum(row["selected"] for row in rows)
    return {
        "provider": "google_ads", "accounts": rows, "discovered_count": len(rows),
        "selected_count": count, "selection_required": count == 0 or not scope["complete"],
        "selection_configured": scope["configured"], "expected_account_ids": list(accounts),
        "missing_selected_account_ids": scope["missing_account_ids"],
        "source_only": True, **NO_EXTERNAL_WRITES,
    }


async def get_google_account_selection(db, owner_id):
    _, integration, accounts = await _read_scope(db, owner_id, require_integration=False)
    return _public_selection(integration, accounts)


class GoogleAccountSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selected_account_ids: list[str] = Field(min_length=1, max_length=MAX_ACCOUNTS)
    expected_account_ids: list[str] = Field(min_length=1, max_length=MAX_ACCOUNTS)


def _selection_http_error(exc, *, applying=False):
    if isinstance(exc, SelectionError):
        code = str(exc)
        status = (403 if code == "unique_active_owner_required" else 422 if (
            code.startswith(("invalid_google_account_id", "duplicate_selected_", "duplicate_expected_"))
            or code == "selected_accounts_outside_expected_scope"
            or code.endswith("_account_ids_required_and_bounded")
        ) else 503 if exc.outcome_unknown else 409)
        unknown = exc.outcome_unknown
    else:
        code, status, unknown = "google_selection_database_unavailable", 503, applying
    message = ("تعذر تأكيد حفظ الاختيار. أعد تحميل الحسابات للتحقق من الحالة الحالية." if unknown
               else "تغيرت قائمة الحسابات أو حالتها. أعد تحميلها وراجع الاختيار." if status == 409
               else "اختر حسابًا متاحًا واحدًا على الأقل دون تكرار." if status == 422
               else "اختيار حسابات التقارير متاح للمالك النشط فقط." if status == 403
               else "تعذر تحميل حسابات Google حاليًا.")
    return HTTPException(status, detail={"code": code, "message": message, "apply_outcome_unknown": unknown})


def attach_google_account_selection_routes(router: APIRouter, db, current_user, require_owner):
    @router.get("/google_ads/accounts-selection")
    async def read_selection(user: dict = Depends(current_user)):
        owner = require_owner(user)
        try:
            return await asyncio.wait_for(get_google_account_selection(db, str(owner["id"])), timeout=10)
        except Exception as exc:
            raise _selection_http_error(exc) from None

    @router.put("/google_ads/accounts-selection")
    async def write_selection(payload: GoogleAccountSelectionInput, user: dict = Depends(current_user)):
        owner = require_owner(user)
        try:
            result = await asyncio.wait_for(set_reporting_accounts(
                db, owner_id=str(owner["id"]), selected_account_ids=payload.selected_account_ids,
                expected_account_ids=payload.expected_account_ids, apply=True,
            ), timeout=30)
            response = result["selection"]
            response["changed"] = result["changed"]
            return response
        except Exception as exc:
            raise _selection_http_error(exc, applying=True) from None

