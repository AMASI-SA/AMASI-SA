"""Owner-only API for the Snapchat decision journal and learning loop."""

from __future__ import annotations

from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .snapchat_decision_ledger import (
    add_decision_annotation,
    get_ad_decision,
    list_account_decision_summaries,
    list_ad_decisions,
    reconcile_snapchat_management_decisions,
)
from .snapchat_native_data_common import SNAPCHAT_PROVIDER_ID


class AdDecisionAnnotationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=2, max_length=4000)
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=30)

    @field_validator("text")
    @classmethod
    def clean_text(cls, value: str) -> str:
        normalized = " ".join(value.split()).strip()
        if not normalized:
            raise ValueError("text is required")
        return normalized


class AdDecisionReconcileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limit: int = Field(default=1000, ge=1, le=10_000)


class AdaptiveReviewInput(BaseModel):
    """Run bounded read-only judgment; suggestions never become provider facts."""

    model_config = ConfigDict(extra="forbid")

    account_id: str | None = Field(default=None, max_length=160)
    max_entities: int = Field(default=5, ge=1, le=5)
    user_suggestions: list[str] = Field(default_factory=list, max_length=20)


def _identity(value: str, *, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized or len(normalized) > 200:
        raise HTTPException(
            status_code=422,
            detail={"code": f"invalid_{field}"},
        )
    return normalized


async def _reconcile_read_model(db: Any, user_id: str) -> None:
    """Best-effort legacy backfill; a journal outage must not hide existing rows."""
    try:
        await reconcile_snapchat_management_decisions(db, user_id, limit=1000)
    except Exception:
        # Terminal management proposals remain the recoverable source.  Reads
        # should continue with whatever immutable journal entries already exist.
        return


def attach_snapchat_decision_routes(
    router: APIRouter,
    db: Any,
    current_user: Callable,
    require_owner: Callable[[Any], dict],
) -> None:
    base = f"/{SNAPCHAT_PROVIDER_ID}/decision-ledger"

    @router.get(f"{base}/accounts")
    async def decision_account_summaries(
        limit_per_account: int = Query(default=5, ge=1, le=25),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        await _reconcile_read_model(db, str(owner["id"]))
        return await list_account_decision_summaries(
            db,
            str(owner["id"]),
            limit_per_account=limit_per_account,
        )

    @router.get(base)
    async def decision_history(
        account_id: str = Query(min_length=1, max_length=160),
        page: int = Query(default=1, ge=1),
        limit: int = Query(default=5, ge=1, le=100),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        await _reconcile_read_model(db, str(owner["id"]))
        return await list_ad_decisions(
            db,
            str(owner["id"]),
            _identity(account_id, field="account_id"),
            page,
            limit,
        )

    @router.post(f"{base}/reconcile")
    async def reconcile_decisions(
        payload: AdDecisionReconcileInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        return await reconcile_snapchat_management_decisions(
            db, str(owner["id"]), limit=payload.limit
        )

    @router.get(f"{base}/diagnose")
    async def diagnose_change(
        date_from: str = Query(min_length=10, max_length=10),
        date_to: str = Query(min_length=10, max_length=10),
        metric: str = Query(
            default="sales_sar",
            pattern=(
                "^(orders|sales_sar|contribution_profit_sar|"
                "ad_spend_sar|roas|cpa_sar)$"
            ),
        ),
        account_id: str | None = Query(default=None, max_length=160),
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        from .snapchat_decision_diagnostics import diagnose_ad_business_change

        try:
            return await diagnose_ad_business_change(
                db,
                str(owner["id"]),
                date_from=date_from,
                date_to=date_to,
                metric=metric,
                account_id=(
                    _identity(account_id, field="account_id") if account_id else None
                ),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "ad_decision_diagnostic_input_invalid",
                    "message": str(exc),
                },
            ) from exc

    @router.get(f"{base}/adaptive-status")
    async def adaptive_status(
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        require_owner(user)
        from .snapchat_adaptive_decision_ai import adaptive_ai_status

        return adaptive_ai_status()

    @router.post(f"{base}/adaptive-review")
    async def adaptive_review(
        payload: AdaptiveReviewInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        from .snapchat_adaptive_decision_ai import (
            acquire_adaptive_review_slot,
            judge_adaptive_snapchat_decisions,
        )
        from .snapchat_intraday_waste_monitor import (
            monitor_snapchat_intraday_waste,
        )
        from .snapchat_decision_metrics import capture_decision_baseline

        account_id = (
            _identity(payload.account_id, field="account_id")
            if payload.account_id
            else None
        )
        await acquire_adaptive_review_slot(db, str(owner["id"]))
        evidence_report = await monitor_snapchat_intraday_waste(
            db,
            str(owner["id"]),
            account_id=account_id,
        )
        candidates = [
            item
            for item in evidence_report.get("items") or []
            if (item.get("recommendation") or {}).get("code")
            in {
                "adaptive_review",
                "investigate_efficiency",
                "investigate_target_missing",
                "learn_conversion_delay",
                "watch_accumulating_evidence",
                "continue_efficient",
            }
        ][: payload.max_entities]
        review_evidence = []
        for item in candidates:
            item_account_id = str(item.get("account_id") or account_id or "")
            item_campaign_id = str(item.get("campaign_id") or "") or None
            try:
                commerce_baseline = await capture_decision_baseline(
                    db,
                    str(owner["id"]),
                    account_id=item_account_id,
                    campaign_id=item_campaign_id,
                )
            except Exception as exc:
                commerce_baseline = {
                    "windows": [],
                    "inventory_verification_status": "unavailable",
                    "coverage": {"error_type": type(exc).__name__},
                }
            review_evidence.append(
                {
                    "objective": "grow_sales_while_protecting_contribution_profit",
                    "entity_evidence": item,
                    "commerce_baseline": commerce_baseline,
                    "coverage": evidence_report.get("coverage") or {},
                    "user_suggestions": [
                        {
                            "value": suggestion,
                            "verification_status": "user_suggestion",
                            "used_as_fact": False,
                        }
                        for suggestion in payload.user_suggestions
                    ],
                    "policy": {
                        "fixed_rules": False,
                        "measured_sales_and_profit_are_primary": True,
                        "provider_write_allowed": False,
                    },
                }
            )
        judgments = await judge_adaptive_snapchat_decisions(review_evidence)
        return {
            "provider": SNAPCHAT_PROVIDER_ID,
            "mode": "supervised_shadow_learning",
            "objective": "grow_sales_while_protecting_contribution_profit",
            "evidence_report": evidence_report,
            "judgments": judgments,
            "proposals_created": 0,
            "provider_write_reached": False,
        }

    @router.get(f"{base}/{{decision_id}}")
    async def decision_detail(
        decision_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        detail = await get_ad_decision(
            db,
            str(owner["id"]),
            _identity(decision_id, field="decision_id"),
        )
        if detail is None:
            raise HTTPException(
                status_code=404,
                detail={"code": "ad_decision_not_found"},
            )
        return detail

    @router.post(f"{base}/{{decision_id}}/annotations")
    async def annotate_decision(
        decision_id: str,
        payload: AdDecisionAnnotationInput,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        try:
            return await add_decision_annotation(
                db,
                str(owner["id"]),
                _identity(decision_id, field="decision_id"),
                {"text": payload.text, "evidence": payload.evidence},
                actor_id=str(owner["id"]),
                actor_kind="mezan_user",
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "ad_decision_not_found"},
            ) from exc

    @router.post(
        f"{base}/{{decision_id}}/evaluate",
        status_code=status.HTTP_200_OK,
    )
    async def evaluate_decision_now(
        decision_id: str,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        owner = require_owner(user)
        from .snapchat_decision_outcomes import evaluate_due_ad_decisions

        try:
            return await evaluate_due_ad_decisions(
                db,
                str(owner["id"]),
                decision_id=_identity(decision_id, field="decision_id"),
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "ad_decision_not_found"},
            ) from exc


__all__ = [
    "AdDecisionAnnotationInput",
    "AdDecisionReconcileInput",
    "AdaptiveReviewInput",
    "attach_snapchat_decision_routes",
]
