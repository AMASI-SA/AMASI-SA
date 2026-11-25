"""Closed navigation source contract for the exact PR #2095 candidate.

Only named financial collections and positive metadata fields are eligible.
There is no arbitrary collection, filter or aggregation input surface.
"""
from __future__ import annotations

import re
from bson import BSON

CONTRACT_VERSION = 2
RAW_FINANCIAL = (
    "mezan_product_cost_profiles_v2", "mezan_products_v2", "mezan_product_resource_bindings_v2",
    "mezan_product_option_cost_bindings_v2", "mezan_cost_resources_v2", "mezan_preparation_pieces_v1",
    "mezan_supplier_invoices_v2", "mezan_order_item_cost_snapshots_v2", "mezan_component_order_lifecycle_v1",
    "mezan_component_consumption_plans_v1", "mezan_component_consumption_units_v1",
    "mezan_inventory_receipts_v2", "purchase_invoices", "mezan_supplier_receiving_events_v1",
    "mezan_supplier_receiving_sessions_v1", "accounting_journal_groups_v2", "accounting_general_ledger_v2",
    "accounting_audit_log_v2", "liabilities",
)
EXPECTED_COLLECTIONS = frozenset((*RAW_FINANCIAL, "unified_orders", "mezan_suppliers_v2", "store_drivers",
    "store_delivery_assignments", "mz2_shipping_setup_v2", "store_delivery_collections",
    "store_delivery_driver_earnings", "mezan_bundle_definitions_v2"))

LIFECYCLE = ("archived", "superseded", "cancelled", "returned", "deleted", "is_deleted", "status", "type",
    "source_kind", "superseded_shipment_ids")
SHIPMENT_ROOTS = ("salla_shipment_id", "tracking_number", "shipping_number", "tracking_url", "shipping_label_url",
    "shipping_status", "shipment_status", "shipment_id", "shipment", "waybill", "awb", "awb_number",
    "tracking", "label_url", "shipping_company", "shipping_company_code")
OUTER_FIELDS = ("_id", "user_id", "order_id", "salla_order_id", "order_number", "order_status", "order_status_slug",
    "payment_method", "g47_salla_snapshot", "last_salla_direct_status_source_updated_at", "salla_shipping_current",
    "store_delivery_assignment_id", "store_delivery_driver_id", "store_delivery_status", "store_delivery_payment_status",
    "delivered_at", "store_delivery_delivered_at", "store_delivery_collection_amount", *LIFECYCLE, *SHIPMENT_ROOTS)
AMOUNTS = ("currency", "paid_amount", "remaining_amount", "has_remaining_amount", "refund_amount", "refunded_amount",
    "pending_refund_amount", "refunded_transactions")
RAW_FIELDS = ("id", "reference_id", "order_number", "date", "created_at", "order_date", "updated_at", "date_updated",
    "status", "status_slug", "currency", "amounts", "total", "total_amount", "payment_method", "payment_methods",
    "payment_status", "payment_collection_status", "collection_status", "is_pending_payment", *AMOUNTS, "refunds", "refunded_at",
    "cancelled_at", "returned_at", "payment", "remaining_action", "payment_actions", "items", "shipping",
    "salla_shipping_current", "shipments", "type", "delivery_method", "shipping_method", "shipping_company",
    "shipping_company_name", "shipping_company_code", "courier_name", "courier_id", "courier_code",
    "delivered_at", "store_delivery_delivered_at", "store_delivery_collection_amount", *LIFECYCLE, *SHIPMENT_ROOTS)

EXACT = "exact"
CURRENT = "current"
ITEM_FIELDS = ("id", "item_id", "order_item_id", "canonical_order_item_id", "order_line_id", "source_item_id",
    "parent_product_id", "product_id", "salla_product_id", "variant_id", "quantity", "options", "choices", "attributes",
    "product_options", "selected_options", "customer_options", "options_raw", "options_normalized", "selected_context",
    "custom_fields", "customizations", "personalization", "fields", "questions", "attachments", "files")
SELECTION_FIELDS = frozenset(("options", "choices", "attributes", "product_options", "selected_options",
    "customer_options", "options_raw", "options_normalized", "selected_context", "custom_fields", "customizations",
    "personalization", "fields", "questions", "attachments", "files"))
IDENTITY = dict.fromkeys(("id", "code", "name", "name_ar", "label", "title", "display_name"), EXACT)
REFUND = dict.fromkeys(("id", "reference_id", "amount", "currency", "status", "date", "created_at", "updated_at",
    "refunded_at", "quantity", "refund_amount", "refunded_amount", "pending_refund_amount"), EXACT)
PAYMENT = dict.fromkeys(("method", "methods", "status", "payment_status", "collection_status", *AMOUNTS,
    "refunds", "refunded_at", "cancelled_at", "returned_at"), EXACT)
PAYMENT.update(refunds=REFUND, refunded_transactions=REFUND)
ACTION = dict.fromkeys(AMOUNTS, EXACT)
ACTION["refunded_transactions"] = REFUND
ITEM = dict.fromkeys(ITEM_FIELDS, EXACT)
ITEM.update(product={"id": EXACT, "options": EXACT}, variant={"id": EXACT, "options": EXACT},
    raw_item={"id": EXACT, "options": EXACT, "custom_fields": EXACT})
SHIPPING_FIELDS = ("id", "shipment_id", "status", "shipment_status", "shipping_status", "type", *LIFECYCLE,
    "provider_updated_at", "shipment_updated_at", "carrier_updated_at", "created_at", "updated_at", "delivered_at",
    "store_delivery_delivered_at", "store_delivery_collection_amount", "company", "company_name", "company_code",
    "shipping_company", "shipping_company_name", "shipping_company_code", "courier", "courier_name", "courier_code",
    "courier_id", "company_id", "external_company_name", "method", "shipping_method", "salla_shipment_id",
    "tracking_number", "tracking_id", "shipping_number", "tracking", "tracking_url", "tracking_link", "label_url",
    "shipping_label_url", "label", "awb", "awb_number", "waybill", "awb_url", "waybill_url")
SHIPPING = dict.fromkeys(SHIPPING_FIELDS, EXACT)
SHIPPING.update(company=IDENTITY, courier=IDENTITY, shipping_company=IDENTITY,
    tracking={"number": EXACT, "url": EXACT})
# The application reads only one nested shipping.shipment level. Preserve that
# exact shape; a deeper unexpected truthy object cannot be silently erased.
SHIPPING_WITH_NESTED = {**SHIPPING, "shipment": SHIPPING}
RAW_POLICY = dict.fromkeys(RAW_FIELDS, EXACT)
RAW_POLICY.update(payment=PAYMENT, remaining_action=ACTION,
    payment_actions={"remaining_action": ACTION, "refund_action": ACTION},
    items=ITEM, shipping=SHIPPING_WITH_NESTED, shipment=SHIPPING_WITH_NESTED,
    shipments=SHIPPING_WITH_NESTED, salla_shipping_current=CURRENT,
    refunds=REFUND, refunded_transactions=REFUND, tracking={"number": EXACT, "url": EXACT})
OUTER_POLICY = dict.fromkeys(OUTER_FIELDS, EXACT)
OUTER_POLICY.update(salla_shipping_current=CURRENT, shipment=SHIPPING_WITH_NESTED,
    tracking={"number": EXACT, "url": EXACT})
PRIVATE_CONTACT_KEYS = frozenset(("customer", "customer_name", "customer_email", "customer_phone", "customers",
    "address", "addresses", "recipient", "recipient_name", "recipient_phone", "receiver", "contact", "contacts",
    "billing_address", "shipping_address", "ship_to", "ship_from", "consignee", "receiver_address", "email", "phone",
    "mobile", "card", "cards", "credit_card", "bank_details"))
CURRENT_FIELDS = frozenset(("company_name", "company_code", "company_logo", "method", "shipment_id",
    "provider_updated_at", "shipment_updated_at", "carrier_updated_at", "source_kind", "source_path", "event_name",
    "tracking_number", "tracking_url", "label_url", "status", "superseded_shipment_ids",
    "verified_order_delivery_transition", "verified_order_completed_transition", "delivered_at",
    "store_delivery_delivered_at", "store_delivery_collection_amount", *LIFECYCLE))
WITNESS = "__private_capture_shape_v2"


def projection(*fields):
    return {field: 1 for field in fields}


def order_source_projection():
    return {**projection(*OUTER_FIELDS), **projection(*(f"raw_by_source.salla_direct.{field}" for field in RAW_FIELDS))}


def order_pipeline(owner, maximum):
    """One fixed read-only aggregation; no caller-supplied Mongo stages."""
    fields = order_source_projection()
    fields[WITNESS] = {
        "parent_type": {"$type": "$raw_by_source"},
        "direct_type": {"$type": "$raw_by_source.salla_direct"},
        "direct_nonempty": {"$cond": [
            {"$eq": [{"$type": "$raw_by_source.salla_direct"}, "object"]},
            {"$gt": [{"$size": {"$objectToArray": "$raw_by_source.salla_direct"}}, 0]}, False]},
    }
    return [{"$match": {"user_id": owner}}, {"$project": fields}, {"$limit": maximum + 1}]


def financial_order(doc, require, checkpoint):
    """Positive financial subtree filter, preserving BSON shape and evidence.

    Only source-observed missing/null parent shapes may be restored from type
    witnesses. Arrays/scalars at the two raw-source parents are unsupported and
    abort. No placeholder, synthetic truthy value or redacted fingerprint is
    substituted for discarded evidence.
    """
    receipt = {"truthiness_checks": 0, "exact_selection_values": 0, "exact_current_observations": 0}
    visits = 0

    def private_observation(value):
        require(value is None or isinstance(value, dict) and set(value) <= CURRENT_FIELDS,
            "ORDER_CURRENT_OBSERVATION_SCHEMA")
        todo, examined = [value], 0
        while todo:
            row = todo.pop()
            examined += 1
            require(examined <= 2_000_000, "ORDER_SHAPE_LIMIT")
            if examined % 256 == 0:
                checkpoint()
            if isinstance(row, dict):
                require(not any(re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(key)).lower() in PRIVATE_CONTACT_KEYS
                    for key in row), "ORDER_PRIVATE_OBSERVATION_FIELD")
                todo.extend(row.values())
            elif isinstance(row, list):
                todo.extend(row)

    def select(value, policy, path=()):
        nonlocal visits
        visits += 1
        require(len(path) <= 64 and visits <= 2_000_000, "ORDER_SHAPE_LIMIT")
        if visits % 256 == 0:
            checkpoint()
        if policy in (EXACT, CURRENT):
            if policy == CURRENT:
                private_observation(value)
                receipt["exact_current_observations"] += 1
            if path and path[-1] in SELECTION_FIELDS:
                receipt["exact_selection_values"] += 1
            result = value
        elif isinstance(value, dict):
            result = {key: select(child, policy[key], (*path, key))
                for key, child in value.items() if key in policy}
            require(bool(value) == bool(result), "ORDER_PROJECTION_ERASES_EVIDENCE")
            receipt["truthiness_checks"] += 1
        elif isinstance(value, list):
            result = [select(child, policy, (*path, str(index))) for index, child in enumerate(value)]
            require(len(result) == len(value), "ORDER_ARRAY_SHAPE_CHANGED")
        else:
            # Scalar or null roots remain exact; never normalize them to {} or [].
            result = value
        if policy in (EXACT, CURRENT):
            require(BSON.encode({"v": value}) == BSON.encode({"v": result}), "ORDER_EXACT_VALUE_CHANGED")
        return result

    witness = doc.get(WITNESS)
    require(isinstance(witness, dict) and set(witness) == {"parent_type", "direct_type", "direct_nonempty"},
        "ORDER_SHAPE_WITNESS_MISSING")
    parent_type, direct_type = witness["parent_type"], witness["direct_type"]
    require(parent_type in {"missing", "null", "object"} and direct_type in {"missing", "null", "object"}
        and type(witness["direct_nonempty"]) is bool, "ORDER_SOURCE_PARENT_SHAPE")
    result = {key: select(value, OUTER_POLICY[key], (key,)) for key, value in doc.items() if key in OUTER_POLICY}
    if parent_type == "missing":
        require(direct_type == "missing", "ORDER_SOURCE_PARENT_SHAPE")
    elif parent_type == "null":
        require(direct_type == "missing", "ORDER_SOURCE_PARENT_SHAPE")
        result["raw_by_source"] = None
    else:
        raw_parent = doc.get("raw_by_source", {})
        require(isinstance(raw_parent, dict), "ORDER_SOURCE_PARENT_SHAPE")
        result["raw_by_source"] = {}
        if direct_type == "null":
            result["raw_by_source"]["salla_direct"] = None
        elif direct_type == "object":
            raw = raw_parent.get("salla_direct", {})
            require(isinstance(raw, dict), "ORDER_SOURCE_PARENT_SHAPE")
            clean = select(raw, RAW_POLICY, ("raw_by_source", "salla_direct"))
            require(bool(clean) == witness["direct_nonempty"], "ORDER_PROJECTION_ERASES_EVIDENCE")
            result["raw_by_source"]["salla_direct"] = clean
        else:
            require(not witness["direct_nonempty"], "ORDER_SOURCE_PARENT_SHAPE")
    checkpoint()
    return result, receipt


def extend_scopes(scopes, owner, maximum):
    """Return one unioned projection per collection, except disjoint users."""
    require_unique = {collection for _, collection, *_ in scopes if collection != "users"}
    require_unique.add("users")
    result = list(scopes)

    def add(scope, collection, fields=None, query=None, cap=None):
        if collection in require_unique:
            raise ValueError("duplicate navigation collection contract")
        require_unique.add(collection)
        result.append((scope, collection, query or {"user_id": owner},
            None if fields is None else projection("_id", *fields), cap or maximum, False))

    account_extra = ("ad_account_id", "connection_provenance", "last_sync_at", "last_observed_at", "source_mode",
        "financial_last_sync_at", "financial_last_observed_at", "financial_data_quality", "financial_coverage.status",
        "financial_coverage.data_state", "financial_coverage.expected_requests", "financial_coverage.completed_requests")
    for index, (scope, collection, query, fields, cap, required) in enumerate(result):
        if collection == "mz2_shipping_setup_v2":
            fields = {**fields, "contracts": 1, "contract_evidence": 1}
        if collection == "mezan_integration_accounts_v2":
            fields = {**fields, **projection(*account_extra)}
        result[index] = (scope, collection, query, fields, cap, required)
    for collection in RAW_FINANCIAL:
        add("financial-" + collection, collection)
    add("financial-orders", "unified_orders", fields=())
    result[-1] = (*result[-1][:3], order_source_projection(), *result[-1][4:])
    add("delivery-assignments", "store_delivery_assignments", ("user_id", "id", "order_id", "order_number",
        "driver_id", "status", "active", "assigned_at", "delivered_at"))
    for collection in ("store_delivery_collections", "store_delivery_driver_earnings"):
        add("cod-" + collection, collection, ("user_id", "order_id", "order_number", "assignment_id"))
    add("bundle-definitions", "mezan_bundle_definitions_v2", ("user_id", "product_id", "product_type"),
        {"user_id": owner, "product_type": "composite"})
    add("provider-fee-policies", "mz2_provider_fee_policies_v2", ("user_id", "provider", "policies", "fee_source_mode"))
    add("supplier-invoice-details", "operational_supplier_credit_invoice_details_v1",
        ("owner_id", "supplier_id", "invoice_id", "detail"), {"owner_id": owner})
    add("provider-statements", "operational_provider_statements_v1",
        ("id", "owner_id", "metadata", "metadata_hash", "uploaded_at"), {"owner_id": owner})
    add("settlement-files", "settlement_files", ("id", "user_id", "provider", "filename", "uploaded_at", "file_hash",
        "file_size", "header.statement_id", "header.invoice_number"),
        {"user_id": owner, "provider": {"$in": ["tabby", "tamara", "salla"]}})
    add("settlement-file-metadata", "accounting_source_files", ("user_id", "file_id", "sha256", "size"))
    add("automation-status", "operational_balance_automation_v1", ("owner_id", "actor_id", "schema_version", "enabled",
        "generation", "session_scope", "next_due_at", "last_attempt_at", "last_success_at", "last_error_code",
        "consecutive_failures", "last_outcome", "last_trace"), {"_id": owner, "owner_id": owner}, 1)
    add("refresh-health", "operational_balance_refresh_health_v1", ("owner_id", "last_attempt", "last_failure",
        "last_success_at", "source_warnings"), {"_id": owner, "owner_id": owner}, 1)
    add("refresh-lease-status", "operational_balance_refresh_leases_v1", ("owner_id", "expires_at"),
        {"_id": owner, "owner_id": owner}, 1)
    coverage = ("financial_coverage.status", "financial_coverage.data_state", "financial_coverage.expected_requests",
        "financial_coverage.completed_requests")
    add("integration-status", "mezan_integrations_v2", ("user_id", "provider", "connection_status",
        "account_selection_configured", "selected_account_ids", "financial_last_sync_at", "financial_data_quality", *coverage))
    add("integration-run-status", "mezan_integration_sync_runs_v2", ("user_id", "provider", "run_type", "status",
        "started_at", "finished_at", "summary.accounts_attempted", "summary.accounts_complete", "summary.errors_count",
        "summary.financial_proof.version", "summary.financial_proof.status", "summary.financial_proof.accounts_complete",
        "summary.financial_proof.errors_count", "summary.financial_proof.coverage.status",
        "summary.financial_proof.coverage.data_state", "summary.financial_proof.coverage.expected_requests",
        "summary.financial_proof.coverage.completed_requests"))
    add("integration-error-codes", "mezan_integration_errors_v2", ("user_id", "provider", "occurred_at", "code"))
    add("salla-status", "salla_integrations", ("user_id", "status", "store_id"))
    add("salla-reconciliation-status", "salla_scheduled_reconciliation", ("schedule_version", "status", "started_at",
        "last_success_at", "completed_slot", "last_result.errors_count", "last_result.truncated",
        "last_result.incomplete_count", "last_result.coverage_complete"), {"_id": owner}, 1)
    add("global-ads-scheduler-status", "mezan_ads_auto_sync_scheduler_v2", ("enabled", "interval_seconds"),
        {"_id": "ads-v2-server-scheduler"}, 1)
    assert EXPECTED_COLLECTIONS <= {row[1] for row in result}
    return tuple(result)
