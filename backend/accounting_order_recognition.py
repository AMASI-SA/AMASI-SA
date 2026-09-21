"""MZ2-native recognition of safe Salla order-export evidence.

The Salla workbook is evidence, not a ledger.  This service turns only rows
that have already passed the evidence classifier into the existing P01 sale
recognition shape.

Rules:
- Salla electronic payments may be recognized directly from the immutable
  Salla export when the row is delivered and carries one explicit payment
  reference whose amount reconciles.
- Tabby/Tamara/Emkan additionally require the provider's own
  payment_transactions row. The Salla filename/method/reference alone is not
  enough to prove capture.
- COD and bank transfer never enter this service.
- Partial-refund rows may recognize the original sale gross, but no refund is
  posted here. Refund accounting remains an independent evidence workflow.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from accounting_atomic import atomic_owner
from accounting_mz2_balances import read_mz2_write_balances
from accounting_module_contract import (
    OPERATION_ID,
    accounting_owner_id,
    require_accounting_permission,
)
from accounting_module_status_routes import fresh_accounting_user
from accounting_receivable_service import digest
from accounting_recognition_evidence import EvidenceError, qualify
from accounting_sales_tax import TaxError
from accounting_sales_tax_service import read_policy, sale_snapshot
from ledger_core import post_txn_group


RIYADH = ZoneInfo("Asia/Riyadh")
READY_STATES = {"ready_for_provider_resolution", "ready_sale_refund_pending_evidence"}
PROVIDERS = {"salla", "tamara", "tabby", "emkan"}


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _source_time(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        raise EvidenceError("delivery_timestamp_required")
    # Salla store exports are interpreted in the store's accounting timezone.
    # The original source text is retained separately in the evidence record.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            local = datetime.strptime(text, fmt).replace(tzinfo=RIYADH)
            return local.astimezone(timezone.utc)
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EvidenceError("delivery_timestamp_invalid") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=RIYADH)
    return parsed.astimezone(timezone.utc)


async def _cutover(db, owner: str) -> str:
    row = await db.settings.find_one(
        {"user_id": owner},
        {"_id": 0, "mezan2_financial_cutover": 1},
    )
    cutover = (row or {}).get("mezan2_financial_cutover") or {}
    if (
        cutover.get("operation_id") != OPERATION_ID
        or cutover.get("status") != "active"
        or not cutover.get("cutover_at")
    ):
        raise EvidenceError("recognition_cutoff_not_configured")
    return cutover["cutover_at"]


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in row.items() if key != "_id"}


def _principal(evidence: dict[str, Any]) -> Decimal:
    payment = evidence.get("payment_reference") or {}
    try:
        principal = Decimal(str(payment.get("amount")))
        current = Decimal(str(evidence.get("current_net_sar")))
        refunded = Decimal(str(evidence.get("refunded_sar") or "0"))
    except Exception as exc:  # noqa: BLE001
        raise EvidenceError("order_amount_evidence_invalid") from exc
    if principal <= 0 or current < 0 or refunded < 0:
        raise EvidenceError("order_amount_evidence_invalid")
    if refunded > 0:
        if principal < refunded or principal - refunded != current:
            raise EvidenceError("refund_amount_conflict")
    elif principal != current:
        raise EvidenceError("payment_amount_conflict")
    return principal


async def _unique_payment_reference(db, owner: str, evidence: dict[str, Any]) -> None:
    payment = evidence.get("payment_reference") or {}
    reference = str(payment.get("reference") or "").strip()
    if not reference:
        raise EvidenceError("payment_reference_missing")
    matches = await db.mz2_salla_order_evidence.find(
        {
            "user_id": owner,
            "payment_reference.reference": reference,
        },
        {"_id": 0, "order_number": 1},
    ).limit(3).to_list(3)
    order_numbers = {str(row.get("order_number") or "") for row in matches}
    if len(order_numbers) != 1 or evidence.get("order_number") not in order_numbers:
        raise EvidenceError("payment_reference_shared_by_multiple_orders")


def _salla_event(owner: str, evidence: dict[str, Any], *, cutoff: str) -> dict[str, Any]:
    del owner
    payment = evidence["payment_reference"]
    principal = _principal(evidence)
    delivered = _source_time(evidence.get("delivery_source_text"))
    cut = datetime.fromisoformat(str(cutoff).replace("Z", "+00:00")).astimezone(timezone.utc)
    if delivered < cut:
        raise EvidenceError("pre_cutover_recognition")
    if delivered > datetime.now(timezone.utc):
        raise EvidenceError("future_source_event")
    reference = str(payment.get("reference") or "").strip()
    if not reference:
        raise EvidenceError("canonical_provider_id_required")
    event = {
        "kind": "sale",
        "provider": "salla",
        "provider_payment_id": reference,
        "canonical_event_id": reference,
        "order_number": evidence["order_number"],
        "amount": format(principal, ".2f"),
        "sale_amount": format(principal, ".2f"),
        "currency": "SAR",
        "recognized_at": delivered.isoformat(),
        "cutover_at": cut.isoformat(),
        "source": {
            "order_evidence_id": evidence["id"],
            "snapshot_id": evidence.get("latest_snapshot_id"),
            "file_id": evidence.get("latest_file_id"),
            "payment_source": "salla_orders_export.payment_reference",
            "payment_provider_raw": payment.get("provider_raw"),
            "payment_reference": reference,
            "delivery_source_text": evidence.get("delivery_source_text"),
            "original_currency": evidence.get("original_currency"),
            "original_amount": evidence.get("original_amount"),
        },
        "idempotency_key": f"bnpl_sale:salla:{reference}",
    }
    event["source_fingerprint"] = _hash(event)
    return event


async def _provider_event(
    db,
    *,
    owner: str,
    evidence: dict[str, Any],
    provider: str,
    cutoff: str,
) -> dict[str, Any]:
    payment_ref = evidence["payment_reference"]
    payment_id = str(payment_ref.get("reference") or "").strip()
    if not payment_id:
        raise EvidenceError("payment_reference_missing")
    payments = await db.payment_transactions.find(
        {
            "user_id": owner,
            "provider": provider,
            "provider_id": payment_id,
        }
    ).limit(2).to_list(2)
    if len(payments) == 0:
        raise EvidenceError("provider_payment_evidence_missing")
    if len(payments) != 1:
        raise EvidenceError("provider_payment_identity_not_unique")

    principal = _principal(evidence)
    delivered = _source_time(evidence.get("delivery_source_text"))
    created = _source_time(evidence.get("order_date_source_text"))
    order = {
        "id": evidence["id"],
        "user_id": owner,
        "order_number": evidence["order_number"],
        "total_amount": format(principal, ".2f"),
        "currency": "SAR",
        "payment_method": provider,
        "order_status": "completed",
        "delivered_at": delivered.isoformat(),
        "order_created_at": created.isoformat(),
    }
    return qualify(
        owner,
        provider,
        order,
        payments[0],
        cutoff=cutoff,
    )


async def prepare_order_recognition(
    db,
    *,
    owner: str,
    evidence_id: str,
) -> dict[str, Any]:
    evidence = await db.mz2_salla_order_evidence.find_one(
        {"user_id": owner, "id": evidence_id},
        {"_id": 0},
    )
    if not evidence:
        raise EvidenceError("order_evidence_missing")
    if evidence.get("conflict"):
        raise EvidenceError("order_evidence_conflict")
    if evidence.get("status") not in READY_STATES:
        raise EvidenceError("order_evidence_not_ready")

    provider = str(evidence.get("accounting_provider") or "")
    if provider not in PROVIDERS:
        raise EvidenceError("order_provider_not_recognizable")
    if evidence.get("order_status") != "تم التوصيل":
        raise EvidenceError("fulfilment_not_proven")
    if not evidence.get("delivery_source_text"):
        raise EvidenceError("fulfilment_timestamp_required")
    await _unique_payment_reference(db, owner, evidence)

    cutoff = await _cutover(db, owner)
    event = (
        _salla_event(owner, evidence, cutoff=cutoff)
        if provider == "salla"
        else await _provider_event(
            db,
            owner=owner,
            evidence=evidence,
            provider=provider,
            cutoff=cutoff,
        )
    )

    economic = {
        key: event[key]
        for key in (
            "kind",
            "provider",
            "provider_payment_id",
            "canonical_event_id",
            "order_number",
            "amount",
            "sale_amount",
            "currency",
            "recognized_at",
        )
    }
    event_key = digest([owner, event["idempotency_key"]])
    economic_hash = digest(economic)
    prior = await db.mz2_recognition_events.find_one(
        {"_id": event_key, "user_id": owner}
    )
    if prior:
        if prior.get("economic_hash") != economic_hash:
            raise EvidenceError("previous_recognition_source_conflict")
        if prior.get("status") != "posted":
            raise EvidenceError("previous_post_result_requires_recovery")
        proposal = dict(prior.get("proposal") or {})
        return {
            **proposal,
            "state": "already_posted",
            "txn_group_id": prior["txn_group_id"],
            "evidence_status": evidence.get("status"),
        }

    existing = await db.general_ledger.find_one(
        {
            "user_id": owner,
            "status": {"$in": ["posted", "reversed"]},
            "$or": [
                {"metadata.idempotency_key": event["idempotency_key"]},
                {
                    "metadata.order_reference_id": event["order_number"],
                    "entry_type": {"$ne": "bnpl_refund"},
                },
                {
                    "metadata.order_number": event["order_number"],
                    "entry_type": {"$ne": "bnpl_refund"},
                },
                {"metadata.salla_order_evidence_id": evidence["id"]},
            ],
        }
    )
    if existing:
        raise EvidenceError("existing_journal_requires_review")

    source_order = {
        "tax_amount": evidence.get("source_tax_sar"),
        "original_currency": evidence.get("original_currency"),
        "original_amount": evidence.get("original_amount"),
    }
    tax = sale_snapshot(await read_policy(db, owner), event, source_order)
    proposal = {
        "state": "eligible",
        "event_key": event_key,
        "event": event,
        "economic_hash": economic_hash,
        "tax": tax,
        "original_key": None,
        "evidence_id": evidence["id"],
        "evidence_status": evidence.get("status"),
        "refund_pending": Decimal(str(evidence.get("refunded_sar") or "0")) > 0,
    }
    proposal["preview_hash"] = digest(proposal)
    return proposal


async def execute_order_recognition(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    evidence_id: str,
    preview_hash: str | None = None,
) -> dict[str, Any]:
    proposal = await prepare_order_recognition(db, owner=owner, evidence_id=evidence_id)
    if proposal["state"] == "already_posted":
        return proposal
    if preview_hash is not None and proposal["preview_hash"] != preview_hash:
        raise EvidenceError("preview_changed_review_again")

    async def commit(scoped):
        latest = await prepare_order_recognition(
            scoped,
            owner=owner,
            evidence_id=evidence_id,
        )
        if latest["state"] == "already_posted":
            return latest
        if latest["preview_hash"] != proposal["preview_hash"]:
            raise EvidenceError("preview_changed_review_again")

        event = latest["event"]
        tax = latest["tax"]
        provider = event["provider"]

        await read_mz2_write_balances(
            scoped,
            owner=owner,
            required_accounts=[
                ("payment_gateway", provider, "receivable"),
            ],
        )

        record = {
            "_id": latest["event_key"],
            "user_id": owner,
            "status": "posting",
            "economic_hash": latest["economic_hash"],
            "proposal": latest,
            "original_key": None,
            "actor_id": actor["id"],
        }
        await scoped.mz2_recognition_events.insert_one(record)

        entries = [
            {
                "entity_type": "payment_gateway",
                "entity_id": provider,
                "sub_account": "receivable",
                "side": "debit",
                "amount": tax["gross"],
                "entry_type": "bnpl_sale",
            },
            {
                "entity_type": "revenue",
                "entity_id": "bnpl_sales",
                "side": "credit",
                "amount": tax["net"],
                "entry_type": "bnpl_sale",
            },
        ]
        if Decimal(tax["tax"]) > 0:
            entries.append(
                {
                    "entity_type": "tax",
                    "entity_id": "sales_vat_payable",
                    "side": "credit",
                    "amount": tax["tax"],
                    "entry_type": "bnpl_sale",
                }
            )

        evidence = await scoped.mz2_salla_order_evidence.find_one(
            {"user_id": owner, "id": evidence_id}
        )
        if not evidence:
            raise EvidenceError("order_evidence_missing")
        result = await post_txn_group(
            scoped,
            user_id=owner,
            actor_id=actor["id"],
            actor_name=actor.get("name") or actor.get("email") or actor["id"],
            entries=entries,
            txn_type="bnpl_sale",
            notes=f"ميزان 2 — إثبات بيع {provider} {event['order_number']}",
            metadata={
                "operation_id": OPERATION_ID,
                "idempotency_key": event["idempotency_key"],
                "provider": provider,
                "provider_id": event["provider_payment_id"],
                "order_reference_id": event["order_number"],
                "recognition_event_key": latest["event_key"],
                "recognized_at": event["recognized_at"],
                "recognition_source": "salla_order_evidence",
                "sales_tax": tax,
                "salla_order_evidence_id": evidence_id,
                "salla_order_snapshot_id": evidence.get("latest_snapshot_id"),
                "salla_order_file_id": evidence.get("latest_file_id"),
                "salla_order_file_hash": evidence.get("latest_file_hash"),
                "payment_reference": (evidence.get("payment_reference") or {}).get("reference"),
                "original_currency": evidence.get("original_currency"),
                "original_amount": evidence.get("original_amount"),
                "current_net_sar": evidence.get("current_net_sar"),
                "refunded_sar_at_recognition": evidence.get("refunded_sar"),
            },
        )
        await scoped.mz2_recognition_events.update_one(
            {
                "_id": latest["event_key"],
                "user_id": owner,
                "status": "posting",
            },
            {
                "$set": {
                    "status": "posted",
                    "txn_group_id": result["txn_group_id"],
                }
            },
        )

        next_status = (
            "recognized_refund_pending_evidence"
            if latest["refund_pending"]
            else "recognized"
        )
        changed = await scoped.mz2_salla_order_evidence.update_one(
            {
                "user_id": owner,
                "id": evidence_id,
                "economic_hash": evidence.get("economic_hash"),
                "status": {"$in": list(READY_STATES)},
            },
            {
                "$set": {
                    "status": next_status,
                    "recognition_event_key": latest["event_key"],
                    "recognition_txn_group_id": result["txn_group_id"],
                    "recognized_at": event["recognized_at"],
                    "recognized_provider": provider,
                    "recognized_gross_sar": tax["gross"],
                    "recognized_tax_sar": tax["tax"],
                    "recognized_net_sar": tax["net"],
                    "recognized_by": actor["id"],
                    "recognition_posted_at": datetime.now(timezone.utc).isoformat(),
                }
            },
        )
        if changed.modified_count != 1:
            raise EvidenceError("order_evidence_changed_during_recognition")
        return {
            **latest,
            "state": "posted",
            "txn_group_id": result["txn_group_id"],
            "evidence_status_after": next_status,
        }

    return await atomic_owner(db, owner, commit)


class RecognizeBatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=100, ge=1, le=500)
    dry_run: bool = True


async def recognition_queue(db, *, owner: str, limit: int = 100) -> dict[str, Any]:
    candidates = await db.mz2_salla_order_evidence.find(
        {
            "user_id": owner,
            "status": {"$in": list(READY_STATES)},
            "conflict": {"$ne": True},
        },
        {"_id": 0},
    ).sort([("updated_source_text", 1), ("order_number", 1)]).limit(limit).to_list(limit)

    items = []
    for evidence in candidates:
        try:
            proposal = await prepare_order_recognition(
                db,
                owner=owner,
                evidence_id=evidence["id"],
            )
            items.append(
                {
                    "evidence_id": evidence["id"],
                    "order_number": evidence["order_number"],
                    "provider": evidence["accounting_provider"],
                    "state": proposal["state"],
                    "gross": proposal.get("tax", {}).get("gross"),
                    "tax": proposal.get("tax", {}).get("tax"),
                    "recognized_at": proposal.get("event", {}).get("recognized_at"),
                    "refund_pending": proposal.get("refund_pending", False),
                    "reasons": [],
                }
            )
        except (EvidenceError, TaxError) as exc:
            items.append(
                {
                    "evidence_id": evidence["id"],
                    "order_number": evidence["order_number"],
                    "provider": evidence.get("accounting_provider"),
                    "state": "waiting",
                    "reasons": [str(exc)],
                }
            )
    return {
        "candidate_count": len(candidates),
        "ready_count": sum(1 for item in items if item["state"] in {"eligible", "already_posted"}),
        "waiting_count": sum(1 for item in items if item["state"] == "waiting"),
        "items": items,
    }


async def recognize_batch(
    db,
    *,
    owner: str,
    actor: dict[str, Any],
    limit: int,
    dry_run: bool,
) -> dict[str, Any]:
    queue = await recognition_queue(db, owner=owner, limit=limit)
    if dry_run:
        return {**queue, "dry_run": True, "posted_count": 0}

    results = []
    for item in queue["items"]:
        if item["state"] not in {"eligible", "already_posted"}:
            results.append(item)
            continue
        try:
            posted = await execute_order_recognition(
                db,
                owner=owner,
                actor=actor,
                evidence_id=item["evidence_id"],
            )
            results.append(
                {
                    **item,
                    "state": posted["state"],
                    "txn_group_id": posted.get("txn_group_id"),
                    "evidence_status_after": posted.get("evidence_status_after"),
                }
            )
        except (EvidenceError, TaxError, HTTPException) as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
            results.append(
                {
                    **item,
                    "state": "blocked",
                    "reasons": [detail],
                }
            )

    return {
        "dry_run": False,
        "candidate_count": queue["candidate_count"],
        "posted_count": sum(1 for item in results if item["state"] == "posted"),
        "already_posted_count": sum(1 for item in results if item["state"] == "already_posted"),
        "blocked_count": sum(1 for item in results if item["state"] in {"waiting", "blocked"}),
        "items": results,
    }


def install_order_recognition_routes(router, db, current_user) -> None:
    base = "/accounting-module/order-recognition"

    async def scope(user: dict[str, Any], permission: str):
        actor = await fresh_accounting_user(db, user)
        require_accounting_permission(actor, permission)
        owner = accounting_owner_id(actor)
        if not owner:
            raise HTTPException(403, "accounting_owner_scope_missing")
        return actor, owner

    @router.get(base + "/queue")
    async def queue(limit: int = 100, user: dict = Depends(current_user)):
        _, owner = await scope(user, "accounting.settlements.view")
        return await recognition_queue(db, owner=owner, limit=min(max(limit, 1), 500))

    @router.post(base + "/recognize-ready")
    async def recognize(payload: RecognizeBatchIn, user: dict = Depends(current_user)):
        actor, owner = await scope(
            user,
            "accounting.settlements.view" if payload.dry_run else "accounting.receivables.post",
        )
        return await recognize_batch(
            db,
            owner=owner,
            actor=actor,
            limit=payload.limit,
            dry_run=payload.dry_run,
        )
