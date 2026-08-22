"""Compatibility wrapper adding a guarded one-off supplier purge endpoint.

Python imports a package before a sibling module of the same name. This wrapper
loads the existing ``mezan_supplier_management_routes.py`` implementation under
an internal alias, re-exports its public contract, and augments its router with
one tightly-scoped maintenance endpoint for the confirmed test supplier
«ابو جبل».

The endpoint is deliberately one-off and fail-closed:
- exact supplier id and company name are both required;
- the owner-level suppliers.manage permission is required;
- an exact confirmation token is required;
- a full Mongo backup snapshot is written before any mutation;
- Product V2 / Salla catalog products are never deleted.
"""
from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

_BASE_PATH = Path(__file__).resolve().parent.parent / "mezan_supplier_management_routes.py"
_SPEC = importlib.util.spec_from_file_location(
    "_mezan_supplier_management_routes_base",
    _BASE_PATH,
)
if _SPEC is None or _SPEC.loader is None:  # pragma: no cover - boot hard-fail
    raise ImportError("unable_to_load_mezan_supplier_management_routes_base")
_BASE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_BASE)

# Re-export the existing module contract used elsewhere in the backend.
MEZAN_SUPPLIERS_V2 = _BASE.MEZAN_SUPPLIERS_V2
MEZAN_SUPPLIER_AUDIT_V2 = _BASE.MEZAN_SUPPLIER_AUDIT_V2
MEZAN_SUPPLIER_INVOICES_V2 = _BASE.MEZAN_SUPPLIER_INVOICES_V2
MezanSupplierWriteRequest = _BASE.MezanSupplierWriteRequest
SUPPLIERS_MANAGE_PERMISSION = _BASE.SUPPLIERS_MANAGE_PERMISSION
SUPPLIERS_READ_PERMISSION = _BASE.SUPPLIERS_READ_PERMISSION
ensure_mezan_supplier_indexes = _BASE.ensure_mezan_supplier_indexes

TARGET_SUPPLIER_ID = "msv2_e0e83c814a12460295d1b3d539fbcfd5"
TARGET_SUPPLIER_NAME = "ابو جبل"
CONFIRM_TOKEN = "DELETE-ABU-JABAL-ALL-DATA"

SESSIONS = "mezan_supplier_receiving_sessions_v1"
RECEIVING_EVENTS = "mezan_supplier_receiving_events_v1"
SHARE_EVIDENCE = "mezan_supplier_invoice_share_evidence_v1"
DISPATCHES = "mezan_supplier_dispatches_v1"
DISPATCH_EVENTS = "mezan_supplier_dispatch_events_v1"
PIECES = "mezan_preparation_pieces_v1"
PIECE_EVENTS = "mezan_preparation_piece_events_v1"
BACKUPS = "maintenance_cleanup_backups"

SUPPLIER_FIELDS_TO_UNSET = {
    "supplier_id": "",
    "supplier_name": "",
    "supplier_service_ids": "",
    "supplier_service_link_status": "",
    "supplier_reassigned_from_id": "",
    "supplier_reassigned_from_name": "",
    "supplier_reassigned_at": "",
    "supplier_reassigned_by_id": "",
    "supplier_reassigned_by_name": "",
    "supplier_reassignment_session_id": "",
    "supplier_assignment_mode": "",
    "supplier_assigned_at_receipt": "",
    "supplier_assigned_from_id": "",
    "supplier_assigned_from_name": "",
    "supplier_assigned_at": "",
    "supplier_assigned_by_id": "",
    "supplier_assigned_by_name": "",
    "supplier_assignment_session_id": "",
    "supplier_receiving_session_id": "",
    "supplier_receiving_reference": "",
    "supplier_receiving_scanned_barcode": "",
    "supplier_dispatch_id": "",
    "supplier_dispatch_reference": "",
    "supplier_dispatch_status": "",
}


class SupplierPurgeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: str = Field(min_length=1, max_length=80)
    expected_company_name: str = Field(min_length=1, max_length=120)


async def _rows(
    db: Any,
    collection: str,
    query: dict[str, Any],
    limit: int = 10000,
) -> list[dict[str, Any]]:
    return await db[collection].find(query, {"_id": 0}).limit(limit).to_list(limit)


def make_mezan_supplier_management_router(db: Any, current_user: Any):
    router = _BASE.make_mezan_supplier_management_router(db, current_user)

    @router.post("/{supplier_id}/purge", include_in_schema=False)
    async def purge_confirmed_test_supplier(
        supplier_id: str,
        payload: SupplierPurgeRequest,
        user: dict = Depends(current_user),
    ) -> dict[str, Any]:
        context = await _BASE._actor_context(db, user)
        _BASE._require_permission(context, SUPPLIERS_MANAGE_PERMISSION)
        merchant_id = context["merchant_id"]
        actor_id = context["actor_id"]

        if supplier_id != TARGET_SUPPLIER_ID:
            raise HTTPException(
                status_code=403,
                detail={"code": "supplier_purge_target_not_allowed"},
            )
        if payload.confirmation != CONFIRM_TOKEN:
            raise HTTPException(
                status_code=422,
                detail={"code": "supplier_purge_confirmation_invalid"},
            )
        if _BASE._normalized(payload.expected_company_name) != _BASE._normalized(
            TARGET_SUPPLIER_NAME
        ):
            raise HTTPException(
                status_code=422,
                detail={"code": "supplier_purge_expected_name_invalid"},
            )

        supplier_selector = {"user_id": merchant_id, "id": supplier_id}
        supplier = await db[MEZAN_SUPPLIERS_V2].find_one(
            supplier_selector,
            {"_id": 0},
        )
        if not supplier:
            raise HTTPException(
                status_code=404,
                detail={"code": "mezan_supplier_not_found"},
            )
        if _BASE._normalized(supplier.get("company_name")) != _BASE._normalized(
            TARGET_SUPPLIER_NAME
        ):
            raise HTTPException(
                status_code=409,
                detail={"code": "supplier_purge_name_mismatch"},
            )

        invoices = await _rows(
            db,
            MEZAN_SUPPLIER_INVOICES_V2,
            {"user_id": merchant_id, "supplier_id": supplier_id},
        )
        invoice_ids = [str(row.get("id")) for row in invoices if row.get("id")]

        ledger_query = {
            "user_id": merchant_id,
            "$or": [
                {"entity_id": supplier_id},
                {"metadata.supplier_id": supplier_id},
                {"metadata.supplier_v2_id": supplier_id},
                {
                    "metadata.supplier_invoice_v2_id": {
                        "$in": invoice_ids or ["__none__"]
                    }
                },
            ],
        }
        ledger_rows = await _rows(db, "general_ledger", ledger_query)

        sessions = await _rows(
            db,
            SESSIONS,
            {"user_id": merchant_id, "supplier_id": supplier_id},
        )
        session_ids = [str(row.get("id")) for row in sessions if row.get("id")]
        dispatches = await _rows(
            db,
            DISPATCHES,
            {"user_id": merchant_id, "supplier_id": supplier_id},
        )
        dispatch_ids = [str(row.get("id")) for row in dispatches if row.get("id")]

        receiving_query = {
            "user_id": merchant_id,
            "$or": [
                {"supplier_id": supplier_id},
                {"session_id": {"$in": session_ids or ["__none__"]}},
                {
                    "supplier_receiving_session_id": {
                        "$in": session_ids or ["__none__"]
                    }
                },
            ],
        }
        evidence_query = {
            "user_id": merchant_id,
            "$or": [
                {"supplier_id": supplier_id},
                {"invoice_id": {"$in": invoice_ids or ["__none__"]}},
                {
                    "supplier_invoice_id": {
                        "$in": invoice_ids or ["__none__"]
                    }
                },
            ],
        }
        dispatch_event_query = {
            "user_id": merchant_id,
            "$or": [
                {"supplier_id": supplier_id},
                {"dispatch_id": {"$in": dispatch_ids or ["__none__"]}},
            ],
        }
        piece_query = {
            "user_id": merchant_id,
            "$or": [
                {"supplier_id": supplier_id},
                {"supplier_receiving_history.supplier_id": supplier_id},
                {"services.completed_by_supplier_id": supplier_id},
                {
                    "services.supplier_invoice_id": {
                        "$in": invoice_ids or ["__none__"]
                    }
                },
                {
                    "supplier_receiving_session_id": {
                        "$in": session_ids or ["__none__"]
                    }
                },
                {
                    "supplier_dispatch_id": {
                        "$in": dispatch_ids or ["__none__"]
                    }
                },
            ],
        }
        piece_event_query = {
            "user_id": merchant_id,
            "$or": [
                {"supplier_id": supplier_id},
                {
                    "supplier_invoice_id": {
                        "$in": invoice_ids or ["__none__"]
                    }
                },
                {
                    "supplier_receiving_session_id": {
                        "$in": session_ids or ["__none__"]
                    }
                },
                {
                    "supplier_dispatch_id": {
                        "$in": dispatch_ids or ["__none__"]
                    }
                },
            ],
        }

        receiving_events = await _rows(db, RECEIVING_EVENTS, receiving_query)
        share_evidence = await _rows(db, SHARE_EVIDENCE, evidence_query)
        dispatch_events = await _rows(db, DISPATCH_EVENTS, dispatch_event_query)
        pieces = await _rows(db, PIECES, piece_query)
        piece_events = await _rows(db, PIECE_EVENTS, piece_event_query)
        supplier_audit = await _rows(
            db,
            MEZAN_SUPPLIER_AUDIT_V2,
            {"user_id": merchant_id, "supplier_id": supplier_id},
        )

        backup_id = f"cleanup_{uuid.uuid4().hex}"
        await db[BACKUPS].insert_one(
            {
                "id": backup_id,
                "type": "abu_jabal_full_supplier_cleanup",
                "created_at": datetime.now(timezone.utc),
                "actor_id": actor_id,
                "supplier_id": supplier_id,
                "supplier_name": supplier.get("company_name"),
                "user_id": merchant_id,
                "snapshot": {
                    "supplier": supplier,
                    "invoices": invoices,
                    "general_ledger": ledger_rows,
                    "sessions": sessions,
                    "receiving_events": receiving_events,
                    "share_evidence": share_evidence,
                    "dispatches": dispatches,
                    "dispatch_events": dispatch_events,
                    "pieces": pieces,
                    "piece_events": piece_events,
                    "supplier_audit": supplier_audit,
                },
            }
        )

        deleted = {}
        deleted["share_evidence"] = (
            await db[SHARE_EVIDENCE].delete_many(evidence_query)
        ).deleted_count
        deleted["receiving_events"] = (
            await db[RECEIVING_EVENTS].delete_many(receiving_query)
        ).deleted_count
        deleted["receiving_sessions"] = (
            await db[SESSIONS].delete_many(
                {"user_id": merchant_id, "supplier_id": supplier_id}
            )
        ).deleted_count
        deleted["dispatch_events"] = (
            await db[DISPATCH_EVENTS].delete_many(dispatch_event_query)
        ).deleted_count
        deleted["dispatches"] = (
            await db[DISPATCHES].delete_many(
                {"user_id": merchant_id, "supplier_id": supplier_id}
            )
        ).deleted_count
        deleted["general_ledger"] = (
            await db.general_ledger.delete_many(ledger_query)
        ).deleted_count
        deleted["invoices"] = (
            await db[MEZAN_SUPPLIER_INVOICES_V2].delete_many(
                {"user_id": merchant_id, "supplier_id": supplier_id}
            )
        ).deleted_count

        pieces_updated = (
            await db[PIECES].update_many(
                piece_query,
                {
                    "$unset": SUPPLIER_FIELDS_TO_UNSET,
                    "$pull": {
                        "supplier_receiving_history": {
                            "$or": [
                                {"supplier_id": supplier_id},
                                {
                                    "invoice_id": {
                                        "$in": invoice_ids or ["__none__"]
                                    }
                                },
                            ]
                        }
                    },
                    "$set": {
                        "updated_at": datetime.now(timezone.utc),
                        "supplier_cleanup_id": backup_id,
                    },
                },
            )
        ).modified_count

        if invoice_ids:
            await db[PIECES].update_many(
                {
                    "user_id": merchant_id,
                    "services.supplier_invoice_id": {"$in": invoice_ids},
                },
                {
                    "$unset": {
                        "services.$[svc].completed_by_supplier_id": "",
                        "services.$[svc].completed_by_supplier_name": "",
                        "services.$[svc].supplier_invoice_id": "",
                        "services.$[svc].supplier_unit_price_halalas": "",
                        "services.$[svc].completed_at": "",
                        "services.$[svc].completed_quantity": "",
                    },
                    "$set": {"services.$[svc].status": "pending"},
                },
                array_filters=[{"svc.supplier_invoice_id": {"$in": invoice_ids}}],
            )

        deleted["piece_events"] = (
            await db[PIECE_EVENTS].delete_many(piece_event_query)
        ).deleted_count
        deleted["supplier_audit"] = (
            await db[MEZAN_SUPPLIER_AUDIT_V2].delete_many(
                {"user_id": merchant_id, "supplier_id": supplier_id}
            )
        ).deleted_count
        deleted["supplier"] = (
            await db[MEZAN_SUPPLIERS_V2].delete_one(supplier_selector)
        ).deleted_count

        remaining = {
            "supplier": await db[MEZAN_SUPPLIERS_V2].count_documents(
                supplier_selector
            ),
            "invoices": await db[MEZAN_SUPPLIER_INVOICES_V2].count_documents(
                {"user_id": merchant_id, "supplier_id": supplier_id}
            ),
            "ledger": await db.general_ledger.count_documents(ledger_query),
            "sessions": await db[SESSIONS].count_documents(
                {"user_id": merchant_id, "supplier_id": supplier_id}
            ),
            "dispatches": await db[DISPATCHES].count_documents(
                {"user_id": merchant_id, "supplier_id": supplier_id}
            ),
            "pieces_still_assigned": await db[PIECES].count_documents(
                {"user_id": merchant_id, "supplier_id": supplier_id}
            ),
        }
        ok = all(int(value) == 0 for value in remaining.values())
        return {
            "ok": ok,
            "executed": True,
            "supplier_id": supplier_id,
            "supplier_name": TARGET_SUPPLIER_NAME,
            "backup_id": backup_id,
            "before": {
                "invoices": len(invoices),
                "general_ledger": len(ledger_rows),
                "receiving_sessions": len(sessions),
                "receiving_events": len(receiving_events),
                "share_evidence": len(share_evidence),
                "dispatches": len(dispatches),
                "dispatch_events": len(dispatch_events),
                "preparation_pieces": len(pieces),
                "piece_events": len(piece_events),
            },
            "deleted": deleted,
            "pieces_updated": pieces_updated,
            "remaining": remaining,
        }

    return router


__all__ = [
    "MEZAN_SUPPLIERS_V2",
    "MEZAN_SUPPLIER_AUDIT_V2",
    "MEZAN_SUPPLIER_INVOICES_V2",
    "MezanSupplierWriteRequest",
    "SUPPLIERS_MANAGE_PERMISSION",
    "SUPPLIERS_READ_PERMISSION",
    "ensure_mezan_supplier_indexes",
    "make_mezan_supplier_management_router",
]
