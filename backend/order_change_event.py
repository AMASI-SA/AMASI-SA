"""Immutable order-change payload contract; no persistence or commercial writes.

PR1 only emits hold/resume events. Commercial variants describe a future
contract and do not authorize accounting or Salla mutations.
"""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


NonEmpty = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, strict=True)]
NonNegative = Annotated[int, Field(ge=0, strict=True)]
UnitIndex = Annotated[int, Field(ge=1, strict=True)]
EventGeneration = NonNegative | NonEmpty
ChangeType = Literal["add_product", "edit_options", "cancel_product", "replace_product", "hold", "resume"]


class FrozenContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ChangeActor(FrozenContract):
    actor_id: NonEmpty
    name: NonEmpty | None = None


class UnitIdentity(FrozenContract):
    """SKU is deliberately not an identity: repeated SKUs are valid units."""
    order_item_id: NonEmpty
    unit_index: UnitIndex
    generation: NonNegative


class UnitSnapshot(UnitIdentity):
    replaces: UnitIdentity | None = None
    replaced_by: UnitIdentity | None = None


class OptionSnapshot(FrozenContract):
    key: NonEmpty
    value: str


def _identity(unit: UnitIdentity) -> tuple[str, int, int]:
    return unit.order_item_id, unit.unit_index, unit.generation


class OrderChangeEvent(FrozenContract):
    order_number: NonEmpty
    change_id: NonEmpty
    idempotency_key: NonEmpty
    change_type: ChangeType
    actor: ChangeActor
    reason: NonEmpty
    timestamp: datetime
    old_units: tuple[UnitSnapshot, ...] = ()
    new_units: tuple[UnitSnapshot, ...] = ()
    old_options: tuple[OptionSnapshot, ...] = ()
    new_options: tuple[OptionSnapshot, ...] = ()
    old_fulfillment_stage: NonEmpty
    affected_employees: tuple[NonEmpty, ...] = ()
    revision: NonNegative
    # Control events may carry the current generation fence token while each
    # immutable unit retains its own numeric historical generation.
    generation: EventGeneration
    required_action: Literal["stop", "reload", "preparation_reconcile"]
    financial_impact: Literal["pending_contract"] = "pending_contract"
    salla_mutation_enabled: Literal[False] = False

    @field_validator("old_units", "new_units", "old_options", "new_options", "affected_employees", mode="before")
    @classmethod
    def freeze_collections(cls, value):
        # Accept JSON-like lists at the producer boundary without retaining a
        # mutable caller-owned collection inside an otherwise frozen event.
        return tuple(value) if isinstance(value, list) else value

    @field_validator("salla_mutation_enabled", mode="before")
    @classmethod
    def require_disabled_salla(cls, value):
        if value is not False:
            raise ValueError("Salla mutation capability must be false")
        return value

    @model_validator(mode="after")
    def validate_event(self):
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        required = {"hold": "stop", "resume": "reload"}.get(self.change_type, "preparation_reconcile")
        if self.required_action != required:
            raise ValueError(f"{self.change_type} requires action {required}")
        for units in (self.old_units, self.new_units):
            if len({_identity(unit) for unit in units}) != len(units):
                raise ValueError("duplicate unit identity")
        if self.change_type not in ("hold", "resume") and isinstance(self.generation, int):
            if any(unit.generation > self.generation for unit in self.old_units):
                raise ValueError("old units cannot belong to a future generation")
            if any(unit.generation != self.generation for unit in self.new_units):
                raise ValueError("new units must belong to the event generation")
        if len(set(self.affected_employees)) != len(self.affected_employees):
            raise ValueError("duplicate affected employee")
        if self.change_type == "add_product" and not self.new_units:
            raise ValueError("add_product requires new units")
        if self.change_type == "cancel_product" and not self.old_units:
            raise ValueError("cancel_product requires preserved old units")
        if self.change_type == "edit_options":
            if not self.old_units or {_identity(u) for u in self.old_units} != {_identity(u) for u in self.new_units}:
                raise ValueError("edit_options must preserve unit identities")
        if self.change_type == "replace_product":
            old = {_identity(unit): unit for unit in self.old_units}
            new = {_identity(unit): unit for unit in self.new_units}
            if not old or not new or old.keys() & new.keys():
                raise ValueError("replacement must preserve distinct old and new units")
            for identity, unit in old.items():
                successor = new.get(_identity(unit.replaced_by)) if unit.replaced_by else None
                if successor is None or successor.replaces is None or _identity(successor.replaces) != identity:
                    raise ValueError("old unit requires reciprocal replaced_by/replaces history")
            for identity, unit in new.items():
                predecessor = old.get(_identity(unit.replaces)) if unit.replaces else None
                if predecessor is None or predecessor.replaced_by is None or _identity(predecessor.replaced_by) != identity:
                    raise ValueError("new unit requires reciprocal replaces/replaced_by history")
        return self


def build_control_event(
    *, order_number: str, change_id: str, idempotency_key: str,
    change_type: Literal["hold", "resume"], actor: ChangeActor,
    reason: str, timestamp: datetime, old_fulfillment_stage: str,
    revision: int, generation: int | str,
    old_units: tuple[UnitSnapshot, ...] = (),
    new_units: tuple[UnitSnapshot, ...] = (),
    old_options: tuple[OptionSnapshot, ...] = (),
    new_options: tuple[OptionSnapshot, ...] = (),
    affected_employees: tuple[str, ...] = (),
) -> OrderChangeEvent:
    """Build a payload for an existing control audit; performs no writes."""
    if change_type not in ("hold", "resume"):
        raise ValueError("PR1 control builder supports only hold/resume")
    return OrderChangeEvent(
        order_number=order_number, change_id=change_id,
        idempotency_key=idempotency_key, change_type=change_type,
        actor=actor, reason=reason, timestamp=timestamp,
        old_fulfillment_stage=old_fulfillment_stage, revision=revision,
        generation=generation, old_units=old_units, new_units=new_units,
        old_options=old_options, new_options=new_options,
        affected_employees=affected_employees,
        required_action="stop" if change_type == "hold" else "reload",
    )
