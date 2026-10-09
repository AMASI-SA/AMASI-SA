"""Durable Mezan review authority for operational preparation only.

The external order status is never changed or treated as financial evidence.
A workflow flag alone cannot grant authority: its completed operation must match
the tenant, order and version. Callers retain their component/custody guards.
"""
from __future__ import annotations

from typing import Any, Iterable

from product_fulfillment_rules import order_is_active, payment_is_eligible


LOCAL_COMPLETION_MODE = "mezan_local_v1"
WORKFLOWS = "order_review_workflows"
OPERATIONS = "order_review_completion_operations"
PREPARATION_STAGES = frozenset({"reviewed", "in_progress", "ready_to_ship"})
ASSIGNMENT_STAGES = frozenset({"reviewed", "in_progress"})
_EXTERNAL_PREPARATION = frozenset({
    "under review", "waiting review", "pending review", "in review",
    "بإنتظار المراجعة", "بانتظار المراجعة", "انتظار المراجعة",
    "بإنتظار المراجعه", "بانتظار المراجعه", "انتظار المراجعه",
    "reviewed", "تم المراجعة", "تمت المراجعة", "تم المراجعه", "تمت المراجعه",
    "processing", "in progress", "قيد التنفيذ", "جاري التنفيذ",
})
_EXTERNAL_COMPLETED = frozenset({"completed", "تم التنفيذ", "تم التجهيز"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _normalized(value: Any) -> str:
    return " ".join(_text(value).casefold().replace("_", " ").split())


def is_known_review_mode(mode: Any) -> bool:
    """Only an absent legacy mode or this exact local contract is supported."""
    return mode is None or mode == LOCAL_COMPLETION_MODE


def assignment_workflow_query() -> dict[str, Any]:
    """Starting one local unit does not close the remaining assignment queue."""
    return {"$or": [
        {"stage": "reviewed"},
        {"stage": "in_progress", "completion_mode": LOCAL_COMPLETION_MODE},
    ]}


async def load_local_assignment_workflows(
    db: Any, *, user_id: str, workflows: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Batch approval and source/component safety reads; never repair on read."""
    from fulfillment_v2_routes import COMPONENT_LIFECYCLES, LEGACY_COMPONENT_COHORT
    from stock_component_consumption_service import PLANS

    approved = await load_local_review_workflows(
        db, user_id=user_id, order_numbers=[row.get("order_number") for row in workflows],
        workflows=workflows,
    )
    if not approved:
        return {}
    numbers = sorted(approved)
    selector = {"user_id": user_id, "order_number": {"$in": numbers}}
    sources = {row["order_number"]: row for row in await db.unified_orders.find(
        selector, {"_id": 0, "order_number": 1, "g47_salla_snapshot": 1},
    ).to_list(len(numbers))}
    lifecycles = {row["order_number"]: row for row in await db[COMPONENT_LIFECYCLES].find(
        selector, {"_id": 0, "order_number": 1, "state": 1, "accepted": 1, "cancelled": 1,
                   "retry_required": 1, "generation": 1, "snapshot_revision": 1},
    ).to_list(len(numbers))}
    plans = {row["order_id"]: row for row in await db[PLANS].find(
        {"user_id": user_id, "order_id": {"$in": numbers}},
        {"_id": 0, "order_id": 1, "source_version": 1},
    ).to_list(len(numbers))}
    eligible = {}
    for number, workflow in approved.items():
        source = sources.get(number)
        lifecycle = lifecycles.get(number) or {}
        watermark = (source or {}).get("g47_salla_snapshot") or {}
        if (source is None or lifecycle.get("cancelled") or lifecycle.get("retry_required")
                or watermark.get("component_pending") or watermark.get("requires_authoritative_refresh")):
            continue
        plan = plans.get(number)
        if plan:
            if (lifecycle.get("state") != "reserved" or lifecycle.get("accepted") is not True
                    or (plan.get("source_version") or {}).get("value") != lifecycle.get("generation")
                    or watermark.get("revision") != lifecycle.get("snapshot_revision")):
                continue
        elif lifecycle.get("state") != LEGACY_COMPONENT_COHORT:
            continue
        eligible[number] = workflow
    return eligible


async def load_review_operational_context(
    db: Any, *, user_id: str, order_numbers: Iterable[str],
) -> tuple[dict[str, dict[str, Any]], set[str]]:
    """Load local proof and explicitly blocked contracts without legacy fallback."""
    numbers = sorted({_text(number) for number in order_numbers} - {""})
    if not numbers:
        return {}, set()
    workflows = await db[WORKFLOWS].find({
        "user_id": user_id, "order_number": {"$in": numbers},
    }, {"_id": 0}).to_list(len(numbers))
    local = await load_local_review_workflows(
        db, user_id=user_id, order_numbers=numbers, workflows=workflows,
    )
    blocked = {
        _text(row.get("order_number")) for row in workflows
        if not is_known_review_mode(row.get("completion_mode"))
        or (row.get("completion_mode") == LOCAL_COMPLETION_MODE
            and _text(row.get("order_number")) not in local)
    }
    return local, blocked


async def load_local_review_workflows(
    db: Any, *, user_id: str, order_numbers: Iterable[str],
    workflows: Iterable[dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    """Read approval proof in batches; never trust a piece or caller snapshot."""
    numbers = sorted({_text(number) for number in order_numbers} - {""})
    if not numbers:
        return {}
    number_set = set(numbers)
    if workflows is None:
        workflows = await db[WORKFLOWS].find({
            "user_id": user_id, "order_number": {"$in": numbers},
            "completion_mode": LOCAL_COMPLETION_MODE,
        }, {"_id": 0}).to_list(len(numbers))
    candidates = {
        _text(row.get("order_number")): row for row in workflows
        if row.get("user_id") == user_id
        and _text(row.get("order_number")) in number_set
        and row.get("completion_mode") == LOCAL_COMPLETION_MODE
        and _text(row.get("review_completion_operation_id"))
    }
    if not candidates:
        return {}
    identities = sorted({_text(row["review_completion_operation_id"]) for row in candidates.values()})
    operations = await db[OPERATIONS].find({
        "user_id": user_id, "order_number": {"$in": numbers},
        "_id": {"$in": identities}, "state": "completed",
        "completion_mode": LOCAL_COMPLETION_MODE,
    }, {"_id": 1, "user_id": 1, "order_number": 1, "state": 1,
        "completion_mode": 1, "superseded_by": 1}).to_list(len(identities))
    proofs = {
        (_text(row.get("_id")), _text(row.get("order_number")))
        for row in operations
        if row.get("user_id") == user_id and row.get("state") == "completed"
        and row.get("completion_mode") == LOCAL_COMPLETION_MODE
        and not row.get("superseded_by")
    }
    return {number: row for number, row in candidates.items()
            if (_text(row["review_completion_operation_id"]), number) in proofs}


def local_review_stage_eligible(
    order: Any, workflow: dict[str, Any] | None,
    allowed_stages: Iterable[str] = PREPARATION_STAGES,
) -> bool:
    """Combine loaded local proof with current external safety constraints."""
    if not workflow or not order or workflow.get("stage") not in allowed_stages:
        return False
    if not order_is_active(order) or not payment_is_eligible(getattr(order, "payment", None)):
        return False
    effective = _normalized(getattr(order, "status_native", None) or getattr(order, "status", None))
    allowed = _EXTERNAL_PREPARATION
    if workflow.get("stage") == "completed":
        allowed = allowed | _EXTERNAL_COMPLETED
    return effective in allowed
