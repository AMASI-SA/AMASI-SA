"""Scope existing local-order mutations to a real transaction after authentication.

FastAPI resolves dependencies and validates request models before this wrapper
runs. The original endpoint still owns its permissions and workflow. No route is
added and no ordinary Salla-only request is redirected or auto-posted financially.
"""
from __future__ import annotations

from functools import wraps
import inspect
from typing import get_type_hints
from .binding import bound, require_bound, transaction
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
    from preparation_file_registry import ensure_preparation_file_registry_indexes
    from store_delivery_handover_routes import ensure_store_delivery_handover_indexes
    from preparation_supplier_dispatch import ensure_supplier_dispatch_indexes
    from supplier_dispatch_share_evidence import ensure_supplier_dispatch_evidence_indexes
    from preparation_route_history import ensure_route_history_indexes
    from .ledger_adapter import ensure_indexes as ensure_financial_indexes
    for prepare in (ensure_supplier_dispatch_indexes, ensure_supplier_dispatch_evidence_indexes, ensure_route_history_indexes, ensure_store_delivery_handover_indexes, _ensure_mezan_image_indexes, ensure_financial_indexes, _ensure_indexes, ensure_preparation_file_registry_indexes, ensure_fulfillment_indexes, ensure_piece_operation_indexes,
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
    tenant = (str(user.get('_mobile_owner_id') or actor) if str(user.get('role') or '').casefold() == 'owner' or user.get('is_owner') is True
              else str(user.get('created_by') or user.get('merchant_id') or actor))
    candidates = list(_strings({k:v for k,v in values.items() if k!='user'}))
    if any(is_local_order_number(n) for n in candidates):
        return True
    if not candidates:
        return False
    identities = list(dict.fromkeys(n for n in candidates if len(n)<=128))[:501]
    if len(identities)>500:
        raise DomainError('mutation_selection_limit_exceeded',422)
    from preparation_piece_operations import PIECES
    from preparation_file_registry import REGISTRY
    from reviewed_preparation_batches import BATCHES as PREPARATION_BATCHES
    from fulfillment_v2_routes import BATCHES as SHIPPING_BATCHES
    # Pieces and files do not use the batches' `id` field. Resolve the actual
    # immutable selectors; never infer a merchant from an untrusted payload.
    from store_delivery_handover_routes import SESSIONS as HANDOVER_SESSIONS, ASSIGNMENTS
    from preparation_supplier_dispatch import DISPATCHES
    scopes = ((DISPATCHES, ('id', 'client_request_id')), (HANDOVER_SESSIONS, ('id',)), (ASSIGNMENTS, ('id',)), (PIECES, ('piece_id', 'file_number', 'batch_id')),
              (PREPARATION_BATCHES, ('id', 'client_request_id')),
              (SHIPPING_BATCHES, ('id',)),
              (REGISTRY, ('file_number', 'client_request_id', 'batch_id')))
    for collection, fields in scopes:
        rows = await db[collection].find({'user_id': tenant,
            '$or': [{key: {'$in': identities}} for key in fields]},
            {'_id': 0, 'order_number': 1, 'order_numbers': 1,
             'lines.order_number': 1, 'lines.order_numbers': 1, 'source_files.cards.order_number': 1,
             'items.order_number': 1, 'accepted.order_number': 1}).to_list(501)
        if len(rows) > 500:
            raise DomainError('mutation_selection_limit_exceeded', 422)
        if any(is_local_order_number(n) for row in rows for n in _strings(row)):
            return True
    payload = values.get('payload')
    if hasattr(payload, 'model_dump'):
        payload = payload.model_dump(mode='json')
    if isinstance(payload, dict) and isinstance(payload.get('selections'), list):
        # New preparation batches carry per-piece/group identities, not order
        # numbers or an existing batch ID. Resolve their current read-only plan.
        from reviewed_preparation_batches import (load_reviewed_product_context,
            plan_preparation_allocations, MAX_REVIEWED_ORDERS)
        context = await load_reviewed_product_context(db, user_id=tenant, limit=MAX_REVIEWED_ORDERS)
        if context.get('truncated'):
            raise DomainError('reviewed_catalog_truncated', 409)
        try:
            planned = plan_preparation_allocations(context['catalog'].get('products') or [], payload['selections'])
        except ValueError:
            # Existing handler returns its authoritative request-validation error.
            return False
        return any(is_local_order_number(row.get('order_number')) for row in planned)
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
        def decorate(endpoint, native_path):
            @wraps(endpoint)
            async def execute(**values):
                try:
                    if not await local_request(db, values):
                        return await endpoint(**values)
                    await prepare_workflow_indexes(db)
                    async def callback(_):
                        # UploadFile is a stream. A real Mongo transaction retry
                        # must replay the same bytes, never an exhausted stream.
                        from starlette.datastructures import UploadFile
                        for value in values.values():
                            if isinstance(value, UploadFile):
                                await value.seek(0)
                        from .access import fresh_principal
                        original_user=values['user']
                        actor_id=original_user.get('_mobile_actor_id') or original_user['id']
                        fresh,tenant=await fresh_principal(db, {'id':actor_id})
                        requested_tenant=(str(original_user.get('_mobile_owner_id') or original_user.get('id'))
                            if str(original_user.get('role') or '').casefold()=='owner' or original_user.get('is_owner') is True
                            else str(original_user.get('created_by') or original_user.get('merchant_id') or ''))
                        if tenant!=requested_tenant:
                            raise DomainError('native_merchant_scope_changed',403)
                        if original_user.get('_session_client'):
                            fresh={**fresh,'_session_client':original_user['_session_client']}
                            from mobile_app_request_context import mobile_app_request_user
                            fresh=await mobile_app_request_user(db,fresh,path='/api'+native_path,
                                method=original_user.get('_mobile_request_method') or 'POST')
                        return await endpoint(**{**values,'user':fresh})
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
        wrapped=decorate(original, route.path)
        route.endpoint=wrapped
        route.dependant.call=wrapped
    return router
