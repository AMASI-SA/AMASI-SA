"""Execution adapters for the shared operational hold fence.

Decorators are applied at definition time so mobile imports and installed
preparation wrappers retain the same guarded function. Provider I/O remains
outside Mongo transactions; the service owns the durable execution claim.
"""
from functools import wraps
from inspect import signature

from fastapi import HTTPException


def target(row):
    result = {key: str(row.get(key) or "").strip()
              for key in ("order_number", "order_item_id", "piece_id")}
    if result["piece_id"]:
        from fulfillment_lifecycle import piece_generation
        result.update(expected_revision=row.get("revision") or 0,
                      expected_generation=piece_generation(row))
    return result


async def _targets(db, owner, arguments, kind):
    from order_review_routes import WORKFLOWS
    if kind == "order":
        return [{"order_number": str(arguments["order_number"]).strip()}]
    if kind == "auto_route":
        return [{"order_number": str(arguments["order"].order_number).strip()}]
    if kind == "batch":
        return [{"order_number": str(number).strip()}
                for number in arguments["batch"].get("order_numbers") or []]
    if kind == "assignment":
        from preparation_piece_operations import BATCHES
        batch = await db[BATCHES].find_one({"user_id": owner,
            "client_request_id": arguments["client_request_id"]}) or {}
        return await _targets(db, owner, {"registry": {"batch_id": batch.get("id")}}, "file")
    if kind == "piece":
        from preparation_piece_operations import PIECES
        piece_id = str(arguments["piece_id"]).strip().lower()
        piece = await db[PIECES].find_one({"user_id": owner,
            "$or": [{"piece_id": piece_id}, {"id": piece_id}]})
        if piece:
            selected = target(piece)
            for key in ("expected_revision", "expected_generation"):
                if arguments.get(key) is not None:
                    selected[key] = arguments[key]
            return [selected]
        workflow = await db[WORKFLOWS].find_one({"user_id": owner, "$or": [
            {"operational_items.operational_item_id": piece_id},
            {"items.direct_assembly_piece_ids": piece_id}]})
        if workflow:
            from fulfillment_lifecycle import virtual_pieces
            virtual = next((row for row in virtual_pieces(workflow)
                if row.get("piece_id") == piece_id), None)
            if virtual is None:
                raise HTTPException(404, detail={"code": "fulfillment_piece_not_found"})
            selected = target(virtual)
            for key in ("expected_revision", "expected_generation"):
                if arguments.get(key) is not None:
                    selected[key] = arguments[key]
            return [selected]
        raise HTTPException(404, detail={"code": "fulfillment_piece_not_found"})
    if kind == "file":
        from preparation_piece_operations import BATCHES, PIECES
        batch_id = str(arguments["registry"].get("batch_id") or "").strip()
        batch = await db[BATCHES].find_one({"user_id": owner, "id": batch_id}) or {}
        rows = await db[PIECES].find({"user_id": owner, "batch_id": batch_id}).to_list(50001)
        if len(rows) > 50000:
            raise HTTPException(409, detail={"code": "fulfillment_lifecycle_target_limit"})
        # Batch lines protect not-yet-materialized units; durable piece ids
        # protect existing piece-only stops as well.
        return [target(row) for row in list(batch.get("lines") or []) + rows if row.get("order_number")]
    if kind == "carrier_scan":
        from carrier_handoff import normalize_shipping_barcode
        row = await db[WORKFLOWS].find_one({"user_id": owner,
            "carrier_label_barcode": normalize_shipping_barcode(arguments["scanned_barcode"]),
            "carrier_label_print_confirmed": True})
        return [{"order_number": row["order_number"]}] if row else []
    raise ValueError("unknown lifecycle execution target")


def guarded_execution(kind, *, defer=False):
    """Hold execution claims across the original function, including awaits."""
    def decorate(function):
        contract = signature(function)

        @wraps(function)
        async def guarded(*args, **kwargs):
            from fulfillment_lifecycle import execution_scope, guarded_owner
            arguments = contract.bind(*args, **kwargs).arguments
            db, owner = arguments["db"], str(arguments["user_id"])
            if not await guarded_owner(db, owner):
                return await function(*args, **kwargs)
            targets = await _targets(db, owner, arguments, kind)
            try:
                async with execution_scope(db, user_id=owner, targets=targets,
                                           operation=function.__name__):
                    return await function(*args, **kwargs)
            except HTTPException as exc:
                if defer and isinstance(exc.detail, dict) and exc.detail.get("code") == "fulfillment_lifecycle_held":
                    return {"promoted": False, "blocked": True, "error_code": "fulfillment_lifecycle_held"}
                raise
        return guarded
    return decorate
