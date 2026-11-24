"""Complete only a source-bound, no-send Salla status/payment read model."""
from copy import deepcopy

CONNECTOR = "salla_direct_status_resync"
PAYMENT_FIELDS = frozenset({
    "payment_method", "payment_status", "paid_amount", "remaining_amount",
    "has_remaining_amount", "is_pending_payment", "payment_collection_status",
    "payment_checkout_url", "receiving_bank_name", "receiving_bank_id", "payment_receipt_url",
})
CUSTOMER_FIELDS = frozenset({"customer_name", "customer_mobile", "customer_email"})
READ_MODEL_FIELDS = PAYMENT_FIELDS | CUSTOMER_FIELDS
NO_SEND = {"no_qoyod_send": True, "eligibility_only": True,
           "manual_send_allowed": False, "auto_send_allowed": False}


def _text(value):
    return type(value) is str and bool(value.strip())


def _no_send(row, owner):
    return (isinstance(row, dict) and row.get("user_id") == owner
            and row.get("connector_key") == row.get("source") == CONNECTOR
            and row.get("pipeline_stage") == "STATUS_SNAPSHOT"
            and all(row.get(key) is value for key, value in NO_SEND.items()))


async def validate_update(db, owner, name, query, update, options):
    """Capability validation; no ledger, lifecycle, source or send permission."""
    if (not isinstance(query, dict) or query.get("user_id") != owner
            or not isinstance(update, dict) or not update
            or set(options) - {"upsert"}
            or ("upsert" in options and type(options["upsert"]) is not bool)):
        return False
    values = update.get("$set")
    if not isinstance(values, dict) or not values:
        return False
    if name == "unified_orders":
        return (set(query) == {"user_id", "order_number"} and _text(query["order_number"])
                and not options.get("upsert") and set(update) == {"$set"}
                and set(values) <= READ_MODEL_FIELDS | {"qoyod_live_preflight_at", "qoyod_live_preflight_source"}
                and values.get("qoyod_live_preflight_source") == "salla_order_details")
    if name != "integration_inbox" or query.get("connector_key") != CONNECTOR:
        return False
    if set(query) == {"user_id", "connector_key", "idempotency_key"}:
        on_insert = update.get("$setOnInsert", {})
        if not isinstance(on_insert, dict):
            return False
        canonical = values.get("canonical_payload")
        number = values.get("salla_order_number")
        status = canonical.get("order_status") if isinstance(canonical, dict) else None
        identity = f"salla:order:{number}:order.updated:{status}"
        return (_no_send(values, owner) and _text(number) and _text(status)
                and canonical.get("order_number") == number
                and query["idempotency_key"] == values.get("idempotency_key") == identity
                and _text(values.get("trace_id"))
                and set(values) == {"trace_id", "user_id", "connector_key", "idempotency_key",
                    "salla_order_number", "source", "received_at", "updated_at", "canonical_payload",
                    "pipeline_stage", *NO_SEND, "salla_direct_status_resync"}
                and set(update) <= {"$set", "$setOnInsert"}
                and (not options.get("upsert") or
                     set(on_insert) == {"id", "created_at"})
                and set(on_insert) <= {"id", "created_at"})
    allowed = {"canonical_payload." + field for field in READ_MODEL_FIELDS} | {
        CONNECTOR + ".payment_facts_complete", CONNECTOR + ".payment_facts_at",
        CONNECTOR + ".customer_identity_facts_complete",
    }
    if (set(query) != {"user_id", "connector_key", "trace_id"} or not _text(query["trace_id"])
            or options.get("upsert") or set(update) != {"$set"} or not set(values) <= allowed):
        return False
    return _no_send(await db.integration_inbox.find_one(query), owner)


async def _require_current_owner(db, owner):
    from fastapi import HTTPException
    from accounting_module_contract import require_owner
    actor = await db.users.find_one({"id": owner},
        {"id": 1, "role": 1, "disabled": 1, "is_active": 1, "deleted_at": 1}) or {}
    require_owner(actor)
    if (actor.get("id") != owner or actor.get("disabled") or actor.get("is_active") is False
            or actor.get("deleted_at")):
        raise HTTPException(403, "source_status_completion_owner_unavailable")


async def complete_status_snapshot(db, owner, order_number, source_result):
    """Retry read-model completion without repeating source/physical admission.

    Source persistence already committed. Recheck the exact saved source and
    revision inside a separate restricted owner transaction. A newer source,
    deletion or cancellation cannot race an old payment/status write into place.
    """
    from fulfillment_v2_routes import component_provider_version
    from operational_atomic import operational_owner
    from salla_integration import sync

    await _require_current_owner(db, owner)
    expected = deepcopy(source_result.get("doc") or {})
    raw = (expected.get("raw_by_source") or {}).get("salla_direct")
    watermark = expected.get("g47_salla_snapshot") or {}
    revision = source_result.get("snapshot_revision", watermark.get("revision"))
    if (not isinstance(raw, dict) or expected.get("user_id") != owner
            or expected.get("order_number") != order_number
            or str(raw.get("reference_id") or raw.get("order_number") or "") != order_number
            or not raw.get("id") or type(revision) is not int or revision < 1
            or any(source_result.get(key) for key in ("blocked", "stale", "skipped"))):
        raise ValueError("source_status_completion_evidence_missing")
    version = component_provider_version(raw)
    if not version:
        raise ValueError("source_status_completion_clock_missing")
    # Derive follow-up fields from admitted native source, not caller values.
    order_doc = sync._salla_order_to_doc(raw)
    status = raw.get("status") or {}
    status = status.get("slug") or status.get("name") if isinstance(status, dict) else status
    cancelled = str(status or "").strip().lower() in {"cancel", "cancelled", "canceled", "refunded", "ملغي", "ملغى"}

    async def complete(scoped):
        await _require_current_owner(scoped, owner)
        current = await scoped.unified_orders.find_one(
            {"user_id": owner, "order_number": order_number},
            {"user_id": 1, "order_number": 1, "raw_by_source.salla_direct": 1, "g47_salla_snapshot": 1},
        ) or {}
        saved = current.get("g47_salla_snapshot") or {}
        if (current.get("user_id") != owner or current.get("order_number") != order_number
                or saved.get("revision") != revision or saved.get("source_updated_at") != version
                or bool(saved.get("cancelled")) != cancelled
                or (source_result.get("unchanged") and saved.get("component_pending") is not False)
                or saved.get("requires_authoritative_refresh")
                or (current.get("raw_by_source") or {}).get("salla_direct") != raw):
            raise ValueError("source_changed_before_status_completion")
        result = await sync._refresh_plan_b_status_snapshot(
            scoped, owner, order_number, order_doc, allow_handoff_transition=False,
        )
        if not isinstance(result, dict) or not _text(result.get("trace_id")) or result.get("no_qoyod_send") is not True:
            raise ValueError("source_status_completion_incomplete")
        # The source transaction already ended; check current owner access again
        # outside this snapshot before the read-model transaction may commit.
        await _require_current_owner(db, owner)
        return result

    return await operational_owner(db, owner, complete, profile="salla_status_snapshot")
