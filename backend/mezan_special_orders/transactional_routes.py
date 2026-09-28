"""Scope existing local-order mutations to a real transaction after authentication.

FastAPI resolves dependencies and validates request models before this wrapper
runs. The original endpoint still owns its permissions and workflow. No route is
added and no ordinary Salla-only request is redirected or auto-posted financially.
"""
from __future__ import annotations

from functools import wraps
import inspect
from typing import get_type_hints
from .binding import bound, transaction
from .canonical_adapter import is_local_order_number
from .domain import DomainError
from .source_hooks import api_error


async def prepare_workflow_indexes(db):
    from order_review_routes import _ensure_indexes
    from fulfillment_v2_routes import ensure_fulfillment_indexes
    from preparation_piece_operations import ensure_piece_operation_indexes
    from reviewed_preparation_batches import ensure_preparation_batch_indexes
    from store_courier_dispatch_routes import ensure_store_courier_dispatch_indexes
    from order_review_image_modes import _ensure_mezan_image_indexes
    from .ledger_adapter import ensure_indexes as ensure_financial_indexes
    for prepare in (_ensure_mezan_image_indexes, ensure_financial_indexes, _ensure_indexes, ensure_fulfillment_indexes, ensure_piece_operation_indexes,
                    ensure_preparation_batch_indexes, ensure_store_courier_dispatch_indexes):
        await prepare(db)


def _strings(value, depth=0):
    if depth > 8:
        return
    if isinstance(value, str):
        yield value
    elif hasattr(value, 'model_dump'):
        yield from _strings(value.model_dump(mode='json'), depth+1)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v, depth+1)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v, depth+1)


async def local_request(db, values):
    user = values.get('user')
    if not isinstance(user, dict):
        return False
    actor = str(user.get('id') or '')
    # This selector grants no access; the original handler performs its normal
    # authorization. It only decides whether its business writes need a session.
    tenant = actor if str(user.get('role') or '').casefold() == 'owner' else str(user.get('created_by') or actor)
    candidates = list(_strings({k:v for k,v in values.items() if k!='user'}))
    if any(is_local_order_number(n) for n in candidates):
        return True
    if not candidates:
        return False
    identities = list(dict.fromkeys(n for n in candidates if len(n)<=128))[:501]
    if len(identities)>500:
        raise DomainError('mutation_selection_limit_exceeded',422)
    from preparation_piece_operations import PIECES
    from reviewed_preparation_batches import BATCHES as PREPARATION_BATCHES
    from fulfillment_v2_routes import BATCHES as SHIPPING_BATCHES
    for collection in (PIECES, PREPARATION_BATCHES, SHIPPING_BATCHES):
        rows = await db[collection].find({'user_id':tenant,'id':{'$in':identities}},
            {'_id':0,'order_number':1,'order_numbers':1,'lines.order_number':1,'items.order_number':1}).to_list(501)
        if any(is_local_order_number(n) for row in rows for n in _strings(row)):
            return True
    return False


def bind_local_mutations(router, db):
    binding = bound(db)
    if binding is None or not binding.enablement.reads:
        return router
    for route in router.routes:
        if not getattr(route,'methods',set()).intersection({'POST','PATCH','PUT','DELETE'}):
            continue
        original = route.dependant.call
        if getattr(original,'_special_transaction_bound',False):
            continue
        def decorate(endpoint):
            @wraps(endpoint)
            async def execute(**values):
                try:
                    if not await local_request(db, values):
                        return await endpoint(**values)
                    await prepare_workflow_indexes(db)
                    async def callback(_):
                        return await endpoint(**values)
                    return await transaction(db, callback)
                except DomainError as exc:
                    raise api_error(exc) from None
            # include_router reconstructs APIRoute from endpoint, not dependant.
            # Resolve original forward annotations before handing FastAPI the
            # wrapper so its request/Depends contract is preserved exactly.
            signature=inspect.signature(endpoint)
            hints=get_type_hints(endpoint)
            execute.__signature__=signature.replace(parameters=[
                p.replace(annotation=hints.get(p.name,p.annotation)) for p in signature.parameters.values()
            ],return_annotation=hints.get('return',signature.return_annotation))
            execute._special_transaction_bound = True
            return execute
        wrapped=decorate(original)
        route.endpoint=wrapped
        route.dependant.call=wrapped
    return router
