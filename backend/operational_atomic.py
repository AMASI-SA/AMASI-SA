"""Restricted, owner-serialized fulfillment and Employee OS setup transactions.

Financial pause is not an operational stop switch. This capability preserves
Mongo atomicity while refusing financial/control writes, including when a
caller catches the refusal. No database or session handle is exposed to callers.
"""
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
import re

from fastapi import HTTPException
from pymongo import ReadPreference
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern


_ACTIVE = ContextVar("operational_transaction", default=None)
_OWNED = frozenset({
    "order_review_acceptance_config_versions",
    "order_review_workflows", "order_review_events", "order_review_completion_operations", "mezan_fulfillment_decisions_v2",
    "mezan_fulfillment_events_v2", "mezan_fulfillment_batches_v2",
    "mezan_inventory_reservations_v2", "mezan_component_order_lifecycle_v1",
    "mezan_component_consumption_plans_v1", "mezan_component_consumption_units_v1",
    "mezan_component_prebuilt_claims_v1", "mezan_preparation_pieces_v1",
    "mezan_preparation_piece_events_v1", "mezan_stock_preparation_orders_v2",
    "warehouse_location_events", "unified_orders",
})
_PROFILES = {
    "fulfillment": _OWNED | {"warehouse_locations", "mezan_inventory_receipts_v2", "products", "payment_transactions", "tamara_attribution_log", "order_review_auth_sessions_v2"},
    "employee_setup": frozenset({
        "mezan_employees_v2", "mezan_employee_salary_contracts_v2",
        "mezan_employee_events_v2",
    }),
    "driver_cash_delivery": frozenset({
        "store_delivery_assignments", "store_delivery_collections", "store_delivery_driver_earnings",
        "store_delivery_delivery_proofs", "store_delivery_events", "unified_orders", "order_review_workflows",
    }),
    "driver_cash_reconciliation": frozenset({"mz2_driver_cash_reconciliations_v1"}),
    "driver_late_delivery_evidence": frozenset({
        "store_delivery_late_evidence_events_v1", "store_delivery_delivery_proofs",
    }),
}
_DRIVER_CASH_INSERTS = {
    "store_delivery_collections": frozenset({
        "id", "user_id", "assignment_id", "order_id", "order_number", "driver_id", "amount", "amount_source",
        "payment_method", "cod_custody_amount", "receipt_reference", "receipt_url", "delivery_proof_reference",
        "delivery_proof_url", "bank_account_id", "bank_name_snapshot", "review_status", "accounting_status",
        "financial_handoff_status", "financial_source", "collected_at", "physical_cash_evidence",
        "ledger_txn_group_id", "accounting_operation_id",
    }),
    "store_delivery_driver_earnings": frozenset({
        "id", "user_id", "assignment_id", "order_id", "order_number", "driver_id", "driver_name_snapshot",
        "amount", "status", "accounting_status", "financial_handoff_status", "financial_source", "earned_at",
        "ledger_txn_group_id", "accounting_operation_id",
    }),
    "store_delivery_events": frozenset({
        "id", "user_id", "event_type", "assignment_id", "driver_id", "order_id", "earning_amount",
        "collection_amount", "payment_method", "amount_source", "receipt_reference", "delivery_proof_reference",
        "delivery_proof_url", "salla_status_slug", "occurred_at", "physical_cash_evidence_id",
    }),
}
_DRIVER_CASH_SETS = {
    "store_delivery_assignments": frozenset({
        "status", "delivered_at", "updated_at", "collection_amount", "collection_method", "payment_review_status",
        "receipt_reference", "receipt_url", "delivery_proof_reference", "delivery_proof_url", "salla_status_slug",
        "salla_status_updated_at", "accounting_status", "ledger_txn_group_id", "accounting_operation_id",
        "financial_handoff_status", "financial_source",
    }),
    "store_delivery_delivery_proofs": frozenset({"status", "bound_at", "bound_assignment_id"}),
    "unified_orders": frozenset({
        "store_delivery_assignment_id", "store_delivery_driver_id", "store_delivery_status", "store_delivery_delivered_at",
        "store_delivery_collection_amount", "store_delivery_collection_method", "store_delivery_payment_status",
        "store_delivery_payment_review_status", "store_delivery_receipt_reference", "store_delivery_receipt_url",
        "store_delivery_proof_reference", "store_delivery_proof_url", "store_delivery_salla_status_slug",
        "store_delivery_salla_status_updated_at", "store_delivery_updated_at",
    }),
    "order_review_workflows": frozenset({
        "stage", "store_courier_assignment_state", "store_courier_delivered_at", "store_courier_delivered_by_id", "updated_at",
    }),
}
_DRIVER_CASH_UNSETS = {
    "store_delivery_assignments": frozenset({
        "delivery_exception_code", "delivery_exception_note", "delivery_exception_at", "delivery_exception_by_driver_id",
        "delivery_exception_evidence_reference", "delivery_exception_evidence_url",
    }),
    "store_delivery_delivery_proofs": frozenset(),
    "unified_orders": frozenset({
        "store_delivery_exception_code", "store_delivery_exception_note", "store_delivery_exception_at",
        "store_delivery_exception_driver_id", "store_delivery_exception_evidence_reference",
        "store_delivery_exception_evidence_url", "store_delivery_customer_service_attention_required",
    }),
    "order_review_workflows": frozenset({
        "store_courier_exception_code", "store_courier_exception_note", "store_courier_exception_at",
        "store_courier_exception_driver_id", "store_courier_exception_evidence_reference",
        "store_courier_exception_evidence_url", "customer_service_attention_required",
    }),
}
_READS = frozenset({"find", "find_one", "count_documents", "distinct", "aggregate"})
_WRITES = frozenset({"insert_one", "insert_many", "update_one", "update_many",
                     "replace_one", "delete_one", "delete_many", "find_one_and_update"})
_CATALOG = frozenset({
    "id", "user_id", "product_id", "auto_catalog_key", "parent_product_id",
    "sku", "sku_normalized", "barcode", "name", "base_name", "name_lower",
    "variant_key", "variant_label", "variant_attributes", "product_type",
    "category_ids", "category_paths", "image_url", "image_urls", "needs_cost",
    "is_active", "imported", "first_seen_order_number", "last_seen_order_number",
    "last_seen_source", "first_seen_at", "last_seen_at", "seen_order_numbers",
    "created_at", "updated_at",
})
_BILLING_METADATA = frozenset({"billing_eligible_at", "effective_settlement_date", "settlement_source"})
_RECEIPT_FIELDS = frozenset({
    "id", "user_id", "idempotency_key", "payload_fingerprint", "status",
    "source_type", "source_id", "source_line_id", "stock_preparation_order_id",
    "stock_preparation_reference", "supplier_id", "supplier_name",
    "mezan_product_id", "salla_product_id", "salla_variant_id", "variant_name",
    "product_name", "sku", "quantity", "preparation_state", "specifications",
    "configuration_key", "warehouse_id", "location_id", "location_code",
    "retention_mode", "received_by", "created_at", "updated_at", "posted_at",
    "failure_code", "component_provenance",
})
_NEW_STOCK_FIELDS = frozenset({
    "item_type", "component_provenance", "receipt_id", "product_id", "mezan_product_id",
    "salla_variant_id", "variant_name", "product_name", "sku", "quantity",
    "preparation_state", "specifications", "configuration_key", "lot_id", "source_type",
    "source_id", "source_line_id", "supplier_id", "retention_mode", "placed_at", "placed_by",
})


def _reject(state, code="operational_financial_write_forbidden"):
    state["failed"] = True
    raise HTTPException(409, detail={"code": code})


def reject_financial_entry():
    """Financial helpers cannot escalate a restricted transaction capability."""
    state = _ACTIVE.get()
    if state is not None:
        _reject(state)


def _read_pipeline(value):
    if isinstance(value, dict):
        return not ({"$out", "$merge"} & value.keys()) and all(_read_pipeline(v) for v in value.values())
    return not isinstance(value, list) or all(_read_pipeline(v) for v in value)


class _Cursor:
    __slots__ = ("__cursor",)

    def __init__(self, cursor):
        self.__cursor = cursor

    async def to_list(self, *args, **kwargs):
        return await self.__cursor.to_list(*args, **kwargs)

    def sort(self, *args, **kwargs):
        self.__cursor = self.__cursor.sort(*args, **kwargs)
        return self

    def limit(self, *args, **kwargs):
        self.__cursor = self.__cursor.limit(*args, **kwargs)
        return self

    def skip(self, *args, **kwargs):
        self.__cursor = self.__cursor.skip(*args, **kwargs)
        return self

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.__cursor.__anext__()


class _Collection:
    __slots__ = ("__collection", "__session", "__state", "__owner")

    def __init__(self, collection, session, state, owner):
        self.__collection, self.__session = collection, session
        self.__state, self.__owner = state, owner

    def __getattr__(self, method):
        if method in _READS:
            def read(*args, **kwargs):
                if "session" in kwargs or (method == "aggregate" and not _read_pipeline(args[0])):
                    _reject(self.__state)
                if args and isinstance(args[0], dict):
                    query = args[0]
                    if self.__state["profile"] == "employee_setup" and self.__collection.name in {
                            "mezan_role_assignments_v2", "mezan_mobile_app_access_v1"}:
                        # Here user_id is the employee's login, not the tenant.
                        # Existing management response reads use explicit owner
                        # scope (or the bounded legacy created_by fallback).
                        if (query.get("owner_user_id") != self.__owner and not (
                                self.__collection.name == "mezan_role_assignments_v2"
                                and query.get("owner_user_id") is None
                                and query.get("created_by") == self.__owner)):
                            _reject(self.__state, "operational_owner_scope_conflict")
                    elif "user_id" in query and query["user_id"] != self.__owner:
                        _reject(self.__state, "operational_owner_scope_conflict")
                result = getattr(self.__collection, method)(*args, session=self.__session, **kwargs)
                return _Cursor(result) if method in {"find", "aggregate"} else result
            return read
        if method not in _WRITES:
            _reject(self.__state)

        async def write(*args, **kwargs):
            if "session" in kwargs or not args:
                _reject(self.__state)
            name = self.__collection.name
            if name not in _PROFILES[self.__state["profile"]]:
                _reject(self.__state)
            if name == "order_review_auth_sessions_v2":
                binding = self.__state.get("review_session")
                expected = ({"_id": binding["origin_session_ref"], "user_id": self.__owner,
                             "actor_id": binding["actor_id"], "epoch": binding["origin_session_epoch"],
                             "state": "active"} if binding else None)
                # No authority creation, revoke, expiry, actor/owner mutation,
                # arbitrary row choice or upsert through this capability.
                query = args[0] if args and isinstance(args[0], dict) else {}
                expires = query.get("expires_at", {})
                cutoff = expires.get("$gt") if isinstance(expires, dict) else None
                if (method != "update_one" or len(args) != 2 or kwargs or not expected
                        or {k: v for k, v in query.items() if k != "expires_at"} != expected
                        or not isinstance(expires, dict) or set(expires) != {"$gt"} or not isinstance(cutoff, datetime)
                        or cutoff.tzinfo is None or args[1] != {"$inc": {"fence": 1}}):
                    _reject(self.__state, "operational_review_session_scope_conflict")
                # A caller cannot backdate this fence to reuse an expired row.
                args = ({**query, "expires_at": {"$gt": max(cutoff, datetime.now(timezone.utc))}}, args[1])
            if self.__state["profile"] == "driver_cash_reconciliation" and method != "insert_one":
                _reject(self.__state)
            if self.__state["profile"] == "driver_late_delivery_evidence" and method != "insert_one":
                _reject(self.__state)
            if self.__state["profile"] == "driver_cash_delivery":
                allowed = {"insert_one"} if name in _DRIVER_CASH_INSERTS else {"update_one", "find_one_and_update"}
                if method not in allowed:
                    _reject(self.__state)
            if self.__state["profile"] == "employee_setup":
                if method not in {"insert_one", "update_one"} or (
                        name == "mezan_employee_events_v2" and method != "insert_one"):
                    _reject(self.__state)
            if name == "tamara_attribution_log" and method != "insert_one":
                _reject(self.__state)
            args = list(deepcopy(args))
            if method.startswith("insert"):
                documents = [args[0]] if method == "insert_one" else args[0]
                if not isinstance(documents, list) or not documents:
                    _reject(self.__state)
                for doc in documents:
                    if not isinstance(doc, dict) or doc.get("user_id") != self.__owner:
                        _reject(self.__state, "operational_owner_scope_conflict")
                    self._document(name, doc)
            else:
                query = args[0]
                if not isinstance(query, dict) or ("user_id" in query and query["user_id"] != self.__owner):
                    _reject(self.__state, "operational_owner_scope_conflict")
                # Immutable per-owner identities remain valid when an existing
                # caller addresses a plan/claim by _id only.
                args[0] = {**query, "user_id": self.__owner}
                if name == "mezan_inventory_receipts_v2":
                    args[0]["source_type"] = "stock_preparation_order"
                if name == "payment_transactions":
                    if query.get("provider", "tamara") != "tamara":
                        _reject(self.__state)
                    args[0]["provider"] = "tamara"
                if method == "replace_one":
                    if name not in _OWNED or args[1].get("user_id") != self.__owner:
                        _reject(self.__state)
                elif method in {"update_one", "update_many", "find_one_and_update"}:
                    await self._update(name, args[0], args[1], kwargs)
                elif name not in _OWNED:
                    _reject(self.__state)
            return await getattr(self.__collection, method)(*args, session=self.__session, **kwargs)
        return write

    def _document(self, name, doc):
        if self.__state["profile"] == "driver_late_delivery_evidence":
            if name == "store_delivery_late_evidence_events_v1":
                from store_delivery_late_evidence import verify_event
                verify_event(doc, self.__owner)
            elif (not set(doc) <= {"token", "user_id", "driver_id", "assignment_id", "evidence_kind",
                    "origin", "filename", "content_type", "size", "sha256", "content", "status",
                    "created_at", "created_by_account_user_id"}
                    or doc.get("origin") != "late_attachment" or doc.get("evidence_kind") != "delivery_proof"
                    or doc.get("status") != "uploaded"):
                _reject(self.__state)
        if self.__state["profile"] == "driver_cash_delivery":
            if not set(doc) <= _DRIVER_CASH_INSERTS[name]:
                _reject(self.__state)
            if name == "store_delivery_events":
                if doc.get("event_type") != "store_delivery_delivered":
                    _reject(self.__state)
            elif (doc.get("accounting_status") != "operational_only" or doc.get("ledger_txn_group_id") is not None
                    or doc.get("accounting_operation_id") is not None):
                _reject(self.__state)
            if name == "store_delivery_collections":
                from store_delivery_cash_evidence import validate_evidence
                if not validate_evidence(doc, self.__owner, doc.get("driver_id")):
                    _reject(self.__state)
        if self.__state["profile"] == "driver_cash_reconciliation":
            if not set(doc) <= {"_id", "id", "user_id", "driver_id", "request_id", "request_hash", "source",
                    "allocations", "reason", "recorded_by", "recorded_at", "financial_effect", "seal"}:
                _reject(self.__state)
            from store_delivery_cash_evidence import validate_link
            validate_link(doc, self.__owner, doc.get("driver_id"))
        if name == "tamara_attribution_log" and not set(doc) <= {
            "user_id", "txn_id", "provider_id", "order_reference_id", "old_source", "new_source", "old_effective", "new_effective", "at",
        }:
            _reject(self.__state)
        if name == "products":
            values = {k: v for k, v in doc.items() if k not in {"cost_current", "cost_avg", "cost_history"}}
            if (not set(values) <= _CATALOG or doc.get("cost_current") is not None
                    or doc.get("cost_avg") is not None or doc.get("cost_history", []) != []):
                _reject(self.__state)
        elif name == "mezan_inventory_receipts_v2":
            if doc.get("source_type") != "stock_preparation_order" or not set(doc) <= _RECEIPT_FIELDS:
                _reject(self.__state)
        elif name in {"warehouse_locations", "payment_transactions"}:
            _reject(self.__state)

    async def _update(self, name, query, update, kwargs):
        if (not isinstance(update, dict) or not update or
                not set(update) <= {"$set", "$unset", "$inc", "$push", "$pull", "$addToSet", "$setOnInsert"}):
            _reject(self.__state)
        fields = {field for values in update.values() for field in values}
        if any((field == "user_id" and (op not in {"$set", "$setOnInsert"} or value != self.__owner))
               or field.startswith("user_id.")
               for op, values in update.items() for field, value in values.items()):
            _reject(self.__state, "operational_owner_scope_conflict")
        if self.__state["profile"] == "driver_cash_delivery":
            if (kwargs.get("upsert") or not set(update) <= {"$set", "$unset"}
                    or not set(update.get("$set", {})) <= _DRIVER_CASH_SETS[name]
                    or not set(update.get("$unset", {})) <= _DRIVER_CASH_UNSETS[name]):
                _reject(self.__state)
            values = update.get("$set", {})
            if name == "store_delivery_assignments" and (
                    query.get("status") != "out_for_delivery" or values.get("status") != "delivered"
                    or values.get("accounting_status") != "operational_only"
                    or values.get("ledger_txn_group_id") is not None or values.get("accounting_operation_id") is not None):
                _reject(self.__state)
            if name == "store_delivery_delivery_proofs" and (
                    query.get("status") != "uploaded" or values.get("status") != "bound"):
                _reject(self.__state)
        if name == "payment_transactions":
            # Existing Tamara order-status attribution is metadata only. Never
            # authorize amounts, balances, inserts, or provider/accounting work.
            if set(update) != {"$set"} or not fields <= _BILLING_METADATA or kwargs.get("upsert"):
                _reject(self.__state)
        elif name == "products":
            if not fields <= _CATALOG or not set(update) <= {"$set", "$addToSet"}:
                _reject(self.__state)
        elif name == "mezan_inventory_receipts_v2":
            if (not fields <= _RECEIPT_FIELDS or not set(update) <= {"$set", "$unset"}
                    or kwargs.get("upsert") or update.get("$set", {}).get("source_type", "stock_preparation_order") != "stock_preparation_order"):
                _reject(self.__state)
        elif name == "warehouse_locations":
            if kwargs.get("upsert") or not set(update) <= {"$set", "$inc", "$push"}:
                _reject(self.__state)
            for op, values in update.items():
                for field, value in values.items():
                    if op == "$inc" and (field == "occupancy.total_quantity" or re.fullmatch(r"occupancy\.items\.(?:\d+|\$\[stock\])\.quantity", field)):
                        if not isinstance(value, (int, float)) or isinstance(value, bool) or value > 0:
                            # Positive placement updates its item and quantity
                            # together through the audited receipt primitive.
                            if not (field == "occupancy.total_quantity" and update.get("$push", {}).get("occupancy.items", {}).get("quantity") == value):
                                _reject(self.__state)
                    elif op == "$set" and field in {"updated_at", "state", "last_verified_scan", "last_scan_verified_at", "occupancy.total_quantity"}:
                        if field == "occupancy.total_quantity" and value != 0:
                            _reject(self.__state)
                    elif op == "$set" and field == "occupancy":
                        before = await self.__collection.find_one(query, session=self.__session)
                        old = (before or {}).get("occupancy")
                        if old is None:
                            if value != {"items": [], "total_quantity": 0}:
                                _reject(self.__state)
                        else:
                            def without_quantities(occupancy):
                                data = deepcopy(occupancy)
                                data.pop("total_quantity", None)
                                for item in data.get("items", []):
                                    item.pop("quantity", None)
                                return data
                            if not isinstance(value, dict) or without_quantities(old) != without_quantities(value):
                                _reject(self.__state)
                            if any(new.get("quantity", 0) < 0 or new.get("quantity", 0) > previous.get("quantity", 0)
                                   for previous, new in zip(old.get("items", []), value.get("items", []))):
                                _reject(self.__state)
                    elif op == "$push" and field == "occupancy.items":
                        if (not isinstance(value, dict) or not set(value) <= _NEW_STOCK_FIELDS
                                or value.get("source_type") != "stock_preparation_order"):
                            _reject(self.__state)
                    else:
                        _reject(self.__state)


class OperationalDatabase:
    __slots__ = ("__database", "__session", "__state", "__owner")

    def __init__(self, db, session, state, owner):
        self.__database, self.__session, self.__state, self.__owner = db, session, state, owner

    def __getitem__(self, name):
        return _Collection(self.__database[name], self.__session, self.__state, self.__owner)

    def __getattr__(self, name):
        if name.startswith("_") or name in {"client", "command", "get_collection", "get_database"}:
            _reject(self.__state)
        return self[name]


async def employee_setup_atomic_owner(db, owner, callback):
    """Employee + salary contract + append-only audit, including during pause.

    Uses the financial owner's same serialization row and a real Mongo
    snapshot/majority transaction. The callback receives only the restricted
    employee_setup capability, never a raw DB/session or a financial bypass.
    Financial history may be read; financial/control/login writes are denied.
    Only the boundary itself increments mz2_atomic_owners.revision; it never
    changes writes_paused or grants financial activation to a missing owner.
    """
    return await operational_owner(db, owner, callback, profile="employee_setup")


async def operational_owner(db, owner, callback, *, profile="fulfillment", review_session=None):
    """Execute audited local work, retaining owner serialization while paused."""
    from accounting_atomic import SessionDatabase
    from accounting_write_control import AccountingDatabase
    if isinstance(db, AccountingDatabase):
        db = db.current()
    active = _ACTIVE.get()
    if active is not None:
        if (active["owner"] != owner or db is not active["db"] or active["profile"] != profile
                or (review_session is not None and active.get("review_session") != review_session)):
            _reject(active, "operational_transaction_scope_conflict")
        return await callback(db)
    if not isinstance(owner, str) or not owner:
        raise HTTPException(409, detail={"code": "operational_owner_required"})
    if profile not in _PROFILES:
        raise HTTPException(409, detail={"code": "operational_profile_invalid"})
    if review_session is not None:
        from review_session_fence import _REFERENCE
        if (profile != "fulfillment" or not isinstance(review_session, dict)
                or set(review_session) != {"actor_id", "origin_session_ref", "origin_session_epoch"}
                or not isinstance(review_session["actor_id"], str) or not review_session["actor_id"]
                or not isinstance(review_session["origin_session_ref"], str)
                or not _REFERENCE.fullmatch(review_session["origin_session_ref"])
                or type(review_session["origin_session_epoch"]) is not int
                or review_session["origin_session_epoch"] != 1):
            raise HTTPException(409, detail={"code": "operational_review_session_scope_conflict"})
        review_session = deepcopy(review_session)

    async def run(root, session):
        state = {"owner": owner, "failed": False, "profile": profile, "review_session": review_session}
        scoped = OperationalDatabase(root, session, state, owner)
        state["db"] = scoped
        token = _ACTIVE.set(state)
        try:
            result = await callback(scoped)
            if state["failed"]:
                _reject(state)
            return result
        finally:
            _ACTIVE.reset(token)

    if isinstance(db, SessionDatabase):
        if db._owner != owner:
            raise HTTPException(409, detail={"code": "operational_transaction_scope_conflict"})
        return await run(db._db, db._session)
    hello = await db.command("hello")
    if not hello.get("setName") or hello.get("logicalSessionTimeoutMinutes") is None:
        raise HTTPException(503, "accounting_requires_transactional_replica_set")
    async with await db.client.start_session() as session:
        async def commit(active_session):
            # Only serialization metadata is initialized. Missing financial
            # controls remain missing and therefore financially fail closed.
            await db.mz2_atomic_owners.update_one(
                {"_id": owner}, {"$inc": {"revision": 1}}, upsert=True, session=active_session)
            return await run(db, active_session)
        return await session.with_transaction(commit, read_concern=ReadConcern("snapshot"),
            write_concern=WriteConcern("majority", j=True), read_preference=ReadPreference.PRIMARY)
