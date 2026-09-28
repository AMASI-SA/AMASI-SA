"""Source routing for the existing review/preparation/shipping workflows.

The shared workflow owns state. This module freezes a source snapshot and checks
its identity; it never fabricates a Salla order or implements a second workflow.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from fastapi import HTTPException

from .binding import require_bound, require_admitted
from .canonical_adapter import is_local_order_number, to_canonical_order
from .domain import DomainError, add_event, digest, dispatch_blockers, source_snapshot
from .repository import COLLECTION, bounded

WORKFLOWS = "order_review_workflows"


def is_local(order) -> bool:
    return getattr(getattr(order, "source", None), "provider", None) == "mezan"


def api_error(exc: DomainError):
    return HTTPException(status_code=exc.http_status, detail={"code": exc.code})


async def source_document(db, tenant_id, order_number, *, write=False):
    require_bound(db, write=write, tenant_id=tenant_id)
    if write:
        require_admitted(db,tenant_id,{"workflow"})
    document = await db[COLLECTION].find_one({
        "tenant_id": str(tenant_id), "order_number": str(order_number), "provider": "mezan",
    }, {"_id": 0})
    if document is None:
        raise DomainError("local_order_not_found", 404)
    to_canonical_order(document, tenant_id=str(tenant_id))
    return document


async def current_document(db, tenant_id, order_number, *, write=False, lock=False):
    document = await source_document(db, tenant_id, order_number, write=write or lock)
    workflow = await db[WORKFLOWS].find_one({
        "user_id": str(tenant_id), "order_number": str(order_number),
    }, {"_id": 0})
    if workflow:
        if (workflow.get("order_id"), workflow.get("source_provider"),
                workflow.get("special_source_digest"), workflow.get("special_source_revision")) != (
                document["order_id"], "mezan", document["snapshot_digest"], document["source_revision"]):
            raise DomainError("shared_workflow_source_identity_mismatch")
        stage = workflow.get("stage")
        if stage not in {"pending_review", "cancelled"} and not document["source_frozen"]:
            raise DomainError("shared_workflow_source_not_frozen")
        document["stage"] = "processing" if stage == "in_progress" else stage
        if lock:
            # A financial transaction must conflict with simultaneous cancellation,
            # reassignment, pickup or delivery, including writers without revision CAS.
            result = await db[WORKFLOWS].update_one({
                "user_id": str(tenant_id), "order_number": str(order_number),
                "stage": stage, "special_source_digest": document["snapshot_digest"],
            }, {"$inc": {"special_financial_fence": 1}})
            if result.matched_count != 1:
                raise DomainError("workflow_changed_during_financial_operation")
    return document, workflow


async def freeze_for_review(db, tenant_id, order, actor_id):
    """CAS freezes precisely the DTO reviewed, not a newer unseen option edit.

    A failed later review may retry the same frozen snapshot. Thawing is never
    implicit and no existing assignment is discarded to make an edit succeed.
    """
    if not is_local(order):
        return {}
    try:
        document, _ = await current_document(db, tenant_id, order.order_number, write=True)
        if (order.special_source_revision, order.special_source_digest) != (
                document["source_revision"], document["snapshot_digest"]):
            raise DomainError("reviewed_source_changed_refresh_required")
        if document["stage"] in {"cancelled", "refunded"}:
            raise DomainError("order_closed")
        proof = {"source_provider": "mezan", "special_source_revision": document["source_revision"],
                 "special_source_digest": document["snapshot_digest"]}
        if not document["source_frozen"]:
            expected = document["revision"]
            document["source_frozen"] = True
            document["revision"] += 1
            add_event(document, "special_order.review_source_frozen", str(actor_id))
            bounded(document)
            result = await db[COLLECTION].replace_one({
                "tenant_id": str(tenant_id), "order_id": document["order_id"],
                "revision": expected, "source_revision": order.special_source_revision,
                "snapshot_digest": order.special_source_digest, "source_frozen": False,
            }, document)
            if result.matched_count != 1:
                # Never accept a newly edited snapshot on behalf of this request.
                current = await source_document(db, tenant_id, order.order_number, write=True)
                if current["stage"] in {"cancelled", "refunded"} or not current["source_frozen"] or current["snapshot_digest"] != order.special_source_digest:
                    raise DomainError("reviewed_source_changed_refresh_required")
        return proof
    except DomainError as exc:
        raise api_error(exc) from None


async def refresh_local_source(db, tenant_id, order_number):
    """Salla refresh callers receive a truthful local-source result instead."""
    if not is_local_order_number(order_number):
        return None
    try:
        await current_document(db, tenant_id, order_number)
        return {"ok": True, "found": True, "updated": False, "skipped": True,
                "source_provider": "mezan", "reason": "local_source_not_salla",
                "order_number": order_number, "no_shipments_api_calls": True}
    except DomainError as exc:
        return {"ok": False, "found": False, "updated": False, "code": exc.code,
                "source_provider": "mezan", "order_number": order_number}


async def guard_dispatch(db, tenant_id, order_numbers):
    for number in dict.fromkeys(order_numbers):
        if not is_local_order_number(number):
            continue
        try:
            document, _ = await current_document(db, tenant_id, number, write=True)
            from .finance_service import verify_document_finance
            await verify_document_finance(db, document)
            problems = dispatch_blockers(document)
            if problems:
                raise DomainError(problems[0])
        except DomainError as exc:
            raise api_error(exc) from None


async def guard_delivery_completion(db, tenant_id, order_number):
    if not is_local_order_number(order_number):
        return
    from .domain import balances
    try:
        document, _ = await current_document(db, tenant_id, order_number, write=True)
        if balances(document)["remaining_minor"]:
            raise DomainError("special_order_collection_confirmation_required")
        await guard_dispatch(db, tenant_id, [order_number])
    except DomainError as exc:
        raise api_error(exc) from None
