"""Source checks for native supplier operations, with no parallel piece engine."""
from .binding import bound, require_bound
from .canonical_adapter import is_local_order_number
from .domain import DomainError
from .source_hooks import current_document, api_error


async def lock_preparation_sources(db, tenant_id, pieces):
    numbers=sorted({str(piece.get("order_number") or "") for piece in pieces
                    if is_local_order_number(piece.get("order_number"))})
    if not numbers:
        return
    try:
        binding=require_bound(db,write=True,tenant_id=tenant_id)
        if binding.session is None:
            raise DomainError("special_order_transaction_required")
        for number in numbers:
            document,workflow=await current_document(db,tenant_id,number,write=True,lock=True)
            if not workflow or document["stage"] not in {"reviewed","processing"}:
                raise DomainError("special_order_not_in_preparation")
    except DomainError as exc:
        raise api_error(exc) from None
