"""Ads V2 — Pydantic models for the 3 new collections.

All models derive from BaseModel and follow the simplified final design
(see /app/memory/ADS_V2_FINAL_DESIGN.md).

The models intentionally allow extra fields (`extra='allow'`) so that
new schema additions in later phases don't require migrations.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional, Literal
from pydantic import BaseModel, Field, ConfigDict


# ── Common ──────────────────────────────────────────────────────────────
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


PROVIDERS = ("meta", "snapchat", "tiktok", "google_ads")
SYNC_STATUSES = (
    "active", "paused", "error", "token_expired",
    "unauthorized", "discovered",
)
REVIEW_STATUSES = (
    "pending", "approved", "rejected", "reopened",
    "held_needs_fx", "held_anomaly", "held_unauthorized", "held_drift",
)

# iter-260 — Architectural separation of TWO orthogonal concerns:
#
#   A) DATA STATE (accounting)  → field `match_status` on ads_daily
#      Describes the trustworthiness of the SSOT spend numbers.
#      Values: matched | pending_platform | drift_review | no_data
#      Notes:  Never reflects API connectivity. `sync_failed` is
#              DEPRECATED here and kept only for read-time
#              reclassification of legacy rows.
#
#   B) CONNECTION STATE (technical)  → field `platform_check_status`
#      Describes whether the last API call to the platform succeeded.
#      Values: ok | last_check_failed | token_expired
#            | rate_limited | api_error
#      Notes:  Lives on ads_daily so the report can show it row-by-row.
#              Updating it MUST NEVER mutate accounting numbers in
#              `spend_native` / `spend_sar` / `bank_fee_sar`.
#
# Hard rule: an API hiccup may only change `platform_check_status`,
# never `match_status`, when valid SSOT data already exists.
MATCH_STATUSES = (
    "matched", "pending_platform", "drift_review",
    "no_data",
    "sync_failed",   # deprecated, retained for legacy rows
)
PLATFORM_CHECK_STATUSES = (
    "ok",                  # last fetch succeeded
    "last_check_failed",   # generic transient error
    "token_expired",       # auth issue
    "rate_limited",        # provider quota hit
    "api_error",           # HTTP/protocol error
)

SYNC_LOG_EVENTS = (
    "sync_run", "sync_failed",
    "review_approved", "review_rejected", "review_reopened",
    "review_held", "review_edit_fx",
    "ledger_posted", "ledger_reversed", "ledger_post_partial_failure",
    "token_expired", "token_alert",
    "account_created", "account_modified", "account_disabled",
    "account_relinked_v1",
    "fx_changed", "bank_fee_changed",
    "reconciliation_checked",
)


# ── ads_accounts schema ─────────────────────────────────────────────────
class V1TokenRef(BaseModel):
    """Read-only pointer to a V1 OAuth/connection document.

    V2 never mutates V1 docs. This struct captures *where* to read the
    current access_token from, with a `linked_at` audit timestamp.
    """
    model_config = ConfigDict(extra="allow")
    provider: str
    collection: str         # e.g. "snapchat_connections" / "meta_connections"
    user_id: str            # same user_id used in the V1 collection
    linked_at: str = Field(default_factory=utc_now_iso)
    snapshot_only: bool = True
    last_token_present_at: Optional[str] = None
    last_token_check_status: Optional[str] = None  # ok | missing | expired
    last_token_check_at: Optional[str] = None


class FxToSar(BaseModel):
    model_config = ConfigDict(extra="allow")
    mode: Literal["manual", "inherit_from_global"] = "manual"
    rate: float = 1.0
    effective_from: str = "2026-01-01"
    source_note: str = ""


class BankFee(BaseModel):
    model_config = ConfigDict(extra="allow")
    enabled: bool = False
    method: Literal["none", "pct", "flat", "pct_plus_flat"] = "none"
    rate_pct: float = 0.0           # e.g. 0.0285 = 2.85%
    flat_amount_sar: float = 0.0
    note: str = ""


class ReviewSettings(BaseModel):
    model_config = ConfigDict(extra="allow")
    auto_approve_under_sar: float = 0.0      # 0 = manual approval required
    drift_warning_threshold_pct: float = 5.0
    drift_block_threshold_pct: float = 15.0


class AdsAccount(BaseModel):
    """ads_accounts collection.

    One row per advertising ad account (per provider). Holds **all**
    per-account settings (FX, bank_fee, review_settings, sync state) and
    a read-only reference to the V1 token document.
    """
    model_config = ConfigDict(extra="allow")

    id: str
    user_id: str

    # core
    provider: str                         # one of PROVIDERS
    external_account_id: str              # ID at the provider
    display_name: str
    currency_native: str = "SAR"
    timezone: str = "Asia/Riyadh"
    organization_id: Optional[str] = None
    organization_name: Optional[str] = None

    # v1 reference (read-only)
    v1_token_ref: Optional[V1TokenRef] = None

    # settings
    fx_to_sar: FxToSar = Field(default_factory=FxToSar)
    bank_fee: BankFee = Field(default_factory=BankFee)
    review_settings: ReviewSettings = Field(default_factory=ReviewSettings)

    # sync state
    sync_enabled: bool = True
    sync_status: str = "discovered"       # one of SYNC_STATUSES
    sync_error_message: Optional[str] = None
    last_sync_started_at: Optional[str] = None
    last_sync_finished_at: Optional[str] = None
    last_synced_date: Optional[str] = None

    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    soft_deleted: bool = False


# ── ads_daily schema (Phase 0 only seeds, no writes yet) ────────────────
class AdsDaily(BaseModel):
    """ads_daily collection — SSOT for numbers.

    Phase 0 does NOT populate this collection (no sync yet). The model is
    declared so Phase 1 can use it directly.
    """
    model_config = ConfigDict(extra="allow")
    id: str
    user_id: str
    account_id: str
    provider: str
    date: str                              # YYYY-MM-DD

    spend_native: float = 0.0
    currency_native: str = "SAR"
    impressions: int = 0
    clicks: int = 0
    purchases: int = 0

    fx_rate: float = 1.0
    fx_source: str = "manual"
    spend_sar: float = 0.0
    bank_fee_sar: float = 0.0
    gross_sar: float = 0.0

    platform_reported_native: Optional[float] = None
    platform_reported_sar: Optional[float] = None
    platform_checked_at: Optional[str] = None
    drift_pct: float = 0.0
    anomaly_flags: list[str] = Field(default_factory=list)

    # iter-260 — Connection state (B) — orthogonal to match_status (A).
    # API failures NEVER alter `match_status` when valid SSOT data
    # exists; they only update this field + platform_check_error.
    platform_check_status: str = "ok"      # one of PLATFORM_CHECK_STATUSES
    platform_check_error: Optional[str] = None
    platform_last_checked_at: Optional[str] = None

    review_status: str = "pending"
    review_decided_at: Optional[str] = None
    review_decided_by: Optional[str] = None
    review_decision_note: Optional[str] = None
    review_reopen_count: int = 0

    ledger_txn_group_id: Optional[str] = None
    ledger_posted_at: Optional[str] = None
    ledger_reversed: bool = False
    ledger_reversal_txn_group_id: Optional[str] = None

    idempotency_key: str
    last_synced_at: Optional[str] = None
    last_recomputed_at: Optional[str] = None
    sources_count: int = 0
    confidence: str = "provisional"        # provisional | final

    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)


# ── ads_sync_logs schema ────────────────────────────────────────────────
class AdsSyncLog(BaseModel):
    """ads_sync_logs collection — append-only audit log.

    Used in Phase 0 to record account_created / account_modified /
    account_relinked_v1 / token_alert events. Phase 1+ adds sync_run,
    Phase 2+ adds review_* and ledger_* events.
    """
    model_config = ConfigDict(extra="allow")

    id: str
    user_id: str
    account_id: Optional[str] = None
    date: Optional[str] = None
    event: str                          # one of SYNC_LOG_EVENTS
    actor_user_id: Optional[str] = None
    actor_email: Optional[str] = None
    details: dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = None
    error_message: Optional[str] = None
    ip_address: Optional[str] = None
    at: str = Field(default_factory=utc_now_iso)


# ── Index registration (called once at startup) ─────────────────────────
async def ensure_indexes(db) -> None:
    """Create indexes on the 3 ads_v2 collections (idempotent)."""
    # ads_accounts
    await db.ads_accounts.create_index(
        [("user_id", 1), ("provider", 1), ("external_account_id", 1)],
        unique=True,
        partialFilterExpression={"soft_deleted": False},
        name="ads_accounts_unique_active",
    )
    await db.ads_accounts.create_index(
        [("user_id", 1), ("sync_status", 1)],
        name="ads_accounts_by_status",
    )

    # ads_daily (phase-1 use, declared in phase-0 for safety)
    await db.ads_daily.create_index(
        [("user_id", 1), ("account_id", 1), ("date", 1)],
        unique=True,
        name="ads_daily_unique_day",
    )
    await db.ads_daily.create_index(
        [("user_id", 1), ("review_status", 1), ("date", -1)],
        name="ads_daily_review_queue",
    )
    await db.ads_daily.create_index(
        [("idempotency_key", 1)],
        unique=True,
        name="ads_daily_idempotency",
    )

    # ads_sync_logs
    await db.ads_sync_logs.create_index(
        [("user_id", 1), ("at", -1)],
        name="ads_sync_logs_recent",
    )
    await db.ads_sync_logs.create_index(
        [("user_id", 1), ("account_id", 1), ("at", -1)],
        name="ads_sync_logs_by_account",
    )
    await db.ads_sync_logs.create_index(
        [("user_id", 1), ("event", 1), ("at", -1)],
        name="ads_sync_logs_by_event",
    )
