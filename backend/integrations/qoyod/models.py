"""Qoyod Invoice MVP — data models + Mongo index helpers.

Five collections (all prefixed `qoyod_*` — no collision with existing):
    qoyod_settings              single-row connector configuration
    qoyod_credentials           encrypted API key store
    qoyod_inbox                 append-only raw webhook events
    qoyod_invoices              one row per Salla order → Qoyod invoice
    qoyod_products_mapping      SKU → Qoyod product mapping
    qoyod_customers_mapping     phone/email → Qoyod contact mapping

Architectural notes (ADR-001):
    #1  Additive       — none of these existed before; we touch no
                         existing collection.
    #4  Canonical      — `qoyod_inbox.canonical_payload` is our typed
                         DTO. Provider-specific JSON lives in
                         `raw_payload`.
    #8  Event Driven   — `qoyod_inbox` is append-only; pipeline stage
                         only mutates derived fields.
    #10 Idempotency    — every inbox row has an idempotency_key based
                         on the upstream order id. Outbound calls to
                         Qoyod also send Idempotency-Key headers.
    #13 Versioning     — every Pydantic model carries `schema_version`
                         so future migrations stay backward-compatible.
    #14 Secrets        — `qoyod_credentials` uses Fernet from
                         `integrations.qoyod.crypto`.
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

# Pipeline stages for `qoyod_inbox.pipeline_stage`. Movement is strictly
# forward; the only "loops" are explicit retries that bump `attempts`
# but keep the row pointing at the failed stage.
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
    """Salla payment-method key → Qoyod account_id used on the Receipt."""
    salla_method:  str
    qoyod_account_id: str
    label_ar:      Optional[str] = None


class QoyodSettings(BaseModel):
    """One document per tenant. For MVP user_id is always 'main'."""
    model_config = ConfigDict(extra="allow")
    schema_version: int = 1
    user_id:        str = "main"

    enabled:              bool = False
    send_only_completed:  bool = True            # user rule: only "تم التنفيذ"

    # Qoyod refs (populated after first /test-connection):
    default_branch_id:  Optional[str] = None
    default_tax_id:     Optional[str] = None      # 15% VAT id in Qoyod
    inventory_account_id: Optional[str] = None    # only used for inventory mode
    cost_account_id:    Optional[str] = None      # COGS account
    default_customer_id: Optional[str] = None     # for guests (override)

    # ── Product Type configuration (user spec — 3 modes) ────────────
    default_product_type: Literal["service", "inventory", "per_product"] = "service"

    # ── Payment-method mapping (Salla key → Qoyod account_id) ───────
    payment_method_mapping: list[QoyodPaymentMethodMapping] = Field(default_factory=list)

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
# Inbox — raw incoming Make.com webhook events
# ─────────────────────────────────────────────────────────────────────
class QoyodInbox(BaseModel):
    """Append-only inbox per ADR-001 #8. The pipeline only mutates
    derived fields (pipeline_stage, canonical_payload, attempts…).
    `raw_payload` and `idempotency_key` are immutable after insert."""
    model_config = ConfigDict(extra="allow")
    schema_version: int = 1

    id:             str
    user_id:        str = "main"
    trace_id:       str                            # links inbox row + invoice + errors

    # Source identification
    source:         Literal["make_com", "manual", "replay"] = "make_com"
    received_at:    datetime = Field(default_factory=_now)
    raw_payload:    dict[str, Any]                  # full JSON as received
    raw_headers:    dict[str, Any] = Field(default_factory=dict)
    signature_status: Literal["verified", "missing", "invalid"] = "missing"

    # Salla order anchor — extracted at insert time for fast lookup
    salla_order_id:     Optional[str] = None
    salla_order_number: Optional[str] = None

    idempotency_key: str                            # "salla:{order_id}" → unique

    # Pipeline state (the only mutable section)
    pipeline_stage: str = "received"
    pipeline_error: Optional[dict[str, Any]] = None
    attempts:       int = 0
    next_retry_at:  Optional[datetime] = None
    processed_at:   Optional[datetime] = None

    # Stage-3 output (canonical DTO)
    canonical_payload: Optional[dict[str, Any]] = None

    # Outcome ref (filled after stage 6/7)
    qoyod_invoice_row_id: Optional[str] = None


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
    attempts:      int = 0
    last_error:    Optional[dict[str, Any]] = None
    last_attempt_at: Optional[datetime] = None
    sent_at:       Optional[datetime] = None

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

    # --- qoyod_inbox ---
    await db.qoyod_inbox.create_index(
        [("user_id", pymongo.ASCENDING),
         ("idempotency_key", pymongo.ASCENDING)],
        unique=True, name="qoyod_inbox_idem_unique",
    )
    await db.qoyod_inbox.create_index(
        [("user_id", pymongo.ASCENDING),
         ("pipeline_stage", pymongo.ASCENDING),
         ("next_retry_at", pymongo.ASCENDING)],
        name="qoyod_inbox_stage_retry",
    )
    await db.qoyod_inbox.create_index(
        [("user_id", pymongo.ASCENDING),
         ("received_at", pymongo.DESCENDING)],
        name="qoyod_inbox_received_at_desc",
    )
    await db.qoyod_inbox.create_index(
        [("salla_order_id", pymongo.ASCENDING)],
        name="qoyod_inbox_order_lookup", sparse=True,
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
