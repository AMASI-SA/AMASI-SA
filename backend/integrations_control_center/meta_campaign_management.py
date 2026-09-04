"""Governed Meta campaign mutations for Mezan V2.

Only native encrypted Meta OAuth credentials are used. Every provider write is
preceded by a persisted preview, explicit owner approval, an idempotent claim,
and followed by a provider read-back verification.
"""
from __future__ import annotations

import uuid
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Literal

import httpx
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .meta_account_selection import get_meta_account_selection
from .meta_native_reporting import _credential
from .meta_oauth_security import meta_appsecret_proof, meta_graph_base
from .meta_management_readiness import inspect_meta_management_readiness

COLLECTION = "mezan_meta_management_proposals_v1"
ENTITY_FIELDS = {
    "campaign": "id,name,account_id,status,effective_status,daily_budget,lifetime_budget",
    "adset": (
        "id,name,account_id,campaign_id,status,effective_status,daily_budget,lifetime_budget,"
        "bid_amount,bid_strategy,billing_event,optimization_goal"
    ),
    "ad": "id,name,account_id,campaign_id,adset_id,status,effective_status",
}
SUPPORTED_BID_STRATEGIES = frozenset({"COST_CAP", "LOWEST_COST_WITH_BID_CAP"})
PROPOSAL_TTL = timedelta(minutes=30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _expired(value: Any) -> bool:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc) <= _now()


def _public_proposal(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    result.pop("_id", None)
    result.pop("user_id", None)
    result.pop("provider_error", None)
    return result


async def ensure_meta_management_indexes(db: Any) -> None:
    await db[COLLECTION].create_index(
        [("user_id", 1), ("proposal_id", 1)],
        unique=True,
        name="meta_management_proposal_unique",
    )
    await db[COLLECTION].create_index(
        [("user_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="meta_management_idempotency_unique",
    )
    await db[COLLECTION].create_index(
        [("user_id", 1), ("status", 1), ("created_at", -1)],
        name="meta_management_status_latest",
    )


class MetaMutationPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    account_id: str = Field(min_length=4, max_length=160)
    entity_type: Literal["campaign", "adset", "ad"]
    entity_id: str = Field(min_length=2, max_length=160)
    action: Literal["update_status", "update_budget", "update_bid", "clone_campaign"]
    status: Literal["ACTIVE", "PAUSED"] | None = None
    amount_native: float | None = Field(default=None, gt=0, le=10_000_000)
    idempotency_key: str = Field(min_length=8, max_length=160)
    new_name: str | None = Field(default=None, min_length=3, max_length=220)

    @model_validator(mode="after")
    def validate_action(self):
        if self.action == "update_status" and self.status is None:
            raise ValueError("status is required for update_status")
        if self.action in {"update_budget", "update_bid"} and self.amount_native is None:
            raise ValueError("amount_native is required")
        if self.action == "update_budget" and self.entity_type not in {"campaign", "adset"}:
            raise ValueError("budgets exist only on campaigns or ad sets")
        if self.action == "update_bid" and self.entity_type != "adset":
            raise ValueError("bid amount exists only on ad sets")
        if self.action == "clone_campaign" and (self.entity_type != "campaign" or not self.new_name):
            raise ValueError("clone_campaign requires a campaign and new_name")
        return self


def _safe_provider_error(response: httpx.Response) -> dict[str, Any]:
    try:
        error = (response.json() or {}).get("error") or {}
    except Exception:  # noqa: BLE001
        error = {}
    return {
        "http_status": response.status_code,
        "code": error.get("code"),
        "subcode": error.get("error_subcode"),
        "type": error.get("type"),
    }


async def _assert_account(db: Any, user_id: str, account_id: str) -> str:
    normalized = account_id if account_id.startswith("act_") else f"act_{account_id}"
    selection = await get_meta_account_selection(db, user_id)
    allowed = {str(row.get("account_id") or "") for row in selection.get("accounts") or []}
    if normalized not in allowed:
        raise HTTPException(status_code=404, detail={"code": "meta_account_not_discovered"})
    return normalized


async def _read_entity(
    client: httpx.AsyncClient,
    access_token: str,
    entity_type: str,
    entity_id: str,
) -> dict[str, Any]:
    response = await client.get(
        f"{meta_graph_base()}/{entity_id}",
        params={
            "access_token": access_token,
            "appsecret_proof": meta_appsecret_proof(access_token),
            "fields": ENTITY_FIELDS[entity_type],
        },
    )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=409,
            detail={"code": "meta_entity_read_failed", "provider": _safe_provider_error(response)},
        )
    result = response.json() or {}
    if str(result.get("id") or "") != entity_id:
        raise HTTPException(status_code=409, detail={"code": "meta_entity_identity_mismatch"})
    return result


def _mutation(payload: MetaMutationPreviewInput, before: dict[str, Any]) -> tuple[dict[str, str], str]:
    if payload.action == "clone_campaign":
        return {"new_name": str(payload.new_name), "status": "PAUSED"}, "clone_campaign"
    if payload.action == "update_status":
        return {"status": str(payload.status)}, "status"
    amount_minor = str(int(round(float(payload.amount_native or 0) * 100)))
    if payload.action == "update_bid":
        strategy = str(before.get("bid_strategy") or "").upper()
        if strategy not in SUPPORTED_BID_STRATEGIES:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "meta_bid_strategy_not_compatible",
                    "message": "استراتيجية المزايدة الحالية لا تقبل تعديل Bid/Cost Cap مباشرة.",
                    "bid_strategy": strategy or None,
                },
            )
        return {"bid_amount": amount_minor}, "bid_amount"
    budget_field = "daily_budget" if before.get("daily_budget") not in (None, "") else "lifetime_budget"
    if before.get(budget_field) in (None, ""):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "meta_budget_not_on_entity",
                "message": "الميزانية ليست محفوظة على هذا الكيان؛ عدّل مستوى الميزانية الفعلي.",
            },
        )
    return {budget_field: amount_minor}, budget_field


async def preview_meta_mutation(
    db: Any,
    user_id: str,
    payload: MetaMutationPreviewInput,
) -> dict[str, Any]:
    account_id = await _assert_account(db, user_id, payload.account_id)
    existing = await db[COLLECTION].find_one(
        {"user_id": user_id, "idempotency_key": payload.idempotency_key}, {"_id": 0}
    )
    if existing:
        return _public_proposal(existing)
    access_token = await _credential(db, user_id, _now())
    async with httpx.AsyncClient(timeout=20.0) as client:
        before = await _read_entity(client, access_token, payload.entity_type, payload.entity_id)
    entity_account = str(before.get("account_id") or "")
    if entity_account and entity_account.removeprefix("act_") != account_id.removeprefix("act_"):
        raise HTTPException(status_code=404, detail={"code": "meta_entity_account_mismatch"})
    mutation, field = _mutation(payload, before)
    proposal_id = str(uuid.uuid4())
    now = _now()
    document = {
        "proposal_id": proposal_id,
        "user_id": user_id,
        "provider": "meta_ads",
        "account_id": account_id,
        "entity_type": payload.entity_type,
        "entity_id": payload.entity_id,
        "entity_name": before.get("name") or payload.entity_id,
        "action": payload.action,
        "field": field,
        "before": {key: before.get(key) for key in ENTITY_FIELDS[payload.entity_type].split(",")},
        "planned": mutation,
        "status": "previewed",
        "idempotency_key": payload.idempotency_key,
        "created_at": now,
        "expires_at": now + PROPOSAL_TTL,
        "updated_at": now,
        "provider_write_reached": False,
        "provider_write_state": "not_attempted",
        "provider_write_uncertain": False,
    }
    try:
        await db[COLLECTION].insert_one(document)
    except Exception:  # noqa: BLE001
        existing = await db[COLLECTION].find_one(
            {"user_id": user_id, "idempotency_key": payload.idempotency_key}, {"_id": 0}
        )
        if existing:
            return _public_proposal(existing)
        raise
    return _public_proposal(document)


async def execute_meta_proposal(db: Any, user_id: str, proposal_id: str) -> dict[str, Any]:
    proposal = await db[COLLECTION].find_one(
        {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0}
    )
    if not proposal:
        raise HTTPException(status_code=404, detail={"code": "meta_proposal_not_found"})
    if proposal.get("status") == "completed":
        return _public_proposal(proposal)
    if proposal.get("status") != "previewed":
        raise HTTPException(status_code=409, detail={"code": "meta_proposal_not_executable"})
    if _expired(proposal.get("expires_at")):
        raise HTTPException(status_code=409, detail={"code": "meta_proposal_expired"})

    readiness = await inspect_meta_management_readiness(db, user_id)
    ready_accounts = {
        str(row.get("account_id") or "")
        for row in readiness.get("accounts") or []
        if row.get("ready") is True
    }
    if proposal.get("account_id") not in ready_accounts:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "meta_management_not_ready",
                "message": "أوقف ميزان التنفيذ لأن صلاحية الكتابة أو دور الحساب لم يعد مثبتًا.",
            },
        )

    # Re-read before claiming the write.  A preview is an immutable approval
    # baseline, not permission to act after the entity's status, budget, bid,
    # identity, or account has changed.
    access_token = await _credential(db, user_id, _now())
    async with httpx.AsyncClient(timeout=20.0) as client:
        current = await _read_entity(
            client,
            access_token,
            proposal["entity_type"],
            proposal["entity_id"],
        )
    before = proposal.get("before") if isinstance(proposal.get("before"), dict) else {}
    compared_fields = {"id", "account_id", "status", "effective_status", proposal["field"]}
    if proposal.get("action") == "update_bid":
        compared_fields.add("bid_strategy")
    changed_fields = sorted(
        field
        for field in compared_fields
        if str(before.get(field) or "") != str(current.get(field) or "")
    )
    if changed_fields:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "meta_proposal_provider_state_changed",
                "changed_fields": changed_fields,
            },
        )
    if _expired(proposal.get("expires_at")):
        raise HTTPException(status_code=409, detail={"code": "meta_proposal_expired"})

    claimed = await db[COLLECTION].update_one(
        {"user_id": user_id, "proposal_id": proposal_id, "status": "previewed"},
        {"$set": {
            "status": "executing",
            "approved_at": _now(),
            "execution_started_at": _now(),
            "provider_write_state": "attempting",
            "updated_at": _now(),
        }},
    )
    if int(getattr(claimed, "modified_count", 0) or 0) != 1:
        raise HTTPException(status_code=409, detail={"code": "meta_proposal_already_claimed"})

    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            if proposal["action"] == "clone_campaign":
                response = await client.post(
                    f"{meta_graph_base()}/{proposal['entity_id']}/copies",
                    data={
                        "deep_copy": "true",
                        "status_option": "PAUSED",
                        "rename_options": json.dumps(
                            {"rename_strategy": "DEEP_RENAME", "rename_suffix": f" - {proposal['planned']['new_name']}"},
                            separators=(",", ":"),
                        ),
                        "access_token": access_token,
                        "appsecret_proof": meta_appsecret_proof(access_token),
                    },
                )
            else:
                response = await client.post(
                    f"{meta_graph_base()}/{proposal['entity_id']}",
                    data={
                        **proposal["planned"],
                        "access_token": access_token,
                        "appsecret_proof": meta_appsecret_proof(access_token),
                    },
                )
            if response.status_code >= 400:
                provider_error = _safe_provider_error(response)
                await db[COLLECTION].update_one(
                    {"user_id": user_id, "proposal_id": proposal_id},
                    {"$set": {
                        "status": "failed",
                        "provider_write_reached": True,
                        "provider_write_state": "rejected",
                        "provider_error": provider_error,
                        "updated_at": _now(),
                    }},
                )
                raise HTTPException(status_code=409, detail={"code": "meta_mutation_rejected", "provider": provider_error})
            response_payload = response.json() or {}
            if proposal["action"] == "clone_campaign":
                copied_id = str(
                    response_payload.get("copied_campaign_id")
                    or response_payload.get("campaign_id")
                    or response_payload.get("id")
                    or ""
                )
                if not copied_id:
                    raise RuntimeError("meta_clone_missing_campaign_id")
                rename_response = await client.post(
                    f"{meta_graph_base()}/{copied_id}",
                    data={
                        "name": proposal["planned"]["new_name"],
                        "status": "PAUSED",
                        "access_token": access_token,
                        "appsecret_proof": meta_appsecret_proof(access_token),
                    },
                )
                if rename_response.status_code >= 400:
                    raise RuntimeError("meta_clone_rename_failed")
                after = await _read_entity(client, access_token, "campaign", copied_id)
                verified = (
                    str(after.get("id") or "") == copied_id
                    and str(after.get("status") or "") == "PAUSED"
                    and str(after.get("name") or "") == proposal["planned"]["new_name"]
                )
            else:
                copied_id = None
                after = await _read_entity(
                    client, access_token, proposal["entity_type"], proposal["entity_id"]
                )
                field = proposal["field"]
                expected = str(proposal["planned"][field])
                actual = str(after.get(field) or "")
                verified = actual == expected
        final_status = "completed" if verified else "verification_failed"
        await db[COLLECTION].update_one(
            {"user_id": user_id, "proposal_id": proposal_id},
            {"$set": {
                "status": final_status,
                "after": {key: after.get(key) for key in ENTITY_FIELDS[proposal["entity_type"]].split(",")},
                "created_entity_id": copied_id,
                "verified": verified,
                "provider_write_reached": True,
                "provider_write_state": "verified" if verified else "uncertain",
                "provider_write_uncertain": not verified,
                "executed_at": _now(),
                "updated_at": _now(),
            }},
        )
        result = await db[COLLECTION].find_one(
            {"user_id": user_id, "proposal_id": proposal_id}, {"_id": 0, "user_id": 0}
        )
        return _public_proposal(result or {})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        await db[COLLECTION].update_one(
            {"user_id": user_id, "proposal_id": proposal_id},
            {"$set": {
                "status": "verification_failed",
                "provider_write_reached": True,
                "provider_write_state": "uncertain",
                "provider_write_uncertain": True,
                "safe_error": type(exc).__name__,
                "updated_at": _now(),
            }},
        )
        raise HTTPException(status_code=502, detail={"code": "meta_mutation_result_uncertain"}) from exc


def attach_meta_campaign_management_routes(
    router: Any,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.post("/meta_ads/management-proposals", status_code=201)
    async def preview(payload: MetaMutationPreviewInput, user: dict = Depends(current_user)) -> dict:
        owner = require_owner(user)
        return await preview_meta_mutation(db, str(owner["id"]), payload)

    @router.post("/meta_ads/management-proposals/{proposal_id}/approve-and-execute")
    async def approve_and_execute(proposal_id: str, user: dict = Depends(current_user)) -> dict:
        owner = require_owner(user)
        return await execute_meta_proposal(db, str(owner["id"]), proposal_id)


__all__ = [
    "MetaMutationPreviewInput",
    "attach_meta_campaign_management_routes",
    "execute_meta_proposal",
    "ensure_meta_management_indexes",
    "preview_meta_mutation",
]
