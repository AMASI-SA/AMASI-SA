"""Read-only veto of an unpaid-placeholder exclusion when prior money exists.

This is not a payment recognizer. Any linked historical financial evidence is
enough to retain an order for review, including reversed or ambiguous evidence.
Only a completed, bounded lookup with no such evidence may authorize a proposed
zero-paid exclusion. No order, accounting entry or source snapshot is written.
"""
from functools import partial
from decimal import Decimal, InvalidOperation

from awaiting_payment_eligibility import ELIGIBILITY_FIELD
from operational_balance_archive import SNAPSHOT, _prepare_read, unpack_financial_snapshot
from operational_read_runtime import load_collections, read_source_cursor, source_read_budget


MAX_CANDIDATES = 250
MAX_SOURCE_ROWS = 2000
STATES = "operational_balance_states_v1"


def _identity(value):
    # bool is an int subclass; it must never bind to order 1.
    return str(value) if type(value) in (str, int) and str(value).strip() else None


def _get(row, path):
    for key in path.split("."):
        if not isinstance(row, dict):
            return None
        row = row.get(key)
    return row


def _values(identities):
    values = set(identities)
    values.update(int(value) for value in identities if value.isascii() and value.isdecimal())
    return list(values)


def _matches(row, paths, by_number, by_native):
    found = set()
    for path, native in paths:
        identity = _identity(_get(row, path))
        found.update((by_native if native else by_number).get(identity, ()))
    return found


def known_financial_history(state, orders, *, owner):
    """Extract candidate business numbers from an already hydrated owner state.

    Engine fact keys contain native IDs, while movement order_number contains
    business references. Exact identity matches and explicit payload identity
    are used; no substring match and no inferred payment from order status.
    A malformed owner state raises rather than asserting an empty history.
    """
    if not isinstance(state, dict) or state.get("owner_id") != owner:
        raise ValueError("awaiting_payment_history_owner_invalid")
    engine = state.get("engine")
    if not isinstance(engine, dict):
        raise ValueError("awaiting_payment_history_state_invalid")
    by_number, by_native = _indices(orders)
    protected = set()
    obligation_orders = {}
    for field in ("facts", "obligations"):
        records = engine.get(field, {})
        if not isinstance(records, dict):
            raise ValueError("awaiting_payment_history_facts_invalid")
        for key, row in records.items():
            if not isinstance(key, str) or not isinstance(row, dict):
                raise ValueError("awaiting_payment_history_fact_invalid")
            matched = _matches(row, (("order_number", False), ("order_id", True),
                                      ("salla_order_id", True)), by_number, by_native)
            for token in key.split(":"):
                matched.update(by_native.get(token, ()))
            if field == "obligations":
                obligation_orders[key] = matched
                # Expected obligations are forecasts, not evidence of incurred
                # cost. Confirmed zero may be ignored only when well-formed;
                # immutable facts still retain prior reversed activity.
                try:
                    confirmed = Decimal(str(row.get("confirmed")))
                    if confirmed.is_finite() and confirmed == 0:
                        continue
                except (InvalidOperation, ValueError, TypeError):
                    pass
            protected.update(matched)
    movements = state.get("movements", [])
    if not isinstance(movements, list):
        raise ValueError("awaiting_payment_history_movements_invalid")
    for row in movements:
        if not isinstance(row, dict):
            raise ValueError("awaiting_payment_history_movement_invalid")
        protected.update(_matches(row, (("order_number", False), ("order_id", True)), by_number, by_native))
        allocations = row.get("allocations", [])
        if not isinstance(allocations, list):
            raise ValueError("awaiting_payment_history_allocations_invalid")
        for allocation in allocations:
            if (not isinstance(allocation, dict) or not isinstance(allocation.get("obligation_id"), str)
                    or not allocation["obligation_id"].strip()):
                raise ValueError("awaiting_payment_history_allocation_invalid")
            obligation = allocation["obligation_id"]
            protected.update(obligation_orders.get(obligation, ()))
            # Retain even an orphaned allocation whose native order identity is
            # explicit; an old/zeroed obligation must not erase prior cash.
            for token in obligation.split(":"):
                protected.update(by_native.get(token, ()))
    return protected


def _indices(orders):
    numbers, natives = {}, {}
    for order in orders:
        number = _identity(order.get("order_number"))
        if number is None:
            raise ValueError("awaiting_payment_history_identity_invalid")
        numbers.setdefault(number, set()).add(number)
        for field in ("order_id", "salla_order_id"):
            native = _identity(order.get(field))
            if native:
                natives.setdefault(native, set()).add(number)
    return numbers, natives


def _scope(owner, paths, numbers, natives):
    clauses = [{path: {"$in": _values(natives if native else numbers)}} for path, native in paths]
    return {"user_id": owner, "$or": clauses}


def _source_specs(owner, by_number, by_native):
    number = (("order_number", False),)
    facts = (("facts.order_number", False),)
    provider = (("order_number", False), ("order_reference_id", False), ("provider_order_id", True))
    # Project only identities and fields used to distinguish actual cost from
    # a reservation/estimate. Receipt images, buyer/address and raw payloads are
    # never requested. Any row in a payment journal is a conservative veto.
    definitions = {
        "mz2_bank_transfer_receipt_events": (facts, ("status",), "any"),
        "order_payment_transactions_v2": (provider, (), "any"),
        "payment_transactions": (provider, (), "any"),
        "payment_refunds": (provider, (), "any"),
        "store_delivery_collections": (number, (), "any"),
        "mz2_shipping_accounting_events": (facts, (), "any"),
        "mz2_shipping_native_events_v2": (number, ("kind",), "shipping"),
        "store_delivery_driver_earnings": (number, (), "any"),
        "mezan_component_consumption_units_v1": ((("order_id", False),), ("state", "consumed_at"), "stock"),
        "mezan_supplier_receiving_events_v1": (number, ("experiment_mode", "experiment_run_id"), "supplier"),
        "mezan_preparation_pieces_v1": (number, ("id", "piece_id", "supplier_receiving_history", "received_at"), "piece"),
        "mezan_order_item_cost_snapshots_v2": (number, ("cost_stage", "cost"), "cost"),
        "mezan_component_order_lifecycle_v1": (number, ("reporting_cost_snapshots",), "lifecycle"),
        "mezan_order_cost_corrections_v1": (number, ("state",), "correction"),
        "general_ledger": ((("metadata.order_reference_id", False), ("metadata.order_number", False),
                            ("metadata.order_id", True)), (), "any"),
    }
    return {name: (_scope(owner, paths, by_number, by_native),
                   {"_id": 0, "user_id": 1, **{path: 1 for path, _ in paths}, **{field: 1 for field in fields}},
                   paths, kind)
            for name, (paths, fields, kind) in definitions.items()}


def _has_cost_evidence(row, kind):
    if kind == "stock":
        return bool(row.get("consumed_at")) or row.get("state") != "reserved"
    if kind == "cost":
        stage = row.get("cost_stage")
        nested = _get(row, "cost.cost_stage")
        if row.get("cost") is not None and not isinstance(row["cost"], dict):
            return True
        return not ((stage == "expected" or nested == "expected")
                    and stage in (None, "expected") and nested in (None, "expected"))
    if kind == "correction":
        return row.get("state") not in {"draft", "pending", "cancelled", "rejected"}
    if kind == "lifecycle":
        snapshots = row.get("reporting_cost_snapshots")
        if snapshots is None:
            return False
        if not isinstance(snapshots, dict):
            return True
        return any(not isinstance(snapshot, dict) or _has_cost_evidence(snapshot, "cost")
                   for snapshot in snapshots.values())
    if kind == "supplier":
        return row.get("experiment_mode") is not True and not row.get("experiment_run_id")
    if kind == "piece":
        return bool(row.get("supplier_receiving_history") or row.get("received_at"))
    if kind == "shipping":
        return row.get("kind") not in {"quote", "estimate"}
    return True


async def _load(db, owner, specs):
    async def reader(database, scoped_owner, name):
        query, projection, *_ = specs[name]
        rows = await read_source_cursor(database[name].find(query, projection).limit(MAX_SOURCE_ROWS + 1), MAX_SOURCE_ROWS + 1)
        if len(rows) > MAX_SOURCE_ROWS:
            raise ValueError("operational_awaiting_history_limit")
        return rows
    return await load_collections(db, owner, specs, reader=reader, per_read_timeout=5)


async def _read_state(db, owner):
    # A packed owner aggregate cannot be sliced on the server. Its authenticated
    # bounded packet is hydrated for facts/obligations only; audit pages and
    # unrelated owner records are never loaded.
    projection = {"_id": 0, "owner_id": 1, "engine.facts": 1, "engine.obligations": 1,
                  "movements": 1, SNAPSHOT: 1}
    specs = {STATES: ({"owner_id": owner}, projection)}
    rows = (await _load(db, owner, specs))[STATES]
    if len(rows) > 1:
        raise ValueError("awaiting_payment_history_duplicate_state")
    if not rows:
        return None
    return await _prepare_read(partial(unpack_financial_snapshot, selected={"facts", "obligations"}), rows[0])


async def _guard_batch(db, owner, orders, *, state=None):
    """Return same-order copies; authorize only checked proposed exclusions.

    Use an owner-scoped snapshot DB/session when available. ``state`` may reuse
    that caller's full operational snapshot (packed or hydrated). Missing,
    malformed, failed or over-capacity evidence never authorizes exclusion.
    The guard records prior *financial* history, not just payments: incurred
    supplier/stock/shipping costs must remain visible if a method later becomes
    waiting. Expected catalogue cost and reserved stock alone are not incurred.
    """
    result = [{**order, ELIGIBILITY_FIELD: dict(order.get(ELIGIBILITY_FIELD) or {})} for order in orders]
    candidates = [row for row in result if row[ELIGIBILITY_FIELD].get("eligible") is False]
    if not candidates:
        return result

    def retain(reason):
        for row in candidates:
            row[ELIGIBILITY_FIELD] = {**row[ELIGIBILITY_FIELD], "eligible": True, "review_required": True,
                                      "reason_code": reason, "history_checked": False}
        return result

    if not owner or len(candidates) > MAX_CANDIDATES or any(row.get("user_id") != owner for row in candidates):
        return retain("awaiting_payment_history_scope_or_limit")
    try:
        by_number, by_native = _indices(candidates)
        with source_read_budget(20):
            if state is None:
                current_state = await _read_state(db, owner)
            else:
                current_state = await _prepare_read(partial(unpack_financial_snapshot, selected={"facts", "obligations"}), state) if SNAPSHOT in state else state
            protected = known_financial_history(current_state, candidates, owner=owner) if current_state is not None else set()
            specs = _source_specs(owner, by_number, by_native)
            loaded = await _load(db, owner, specs)
            piece_numbers, invoice_numbers = {}, {}
            for name, rows in loaded.items():
                paths, kind = specs[name][2:]
                for row in rows:
                    if row.get("user_id") != owner:
                        raise ValueError("awaiting_payment_history_scope_invalid")
                    matched = _matches(row, paths, by_number, by_native)
                    if not matched:
                        raise ValueError("awaiting_payment_history_binding_invalid")
                    if _has_cost_evidence(row, kind):
                        protected.update(matched)
                    if kind == "piece":
                        for field in ("id", "piece_id"):
                            identity = _identity(row.get(field))
                            if identity:
                                piece_numbers.setdefault(identity, set()).update(matched)
                        history = row.get("supplier_receiving_history") or []
                        history = [history] if isinstance(history, dict) else history
                        if isinstance(history, list):
                            for event in history:
                                identity = _identity(_get(event, "invoice_id"))
                                if identity:
                                    invoice_numbers.setdefault(identity, set()).update(matched)
            if piece_numbers or invoice_numbers:
                query = {"user_id": owner, "$or": [{"id": {"$in": _values(invoice_numbers)}},
                                                     {"lines.piece_ids": {"$in": _values(piece_numbers)}}]}
                projection = {"_id": 0, "user_id": 1, "id": 1, "lines.piece_ids": 1, "status": 1,
                              "approved_at": 1, "txn_group_id": 1, "mz2_txn_group_id": 1, "liability_created": 1}
                invoices = (await _load(db, owner, {"mezan_supplier_invoices_v2": (query, projection)}))["mezan_supplier_invoices_v2"]
                for row in invoices:
                    if row.get("user_id") != owner:
                        raise ValueError("awaiting_payment_history_scope_invalid")
                    matched = set(invoice_numbers.get(_identity(row.get("id")), ()))
                    for line in row.get("lines") or []:
                        for identity in line.get("piece_ids") or []:
                            matched.update(piece_numbers.get(_identity(identity), ()))
                    if not matched:
                        raise ValueError("awaiting_payment_history_invoice_binding_invalid")
                    if (row.get("status") not in {"draft", "pending", "cancelled", "rejected"}
                            or row.get("approved_at") or row.get("txn_group_id") or row.get("mz2_txn_group_id") or row.get("liability_created")):
                        protected.update(matched)
        for row in candidates:
            decision = row[ELIGIBILITY_FIELD]
            decision["history_checked"] = True
            if str(row["order_number"]) in protected:
                decision.update(eligible=True, review_required=True, reason_code="awaiting_payment_prior_financial_history")
        return result
    except Exception:
        # CancelledError inherits BaseException: cancellation propagates after
        # the shared reader joins owned work; it never becomes an empty lookup.
        return retain("awaiting_payment_history_unavailable")


async def guard_financial_cohort_history(db, owner, orders, *, state=None):
    """Check bounded batches under one shared read budget, preserving row order.

    Larger merchant cohorts do not fail merely because they exceed a query
    batch. The owner aggregate is loaded once and reused. If the shared deadline
    expires, unchecked batches remain visible for review; checked batches keep
    their auditable decisions. There are no database writes.
    """
    proposed = [row for row in orders if (row.get(ELIGIBILITY_FIELD) or {}).get("eligible") is False]
    if len(proposed) <= MAX_CANDIDATES:
        return await _guard_batch(db, owner, orders, state=state)
    with source_read_budget(20):
        try:
            shared = state if state is not None else await _read_state(db, owner)
            if shared is None:
                shared = {"owner_id": owner, "engine": {"facts": {}, "obligations": {}}, "movements": []}
            if SNAPSHOT in shared:
                shared = await _prepare_read(partial(unpack_financial_snapshot, selected={"facts", "obligations"}), shared)
        except Exception:
            shared = {}  # Invalid state retains every proposed exclusion.
        checked = {}
        for start in range(0, len(proposed), MAX_CANDIDATES):
            originals = proposed[start:start + MAX_CANDIDATES]
            batch = await _guard_batch(db, owner, originals, state=shared)
            checked.update((id(original), value) for original, value in zip(originals, batch))
        return [checked.get(id(row), {**row, ELIGIBILITY_FIELD: dict(row.get(ELIGIBILITY_FIELD) or {})}) for row in orders]
