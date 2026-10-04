"""Authenticated, write-free projection adapters. No financial writers imported."""
from fastapi import Depends, HTTPException
from pydantic import BaseModel, Field
from supplier_invoice_display import project_supplier_invoice_display


class DisplayRequest(BaseModel):
    lines: list[dict] = Field(max_length=5000)
    pieces: list[dict] = Field(max_length=5000)
    expected_total_halalas: int | None = Field(default=None, ge=0, le=2**53-1)


def register_display_routes(router, db, current_user, actor_context, require_permission, permission):
    @router.post("/display-groups")
    async def display_groups(payload: DisplayRequest, user: dict = Depends(current_user)):
        context = await actor_context(db, user)
        require_permission(context, permission)
        try:
            display = project_supplier_invoice_display(payload.lines, payload.pieces,
                expected_total_halalas=payload.expected_total_halalas)
        except (ValueError, TypeError, AttributeError) as exc:
            code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith("supplier_display_") else "supplier_display_input_invalid"
            raise HTTPException(422, detail={"code":code}) from None
        return {"ok":True, "display":display}


async def load_invoice_display(db, invoice, merchant_id):
    """Use finalized event snapshots, never current product prices or line[0]."""
    ids = [key for line in invoice.get("lines", []) for key in line.get("piece_ids", [])]
    if not ids:
        raise ValueError("supplier_display_piece_identity_missing")
    fields = {name:1 for name in (
        "piece_id", "product_id", "product_name", "sku", "variant_id", "salla_variant_id",
        "order_item_id", "order_number", "unit_index", "batch_id", "file_number",
        "product_options", "product_options_snapshot", "options", "specifications",
        "customer_service_instructions", "service_specifications", "services", "invoice_services",
        "selected_image_url", "reference_product_unit_price_halalas", "reference_product_option_cost_halalas",
    )}
    fields["_id"] = 0
    rows = await db["mezan_supplier_receiving_events_v1"].find({
        "user_id":merchant_id, "session_id":invoice.get("session_id"),
        "supplier_invoice_id":invoice.get("id"), "piece_id":{"$in":ids},
        "event_type":{"$in":["supplier_piece_service_recorded", "supplier_piece_service_simulated"]},
    }, fields).to_list(length=len(ids)+1)
    return project_supplier_invoice_display(invoice.get("lines", []), rows,
        expected_total_halalas=invoice.get("total_halalas"))
