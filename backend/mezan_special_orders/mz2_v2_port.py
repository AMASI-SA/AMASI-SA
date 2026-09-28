"""Opt-in V2 transport contract; not installed in any live special-order route.

Business owners must validate evidence, allocations, accounts, tax, purpose and
local writer admission before calling this port. It ONLY joins an existing
Accounting V2 owner transaction; it never starts a transaction, activates V2,
changes a control, creates an opening, or falls back to the old ledger.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any

MAX_MINOR = 10**14
SOURCE = "mezan_special_orders_v2"
PURPOSES = frozenset({"replacement", "gift", "creator", "marketing"})
# Existing accounting action keys, not new grants. The caller fixes the action;
# this argument must not come from a customer/employee HTTP payload.
WRITE_PERMISSIONS = frozenset({
    "accounting.receivables.post", "accounting.settlements.post",
    "accounting.purchases.post", "accounting.refunds.pay",
})


class V2PortError(ValueError):
    """Literal code only; no raw exception or customer input in public errors."""


def _key(value: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}", value):
        raise V2PortError("special_v2_identity_invalid")
    return value


def _hash(value: str) -> str:
    if type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise V2PortError("special_v2_evidence_digest_required")
    return value


def _time(value: str) -> str:
    try:
        if type(value) is not str:
            raise ValueError()
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if when.utcoffset() is None:
            raise ValueError()
        return when.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
    except (ValueError, TypeError):
        raise V2PortError("special_v2_effective_time_invalid") from None


@dataclass(frozen=True)
class V2Leg:
    key: str
    entity_type: str
    entity_id: str
    side: str
    amount_minor: int
    entry_type: str
    sub_account: str | None = None

    def __post_init__(self):
        for text in (self.key, self.entity_type, self.entity_id, self.entry_type):
            _key(text)
        if self.sub_account is not None:
            _key(self.sub_account)
        if self.side not in {"debit", "credit"}:
            raise V2PortError("special_v2_side_invalid")
        if type(self.amount_minor) is not int or not 0 < self.amount_minor <= MAX_MINOR:
            raise V2PortError("special_v2_positive_minor_required")

    def payload(self) -> dict:
        # Integer formatting avoids both binary float and Decimal context loss.
        amount = f"{self.amount_minor // 100}.{self.amount_minor % 100:02d}"
        return dict(leg_key=self.key, entity_type=self.entity_type,
                    entity_id=self.entity_id, side=self.side, amount=amount,
                    entry_type=self.entry_type, sub_account=self.sub_account)


@dataclass(frozen=True)
class V2Event:
    owner: str
    order_id: str
    event_id: str
    purpose: str
    effective_at: str
    evidence_digest: str
    policy_digest: str
    legs: tuple[V2Leg, ...]

    def __post_init__(self):
        for text in (self.owner, self.order_id, self.event_id):
            _key(text)
        if self.purpose not in PURPOSES:
            raise V2PortError("special_v2_purpose_invalid")
        _time(self.effective_at)
        _hash(self.evidence_digest)
        _hash(self.policy_digest)
        if type(self.legs) is not tuple or not 2 <= len(self.legs) <= 1000 or any(type(leg) is not V2Leg for leg in self.legs):
            raise V2PortError("special_v2_legs_invalid")
        if len({leg.key for leg in self.legs}) != len(self.legs):
            raise V2PortError("special_v2_duplicate_leg")
        if sum(leg.amount_minor * (1 if leg.side == "debit" else -1) for leg in self.legs):
            raise V2PortError("special_v2_unbalanced")

    def payload(self) -> dict:
        identity = hashlib.sha256(json.dumps(
            [self.owner, self.order_id, self.event_id], separators=(",", ":"),
        ).encode()).hexdigest()
        return dict(user_id=self.owner, idempotency_key="special-order:" + identity,
                    txn_type="special_order_event", source=SOURCE,
                    effective_at=_time(self.effective_at),
                    entries=[leg.payload() for leg in sorted(self.legs, key=lambda leg: leg.key)],
                    notes="", metadata=dict(special_order_id=self.order_id,
                        special_event_id=self.event_id, purpose=self.purpose,
                        evidence_digest=self.evidence_digest, policy_digest=self.policy_digest))


async def post_in_owner_transaction(scoped: Any, *, actor_id: str,
                                    required_permission: str, event: V2Event) -> dict:
    """Use the published public V2 API and return its verified journal.

    Caller must enter accounting_atomic.atomic_owner FIRST and bind the local
    special-order scope to that SAME session. Never pass SpecialOrderDatabase
    instead of SessionDatabase. The global control therefore owns commit/abort.
    This internal port does not replace bank evidence or business authorization.
    """
    from accounting_atomic import SessionDatabase
    from accounting_write_control import fresh_actor, write_state
    from accounting_module_contract import accounting_owner_id, require_accounting_permission
    from accounting_writer_transition import assert_writer_allowed
    from accounting_ledger_v2 import post_journal_v2, verify_active_opening_v2, verify_journal_v2

    _key(actor_id)
    if type(event) is not V2Event or required_permission not in WRITE_PERMISSIONS:
        raise V2PortError("special_v2_posting_contract_invalid")
    if not isinstance(scoped, SessionDatabase) or getattr(scoped, "_owner", None) != event.owner:
        raise V2PortError("special_v2_accounting_owner_transaction_required")
    session = scoped._session
    if session is None or getattr(session, "in_transaction", False) is not True:
        raise V2PortError("special_v2_active_transaction_required")
    actor = await fresh_actor(scoped, {"id": actor_id})
    require_accounting_permission(actor, required_permission)
    if accounting_owner_id(actor) != event.owner:
        raise V2PortError("special_v2_actor_scope_mismatch")
    # Recheck even for joined transactions; never call resume or bootstrap.
    if (await write_state(scoped, event.owner))["paused"]:
        raise V2PortError("special_v2_global_writes_paused")
    await assert_writer_allowed(scoped, event.owner, "v2")
    settings = await scoped.settings.find_one({"user_id": event.owner}, {"_id": 0, "mezan2_financial_cutover": 1})
    cutover = (settings or {}).get("mezan2_financial_cutover") or {}
    raw = scoped._db
    if cutover.get("status") != "active" or not await verify_active_opening_v2(
        raw, user_id=event.owner, cutover=cutover, mongo_session=session,
    ):
        raise V2PortError("special_v2_verified_opening_required")
    request = event.payload()
    journal = await post_journal_v2(raw, actor_id=actor_id, actor_name=actor_id,
                                    mongo_session=session, **request)
    group = journal["group"]
    proof = await verify_journal_v2(raw, user_id=event.owner,
        txn_group_id=group["txn_group_id"], mongo_session=session)
    expected = {leg["leg_key"]: leg for leg in request["entries"]}
    actual = {leg["leg_key"]: {key: leg.get(key) for key in next(iter(expected.values()))}
              for leg in journal["entries"]}
    if (proof.get("verified") is not True or proof.get("content_hash") != group.get("content_hash")
        or group.get("user_id") != event.owner or group.get("schema_version") != 2
        or group.get("currency") != "SAR" or group.get("status") != "posted"
        or group.get("source") != SOURCE or group.get("idempotency_key") != request["idempotency_key"]
        or group.get("metadata") != request["metadata"] or group.get("effective_at") != request["effective_at"]
        or len(journal["entries"]) != len(expected) or actual != expected):
        raise V2PortError("special_v2_readback_mismatch")
    return deepcopy(journal)
