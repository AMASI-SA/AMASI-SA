#!/usr/bin/env python3
"""Guarded cleanup for the experimental Mezan V2 supplier "ابو جبل".

Dry-run is the default. Production deletion requires BOTH --execute and the
exact confirmation token. The script refuses to delete the supplier when it
finds any non-experiment supplier invoice or any general-ledger entry tied to
that supplier. Before mutation it writes a complete maintenance backup snapshot
into MongoDB so the cleanup is auditable/recoverable.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / "backend" / ".env")

SUPPLIERS = "mezan_suppliers_v2"
SUPPLIER_AUDIT = "mezan_supplier_audit_v2"
INVOICES = "mezan_supplier_invoices_v2"
SESSIONS = "mezan_supplier_receiving_sessions_v1"
RECEIVING_EVENTS = "mezan_supplier_receiving_events_v1"
SHARE_EVIDENCE = "mezan_supplier_invoice_share_evidence_v1"
DISPATCHES = "mezan_supplier_dispatches_v1"
DISPATCH_EVENTS = "mezan_supplier_dispatch_events_v1"
PIECES = "mezan_preparation_pieces_v1"
PIECE_EVENTS = "mezan_preparation_piece_events_v1"
BACKUPS = "maintenance_cleanup_backups"
CONFIRM_TOKEN = "DELETE-ABU-JABAL-TEST-DATA"
DEFAULT_NAME = "ابو جبل"

SUPPLIER_FIELDS_TO_UNSET = {
    "supplier_id": "", "supplier_name": "", "supplier_service_ids": "",
    "supplier_service_link_status": "", "supplier_reassigned_from_id": "",
    "supplier_reassigned_from_name": "", "supplier_reassigned_at": "",
    "supplier_reassigned_by_id": "", "supplier_reassigned_by_name": "",
    "supplier_reassignment_session_id": "", "supplier_assignment_mode": "",
    "supplier_assigned_at_receipt": "", "supplier_assigned_from_id": "",
    "supplier_assigned_from_name": "", "supplier_assigned_at": "",
    "supplier_assigned_by_id": "", "supplier_assigned_by_name": "",
    "supplier_assignment_session_id": "", "supplier_receiving_session_id": "",
    "supplier_receiving_reference": "", "supplier_receiving_scanned_barcode": "",
    "supplier_dispatch_id": "", "supplier_dispatch_reference": "",
    "supplier_dispatch_status": "",
}


def _norm(value: Any) -> str:
    text = str(value or "").strip().casefold()
    text = text.replace("أ", "ا").replace("إ", "ا").replace("آ", "ا")
    return re.sub(r"\s+", " ", text)


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items() if k != "_id"}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    return value


async def _rows(db: Any, collection: str, query: dict[str, Any], limit: int = 10000) -> list[dict[str, Any]]:
    return await db[collection].find(query, {"_id": 0}).limit(limit).to_list(limit)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--supplier-name", default=DEFAULT_NAME)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()

    mongo_url = os.environ.get("MONGO_URL", "").strip()
    db_name = os.environ.get("DB_NAME", "").strip()
    if not mongo_url or not db_name:
        raise SystemExit("MONGO_URL/DB_NAME are required")

    client = AsyncIOMotorClient(mongo_url)
    db = client[db_name]

    candidates = await db[SUPPLIERS].find({}, {"_id": 0}).to_list(5000)
    matches = [row for row in candidates if _norm(row.get("company_name")) == _norm(args.supplier_name)]
    if len(matches) != 1:
        print(json.dumps({
            "ok": False,
            "reason": "supplier_match_must_be_exactly_one",
            "matches": [{"id": r.get("id"), "company_name": r.get("company_name"), "user_id": r.get("user_id")} for r in matches],
        }, ensure_ascii=False, indent=2, default=str))
        return 2

    supplier = matches[0]
    supplier_id = str(supplier.get("id") or "").strip()
    user_id = str(supplier.get("user_id") or "").strip()
    if not supplier_id or not user_id:
        raise SystemExit("supplier is missing id/user_id")

    experiment_invoices = await _rows(db, INVOICES, {"user_id": user_id, "supplier_id": supplier_id, "experiment_mode": True})
    real_invoices = await _rows(db, INVOICES, {"user_id": user_id, "supplier_id": supplier_id, "experiment_mode": {"$ne": True}})
    invoice_ids = [str(r.get("id")) for r in experiment_invoices if r.get("id")]
    ledger_rows = await _rows(db, "general_ledger", {"user_id": user_id, "$or": [
        {"entity_id": supplier_id}, {"metadata.supplier_id": supplier_id},
        {"metadata.supplier_v2_id": supplier_id},
        {"metadata.supplier_invoice_v2_id": {"$in": invoice_ids or ["__none__"]}},
    ]})

    sessions = await _rows(db, SESSIONS, {"user_id": user_id, "supplier_id": supplier_id})
    session_ids = [str(r.get("id")) for r in sessions if r.get("id")]
    dispatches = await _rows(db, DISPATCHES, {"user_id": user_id, "supplier_id": supplier_id})
    dispatch_ids = [str(r.get("id")) for r in dispatches if r.get("id")]
    receiving_events = await _rows(db, RECEIVING_EVENTS, {"user_id": user_id, "$or": [
        {"supplier_id": supplier_id}, {"session_id": {"$in": session_ids or ["__none__"]}},
        {"supplier_receiving_session_id": {"$in": session_ids or ["__none__"]}},
    ]})
    share_evidence = await _rows(db, SHARE_EVIDENCE, {"user_id": user_id, "$or": [
        {"supplier_id": supplier_id}, {"invoice_id": {"$in": invoice_ids or ["__none__"]}},
        {"supplier_invoice_id": {"$in": invoice_ids or ["__none__"]}},
    ]})
    dispatch_events = await _rows(db, DISPATCH_EVENTS, {"user_id": user_id, "$or": [
        {"supplier_id": supplier_id}, {"dispatch_id": {"$in": dispatch_ids or ["__none__"]}},
    ]})
    pieces = await _rows(db, PIECES, {"user_id": user_id, "$or": [
        {"supplier_id": supplier_id}, {"supplier_receiving_history.supplier_id": supplier_id},
        {"services.completed_by_supplier_id": supplier_id},
        {"supplier_receiving_session_id": {"$in": session_ids or ["__none__"]}},
        {"supplier_dispatch_id": {"$in": dispatch_ids or ["__none__"]}},
    ]})
    piece_ids = [str(r.get("piece_id") or r.get("id")) for r in pieces if r.get("piece_id") or r.get("id")]
    piece_events = await _rows(db, PIECE_EVENTS, {"user_id": user_id, "$or": [
        {"supplier_id": supplier_id}, {"piece_id": {"$in": piece_ids or ["__none__"]}},
        {"supplier_invoice_id": {"$in": invoice_ids or ["__none__"]}},
    ]})
    supplier_audit = await _rows(db, SUPPLIER_AUDIT, {"user_id": user_id, "supplier_id": supplier_id})

    blocked = bool(real_invoices or ledger_rows)
    print(json.dumps({
        "ok": not blocked,
        "mode": "execute" if args.execute else "dry_run",
        "supplier": {"id": supplier_id, "company_name": supplier.get("company_name"), "user_id": user_id},
        "blockers": {"real_invoices": len(real_invoices), "general_ledger_entries": len(ledger_rows)},
        "eligible_test_records": {
            "experiment_invoices": len(experiment_invoices), "receiving_sessions": len(sessions),
            "receiving_events": len(receiving_events), "invoice_share_evidence": len(share_evidence),
            "supplier_dispatches": len(dispatches), "supplier_dispatch_events": len(dispatch_events),
            "preparation_pieces_to_unassign": len(pieces), "piece_events_related": len(piece_events),
            "supplier_audit_rows": len(supplier_audit), "supplier_record": 1,
        },
        "will_never_delete_product_catalog": True,
        "will_never_delete_salla_products": True,
    }, ensure_ascii=False, indent=2, default=str))

    if blocked:
        print("REFUSED: real supplier financial data exists; no mutation performed.")
        return 3
    if not args.execute:
        print("DRY_RUN_ONLY")
        return 0
    if args.confirm != CONFIRM_TOKEN:
        print("REFUSED: incorrect or missing confirmation token.")
        return 4

    backup_id = f"cleanup_{uuid.uuid4().hex}"
    await db[BACKUPS].insert_one({
        "id": backup_id, "type": "experimental_supplier_cleanup",
        "created_at": datetime.now(timezone.utc), "supplier_id": supplier_id,
        "supplier_name": supplier.get("company_name"), "user_id": user_id,
        "snapshot": _jsonable({
            "supplier": supplier, "experiment_invoices": experiment_invoices,
            "sessions": sessions, "receiving_events": receiving_events,
            "share_evidence": share_evidence, "dispatches": dispatches,
            "dispatch_events": dispatch_events, "pieces": pieces,
            "piece_events": piece_events, "supplier_audit": supplier_audit,
        }),
    })

    await db[SHARE_EVIDENCE].delete_many({"user_id": user_id, "$or": [
        {"supplier_id": supplier_id}, {"invoice_id": {"$in": invoice_ids or ["__none__"]}},
        {"supplier_invoice_id": {"$in": invoice_ids or ["__none__"]}},
    ]})
    await db[RECEIVING_EVENTS].delete_many({"user_id": user_id, "$or": [
        {"supplier_id": supplier_id}, {"session_id": {"$in": session_ids or ["__none__"]}},
        {"supplier_receiving_session_id": {"$in": session_ids or ["__none__"]}},
    ]})
    await db[SESSIONS].delete_many({"user_id": user_id, "supplier_id": supplier_id})
    await db[DISPATCH_EVENTS].delete_many({"user_id": user_id, "$or": [
        {"supplier_id": supplier_id}, {"dispatch_id": {"$in": dispatch_ids or ["__none__"]}},
    ]})
    await db[DISPATCHES].delete_many({"user_id": user_id, "supplier_id": supplier_id})
    await db[INVOICES].delete_many({"user_id": user_id, "supplier_id": supplier_id, "experiment_mode": True})

    selector = {"user_id": user_id, "$or": [
        {"supplier_id": supplier_id}, {"supplier_receiving_history.supplier_id": supplier_id},
        {"services.completed_by_supplier_id": supplier_id},
        {"supplier_receiving_session_id": {"$in": session_ids or ["__none__"]}},
        {"supplier_dispatch_id": {"$in": dispatch_ids or ["__none__"]}},
    ]}
    await db[PIECES].update_many(selector, {
        "$unset": SUPPLIER_FIELDS_TO_UNSET,
        "$pull": {"supplier_receiving_history": {"$or": [
            {"supplier_id": supplier_id}, {"invoice_id": {"$in": invoice_ids or ["__none__"]}},
        ]}},
        "$set": {"updated_at": datetime.now(timezone.utc), "test_supplier_cleanup_id": backup_id},
    })
    if invoice_ids:
        await db[PIECES].update_many(
            {"user_id": user_id, "services.supplier_invoice_id": {"$in": invoice_ids}},
            {"$unset": {
                "services.$[svc].completed_by_supplier_id": "",
                "services.$[svc].completed_by_supplier_name": "",
                "services.$[svc].supplier_invoice_id": "",
                "services.$[svc].supplier_unit_price_halalas": "",
            }},
            array_filters=[{"svc.supplier_invoice_id": {"$in": invoice_ids}}],
        )

    await db[PIECE_EVENTS].delete_many({"user_id": user_id, "$or": [
        {"supplier_id": supplier_id}, {"supplier_invoice_id": {"$in": invoice_ids or ["__none__"]}},
    ]})
    await db[SUPPLIER_AUDIT].delete_many({"user_id": user_id, "supplier_id": supplier_id})
    await db[SUPPLIERS].delete_one({"user_id": user_id, "id": supplier_id})

    remaining = {
        "supplier": await db[SUPPLIERS].count_documents({"user_id": user_id, "id": supplier_id}),
        "experiment_invoices": await db[INVOICES].count_documents({"user_id": user_id, "supplier_id": supplier_id, "experiment_mode": True}),
        "sessions": await db[SESSIONS].count_documents({"user_id": user_id, "supplier_id": supplier_id}),
        "dispatches": await db[DISPATCHES].count_documents({"user_id": user_id, "supplier_id": supplier_id}),
        "pieces_still_assigned": await db[PIECES].count_documents({"user_id": user_id, "supplier_id": supplier_id}),
    }
    print(json.dumps({
        "ok": all(v == 0 for v in remaining.values()), "executed": True,
        "backup_id": backup_id, "remaining": remaining,
    }, ensure_ascii=False, indent=2, default=str))
    return 0 if all(v == 0 for v in remaining.values()) else 5


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
