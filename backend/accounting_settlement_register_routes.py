"""Searchable settlement register and auditable detail view for P01."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import Depends, HTTPException, Query

from accounting_module_contract import accounting_owner_id, require_accounting_permission
from accounting_module_status_routes import fresh_accounting_user
from accounting_settlement_service import canonical_provider

REGISTER_STATUSES = frozenset({
    "draft",
    "needs_review",
    "matched",
    "ready_for_review",
    "reviewed",
    "posting",
    "posted",
    "rejected",
    "reversed",
})


def _clean(value: Any) -> str:
    return str(value or "").strip()


def _date(value: Any):
    raw = _clean(value)[:10]
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return None


def _period_overlaps(document: dict[str, Any], start, end) -> bool:
    doc_start = _date(document.get("period_from")) or _date(document.get("statement_date"))
    doc_end = _date(document.get("period_to")) or _date(document.get("statement_date"))
    if start and doc_end and doc_end < start:
        return False
    if end and doc_start and doc_start > end:
        return False
    return True


def _matches_q(document: dict[str, Any], q: str) -> bool:
    if not q:
        return True
    haystack = " ".join((
        _clean(document.get("statement_reference")),
        _clean(document.get("provider_label")),
        _clean(document.get("provider")),
        _clean(document.get("bank_account_name")),
        _clean((document.get("source_snapshot") or {}).get("filename")),
        _clean(document.get("ledger_txn_group_id")),
    )).lower()
    return q.lower() in haystack


def _register_item(document: dict[str, Any]) -> dict[str, Any]:
    out = dict(document)
    out.pop("_id", None)
    return {
        "id": out.get("id"),
        "provider": out.get("provider"),
        "provider_label": out.get("provider_label"),
        "status": out.get("status"),
        "workflow_state": out.get("workflow_state") or out.get("status"),
        "statement_reference": out.get("statement_reference"),
        "statement_date": out.get("statement_date"),
        "period_from": out.get("period_from"),
        "period_to": out.get("period_to"),
        "currency": out.get("currency") or "SAR",
        "bank_account_id": out.get("bank_account_id"),
        "bank_account_name": out.get("bank_account_name"),
        "bank_transaction_id": out.get("bank_transaction_id"),
        "bank_transaction_difference": out.get("bank_transaction_difference"),
        "reported_net": float((out.get("amounts") or {}).get("reported_net") or 0),
        "gross_sales": float((out.get("amounts") or {}).get("gross_sales") or 0),
        "review_count": len(out.get("review_reasons") or []),
        "source_file_id": out.get("source_file_id"),
        "source_filename": (out.get("source_snapshot") or {}).get("filename"),
        "ledger_txn_group_id": out.get("ledger_txn_group_id"),
        "journal_href": (
            f"/transactions?txn_group_id={out.get('ledger_txn_group_id')}"
            if out.get("ledger_txn_group_id") else None
        ),
        "created_at": out.get("created_at"),
        "updated_at": out.get("updated_at"),
        "posted_at": out.get("posted_at"),
    }


async def _scope(db, user: dict[str, Any]) -> tuple[dict[str, Any], str]:
    actor = await fresh_accounting_user(db, user)
    require_accounting_permission(actor, "accounting.settlements.view")
    owner_id = accounting_owner_id(actor)
    if not owner_id:
        raise HTTPException(403, "لا يوجد مالك بيانات محاسبية مرتبط بالمستخدم")
    return actor, owner_id


def install_accounting_settlement_register_routes(router, db, current_user):
    @router.get("/accounting-module/settlements/register")
    async def settlement_register(
        q: Optional[str] = Query(default=None, max_length=120),
        provider: Optional[str] = Query(default=None),
        status: Optional[str] = Query(default=None),
        bank_account_id: Optional[str] = Query(default=None),
        period_from: Optional[str] = Query(default=None),
        period_to: Optional[str] = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
        user: dict = Depends(current_user),
    ):
        _actor, owner_id = await _scope(db, user)
        query: dict[str, Any] = {"user_id": owner_id}
        if provider:
            query["provider"] = canonical_provider(provider)
        if status:
            statuses = [_clean(item) for item in status.split(",") if _clean(item)]
            invalid = [item for item in statuses if item not in REGISTER_STATUSES]
            if invalid:
                raise HTTPException(400, f"حالات غير معروفة: {', '.join(invalid)}")
            expanded = set(statuses)
            if "matched" in expanded:
                expanded.add("ready_for_review")
            query["status"] = {"$in": sorted(expanded)}
        if bank_account_id:
            query["bank_account_id"] = _clean(bank_account_id)

        documents = await db.accounting_settlements_v2.find(
            query,
            {"_id": 0},
        ).sort("updated_at", -1).to_list(2000)
        start = _date(period_from)
        end = _date(period_to)
        search = _clean(q)
        filtered = [
            document for document in documents
            if _period_overlaps(document, start, end)
            and _matches_q(document, search)
        ]
        items = [_register_item(document) for document in filtered[:limit]]
        return {
            "items": items,
            "count": len(items),
            "total_filtered": len(filtered),
            "filters": {
                "q": search or None,
                "provider": provider,
                "status": status,
                "bank_account_id": bank_account_id,
                "period_from": period_from,
                "period_to": period_to,
            },
        }

    @router.get("/accounting-module/settlements/register/{draft_id}")
    async def settlement_register_detail(
        draft_id: str,
        user: dict = Depends(current_user),
    ):
        _actor, owner_id = await _scope(db, user)
        draft = await db.accounting_settlements_v2.find_one(
            {"id": draft_id, "user_id": owner_id},
            {"_id": 0},
        )
        if not draft:
            raise HTTPException(404, "تسوية السجل غير موجودة")

        source_file = None
        if draft.get("source_file_id"):
            source_file = await db.settlement_files.find_one(
                {
                    "id": draft.get("source_file_id"),
                    "user_id": owner_id,
                },
                {"_id": 0},
            )
        source_entries = []
        if draft.get("source_file_id"):
            source_entries = await db.settlement_entries.find(
                {
                    "file_id": draft.get("source_file_id"),
                    "user_id": owner_id,
                },
                {"_id": 0},
            ).sort("created_at", 1).to_list(1000)
        ledger_entries = []
        if draft.get("ledger_txn_group_id"):
            ledger_entries = await db.general_ledger.find(
                {
                    "txn_group_id": draft.get("ledger_txn_group_id"),
                    "user_id": owner_id,
                },
                {"_id": 0},
            ).sort("created_at", 1).to_list(100)

        return {
            "register_item": _register_item(draft),
            "draft": draft,
            "evidence": {
                "file": source_file,
                "entries": source_entries,
                "entry_count": len(source_entries),
                "file_locked": True,
            },
            "bank_movement": draft.get("bank_transaction_snapshot"),
            "ledger": {
                "txn_group_id": draft.get("ledger_txn_group_id"),
                "entries": ledger_entries,
                "entry_count": len(ledger_entries),
                "journal_href": (
                    f"/transactions?txn_group_id={draft.get('ledger_txn_group_id')}"
                    if draft.get("ledger_txn_group_id") else None
                ),
            },
        }

    return router


__all__ = [
    "REGISTER_STATUSES",
    "install_accounting_settlement_register_routes",
]
