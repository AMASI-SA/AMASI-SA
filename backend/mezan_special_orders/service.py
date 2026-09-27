"""Application service with explicit trusted ports and no automatic side effects."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5
import json
import re

from .contracts import (COMMAND_MODELS, Actor, CostProof, CreateOrder, Evidence, LedgerProof,
                        OptionValue, OriginalOrder, Product, ReceiptClaim, WorkflowProof)
from .domain import (DomainError, add_event, add_receipt, amend_options, balances,
                     consume_financial_proof, cost_report, creation, digest,
                     dispatch_blockers, observe_workflow, operational_projection)
from .repository import Store


class Ports(Protocol):
    """Adapters MUST use the tenant and re-read authoritative facts.

    Evidence: ownership, digest, MIME/size/scan and carrier metadata validation.
    Ledger: posted MZ2 movement, allocation ownership, bank/driver, amount and evidence.
    Workflow: existing fulfillment's validated transition plus frozen source revision.
    publish: existing handler deduplicates by event_id before any business write.
    All financial methods below are reads, not permission to post a journal.
    """
    async def original(self, tenant_id: str, number: str) -> OriginalOrder: ...
    async def product(self, tenant_id: str, product_id: str, variant_id: str | None) -> Product: ...
    async def evidence(self, tenant_id: str, evidence: Evidence, *, carrier_key: str | None = None, tracking_number: str | None = None) -> bool: ...
    async def fx(self, tenant_id: str, snapshot: dict) -> bool: ...
    async def payment(self, tenant_id: str, order_id: str, movement_id: str) -> LedgerProof: ...
    async def cost(self, tenant_id: str, order_id: str, movement_id: str) -> CostProof: ...
    async def workflow(self, tenant_id: str, order_id: str, event_id: str) -> WorkflowProof: ...
    async def publish(self, tenant_id: str, event_id: str, event: dict, order: dict) -> None: ...


class UnboundPorts:
    """Fail closed until real source, MZ2 and shared-workflow adapters are reviewed."""
    async def _blocked(self, *args, **kwargs):
        raise DomainError("integration_adapter_not_bound", 503)
    original = product = evidence = fx = payment = cost = workflow = publish = _blocked


@dataclass
class Gates:
    # No implicit environment activation. Creation and processing are independent.
    creation_enabled: bool = False
    commands_enabled: bool = False
    dispatch_enabled: bool = False


def require(actor: Actor, permission: str) -> None:
    if permission not in actor.permissions:
        raise DomainError("permission_required", 403)


def validate_key(key: str) -> None:
    if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{8,128}", key):
        raise DomainError("invalid_idempotency_key", 422)


def public_view(order: dict, actor: Actor) -> dict:
    result = operational_projection(order)
    result["revision"] = order["revision"]
    result["source_frozen"] = order["source_frozen"]
    result["dispatch_blockers"] = dispatch_blockers(order)
    if "special_orders.finance" in actor.permissions:
        result["cost_report"] = cost_report(order)
        result["allocations"] = order["allocations"]
        result["receipt_claims"] = order["receipt_claims"]
        result["payments"] = order["payments"]
    else:
        # Only amount to collect travels to operational employees; never bank evidence.
        result["balances"] = {k: v for k, v in result["balances"].items() if k in {"remaining_minor", "cod_to_collect_minor"}}
    return result


class SpecialOrderService:
    def __init__(self, store: Store, ports: Ports | None = None, gates: Gates | None = None):
        self.store = store
        self.ports = ports if ports is not None else UnboundPorts()
        self.gates = gates if gates is not None else Gates()

    async def _get(self, actor: Actor, order_id: str) -> dict:
        document = await self.store.get(actor.tenant_id, order_id)
        if document is None:
            raise DomainError("order_not_found", 404)
        return document

    async def get(self, actor: Actor, order_id: str) -> dict:
        require(actor, "special_orders.read")
        return public_view(await self._get(actor, order_id), actor)

    async def list(self, actor: Actor, limit: int = 30, before: tuple[str, str] | None = None) -> dict:
        require(actor, "special_orders.read")
        if type(limit) is not int or not 1 <= limit <= 100:
            raise DomainError("invalid_page_limit", 422)
        docs = await self.store.list(actor.tenant_id, limit + 1, before)
        page = docs[:limit]
        next_page = (page[-1]["created_at"], page[-1]["order_id"]) if len(docs) > limit else None
        return {"items": [public_view(d, actor) for d in page], "next": next_page}

    async def create(self, actor: Actor, request: CreateOrder, key: str) -> dict:
        require(actor, "special_orders.create")
        validate_key(key)
        identity = str(uuid5(NAMESPACE_URL, json.dumps(["mezan-special-v1", actor.tenant_id, key])))
        existing = await self.store.get(actor.tenant_id, identity)
        fingerprint = digest(request.model_dump(mode="json"))
        # Allow safe read-back of an already committed retry even when creation is paused.
        if existing:
            if existing["create_fingerprint"] != fingerprint:
                raise DomainError("idempotency_payload_conflict")
            return public_view(existing, actor)
        if not self.gates.creation_enabled:
            raise DomainError("creation_disabled", 403)
        if not await self.ports.fx(actor.tenant_id, request.fx.model_dump(mode="json")):
            raise DomainError("fx_snapshot_not_verified", 422)
        original = await self.ports.original(actor.tenant_id, request.original_order_number) if request.original_order_number else None
        products = {}
        for selection in request.items:
            if selection.product_id:
                pkey = (selection.product_id, selection.variant_id)
                if pkey not in products:
                    products[pkey] = await self.ports.product(actor.tenant_id, *pkey)
        if request.delivery.label:
            valid = await self.ports.evidence(actor.tenant_id, request.delivery.label,
                        carrier_key=request.delivery.carrier_key, tracking_number=request.delivery.tracking_number)
            if not valid:
                raise DomainError("shipping_evidence_not_verified", 422)
        order = creation(actor, request, key, original, products)
        if not await self.store.insert(order):
            order = await self._get(actor, identity)
            if order["create_fingerprint"] != fingerprint:
                raise DomainError("idempotency_payload_conflict")
        return public_view(order, actor)

    async def command(self, actor: Actor, order_id: str, expected_revision: int, key: str,
                      operation: str, payload: dict) -> dict:
        permissions = {"amend_options": "special_orders.edit", "freeze_source": "special_orders.review",
            "attach_receipt": "special_orders.finance", "reject_receipt": "special_orders.finance",
            "observe_payment": "special_orders.reconcile", "observe_cost": "special_orders.reconcile",
            "observe_workflow": "special_orders.integrate"}
        if operation not in permissions:
            raise DomainError("unsupported_command", 422)
        require(actor, permissions[operation])
        validate_key(key)
        if type(expected_revision) is not int or expected_revision < 1:
            raise DomainError("invalid_expected_revision", 422)
        payload = COMMAND_MODELS[operation].model_validate(payload).model_dump(mode="json")
        order = await self._get(actor, order_id)
        command_key = digest(key)
        fingerprint = digest([operation, payload])
        prior = order["command_log"].get(command_key)
        if prior:
            if prior["fingerprint"] != fingerprint:
                raise DomainError("idempotency_payload_conflict")
            return public_view(order, actor)
        if not self.gates.commands_enabled:
            raise DomainError("commands_disabled", 403)
        if order["revision"] != expected_revision:
            raise DomainError("revision_conflict")
        if operation == "amend_options":
            if set(payload) != {"line_key", "options"}:
                raise DomainError("invalid_command_fields", 422)
            values = tuple(OptionValue.model_validate(v) for v in payload["options"])
            amend_options(order, payload["line_key"], values)
        elif operation == "freeze_source":
            if payload:
                raise DomainError("invalid_command_fields", 422)
            if order["stage"] != "pending_review":
                raise DomainError("source_not_in_review")
            order["source_frozen"] = True
        elif operation == "attach_receipt":
            claim = ReceiptClaim.model_validate(payload)
            if not await self.ports.evidence(actor.tenant_id, claim.evidence):
                raise DomainError("receipt_evidence_not_verified", 422)
            add_receipt(order, claim)
        elif operation == "reject_receipt":
            if set(payload) != {"claim_id", "reason"} or not isinstance(payload["reason"], str) or not 3 <= len(payload["reason"].strip()) <= 500:
                raise DomainError("invalid_command_fields", 422)
            claim = next((c for c in order["receipt_claims"] if c["claim_id"] == payload["claim_id"] and c["state"] == "pending"), None)
            if claim is None:
                raise DomainError("pending_receipt_claim_required")
            claim["state"] = "rejected"
            claim["rejection_reason"] = payload["reason"].strip()
        elif operation in {"observe_payment", "observe_cost"}:
            if set(payload) != {"movement_id"}:
                raise DomainError("invalid_command_fields", 422)
            method = self.ports.payment if operation == "observe_payment" else self.ports.cost
            proof = await method(actor.tenant_id, order_id, payload["movement_id"])
            expected_type = LedgerProof if operation == "observe_payment" else CostProof
            if not isinstance(proof, expected_type) or proof.movement_id != payload["movement_id"]:
                raise DomainError("invalid_financial_proof")
            consume_financial_proof(order, proof)
        elif operation == "observe_workflow":
            if set(payload) != {"event_id"}:
                raise DomainError("invalid_command_fields", 422)
            proof = await self.ports.workflow(actor.tenant_id, order_id, payload["event_id"])
            if not isinstance(proof, WorkflowProof) or proof.event_id != payload["event_id"]:
                raise DomainError("invalid_workflow_proof")
            observe_workflow(order, proof)
        order["revision"] += 1
        event_id = add_event(order, "special_order." + operation, actor.actor_id)
        order["command_log"][command_key] = {"fingerprint": fingerprint, "revision": order["revision"], "event_id": event_id}
        if not await self.store.replace(order, expected_revision):
            current = await self._get(actor, order_id)
            prior = current["command_log"].get(command_key)
            if prior and prior["fingerprint"] == fingerprint:
                return public_view(current, actor)
            raise DomainError("revision_conflict")
        return public_view(order, actor)

    async def dispatch(self, actor: Actor, order_id: str, event_id: str) -> dict:
        require(actor, "special_orders.integrate")
        if not self.gates.dispatch_enabled:
            raise DomainError("outbox_dispatch_disabled", 403)
        order = await self._get(actor, order_id)
        event = next((e for e in order["outbox"] if e["event_id"] == event_id), None)
        if event is None:
            raise DomainError("event_not_found", 404)
        if event["state"] == "acknowledged":
            return {"acknowledged": True, "replayed": True, "event_id": event_id}
        # At-least-once transport; the shared consumer MUST deduplicate event_id.
        await self.ports.publish(actor.tenant_id, event_id, event, operational_projection(order))
        for _ in range(5):
            current = await self._get(actor, order_id)
            current_event = next(e for e in current["outbox"] if e["event_id"] == event_id)
            if current_event["state"] == "acknowledged":
                return {"acknowledged": True, "replayed": True, "event_id": event_id}
            revision = current["revision"]
            current_event["state"] = "acknowledged"
            current["revision"] += 1
            if await self.store.replace(current, revision):
                return {"acknowledged": True, "replayed": False, "event_id": event_id}
        raise DomainError("outbox_ack_conflict_retry_same_event")
