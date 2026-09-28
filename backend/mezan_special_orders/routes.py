"""Opt-in router factory. Not registered by server.py or order_engine imports."""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import ValidationError

from .contracts import Actor, Command, CreateOrder, WorkflowReference
from .domain import DomainError
from .service import SpecialOrderService


def make_special_orders_router(service: SpecialOrderService, current_actor: Callable) -> APIRouter:
    router = APIRouter(prefix="/special-orders-v1", tags=["Mezan special orders — gated"])

    async def invoke(awaitable):
        try:
            return await awaitable
        except DomainError as exc:
            raise HTTPException(status_code=exc.http_status, detail={"code": exc.code}) from None
        except ValidationError:
            # Do not serialize exception strings/input values (bank and customer data).
            raise HTTPException(status_code=422, detail={"code": "invalid_special_order_payload"}) from None
        except Exception:
            # Logging belongs to the application's sanitized error middleware.
            raise HTTPException(status_code=503, detail={"code": "special_order_dependency_unavailable"}) from None

    async def actor_dependency(actor=Depends(current_actor)) -> Actor:
        if not isinstance(actor, Actor):
            raise HTTPException(status_code=403, detail={"code": "trusted_actor_required"})
        return actor

    @router.post("", status_code=201)
    async def create(request: CreateOrder, actor: Actor = Depends(actor_dependency),
                     idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128)):
        return await invoke(service.create(actor, request, idempotency_key))

    @router.get("")
    async def list_orders(actor: Actor = Depends(actor_dependency), limit: int = Query(30, ge=1, le=100),
                          before_date: str | None = Query(None, max_length=40),
                          before_id: str | None = Query(None, max_length=36)):
        before = None
        if (before_date is None) != (before_id is None):
            raise HTTPException(status_code=422, detail={"code": "complete_cursor_required"})
        if before_date is not None:
            try:
                when = datetime.fromisoformat(before_date)
                if when.utcoffset() is None:
                    raise ValueError()
                UUID(before_id)
            except ValueError:
                raise HTTPException(status_code=422, detail={"code": "invalid_cursor"}) from None
            before = (before_date, before_id)
        return await invoke(service.list(actor, limit, before))

    @router.get("/{order_id}")
    async def detail(order_id: UUID, actor: Actor = Depends(actor_dependency)):
        return await invoke(service.get(actor, str(order_id)))

    @router.post("/{order_id}/commands")
    async def command(order_id: UUID, request: Command, actor: Actor = Depends(actor_dependency),
                      idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128)):
        return await invoke(service.command(actor, str(order_id), request.expected_revision,
                                            idempotency_key, request.operation, request.payload))

    @router.post("/{order_id}/outbox/dispatch")
    async def dispatch(order_id: UUID, request: WorkflowReference, actor: Actor = Depends(actor_dependency)):
        return await invoke(service.dispatch(actor, str(order_id), request.event_id))

    return router
