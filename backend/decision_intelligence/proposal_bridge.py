"""Durable Phase 5 proposals bridged into existing provider Action Gates.

The scheduler may persist provider-neutral proposals here.  It never previews,
approves, or executes them.  Provider reads begin only when an owner explicitly
requests a preview, and provider writes remain wholly owned by the existing
Snapchat and Meta management gates after a second explicit owner request.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Literal

from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

PROPOSAL_COLLECTION = "mezan_decision_intelligence_phase5_proposals_v1"
PROPOSAL_SOURCE = "decision_intelligence_phase5"
PROPOSAL_TTL = timedelta(minutes=30)
SNAPCHAT_GATE_COLLECTION = "mezan_snapchat_campaign_proposals_v1"
META_GATE_COLLECTION = "mezan_meta_management_proposals_v1"

Provider = Literal["snapchat_ads", "meta_ads"]
EntityType = Literal["campaign", "ad_group", "ad"]

ACTION_CAPABILITIES: dict[str, dict[str, dict[str, Any]]] = {
    "snapchat_ads": {
        "pause": {
            "entity_types": ("campaign", "ad_group", "ad"),
            "provider_action": "status_update",
            "rollback": True,
        },
        "resume": {
            "entity_types": ("campaign", "ad_group", "ad"),
            "provider_action": "status_update",
            "rollback": True,
        },
        "budget_reduce": {
            "entity_types": ("campaign", "ad_group"),
            "provider_action": "daily_budget_update",
            "rollback": True,
        },
        "budget_scale": {
            "entity_types": ("campaign", "ad_group"),
            "provider_action": "daily_budget_update",
            "rollback": True,
        },
        # The provider gate can write ad-squad bid controls, but the current
        # Unified Snapchat evidence does not carry a bid baseline.  Keep this
        # non-executable until that evidence contract is decision-grade.
        "bid_adjust": {
            "entity_types": (),
            "provider_action": "ad_squad_bid_update",
            "rollback": True,
            "blocked_by": "phase5_snapchat_bid_baseline_unavailable",
        },
    },
    "meta_ads": {
        "pause": {
            "entity_types": ("campaign", "ad_group", "ad"),
            "provider_action": "update_status",
            "rollback": False,
        },
        "resume": {
            "entity_types": ("campaign", "ad_group", "ad"),
            "provider_action": "update_status",
            "rollback": False,
        },
        "budget_reduce": {
            "entity_types": ("campaign", "ad_group"),
            "provider_action": "update_budget",
            "rollback": False,
        },
        "budget_scale": {
            "entity_types": ("campaign", "ad_group"),
            "provider_action": "update_budget",
            "rollback": False,
        },
        "bid_adjust": {
            "entity_types": ("ad_group",),
            "provider_action": "update_bid",
            "rollback": False,
            "required_bid_strategies": (
                "COST_CAP",
                "LOWEST_COST_WITH_BID_CAP",
            ),
        },
    },
}

_ACTION_ALIASES = {
    "pause": "pause",
    "resume": "resume",
    "reduce": "budget_reduce",
    "budget_reduce": "budget_reduce",
    "decrease_budget": "budget_reduce",
    "scale": "budget_scale",
    "budget_scale": "budget_scale",
    "increase_budget": "budget_scale",
    "bid_adjust": "bid_adjust",
    "update_bid": "bid_adjust",
}

PreviewDispatcher = Callable[..., Awaitable[dict[str, Any]]]
ExecuteDispatcher = Callable[..., Awaitable[dict[str, Any]]]
AccountingGate = Callable[[Any, str, str], Awaitable[dict[str, Any]]]


class Phase5ProposalPreviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Provider
    entity_type: EntityType
    entity_id: str = Field(min_length=1, max_length=160)
    recommendation_fingerprint: str = Field(min_length=32, max_length=80)
    expected_revision: int = Field(ge=1)


class Phase5ProposalApprovalInput(Phase5ProposalPreviewInput):
    provider_state_fingerprint: str = Field(min_length=32, max_length=80)
    confirm_token: str | None = Field(default=None, min_length=16, max_length=240)
    provider_proposal_revision: int | None = Field(default=None, ge=1)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed != parsed or abs(parsed) == float("inf"):
        return None
    return parsed


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_copy(value: Any) -> Any:
    """Copy bounded JSON while removing credential-shaped keys."""
    forbidden = (
        "access_token",
        "refresh_token",
        "authorization",
        "client_secret",
        "password",
        "credential",
        "ciphertext",
    )
    if isinstance(value, dict):
        return {
            str(key): _safe_copy(item)
            for key, item in value.items()
            if not any(fragment in str(key).lower() for fragment in forbidden)
        }
    if isinstance(value, (list, tuple)):
        return [_safe_copy(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_expired(row: dict[str, Any], *, now: datetime | None = None) -> bool:
    expires_at = _parse_datetime(row.get("expires_at"))
    return expires_at is None or expires_at <= (now or _now())


def phase5_proposal_capability_matrix() -> dict[str, Any]:
    return {
        provider: {
            action: {
                **_safe_copy(details),
                "automatic_execution": False,
                "existing_action_gate_reused": True,
            }
            for action, details in actions.items()
        }
        for provider, actions in ACTION_CAPABILITIES.items()
    }


def _current_state(decision: dict[str, Any]) -> dict[str, Any]:
    entity = decision.get("entity") if isinstance(decision.get("entity"), dict) else {}
    evidence = (
        decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
    )
    source = evidence.get("current_state_snapshot")
    source = source if isinstance(source, dict) else {}
    allowed = (
        "status",
        "effective_status",
        "active",
        "campaign_id",
        "ad_group_id",
        "daily_budget_native",
        "lifetime_budget_native",
        "bid_amount_native",
        "bid_strategy",
        "billing_event",
        "optimization_goal",
        "currency_scope",
        "updated_at",
        "settings_evidence_status",
        "source",
    )
    state = {key: source.get(key) for key in allowed if key in source}
    state.setdefault("status", entity.get("status"))
    state.setdefault("active", entity.get("active"))
    state.setdefault("campaign_id", entity.get("campaign_id"))
    state.setdefault("ad_group_id", entity.get("ad_group_id"))
    return _safe_copy(state)


def _normalized_action(decision: dict[str, Any]) -> str:
    recommendation = (
        decision.get("recommendation")
        if isinstance(decision.get("recommendation"), dict)
        else {}
    )
    raw = (
        str(
            recommendation.get("normalized_action")
            or recommendation.get("action_type")
            or recommendation.get("action")
            or ""
        )
        .strip()
        .lower()
    )
    if raw == "test":
        simulation = (
            decision.get("simulation")
            if isinstance(decision.get("simulation"), dict)
            else {}
        )
        proposed = simulation.get("proposed_change")
        proposed = proposed if isinstance(proposed, dict) else {}
        percent = _number(proposed.get("budget_change_pct")) or 0.0
        if percent > 0:
            return "budget_scale"
        if percent < 0:
            return "budget_reduce"
        return "monitor"
    return _ACTION_ALIASES.get(raw, raw or "unsupported")


def _proposed_state(
    decision: dict[str, Any],
    *,
    provider: str,
    action: str,
    current: dict[str, Any],
) -> tuple[dict[str, Any], str | None]:
    recommendation = (
        decision.get("recommendation")
        if isinstance(decision.get("recommendation"), dict)
        else {}
    )
    explicit = recommendation.get("proposed_state")
    explicit = explicit if isinstance(explicit, dict) else {}
    simulation = (
        decision.get("simulation")
        if isinstance(decision.get("simulation"), dict)
        else {}
    )
    simulated = simulation.get("proposed_change")
    simulated = simulated if isinstance(simulated, dict) else {}

    if action in {"pause", "resume"}:
        return {"status": "PAUSED" if action == "pause" else "ACTIVE"}, None
    if action in {"budget_reduce", "budget_scale"}:
        current_daily = _number(current.get("daily_budget_native"))
        current_lifetime = _number(current.get("lifetime_budget_native"))
        if provider == "snapchat_ads" or current_daily is not None:
            field = "daily_budget_native"
            current_amount = current_daily
        else:
            field = "lifetime_budget_native"
            current_amount = current_lifetime
        if current_amount is None or current_amount <= 0:
            return {}, "current_budget_unavailable"
        percent = _number(
            explicit.get("budget_change_pct", simulated.get("budget_change_pct"))
        )
        if percent is None or percent == 0:
            percent = 5.0 if action == "budget_scale" else -5.0
        if action == "budget_scale" and percent <= 0:
            return {}, "budget_direction_mismatch"
        if action == "budget_reduce" and percent >= 0:
            return {}, "budget_direction_mismatch"
        amount = round(current_amount * (1.0 + percent / 100.0), 6)
        if amount <= 0:
            return {}, "proposed_budget_invalid"
        return {
            "budget_field": field,
            "amount_native": amount,
            "budget_change_pct": percent,
        }, None
    if action == "bid_adjust":
        amount = _number(
            explicit.get("bid_amount_native", explicit.get("amount_native"))
        )
        if amount is None or amount <= 0:
            return {}, "proposed_bid_unavailable"
        return {"bid_amount_native": amount}, None
    return {}, "unsupported_action"


def _fingerprint_state(action: str, state: dict[str, Any]) -> dict[str, Any]:
    fields = ["status", "effective_status"]
    if action in {"budget_reduce", "budget_scale"}:
        fields.extend(("daily_budget_native", "lifetime_budget_native"))
    if action == "bid_adjust":
        fields.extend(("bid_amount_native", "bid_strategy"))
    normalized: dict[str, Any] = {}
    for field in fields:
        value = state.get(field)
        if field.endswith("_native"):
            value = _number(value)
        elif field in {"status", "effective_status", "bid_strategy"}:
            value = str(value or "").strip().upper() or None
        normalized[field] = value
    return normalized


def provider_state_fingerprint(
    *,
    provider: str,
    entity_type: str,
    entity_id: str,
    action: str,
    state: dict[str, Any],
) -> str:
    return _digest(
        {
            "provider": provider,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "state": _fingerprint_state(action, state),
        }
    )


def normalize_phase5_recommendation(
    result: dict[str, Any],
    decision: dict[str, Any],
    *,
    tenant_id: str,
) -> dict[str, Any]:
    provider = str(result.get("provider") or "")
    entity = decision.get("entity") if isinstance(decision.get("entity"), dict) else {}
    entity_type = str(entity.get("level") or "")
    entity_id = str(entity.get("id") or "")
    action = _normalized_action(decision)
    current = _current_state(decision)
    capability = ACTION_CAPABILITIES.get(provider, {}).get(action)
    blocked_by: list[str] = []
    if decision.get("status") != "RECOMMENDATION_SHADOW":
        blocked_by.append("phase5_decision_not_recommended")
    if not capability:
        blocked_by.append("unsupported_action")
    elif entity_type not in set(capability.get("entity_types") or ()):
        blocked_by.append(
            str(capability.get("blocked_by") or "unsupported_entity_action")
        )
    proposed, proposed_error = _proposed_state(
        decision,
        provider=provider,
        action=action,
        current=current,
    )
    if proposed_error:
        blocked_by.append(proposed_error)
    if not tenant_id or not entity_id:
        blocked_by.append("proposal_identity_incomplete")
    if provider not in ACTION_CAPABILITIES:
        blocked_by.append("unsupported_provider")
    if result.get("decision_ready") is not True:
        blocked_by.append("phase5_readiness_failed")
    configured_status = str(current.get("status") or "").strip().upper()
    if action in {"budget_reduce", "budget_scale", "pause"} and (
        current.get("active") is not True
        or configured_status not in {"ACTIVE", "ENABLED"}
    ):
        blocked_by.append("current_entity_not_active")
    if action == "resume" and configured_status != "PAUSED":
        blocked_by.append("current_entity_not_paused")
    if action == "bid_adjust" and provider == "meta_ads":
        strategy = str(current.get("bid_strategy") or "").upper()
        if strategy not in set(capability.get("required_bid_strategies") or ()):
            blocked_by.append("meta_bid_strategy_not_compatible")

    recommendation = (
        decision.get("recommendation")
        if isinstance(decision.get("recommendation"), dict)
        else {}
    )
    evidence = (
        decision.get("evidence") if isinstance(decision.get("evidence"), dict) else {}
    )
    evidence_snapshot = {
        "window": _safe_copy(result.get("period") or {}),
        "evidence_id": decision.get("decision_id"),
        "metrics": _safe_copy(evidence.get("metrics") or {}),
        "quality": _safe_copy(evidence.get("quality") or {}),
        "lineage": _safe_copy(evidence.get("lineage") or {}),
    }
    fingerprint_material = {
        "provider": provider,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action_type": action,
        "current_state_snapshot": current,
        "proposed_state": proposed,
        "reason": recommendation.get("reason"),
        "evidence": evidence_snapshot,
    }
    recommendation_fingerprint = _digest(fingerprint_material)
    recommendation_id = f"di-p5-rec-{recommendation_fingerprint[:32]}"
    idempotency_material = {
        "tenant_id": tenant_id,
        "provider": provider,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action_type": action,
        "target_state": proposed,
        "evidence_window": evidence_snapshot["window"],
        # Material evidence changes within the same closed window must produce
        # a new proposal; identical hourly evaluations must not.
        "recommendation_fingerprint": recommendation_fingerprint,
    }
    idempotency_key = f"di-p5-{_digest(idempotency_material)}"
    state_fingerprint = provider_state_fingerprint(
        provider=provider,
        entity_type=entity_type,
        entity_id=entity_id,
        action=action,
        state=current,
    )
    return {
        "recommendation_id": recommendation_id,
        "provider": provider,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "action_type": action,
        "current_state_snapshot": current,
        "proposed_state": proposed,
        "reason": recommendation.get("reason"),
        "evidence": evidence_snapshot,
        "confidence": recommendation.get("confidence"),
        "priority": recommendation.get("priority_score"),
        "recommendation_fingerprint": recommendation_fingerprint,
        "idempotency_key": idempotency_key,
        "provider_state_fingerprint": state_fingerprint,
        "executable": not blocked_by,
        "blocked_by": list(dict.fromkeys(blocked_by)),
    }


async def ensure_phase5_proposal_indexes(db: Any) -> None:
    await db[PROPOSAL_COLLECTION].create_index(
        [("user_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="phase5_proposal_idempotency_unique",
    )
    await db[PROPOSAL_COLLECTION].create_index(
        [("user_id", 1), ("status", 1), ("created_at", -1)],
        name="phase5_proposal_status_latest",
    )


def _public_proposal(row: dict[str, Any]) -> dict[str, Any]:
    output = _safe_copy(row)
    output.pop("_id", None)
    output.pop("user_id", None)
    return output


async def persist_phase5_proposals(
    db: Any,
    user_id: str,
    result: dict[str, Any],
    *,
    source_run_id: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Persist only executable recommendations; never call a provider."""
    current = now or _now()
    normalized = [
        normalize_phase5_recommendation(result, decision, tenant_id=str(user_id))
        for decision in list(result.get("decisions") or [])
    ]
    executable = [item for item in normalized if item.get("executable") is True]
    if executable:
        await ensure_phase5_proposal_indexes(db)
    created = 0
    deduplicated = 0
    proposals: list[dict[str, Any]] = []
    for item in executable:
        proposal_hash = _digest(
            {
                "tenant_id": str(user_id),
                "idempotency_key": item["idempotency_key"],
            }
        )
        proposal_id = f"di-p5-prop-{proposal_hash[:32]}"
        readiness_snapshot = {
            "decision_ready": result.get("decision_ready") is True,
            "gates": _safe_copy(result.get("gates") or {}),
        }
        financial_gate = dict(
            (readiness_snapshot.get("gates") or {}).get("financial_coverage") or {}
        )
        profitability_snapshot = {
            "status": (
                "complete" if financial_gate.get("passed") is True else "incomplete"
            ),
            "financial_coverage_gate": financial_gate,
            "contribution_profit_sar": (item.get("evidence") or {})
            .get("metrics", {})
            .get("contribution_profit_sar"),
            "salla_roas": (item.get("evidence") or {})
            .get("metrics", {})
            .get("salla_roas"),
            "write_performed": False,
        }
        document = {
            "proposal_id": proposal_id,
            "user_id": str(user_id),
            "tenant_id": str(user_id),
            "provider": item["provider"],
            "account_id": str((result.get("account") or {}).get("id") or ""),
            "entity_type": item["entity_type"],
            "entity_id": item["entity_id"],
            "action_type": item["action_type"],
            "current_state_snapshot": item["current_state_snapshot"],
            "proposed_state": item["proposed_state"],
            "reason": item["reason"],
            "evidence": item["evidence"],
            "confidence": item["confidence"],
            "priority": item["priority"],
            "created_at": current,
            "expires_at": current + PROPOSAL_TTL,
            "source": PROPOSAL_SOURCE,
            "source_run_id": source_run_id,
            "recommendation_id": item["recommendation_id"],
            "recommendation_fingerprint": item["recommendation_fingerprint"],
            "idempotency_key": item["idempotency_key"],
            "readiness_snapshot": readiness_snapshot,
            "profitability_accounting_snapshot": profitability_snapshot,
            "provider_state_fingerprint": item["provider_state_fingerprint"],
            "status": "pending_preview",
            "revision": 1,
            "automatic_execution": False,
            "existing_action_gate_required": True,
            "provider_write_reached": False,
            "accounting_write_reached": False,
            "trace": {
                "run_id": source_run_id,
                "recommendation_id": item["recommendation_id"],
                "proposal_id": proposal_id,
                "approval_id": None,
                "execution_id": None,
                "verification": None,
            },
        }
        result_update = await db[PROPOSAL_COLLECTION].update_one(
            {"_id": proposal_id},
            {
                "$setOnInsert": document,
                "$set": {"last_seen_at": current, "last_source_run_id": source_run_id},
                "$addToSet": {"source_run_ids": source_run_id},
            },
            upsert=True,
        )
        was_created = getattr(result_update, "upserted_id", None) is not None
        created += int(was_created)
        deduplicated += int(not was_created)
        row = await db[PROPOSAL_COLLECTION].find_one({"_id": proposal_id}, {"_id": 0})
        proposals.append(_public_proposal(row or document))
    return {
        "source_run_id": source_run_id,
        "created": created,
        "deduplicated": deduplicated,
        "proposals": proposals,
        "non_executable_recommendations": [
            item for item in normalized if item.get("executable") is not True
        ],
        "provider_calls": 0,
        "provider_writes": 0,
        "automatic_execution": False,
    }


def _validate_identity(
    proposal: dict[str, Any], payload: Phase5ProposalPreviewInput
) -> None:
    comparisons = {
        "provider": payload.provider,
        "entity_type": payload.entity_type,
        "entity_id": payload.entity_id,
    }
    for field, supplied in comparisons.items():
        if str(proposal.get(field) or "") != str(supplied):
            raise HTTPException(
                status_code=409,
                detail={"code": f"phase5_proposal_{field}_mismatch"},
            )
    if proposal.get("recommendation_fingerprint") != payload.recommendation_fingerprint:
        raise HTTPException(
            status_code=409,
            detail={"code": "phase5_proposal_recommendation_fingerprint_mismatch"},
        )
    if int(proposal.get("revision") or 0) != payload.expected_revision:
        raise HTTPException(
            status_code=409,
            detail={"code": "phase5_proposal_revision_mismatch"},
        )


def _provider_entity_type(provider: str, entity_type: str) -> str:
    if provider == "snapchat_ads" and entity_type == "ad_group":
        return "ad_squad"
    if provider == "meta_ads" and entity_type == "ad_group":
        return "adset"
    return entity_type


def _snapchat_payload(proposal: dict[str, Any]) -> dict[str, Any]:
    action = str(proposal.get("action_type") or "")
    entity_type = str(proposal.get("entity_type") or "")
    provider_entity = _provider_entity_type("snapchat_ads", entity_type)
    if action in {"pause", "resume"}:
        changes = {"status": (proposal.get("proposed_state") or {}).get("status")}
    elif action in {"budget_reduce", "budget_scale"}:
        amount = _number((proposal.get("proposed_state") or {}).get("amount_native"))
        changes = {"daily_budget_micro": int(round(float(amount or 0) * 1_000_000))}
    elif action == "bid_adjust":
        amount = _number(
            (proposal.get("proposed_state") or {}).get("bid_amount_native")
        )
        changes = {"bid_micro": int(round(float(amount or 0) * 1_000_000))}
    else:
        raise HTTPException(
            status_code=409, detail={"code": "phase5_action_unsupported"}
        )
    current = proposal.get("current_state_snapshot") or {}
    parent_id = None
    if entity_type == "ad_group":
        parent_id = current.get("campaign_id")
    elif entity_type == "ad":
        parent_id = current.get("ad_group_id")
    return {
        "action": f"{provider_entity}.update",
        "account_id": (proposal.get("evidence") or {}).get("account_id")
        or proposal.get("account_id"),
        "target_id": proposal.get("entity_id"),
        "parent_id": parent_id,
        "payload": changes,
        "reason": proposal.get("reason") or "Decision Intelligence Phase 5 proposal",
        "idempotency_key": proposal.get("idempotency_key"),
        "activation_acknowledged": action == "resume",
        "supporting_evidence": [
            {
                "kind": "decision_intelligence_phase5",
                "value": {
                    "recommendation_id": proposal.get("recommendation_id"),
                    "recommendation_fingerprint": proposal.get(
                        "recommendation_fingerprint"
                    ),
                },
                "source": PROPOSAL_SOURCE,
                "observed_at": _safe_copy(proposal.get("created_at")),
                "verification_status": "inferred",
                "confidence": 0.0,
                "used_in_decision": False,
                "weight": 0.0,
            }
        ],
        "safety_protocol_version": 2,
    }


def _meta_payload(proposal: dict[str, Any]) -> dict[str, Any]:
    action = str(proposal.get("action_type") or "")
    proposed = proposal.get("proposed_state") or {}
    if action in {"pause", "resume"}:
        gate_action = "update_status"
        status = proposed.get("status")
        amount = None
    elif action in {"budget_reduce", "budget_scale"}:
        gate_action = "update_budget"
        status = None
        amount = proposed.get("amount_native")
    elif action == "bid_adjust":
        gate_action = "update_bid"
        status = None
        amount = proposed.get("bid_amount_native")
    else:
        raise HTTPException(
            status_code=409, detail={"code": "phase5_action_unsupported"}
        )
    return {
        "account_id": proposal.get("account_id"),
        "entity_type": _provider_entity_type(
            "meta_ads", str(proposal.get("entity_type") or "")
        ),
        "entity_id": proposal.get("entity_id"),
        "action": gate_action,
        "status": status,
        "amount_native": amount,
        "idempotency_key": proposal.get("idempotency_key"),
    }


def _normalized_provider_state(provider: str, raw: dict[str, Any]) -> dict[str, Any]:
    if provider == "snapchat_ads":
        return {
            "status": raw.get("status"),
            "effective_status": raw.get("effective_status"),
            "daily_budget_native": (
                _number(raw.get("daily_budget_micro")) / 1_000_000
                if _number(raw.get("daily_budget_micro")) is not None
                else None
            ),
            "bid_amount_native": (
                _number(raw.get("bid_micro")) / 1_000_000
                if _number(raw.get("bid_micro")) is not None
                else None
            ),
            "bid_strategy": raw.get("bid_strategy"),
        }
    return {
        "status": raw.get("status"),
        "effective_status": raw.get("effective_status"),
        "daily_budget_native": (
            _number(raw.get("daily_budget")) / 100
            if _number(raw.get("daily_budget")) is not None
            else None
        ),
        "lifetime_budget_native": (
            _number(raw.get("lifetime_budget")) / 100
            if _number(raw.get("lifetime_budget")) is not None
            else None
        ),
        "bid_amount_native": (
            _number(raw.get("bid_amount")) / 100
            if _number(raw.get("bid_amount")) is not None
            else None
        ),
        "bid_strategy": raw.get("bid_strategy"),
    }


async def _preview_existing_action_gate(
    db: Any, user_id: str, actor_id: str, proposal: dict[str, Any]
) -> dict[str, Any]:
    provider = str(proposal.get("provider") or "")
    if provider == "snapchat_ads":
        from integrations_control_center.snapchat_campaign_management import (
            PROPOSAL_COLLECTION as SNAPCHAT_PROPOSAL_COLLECTION,
            SnapchatManagementProposalInput,
            create_snapchat_management_proposal,
        )

        preview = await create_snapchat_management_proposal(
            db,
            user_id,
            actor_id,
            SnapchatManagementProposalInput(**_snapchat_payload(proposal)),
        )
        raw = await db[SNAPCHAT_PROPOSAL_COLLECTION].find_one(
            {"user_id": user_id, "proposal_id": preview.get("proposal_id")},
            {"_id": 0, "original_snapshot": 1},
        )
        return {
            "provider_proposal_id": preview.get("proposal_id"),
            "proposal": preview,
            "current_state": _normalized_provider_state(
                provider, (raw or {}).get("original_snapshot") or {}
            ),
        }
    if provider == "meta_ads":
        from integrations_control_center.meta_campaign_management import (
            MetaMutationPreviewInput,
            preview_meta_mutation,
        )

        preview = await preview_meta_mutation(
            db, user_id, MetaMutationPreviewInput(**_meta_payload(proposal))
        )
        return {
            "provider_proposal_id": preview.get("proposal_id"),
            "proposal": preview,
            "current_state": _normalized_provider_state(
                provider, preview.get("before") or {}
            ),
        }
    raise HTTPException(status_code=409, detail={"code": "phase5_provider_unsupported"})


def _safe_provider_summary(result: dict[str, Any]) -> dict[str, Any]:
    proposal = (
        result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    )
    allowed = (
        "proposal_id",
        "status",
        "revision",
        "action",
        "account_id",
        "target_id",
        "entity_type",
        "entity_id",
        "field",
        "planned",
        "expires_at",
        "created_at",
        "approved_at",
        "executed_at",
        "verified",
        "verification",
        "provider_write_reached",
        "provider_write_state",
        "provider_write_uncertain",
        "rollback",
        "failure",
        "recovery_action",
    )
    return {key: _safe_copy(proposal.get(key)) for key in allowed if key in proposal}


async def _invalidate_provider_preview(
    db: Any,
    *,
    user_id: str,
    provider: str,
    provider_proposal_id: str,
    provider_status: str,
) -> None:
    collection = (
        SNAPCHAT_GATE_COLLECTION if provider == "snapchat_ads" else META_GATE_COLLECTION
    )
    await db[collection].update_one(
        {
            "user_id": user_id,
            "proposal_id": provider_proposal_id,
            "status": provider_status,
        },
        {
            "$set": {
                "status": "invalidated_phase5_state_drift",
                "invalidated_at": _now(),
                "invalidation_reason": "provider_state_changed_since_phase5_proposal",
            }
        },
    )


async def preview_phase5_proposal(
    db: Any,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    payload: Phase5ProposalPreviewInput,
    *,
    dispatcher: PreviewDispatcher = _preview_existing_action_gate,
) -> dict[str, Any]:
    proposal = await db[PROPOSAL_COLLECTION].find_one(
        {"user_id": str(user_id), "proposal_id": proposal_id}, {"_id": 0}
    )
    if not proposal:
        raise HTTPException(
            status_code=404, detail={"code": "phase5_proposal_not_found"}
        )
    _validate_identity(proposal, payload)
    if _is_expired(proposal):
        raise HTTPException(status_code=409, detail={"code": "phase5_proposal_expired"})
    if proposal.get("status") not in {"pending_preview", "previewed"}:
        raise HTTPException(
            status_code=409, detail={"code": "phase5_proposal_not_previewable"}
        )
    dispatched = await dispatcher(db, str(user_id), str(actor_id), proposal)
    provider_proposal_id = str(dispatched.get("provider_proposal_id") or "")
    if not provider_proposal_id:
        raise HTTPException(
            status_code=502, detail={"code": "phase5_provider_preview_missing_id"}
        )
    provider_proposal = (
        dispatched.get("proposal")
        if isinstance(dispatched.get("proposal"), dict)
        else {}
    )
    provider_status = str(provider_proposal.get("status") or "")
    valid_preview_statuses = (
        {"previewed", "previewed_v2"}
        if proposal.get("provider") == "snapchat_ads"
        else {"previewed"}
    )
    if provider_status not in valid_preview_statuses:
        raise HTTPException(
            status_code=409,
            detail={"code": "phase5_existing_action_gate_proposal_not_previewable"},
        )
    provider_state = dispatched.get("current_state")
    provider_state = provider_state if isinstance(provider_state, dict) else {}
    observed_fingerprint = provider_state_fingerprint(
        provider=str(proposal.get("provider") or ""),
        entity_type=str(proposal.get("entity_type") or ""),
        entity_id=str(proposal.get("entity_id") or ""),
        action=str(proposal.get("action_type") or ""),
        state=provider_state,
    )
    if observed_fingerprint != proposal.get("provider_state_fingerprint"):
        await _invalidate_provider_preview(
            db,
            user_id=str(user_id),
            provider=str(proposal.get("provider") or ""),
            provider_proposal_id=provider_proposal_id,
            provider_status=provider_status,
        )
        await db[PROPOSAL_COLLECTION].update_one(
            {"user_id": str(user_id), "proposal_id": proposal_id},
            {
                "$set": {
                    "status": "revalidation_required",
                    "revalidation_reason": "provider_state_changed_since_phase5_proposal",
                    "provider_preview_id": provider_proposal_id,
                    "provider_preview_state": _safe_copy(provider_state),
                    "observed_provider_state_fingerprint": observed_fingerprint,
                    "previewed_at": _now(),
                },
                "$inc": {"revision": 1},
            },
        )
        raise HTTPException(
            status_code=409,
            detail={"code": "phase5_proposal_provider_state_changed"},
        )
    provider_revision = int(provider_proposal.get("revision") or 1)
    updated = await db[PROPOSAL_COLLECTION].update_one(
        {
            "user_id": str(user_id),
            "proposal_id": proposal_id,
            "status": proposal.get("status"),
            "revision": payload.expected_revision,
        },
        {
            "$set": {
                "status": "previewed",
                "provider_proposal_id": provider_proposal_id,
                "provider_proposal_revision": provider_revision,
                "provider_preview": _safe_provider_summary(dispatched),
                "provider_preview_state": _safe_copy(provider_state),
                "previewed_at": _now(),
            },
            "$inc": {"revision": 1},
        },
    )
    if int(getattr(updated, "matched_count", 1) or 0) != 1:
        raise HTTPException(status_code=409, detail={"code": "phase5_preview_race"})
    row = await db[PROPOSAL_COLLECTION].find_one(
        {"user_id": str(user_id), "proposal_id": proposal_id}, {"_id": 0}
    )
    response = _public_proposal(row or proposal)
    if provider_proposal.get("confirm_token"):
        response["approval_challenge"] = {
            "confirm_token": provider_proposal["confirm_token"],
            "provider_proposal_revision": provider_revision,
        }
    return response


def _error_detail(exc: BaseException) -> dict[str, Any]:
    detail = getattr(exc, "detail", None)
    if isinstance(detail, dict):
        return _safe_copy(detail)
    return {"code": type(exc).__name__}


async def _execute_existing_action_gate(
    db: Any,
    user_id: str,
    actor_id: str,
    proposal: dict[str, Any],
    payload: Phase5ProposalApprovalInput,
) -> dict[str, Any]:
    provider = str(proposal.get("provider") or "")
    provider_proposal_id = str(proposal.get("provider_proposal_id") or "")
    if provider == "snapchat_ads":
        from integrations_control_center.snapchat_campaign_management import (
            PROPOSAL_COLLECTION as SNAPCHAT_PROPOSAL_COLLECTION,
            SnapchatManagementApprovalInput,
            approve_snapchat_management_proposal,
            execute_snapchat_management_proposal,
        )

        if not payload.confirm_token or payload.provider_proposal_revision is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "phase5_snapchat_approval_challenge_required"},
            )
        try:
            await approve_snapchat_management_proposal(
                db,
                user_id,
                actor_id,
                provider_proposal_id,
                SnapchatManagementApprovalInput(
                    confirm_token=payload.confirm_token,
                    expected_revision=payload.provider_proposal_revision,
                ),
            )
            result = await execute_snapchat_management_proposal(
                db, user_id, actor_id, provider_proposal_id
            )
            return {"proposal": result}
        except Exception as exc:  # provider gate persists its exact durable state
            row = await db[SNAPCHAT_PROPOSAL_COLLECTION].find_one(
                {"user_id": user_id, "proposal_id": provider_proposal_id}, {"_id": 0}
            )
            return {"proposal": row or {}, "error": _error_detail(exc)}
    if provider == "meta_ads":
        from integrations_control_center.meta_campaign_management import (
            COLLECTION as META_PROPOSAL_COLLECTION,
            execute_meta_proposal,
        )

        try:
            result = await execute_meta_proposal(db, user_id, provider_proposal_id)
            return {"proposal": result}
        except Exception as exc:  # provider gate persists its exact durable state
            row = await db[META_PROPOSAL_COLLECTION].find_one(
                {"user_id": user_id, "proposal_id": provider_proposal_id}, {"_id": 0}
            )
            return {"proposal": row or {}, "error": _error_detail(exc)}
    raise HTTPException(status_code=409, detail={"code": "phase5_provider_unsupported"})


async def _default_accounting_gate(
    db: Any, user_id: str, action: str
) -> dict[str, Any]:
    from campaign_ai_profit_accounting_gate import (
        require_profit_accounting_complete_for_scale,
    )

    return await require_profit_accounting_complete_for_scale(db, user_id, action)


def _execution_semantics(result: dict[str, Any]) -> str:
    proposal = (
        result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    )
    rollback = (
        proposal.get("rollback") if isinstance(proposal.get("rollback"), dict) else {}
    )
    if rollback.get("status") in {"completed", "rolled_back"}:
        return "rolled_back"
    if (
        rollback.get("required") is True
        or proposal.get("status") == "rollback_required"
    ):
        return "rollback_required"
    if proposal.get("provider_write_uncertain") is True:
        return "outcome_unknown"
    status = str(proposal.get("status") or "")
    if status == "completed" and proposal.get("verified") is not False:
        return "succeeded"
    if status in {"verification_pending", "verification_required"}:
        return "verification_pending"
    if proposal.get("provider_write_state") == "confirmed" and not proposal.get(
        "verification"
    ):
        return "verification_pending"
    return "failed_before_write"


def _verification_result(result: dict[str, Any], semantics: str) -> dict[str, Any]:
    proposal = (
        result.get("proposal") if isinstance(result.get("proposal"), dict) else {}
    )
    verification = (
        proposal.get("verification")
        if isinstance(proposal.get("verification"), dict)
        else {}
    )
    return {
        "status": semantics,
        "provider_status": proposal.get("status"),
        "verified": (
            proposal.get("verified") is True or verification.get("verified") is True
        ),
        "provider_write_reached": proposal.get("provider_write_reached") is True,
        "provider_write_state": proposal.get("provider_write_state") or "not_attempted",
        "provider_write_uncertain": proposal.get("provider_write_uncertain") is True,
        "checked_at": verification.get("verified_at") or proposal.get("executed_at"),
        "error": _safe_copy(result.get("error") or {}),
    }


async def approve_and_execute_phase5_proposal(
    db: Any,
    user_id: str,
    actor_id: str,
    proposal_id: str,
    payload: Phase5ProposalApprovalInput,
    *,
    dispatcher: ExecuteDispatcher = _execute_existing_action_gate,
    accounting_gate: AccountingGate = _default_accounting_gate,
) -> dict[str, Any]:
    proposal = await db[PROPOSAL_COLLECTION].find_one(
        {"user_id": str(user_id), "proposal_id": proposal_id}, {"_id": 0}
    )
    if not proposal:
        raise HTTPException(
            status_code=404, detail={"code": "phase5_proposal_not_found"}
        )
    _validate_identity(proposal, payload)
    if payload.provider_state_fingerprint != proposal.get("provider_state_fingerprint"):
        raise HTTPException(
            status_code=409,
            detail={"code": "phase5_proposal_provider_state_fingerprint_mismatch"},
        )
    if _is_expired(proposal):
        raise HTTPException(status_code=409, detail={"code": "phase5_proposal_expired"})
    if proposal.get("status") != "previewed":
        raise HTTPException(
            status_code=409, detail={"code": "phase5_proposal_owner_approval_required"}
        )
    expected_provider_revision = int(proposal.get("provider_proposal_revision") or 1)
    if (
        payload.provider_proposal_revision is not None
        and payload.provider_proposal_revision != expected_provider_revision
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "phase5_provider_proposal_revision_mismatch"},
        )
    if proposal.get("provider") == "snapchat_ads" and (
        not payload.confirm_token or payload.provider_proposal_revision is None
    ):
        raise HTTPException(
            status_code=409,
            detail={"code": "phase5_snapchat_approval_challenge_required"},
        )
    if proposal.get("action_type") == "budget_scale":
        accounting_snapshot = await accounting_gate(db, str(user_id), "scale")
    else:
        accounting_snapshot = {"complete": True, "scale_gate_applied": False}
    if _is_expired(proposal):
        raise HTTPException(status_code=409, detail={"code": "phase5_proposal_expired"})
    approval_id = f"di-p5-appr-{uuid.uuid4().hex}"
    execution_id = f"di-p5-exec-{uuid.uuid4().hex}"
    claimed = await db[PROPOSAL_COLLECTION].update_one(
        {
            "user_id": str(user_id),
            "proposal_id": proposal_id,
            "status": "previewed",
            "revision": payload.expected_revision,
        },
        {
            "$set": {
                "status": "approved",
                "approval_id": approval_id,
                "approved_by": str(actor_id),
                "approved_at": _now(),
                "execution_id": execution_id,
                "execution_started_at": _now(),
                "approval_accounting_snapshot": _safe_copy(accounting_snapshot),
                "trace.approval_id": approval_id,
                "trace.execution_id": execution_id,
            },
            "$inc": {"revision": 1},
        },
    )
    if int(getattr(claimed, "modified_count", 0) or 0) != 1:
        raise HTTPException(status_code=409, detail={"code": "phase5_approval_race"})
    try:
        provider_result = await dispatcher(
            db, str(user_id), str(actor_id), proposal, payload
        )
    except Exception as exc:  # fail before a provider gate result is available
        provider_result = {"proposal": {}, "error": _error_detail(exc)}
    semantics = _execution_semantics(provider_result)
    verification = _verification_result(provider_result, semantics)
    await db[PROPOSAL_COLLECTION].update_one(
        {"user_id": str(user_id), "proposal_id": proposal_id},
        {
            "$set": {
                "status": semantics,
                "finished_at": _now(),
                "provider_result": _safe_provider_summary(provider_result),
                "verification_result": verification,
                "provider_write_reached": verification["provider_write_reached"],
                "trace.verification": verification,
            },
            "$inc": {"revision": 1},
        },
    )
    row = await db[PROPOSAL_COLLECTION].find_one(
        {"user_id": str(user_id), "proposal_id": proposal_id}, {"_id": 0}
    )
    return _public_proposal(row or proposal)


def attach_phase5_proposal_bridge_routes(
    router: Any,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    @router.get("/decision-intelligence/phase5/proposal-capabilities")
    async def capabilities(user: dict = Depends(current_user)) -> dict[str, Any]:
        require_owner(user)
        return phase5_proposal_capability_matrix()

    @router.get("/decision-intelligence/phase5/proposals")
    async def list_proposals(
        limit: int = Query(default=50, ge=1, le=100),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        cursor = (
            db[PROPOSAL_COLLECTION]
            .find({"user_id": str(owner["id"])}, {"_id": 0, "user_id": 0})
            .sort("created_at", -1)
            .limit(limit)
        )
        rows = await cursor.to_list(length=limit)
        return {"proposals": [_public_proposal(row) for row in rows]}

    @router.post("/decision-intelligence/phase5/proposals/{proposal_id}/preview")
    async def preview(
        proposal_id: str,
        payload: Phase5ProposalPreviewInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await preview_phase5_proposal(
            db,
            str(owner["id"]),
            str(owner["id"]),
            proposal_id,
            payload,
        )

    @router.post(
        "/decision-intelligence/phase5/proposals/{proposal_id}/approve-and-execute"
    )
    async def approve_and_execute(
        proposal_id: str,
        payload: Phase5ProposalApprovalInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await approve_and_execute_phase5_proposal(
            db,
            str(owner["id"]),
            str(owner["id"]),
            proposal_id,
            payload,
        )


__all__ = [
    "ACTION_CAPABILITIES",
    "PROPOSAL_COLLECTION",
    "Phase5ProposalApprovalInput",
    "Phase5ProposalPreviewInput",
    "approve_and_execute_phase5_proposal",
    "attach_phase5_proposal_bridge_routes",
    "ensure_phase5_proposal_indexes",
    "normalize_phase5_recommendation",
    "persist_phase5_proposals",
    "phase5_proposal_capability_matrix",
    "preview_phase5_proposal",
    "provider_state_fingerprint",
]
