"""Qoyod Invoice MVP — data models + Mongo index helpers.

Collections introduced in this module:

  Generic (reusable across all input connectors — per user feedback
  on Day 1 review so the inbox can serve Salla / Make / Qoyod etc.):
      integration_inbox            append-only raw webhook/poll events

  Qoyod-specific:
      qoyod_settings               connector configuration
      qoyod_credentials            encrypted API key store
      qoyod_invoices               one row per Salla order → Qoyod invoice
      qoyod_products_mapping       SKU → Qoyod product mapping
      qoyod_customers_mapping      phone/email → Qoyod contact mapping

Architectural notes (ADR-001):
    #1  Additive       — none of these existed before.
    #4  Canonical      — `integration_inbox.canonical_payload` is our
                         typed DTO. Provider JSON lives in `raw_payload`.
    #8  Event Driven   — `integration_inbox` is append-only.
    #10 Idempotency    — every inbox row has an `idempotency_key`.
    #11 Multi-Tenant   — every model has `user_id` even though MVP
                         uses a single "main" tenant.
    #13 Versioning     — `schema_version: int = 1` on every model.
    #14 Secrets        — `qoyod_credentials` uses Fernet.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Literal

from pydantic import BaseModel, ConfigDict, Field
import pymongo


# ─────────────────────────────────────────────────────────────────────
# Constants — canonical vocabularies
# ─────────────────────────────────────────────────────────────────────
PRODUCT_TYPE_MODES = ("service", "inventory", "per_product")
PRODUCT_TYPES      = ("service", "inventory")

# Pipeline stages — LEGACY lowercase tuple kept for backwards compat with
# the Day-1 lock-in tests. The canonical Pre-Day 3 state machine uses the
# UPPERCASE vocabulary defined in `state_machine.py` (HAPPY_PATH +
# failure stages + RETRYING/SKIPPED). New code MUST use those.
PIPELINE_STAGES = (
    "received",        # raw webhook stored, nothing else
    "normalized",      # canonical_payload filled
    "rules_applied",   # business rules passed (send/skip decision)
    "customer_ready",  # 4a done
    "products_ready",  # 4b done
    "invoiced",        # 4c done — invoice exists in Qoyod
    "receipted",       # 4d done — receipt exists in Qoyod
    "completed",       # final happy state
    "skipped",         # business rule decided not to send (e.g. unpaid)
    "failed",          # terminal failure — needs human intervention
)

# Top-level invoice status used by the UI / monitoring page.
INVOICE_STATUSES = (
    "pending",                      # waiting for pipeline to start
    "sent",                         # invoice + receipt both succeeded
    "invoice_sent_receipt_failed",  # split state per user spec
    "failed",                       # terminal — needs intervention
    "retrying",                     # transient — background worker active
    "skipped",                      # business rule decided not to send
)

# ─── Compliance Watch — eligibility vocabulary (Pre-Day 3 spec) ──────
# Computed live for every order with status "تم التنفيذ" so the UI can
# show: "is this order supposed to be in Qoyod yet? where is it stuck?"
ELIGIBILITY_STATUSES = (
    "not_eligible",                  # order not "تم التنفيذ" yet
    "eligible_pending",              # ready to send, not yet sent
    "sent_to_qoyod",                 # invoice + receipt both done
    "failed_before_qoyod",           # failed somewhere before invoice
    "invoice_sent_receipt_failed",   # invoice succeeded, receipt failed
)

# Reason codes — one Arabic-friendly label per cause. Keep the list
# closed so the UI can map every code to a translated string.
ELIGIBILITY_REASONS = (
    "order_status_not_completed",      # status ≠ تم التنفيذ
    "order_completed_ready_to_send",   # eligible, nothing wrong
    "missing_customer_data",           # buyer name/phone missing
    "missing_product_mapping",         # SKU has no Qoyod mapping
    "payment_method_mapping_missing",  # payment method not mapped
    "qoyod_api_error",                 # Qoyod API rejected
    "already_sent",                    # invoice already exists
)

# Send trigger — per user directive: only when Salla status = "تم التنفيذ".
# We store the Salla string verbatim so we never normalise away the
# original signal. Mapped to canonical SHIP_COMPLETED at normalization.
SALLA_TRIGGER_STATUS = "completed"   # ASCII alias; Arabic = "تم التنفيذ"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Settings — single-row connector config
# ─────────────────────────────────────────────────────────────────────
class QoyodPaymentMethodMapping(BaseModel):
    """Salla payment-method key → Qoyod account_id used on the Receipt.

    Iter-293 — Two new fields:
      • posting_mode: paid_receipt | credit_invoice_only | disabled.
        Defaults to paid_receipt for backwards-compat with rows that
        pre-date this field.
      • qoyod_account_id is now Optional[str] because COD rows
        (posting_mode=credit_invoice_only) MUST NOT have an account.
    """
    salla_method:     str
    qoyod_account_id: Optional[str] = None
    posting_mode:     Optional[str] = "paid_receipt"
    label_ar:         Optional[str] = None


class QoyodCapabilityFlags(BaseModel):
    """Fine-grained on/off switches per Qoyod operation. Lets the
    merchant turn off receipt creation (for example) while keeping
    invoice creation on, without touching code — per user spec.

    Default state: all ON because the MVP exercises all four.
    """
    model_config = ConfigDict(extra="allow")
    create_customers: bool = True   # 4a
    create_products:  bool = True   # 4b
    create_invoices:  bool = True   # 4c
    create_receipts:  bool = True   # 4d


class QoyodSettings(BaseModel):
    """One document per tenant. For MVP user_id is always 'main'.

    All fields below are forward-compatible: the MVP uses only a subset,
    but the schema is wide enough to accept future toggles without a
    migration. This keeps ADR-001 #1 (Additive) intact when we later
    grow the integration.
    """
    model_config = ConfigDict(extra="allow")
    schema_version: int = 1
    user_id:        str = "main"

    # ─── Master switches ────────────────────────────────────────────
    enabled:           bool = False     # ADR-001 #3 — connector master toggle
    auto_send:         bool = True      # pipeline runs automatically on inbox
    auto_receipt:      bool = True      # create receipt right after invoice
    # Day 5 safety net — when True the pipeline goes through every step,
    # builds the EXACT Qoyod payload, but skips the final POST. The
    # payload snapshot is saved to the row so the operator can review
    # large batches before going live.
    dry_run_mode:      bool = False

    # ─── Send rule (per user directive on Day 1 / Day 4) ────────────
    # The Invoice Trigger Policy controls WHEN an order becomes eligible
    # for Qoyod and what date the invoice carries. NEVER hard-code "paid"
    # as the sole trigger — the merchant is responsible (legally, for VAT
    # and Zakat) for what status drives the invoice date.
    #
    #   `invoice_trigger_statuses`  — closed list; matched against the
    #       order's CANONICAL status. Default ["completed"] (= Arabic
    #       "تم التنفيذ"). Multiple statuses allowed (e.g. ["completed",
    #       "delivered"]).
    #   `invoice_date_source`       — picks WHICH timestamp goes on the
    #       Qoyod invoice. Iter-293.4-rev9 canonical value: `send_date`
    #       = current Asia/Riyadh moment when the invoice is being
    #       created in قيود (ZATCA-correct issue_date). Legacy values
    #       `completed_at` / `trigger_status_date` (Salla-side event
    #       timestamps) are auto-migrated to `send_date` on load.
    #   `trigger_once_only`         — if True (default), once a Qoyod
    #       invoice exists for a Salla order we ignore subsequent status
    #       transitions — never auto-re-create or duplicate.
    invoice_trigger_statuses: list[str] = Field(
        default_factory=lambda: ["completed"])
    invoice_date_source: Literal[
        "send_date", "trigger_status_date", "completed_at",
        "paid_at", "created_at",
    ] = "send_date"
    trigger_once_only: bool = True

    # ─── Legacy single-status field (deprecated — read-only mirror) ──
    # Kept for backwards compatibility with existing DB docs written by
    # Day 1 settings. New code reads `invoice_trigger_statuses` only.
    invoice_trigger_status: Optional[str] = None

    # ─── Qoyod refs (populated after first /test-connection) ────────
    default_branch_id:    Optional[str] = None
    default_tax_id:       Optional[str] = None    # 15% VAT id in Qoyod
    inventory_account_id: Optional[str] = None    # only used for inventory mode
    cost_account_id:      Optional[str] = None    # COGS account
    default_customer_id:  Optional[str] = None    # for guests (override)

    # ─── Product Type configuration (user spec — 3 modes) ───────────
    default_product_type: Literal["service", "inventory", "per_product"] = "service"

    # ─── Payment-method mapping (Salla key → Qoyod account_id) ──────
    payment_method_mapping: list[QoyodPaymentMethodMapping] = Field(default_factory=list)

    # ─── Capability flags (per Day-1 review) ────────────────────────
    capabilities: QoyodCapabilityFlags = Field(
        default_factory=QoyodCapabilityFlags)

    # ─── Legacy-Adapter enrichment toggle ───────────────────────────
    # When `false` (default): a payload arriving WITHOUT line items
    # (neither `items[]` nor `packages[]`) is dead-on-arrival. The row
    # transitions to FAILED_VALIDATION with code `missing_items_no_enricher`
    # and is NEVER promoted to invoice creation.
    # When `true`: the same payload transitions to NEEDS_ENRICHMENT and
    # the (separately implemented) Salla-API enricher is invoked.
    # User policy 2026-06-26: opt-in only. Default OFF.
    enrichment_fallback_enabled: bool = False

    # ─── Backfill mode (Iter-267, user directive 2026-02-27) ────────
    # Controls what the background worker does with pre-activation rows
    # still in flight (NORMALIZED / CUSTOMER_RESOLVED / PRODUCT_RESOLVED)
    # the moment Go-Live is flipped on.
    #
    #   "now_forward_only" (DEFAULT, strict)
    #       Worker IGNORES every row where
    #       `received_at < go_live_activated_at`. Such pre-activation
    #       rows are transitioned to SKIPPED with reason
    #       `pre_activation_skipped` so they show up in the monitor
    #       but never reach Qoyod. Production receives only webhooks
    #       arriving AFTER activation.
    #
    #   "backfill_unsent" (explicit operator opt-in)
    #       Worker drains pre-activation rows too. Use only when the
    #       operator explicitly wants to push old unsent orders.
    backfill_mode: Literal[
        "now_forward_only", "backfill_unsent"
    ] = "now_forward_only"

    # ─── Future-ready placeholders (reserved; not used by MVP) ──────
    # These keep the schema stable when we later add refunds, sync
    # cancellations, ZATCA local validation, custom invoice prefixes,
    # tax exemption rules, multi-branch routing, etc. Adding a value
    # here today costs nothing; it spares us a migration tomorrow.
    auto_credit_note_on_refund:  bool = False    # future: refund flow
    auto_cancel_on_salla_cancel: bool = False    # future: cancellation flow
    invoice_number_prefix:       Optional[str] = None  # future: numbering policy
    branch_routing_rule:         Optional[str] = None  # future: multi-branch
    tax_exemption_rule:          Optional[str] = None  # future: per-customer
    custom_metadata:             dict[str, Any] = Field(default_factory=dict)

    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)


# ─────────────────────────────────────────────────────────────────────
# Credentials — encrypted API key (Fernet-wrapped)
# ─────────────────────────────────────────────────────────────────────
class QoyodCredentials(BaseModel):
    """Stores the encrypted Qoyod API key. NEVER returned to API responses."""
    model_config = ConfigDict(extra="allow", arbitrary_types_allowed=True)
    schema_version: int = 1
    user_id:        str = "main"
    api_key_enc:    bytes                          # opaque ciphertext
    fingerprint:    str                            # short hash for UI ("abc…123")
    last_verified_at: Optional[datetime] = None
    rotated_at:     Optional[datetime] = None
    created_at:     datetime = Field(default_factory=_now)
    updated_at:     datetime = Field(default_factory=_now)


# ─────────────────────────────────────────────────────────────────────
# Inbox — raw incoming events (generic — reusable across connectors)
# ─────────────────────────────────────────────────────────────────────
class IntegrationInbox(BaseModel):
    """Append-only inbox per ADR-001 #8. Generic across all input
    connectors (Make.com today; Salla / Tamara / others later) — the
    `connector_key` discriminator keeps rows from different sources
    in one queryable table. The pipeline only mutates derived fields
    (pipeline_stage, canonical_payload, attempts…). `raw_payload` and
    `idempotency_key` are immutable after insert.
    """
    model_config = ConfigDict(extra="allow")
    schema_version: int = 1

    id:             str
    user_id:        str = "main"
    trace_id:       str                            # links inbox row + outcome
    connector_key:  str                            # "make_com_qoyod" today;
                                                   # "salla_direct" later; etc.

    # Source identification
    source:         Literal["webhook", "cron", "manual", "replay"] = "webhook"
    received_at:    datetime = Field(default_factory=_now)
    raw_payload:    dict[str, Any]
    raw_headers:    dict[str, Any] = Field(default_factory=dict)
    signature_status: Literal["verified", "missing", "invalid"] = "missing"

    # Salla order anchor — extracted at insert time for fast lookup
    salla_order_id:     Optional[str] = None
    salla_order_number: Optional[str] = None

    idempotency_key: str                            # unique per (connector_key, key)

    # Pipeline state (the only mutable section)
    # Stage tokens are canonical UPPERCASE per the Pre-Day 3 state
    # machine. Initial value `"NEW"` is set when the row is first
    # created; transitions go strictly through
    # `integrations.qoyod.state_machine.transition()`.
    pipeline_stage: str = "NEW"
    pipeline_error: Optional[dict[str, Any]] = None
    attempts:       int = 0
    next_retry_at:  Optional[datetime] = None
    processed_at:   Optional[datetime] = None

    # Append-only timeline of every state transition. Each entry:
    #   {from_stage, to_stage, at, actor, note?, error?}
    # Written ONLY by `state_machine.transition()` so the format
    # stays consistent for the Timeline UI.
    stage_history:  list[dict[str, Any]] = Field(default_factory=list)

    # ─── Audit Trail (Pre-Day 3 spec — every row is self-describing) ─
    # All five fields are set by `state_machine.transition()` so the
    # operator never has to reconstruct lifecycle data from logs.
    pipeline_started_at:  Optional[datetime] = None
    pipeline_finished_at: Optional[datetime] = None
    pipeline_duration_ms: Optional[int]      = None
    pipeline_outcome:     Optional[str]      = None  # terminal stage name
    last_success_stage:   Optional[str]      = None
    last_failed_stage:    Optional[str]      = None

    # Stage-3 output (canonical DTO)
    canonical_payload: Optional[dict[str, Any]] = None

    # Outcome ref (filled after invoiced/receipted stages)
    qoyod_invoice_row_id: Optional[str] = None


# Backwards-compat alias kept for one iteration so any half-written
# call sites don't break. Will be removed once Day 3 imports settle.
QoyodInbox = IntegrationInbox


# ─────────────────────────────────────────────────────────────────────
# Invoices — the audit ledger of "did this order reach Qoyod?"
# ─────────────────────────────────────────────────────────────────────
class QoyodInvoiceRecord(BaseModel):
    """One row per Salla order processed for Qoyod. The single
    source of truth for "what was sent and what came back"."""
    model_config = ConfigDict(extra="allow")
    schema_version: int = 1

    id:           str
    user_id:      str = "main"
    trace_id:     str

    salla_order_id:      str
    salla_order_number:  Optional[str] = None
    salla_order_status:  Optional[str] = None
    # User rule: invoice_date = transition date to "تم التنفيذ"
    completed_at_salla:  Optional[datetime] = None

    # Qoyod refs (fill as pipeline progresses)
    qoyod_invoice_id:      Optional[str] = None
    qoyod_invoice_number:  Optional[str] = None
    qoyod_receipt_id:      Optional[str] = None
    qoyod_customer_id:     Optional[str] = None

    # Summary numbers (for the monitoring page)
    customer_label: Optional[str] = None
    total_amount:   Optional[float] = None
    tax_amount:     Optional[float] = None
    items_count:    int = 0
    payment_method: Optional[str] = None

    # Status & error
    status:        str = "pending"      # one of INVOICE_STATUSES
    # Pipeline stage mirror — kept in sync with the matching inbox row
    # so the Invoices Data Grid can render the precise stage without a
    # join. Values come from the UPPERCASE canonical state machine.
    pipeline_stage: str = "NEW"
    # Compliance Watch (Pre-Day 3 spec)
    eligibility_status: str = "not_eligible"     # one of ELIGIBILITY_STATUSES
    eligibility_reason: str = "order_status_not_completed"  # one of ELIGIBILITY_REASONS
    attempts:      int = 0
    last_error:    Optional[dict[str, Any]] = None
    last_attempt_at: Optional[datetime] = None
    sent_at:       Optional[datetime] = None

    # Append-only timeline (mirrors the inbox row's history so the
    # invoice view stays self-contained for the operator).
    stage_history: list[dict[str, Any]] = Field(default_factory=list)

    # Audit Trail — mirrors `IntegrationInbox` so a single row is
    # enough for the operator to reason about lifecycle + duration.
    pipeline_started_at:  Optional[datetime] = None
    pipeline_finished_at: Optional[datetime] = None
    pipeline_duration_ms: Optional[int]      = None
    pipeline_outcome:     Optional[str]      = None
    last_success_stage:   Optional[str]      = None
    last_failed_stage:    Optional[str]      = None

    created_at:    datetime = Field(default_factory=_now)
    updated_at:    datetime = Field(default_factory=_now)


# ─────────────────────────────────────────────────────────────────────
# Mappings — auto-populated lookups (no manual entry in MVP)
# ─────────────────────────────────────────────────────────────────────
class QoyodProductMapping(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: int = 1
    user_id:           str = "main"
    sku:               str                          # unique per user
    qoyod_product_id:  str
    qoyod_product_name: Optional[str] = None
    product_type:      Literal["service", "inventory"] = "service"
    is_non_stock:      bool = True
    inventory_account_id: Optional[str] = None
    cost_account_id:      Optional[str] = None
    resolved_via:      Literal["global_setting", "per_product_override"] = "global_setting"
    auto_created:      bool = True
    created_at:        datetime = Field(default_factory=_now)


class QoyodCustomerMapping(BaseModel):
    model_config = ConfigDict(extra="allow")
    schema_version: int = 1
    user_id:           str = "main"
    lookup_key:        str                          # phone (E.164) or email (lower)
    lookup_kind:       Literal["phone", "email", "guest_order"] = "phone"
    qoyod_customer_id: str
    customer_name:     Optional[str] = None
    phone:             Optional[str] = None
    email:             Optional[str] = None
    auto_created:      bool = True
    created_at:        datetime = Field(default_factory=_now)


# ─────────────────────────────────────────────────────────────────────
# Index management — called once at startup
# ─────────────────────────────────────────────────────────────────────
async def ensure_qoyod_indexes(db) -> None:
    """Idempotent index creation. Safe to call multiple times.

    Indexes are defined here (next to the models) to make schema
    auditing trivial. Each index has a `name=` so future migrations
    can target them precisely.
    """
    # --- qoyod_settings ---
    await db.qoyod_settings.create_index(
        [("user_id", pymongo.ASCENDING)],
        unique=True, name="qoyod_settings_user_unique",
    )

    # --- qoyod_credentials ---
    await db.qoyod_credentials.create_index(
        [("user_id", pymongo.ASCENDING)],
        unique=True, name="qoyod_credentials_user_unique",
    )

    # --- integration_inbox (generic for all input connectors) ---
    # Idempotency unique key is scoped per (user_id, connector_key,
    # idempotency_key) so two connectors can reuse the same upstream
    # identifier without collision.
    await db.integration_inbox.create_index(
        [("user_id", pymongo.ASCENDING),
         ("connector_key", pymongo.ASCENDING),
         ("idempotency_key", pymongo.ASCENDING)],
        unique=True, name="integration_inbox_idem_unique",
    )
    await db.integration_inbox.create_index(
        [("user_id", pymongo.ASCENDING),
         ("pipeline_stage", pymongo.ASCENDING),
         ("next_retry_at", pymongo.ASCENDING)],
        name="integration_inbox_stage_retry",
    )
    await db.integration_inbox.create_index(
        [("user_id", pymongo.ASCENDING),
         ("received_at", pymongo.DESCENDING)],
        name="integration_inbox_received_at_desc",
    )
    await db.integration_inbox.create_index(
        [("salla_order_id", pymongo.ASCENDING)],
        name="integration_inbox_order_lookup", sparse=True,
    )
    await db.integration_inbox.create_index(
        [("user_id", pymongo.ASCENDING),
         ("salla_order_number", pymongo.ASCENDING)],
        name="integration_inbox_owner_order_number", sparse=True,
    )

    # --- qoyod_invoices ---
    await db.qoyod_invoices.create_index(
        [("user_id", pymongo.ASCENDING),
         ("salla_order_id", pymongo.ASCENDING)],
        unique=True, name="qoyod_invoices_order_unique",
    )
    await db.qoyod_invoices.create_index(
        [("user_id", pymongo.ASCENDING),
         ("status", pymongo.ASCENDING),
         ("updated_at", pymongo.DESCENDING)],
        name="qoyod_invoices_status_updated",
    )
    await db.qoyod_invoices.create_index(
        [("trace_id", pymongo.ASCENDING)],
        name="qoyod_invoices_trace",
    )
    # Compliance Watch lookup — surfaces orders that ARE eligible
    # but haven't reached "sent_to_qoyod" yet.
    await db.qoyod_invoices.create_index(
        [("user_id", pymongo.ASCENDING),
         ("eligibility_status", pymongo.ASCENDING),
         ("updated_at", pymongo.DESCENDING)],
        name="qoyod_invoices_eligibility",
    )

    # --- qoyod_products_mapping ---
    await db.qoyod_products_mapping.create_index(
        [("user_id", pymongo.ASCENDING),
         ("sku", pymongo.ASCENDING)],
        unique=True, name="qoyod_products_sku_unique",
    )

    # --- qoyod_customers_mapping ---
    await db.qoyod_customers_mapping.create_index(
        [("user_id", pymongo.ASCENDING),
         ("lookup_key", pymongo.ASCENDING)],
        unique=True, name="qoyod_customers_lookup_unique",
    )

    # --- qoyod_webhook_events (Iter-293 audit log) ---
    # Append-only log of every webhook arrival from Make/Salla. Used
    # by the Webhook Monitor UI. Separate from integration_inbox so a
    # noisy upstream can't bloat the processing queue.
    #   Retention: 7-day TTL + a soft 1000-row cap (enforced by query
    #   side, not by index — capped collections can't have TTL).
    await db.qoyod_webhook_events.create_index(
        [("user_id", pymongo.ASCENDING),
         ("received_at", pymongo.DESCENDING)],
        name="qoyod_webhook_events_received",
    )
    await db.qoyod_webhook_events.create_index(
        [("received_at", pymongo.ASCENDING)],
        name="qoyod_webhook_events_ttl",
        expireAfterSeconds=7 * 24 * 3600,
    )
    await db.qoyod_webhook_events.create_index(
        [("user_id", pymongo.ASCENDING),
         ("salla_order_id", pymongo.ASCENDING)],
        name="qoyod_webhook_events_order_lookup", sparse=True,
    )
    await db.qoyod_webhook_events.create_index(
        [("user_id", pymongo.ASCENDING),
         ("event_type", pymongo.ASCENDING),
         ("received_at", pymongo.DESCENDING)],
        name="qoyod_webhook_events_event_type",
    )

    # --- validated Plan-B automatic sender ---
    # Retain durable audit history for reconciliation and future analytics.
    # The short worker lease itself is uniquely keyed by Mongo's `_id`.
    await db.qoyod_manual_auto_runs.create_index(
        [("run_id", pymongo.ASCENDING)],
        unique=True, name="qoyod_manual_auto_runs_run_unique",
    )
    await db.qoyod_manual_auto_runs.create_index(
        [("started_at", pymongo.DESCENDING)],
        name="qoyod_manual_auto_runs_started",
    )
    await db.qoyod_manual_auto_runs.create_index(
        [("status", pymongo.ASCENDING),
         ("finished_at", pymongo.DESCENDING)],
        name="qoyod_manual_auto_runs_status_finished",
    )
