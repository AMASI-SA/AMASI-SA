"""Financial eligibility and server-derived legs for reversing a saved manual movement."""
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from zoneinfo import ZoneInfo

from operational_financial_activity_runtime import checked_rows

ASSETS = {"bank", "cash"}
ACCOUNT_PAYMENTS = {"employee", "supplier", "external_person"}
MANUAL_SOURCES = {"mezan2", "employee_app"}
DOCUMENT = "هذه الحركة مرتبطة بمستند أو تسوية. تُراجع من مصدرها حتى تُصحح جميع آثارها."
UNSUPPORTED = "هذا النوع يحتاج تصحيحًا من مساره المحاسبي."
AUTOMATIC = "هذه حركة آلية. تُراجع من مصدرها."
INVALID = "تعذر التحقق من الحركة الأصلية."
ALREADY = "سبق عكس هذه الحركة."
IS_REVERSAL = "هذه حركة عكس مرتبطة بالأصل."
LINK_FIELDS = (
    "order_number", "order_id", "original_order_number", "exchange_id", "driver_review_event_id",
    "workspace_group", "bank_movement_id", "bank_arrival_confirmed", "fee_reference",
    "bank_currency", "bank_amount", "bank_fee_amount", "funding_policy", "external_operation",
    "invoice_id", "original_obligation_id", "external_balance_snapshot", "funding_mode",
)


@dataclass
class ReversalIndex:
    originals: dict = field(default_factory=dict)
    reversed: set = field(default_factory=set)
    linked: set = field(default_factory=set)
    valid: bool = True

    def add(self, row):
        identity = row.get("id") if isinstance(row, dict) else None
        if not isinstance(identity, str) or not identity:
            self.valid = False
            return
        if identity in self.originals:
            self.linked.add(identity)
        self.originals[identity] = row
        if isinstance(row.get("reverse_of"), str):
            self.reversed.add(row["reverse_of"])
        if isinstance(row.get("workspace_group"), str):
            self.linked.add(row["workspace_group"])


def index_documents(index, state):
    """Some workflows adopt a previously entered movement without editing it."""
    try:
        for exchange in checked_rows(state.get("customer_exchanges", ())):
            for item in checked_rows(exchange["contributions"]):
                index.linked.add(item["movement_id"])
        for invoice in checked_rows(state.get("native_purchase_links", {}).values()):
            for payment in checked_rows(invoice["payments"].values()):
                index.linked.add(payment["movement_id"])
        for review in checked_rows(state.get("driver_payment_review_facts", {}).values()):
            index.linked.update((review.get("movement_id"), review.get("bank_movement_id")))
    except (AttributeError, KeyError, TypeError):
        index.valid = False
    return index


def reversal_index(state):
    """Build once per page; write callers rebuild inside the aggregate CAS."""
    index = ReversalIndex()
    for row in checked_rows(state.get("movements", ())):
        index.add(row)
    return index_documents(index, state)


def reversal_id(original_id):
    return "movement-reversal:" + sha256(original_id.encode("utf-8")).hexdigest()


def inspect(state, original, *, index=None):
    """Return an explanation when this movement cannot be reversed in isolation."""
    if not isinstance(original, dict):
        return INVALID
    index = index if index is not None else reversal_index(state)
    identity = original.get("id")
    if not index.valid or not isinstance(identity, str) or index.originals.get(identity) != original:
        return INVALID
    if original.get("reverse_of"):
        return IS_REVERSAL
    if identity in index.reversed:
        return ALREADY
    if (original.get("source") not in MANUAL_SOURCES or not original.get("actor_id")
            or original["actor_id"] in {"operational_order_worker", "operational_worker"}
            or any(key.startswith("automatic_") and value for key, value in original.items())):
        return AUTOMATIC
    if (identity in index.linked or original.get("allocations") not in ([], None)
            or any(original.get(key) for key in LINK_FIELDS)):
        return DOCUMENT
    try:
        amount = Decimal(str(original["amount"]))
        if not amount.is_finite() or amount <= 0 or amount != amount.quantize(Decimal(".01")):
            return INVALID
        if Decimal(str(original.get("actual_fee_amount", "0"))) != 0:
            return DOCUMENT
    except (KeyError, InvalidOperation, ValueError):
        return INVALID
    currency, direction = original.get("currency"), original.get("direction")
    if (not isinstance(currency, str) or len(currency) != 3 or not currency.isascii() or not currency.isalpha()
            or currency != currency.upper() or direction not in {"incoming", "outgoing"} or not original.get("party_id")):
        return INVALID
    party, kind = original.get("party_type"), original.get("kind")
    operation = original.get("workspace_operation")
    bank, bank_id = original.get("bank_kind"), original.get("bank_id")
    resources = original.get("workspace_resources", [])
    if not isinstance(resources, list) or any(
        not isinstance(resource, dict) or (resource.get("kind"), resource.get("id"))
        not in ((party, original["party_id"]), (bank, bank_id)) for resource in resources
    ):
        return DOCUMENT
    cash_leg = bank in ASSETS and bool(bank_id)
    scoped = original.get("settlement_scope")
    if kind == "correction" and not bank and not bank_id and not scoped:
        if party in ASSETS and not operation:
            return None
        if party in {"employee", "supplier"} and operation in {None, party + ".accrual", party + ".adjustment"}:
            return None
    if not cash_leg or (original["party_id"] == bank_id and party == bank):
        return UNSUPPORTED
    if party in ASSETS and kind == "transfer" and operation in {None, "accounts.transfer"} and not scoped:
        return None
    action = {"outgoing": "payment", "incoming": "collection"}[direction]
    if kind != action:
        return UNSUPPORTED
    if party == "operating_expense" and direction == "outgoing" and operation in {None, "expense.pay"} and not scoped:
        return None
    if party == "owner_withdrawal" and not scoped:
        if ((direction == "outgoing" and operation in {None, "owner.withdraw"})
                or (direction == "incoming" and operation == "owner.return")):
            return None
    if party in ACCOUNT_PAYMENTS and scoped == "account":
        expected = party + "." + action
        if party == "external_person":
            expected = "external.pay" if direction == "outgoing" else "external.receive"
        if operation in {None, expected}:
            return None
    return UNSUPPORTED


def build_reverse(original, id, actor, reason, stamp):
    """Called only after inspect under CAS. Proofs and allocation IDs belong to the original."""
    if id != reversal_id(original["id"]):
        raise ValueError("operational_reversal_id_invalid")
    when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    if when.tzinfo is None or not isinstance(reason, str) or not reason.strip() or not actor.get("id"):
        raise ValueError("operational_reversal_provenance_invalid")
    direction = "outgoing" if original["direction"] == "incoming" else "incoming"
    kind = original["kind"]
    if kind in {"payment", "collection"}:
        kind = "payment" if direction == "outgoing" else "collection"
    inverse = {key: original.get(key) for key in (
        "party_type", "party_id", "name", "currency", "amount", "bank_kind", "bank_id", "bank_name",
    )}
    inverse.update(
        id=id, request_id=id, reverse_of=original["id"], direction=direction, kind=kind,
        actor_id=actor["id"], recorded_by={"id": actor["id"], "name": actor.get("name") or "غير موثق"},
        source=original["source"], occurred_at=stamp,
        business_date=when.astimezone(ZoneInfo("Asia/Riyadh")).date().isoformat(),
        workspace_reason="reversal", reference=id, note=reason.strip(),
        actual_fee_amount="0.00", allocations=[], receipt_id=None, order_number=None,
    )
    if original.get("settlement_scope") == "account":
        inverse["settlement_scope"] = "account"
    if original["party_type"] == "owner_withdrawal":
        inverse["workspace_operation"] = "owner.withdraw" if direction == "outgoing" else "owner.return"
    return inverse
