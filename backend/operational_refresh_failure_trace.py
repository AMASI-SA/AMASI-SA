"""Sanitized, bounded failure trace for operational source refresh.

Only fixed stage labels and non-identifying timings are retained. Raw exception
text, order/customer identities, URLs, cursor documents and amounts are never
persisted. No dependencies on FastAPI, MongoDB, or running refresh tasks.
"""
from __future__ import annotations

import math

MAX_STAGES = 20
MAX_READS = 4
MAX_DURATION_MS = 300000

STAGES = frozenset({
    "native_evidence", "aggregate", "source_collection", "source_collision",
    "reconciliation", "profit_snapshot", "bank_claims", "party_catalogs",
    "payment_evidence_link",
    "bundle_order_normalization", "bundle_cost_authority", "snapshot_load", "snapshot_compare", "snapshot_pack",
    "snapshot_commit", "snapshot_raw_read", "snapshot_unpack",
    "snapshot_copy_prepare", "orders_cursor_build", "orders_cursor_read",
    "orders_rows_validate", "source_primary_reads", "source_fee_settings",
    "source_setup", "source_statements", "source_delivery_history",
    "source_transactions", "source_native_links", "source_bundles",
    "source_option_catalog", "source_orders", "source_order_indexes",
    "source_bank_catalog", "source_invoices", "source_other_sources",
    "source_native_overlaps", "source_finalize",
})
OUTCOMES = frozenset({"complete", "failed", "cancelled"})
CURSOR_KINDS = frozenset({"motor", "coroutine", "unknown"})
READ_FIELDS = frozenset({
    "queue_ms", "budget_ms", "first_row_ms", "last_checkpoint_ms",
    "read_elapsed_ms", "cancel_join_ms", "close_ms",
})
OPTIONAL_FIELDS = frozenset({"first_row_ms", "last_checkpoint_ms", "read_elapsed_ms", "close_ms"})


def _duration(value):
    if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= MAX_DURATION_MS:
        return None
    return round(value, 3)


def safe_failure_trace(payload):
    """Sanitize untrusted (possibly partial) timeout diagnostics, fail closed."""
    result = {"stage_timings": [], "primary_orders_reads": []}
    if type(payload) is not dict:
        return result
    for entry in payload.get("stage_timings", [])[-MAX_STAGES:] if type(payload.get("stage_timings")) is list else ():
        if (type(entry) is not dict or type(entry.get("stage")) is not str
                or entry["stage"] not in STAGES or type(entry.get("outcome")) is not str
                or entry["outcome"] not in OUTCOMES):
            continue
        duration = _duration(entry.get("duration_ms"))
        if duration is not None:
            result["stage_timings"].append({
                "stage":entry["stage"], "outcome":entry["outcome"], "duration_ms":duration})
    for entry in payload.get("primary_orders_reads", [])[-MAX_READS:] if type(payload.get("primary_orders_reads")) is list else ():
        if (type(entry) is not dict or entry.get("collection") != "unified_orders"
                or type(entry.get("outcome")) is not str or entry["outcome"] not in OUTCOMES
                or type(entry.get("cursor_kind")) is not str or entry["cursor_kind"] not in CURSOR_KINDS):
            continue
        retained, checkpoint = entry.get("rows_retained"), entry.get("last_checkpoint_rows")
        if (type(retained) is not int or type(checkpoint) is not int
                or not 0 <= checkpoint <= retained <= 20001):
            continue
        row = {"collection":"unified_orders", "outcome":entry["outcome"],
               "cursor_kind":entry["cursor_kind"],
               "rows_retained":retained, "last_checkpoint_rows":checkpoint}
        valid = True
        for name in READ_FIELDS:
            value = entry.get(name)
            if value is None and name in OPTIONAL_FIELDS:
                row[name] = None
            elif (number := _duration(value)) is not None:
                row[name] = number
            else:
                valid = False
                break
        if valid:
            result["primary_orders_reads"].append(row)
    return result
