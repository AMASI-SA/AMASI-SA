"""Response and persistence models for Apps & Integrations V2."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


COLLECTION_NAMES = (
    "mezan_integrations_v2",
    "mezan_integration_accounts_v2",
    "mezan_integration_permissions_v2",
    "mezan_integration_health_v2",
    "mezan_integration_sync_runs_v2",
    "mezan_integration_errors_v2",
    "mezan_campaign_product_links_v2",
)


async def ensure_integrations_control_center_indexes(db: Any) -> None:
    """Create the isolated V2 indexes; safe to call on every startup."""
    await db.mezan_integrations_v2.create_index(
        [("user_id", 1), ("provider", 1)],
        unique=True,
        name="mezan_integrations_v2_user_provider_unique",
    )
    await db.mezan_integrations_v2.create_index(
        [("user_id", 1), ("connection_status", 1)],
        name="mezan_integrations_v2_user_status",
    )
    await db.mezan_integration_accounts_v2.create_index(
        [("user_id", 1), ("provider", 1), ("external_account_id", 1)],
        unique=True,
        name="mezan_integration_accounts_v2_identity_unique",
    )
    await db.mezan_integration_accounts_v2.create_index(
        [("user_id", 1), ("provider", 1), ("connection_status", 1)],
        name="mezan_integration_accounts_v2_user_provider_status",
    )
    await db.mezan_integration_permissions_v2.create_index(
        [("user_id", 1), ("provider", 1), ("permission_key", 1)],
        unique=True,
        name="mezan_integration_permissions_v2_key_unique",
    )
    await db.mezan_integration_health_v2.create_index(
        [("user_id", 1), ("provider", 1), ("checked_at", -1)],
        name="mezan_integration_health_v2_provider_latest",
    )
    await db.mezan_integration_health_v2.create_index(
        [("user_id", 1), ("health_status", 1), ("checked_at", -1)],
        name="mezan_integration_health_v2_status_latest",
    )
    await db.mezan_integration_sync_runs_v2.create_index(
        [("user_id", 1), ("run_id", 1)],
        unique=True,
        name="mezan_integration_sync_runs_v2_run_unique",
    )
    await db.mezan_integration_sync_runs_v2.create_index(
        [("user_id", 1), ("provider", 1), ("started_at", -1)],
        name="mezan_integration_sync_runs_v2_provider_latest",
    )
    await db.mezan_integration_sync_runs_v2.create_index(
        [("user_id", 1), ("provider", 1), ("status", 1)],
        unique=True,
        partialFilterExpression={
            "run_type": "analytics_refresh",
            "status": "running",
        },
        name="mezan_integration_sync_runs_v2_one_running",
    )
    await db.mezan_integration_sync_runs_v2.create_index(
        [
            ("user_id", 1),
            ("provider", 1),
            ("idempotency_key", 1),
            ("finished_at", -1),
        ],
        name="mezan_integration_sync_runs_v2_idempotency",
    )
    await db.mezan_integration_errors_v2.create_index(
        [("user_id", 1), ("error_id", 1)],
        unique=True,
        name="mezan_integration_errors_v2_error_unique",
    )
    await db.mezan_integration_errors_v2.create_index(
        [("user_id", 1), ("provider", 1), ("occurred_at", -1)],
        name="mezan_integration_errors_v2_provider_latest",
    )
    await db.mezan_campaign_product_links_v2.create_index(
        [("user_id", 1), ("idempotency_key", 1)],
        unique=True,
        name="mezan_campaign_product_links_v2_idempotency_unique",
    )
    await db.mezan_campaign_product_links_v2.create_index(
        [("user_id", 1), ("provider", 1), ("product_id", 1)],
        name="mezan_campaign_product_links_v2_product",
    )
    await db.mezan_campaign_product_links_v2.create_index(
        [("user_id", 1), ("provider", 1), ("campaign_id", 1)],
        name="mezan_campaign_product_links_v2_campaign",
    )


class ConnectionStatus(str, Enum):
    CONNECTED = "connected"
    DATA_AVAILABLE = "data_available"
    NOT_CONNECTED = "not_connected"
    NOT_CONFIGURED = "not_configured"
    NEEDS_REAUTH = "needs_reauth"
    EXPIRED = "expired"
    ERROR = "error"
    PLANNED = "planned"
    UNKNOWN = "unknown"


class ConnectionProvenance(str, Enum):
    """How Mezan receives or manages the provider integration.

    This is deliberately orthogonal to ``connection_status``.  A legacy
    connector may be operational, while a data feed may contain fresh rows
    without proving any provider-management connection.
    """

    API_CONNECTION = "api_connection"
    LEGACY_INTEGRATION = "legacy_integration"
    DATA_FEED = "data_feed"
    DISCONNECTED = "disconnected"
    PLANNED = "planned"
    UNKNOWN = "unknown"


class CapabilityState(str, Enum):
    AVAILABLE = "available"
    APPROVAL_REQUIRED = "approval_required"
    BLOCKED_MISSING_PERMISSION = "blocked_missing_permission"
    BLOCKED_MISSING_DATA = "blocked_missing_data"
    NOT_CONNECTED = "not_connected"
    PLANNED = "planned"
    UNKNOWN = "unknown"


class SecretSafeModel(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class CampaignProductLinkRecord(SecretSafeModel):
    """Future identity graph only; Phase 1 exposes no mutation endpoint."""

    link_id: str
    idempotency_key: str
    user_id: str
    provider: str
    mezan_integration_account_id: str
    product_id: str
    campaign_id: str
    ad_group_id: str | None = None
    ad_id: str | None = None
    creative_id: str | None = None
    landing_page_url: str | None = None
    performance: dict[str, int | float | None] = Field(default_factory=dict)
    cost: float | None = None
    revenue: float | None = None
    profit: float | None = None
    currency: str | None = None
    status: str = "proposed"
    created_at: datetime | str
    updated_at: datetime | str


class CapabilityEntry(SecretSafeModel):
    state: CapabilityState
    available: bool = False
    approval_required: bool = False
    blocked_by_policy: bool = False
    reason: str


class PermissionSummary(SecretSafeModel):
    current: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    unknown: bool = False


class IntegrationAccount(SecretSafeModel):
    mezan_integration_account_id: str
    provider: str
    external_account_id: str | None = None
    store_id: str | None = None
    ad_account_id: str | None = None
    display_name: str | None = None
    currency: str | None = None
    timezone: str | None = None
    connection_status: ConnectionStatus
    capabilities: dict[str, CapabilityEntry] = Field(default_factory=dict)
    permissions: list[str] = Field(default_factory=list)
    last_sync_at: datetime | str | None = None
    data_delay_minutes: int | None = Field(default=None, ge=0)
    health_score: int | None = Field(default=None, ge=0, le=100)
    source_mode: str
    connection_provenance: ConnectionProvenance


class HealthSummary(SecretSafeModel):
    status: str
    score: int | None = Field(default=None, ge=0, le=100)
    checked_at: datetime | str | None = None
    data_quality: str


class ActionState(SecretSafeModel):
    enabled: bool
    reason: str | None = None
    href: str | None = None


class ProviderCard(SecretSafeModel):
    provider: str
    name: str
    name_ar: str
    category: str
    connection_status: ConnectionStatus
    connection_provenance: ConnectionProvenance
    source_mode: str
    accounts: list[IntegrationAccount] = Field(default_factory=list)
    permissions: PermissionSummary
    capabilities: dict[str, CapabilityEntry] = Field(default_factory=dict)
    last_sync_at: datetime | str | None = None
    data_delay_minutes: int | None = Field(default=None, ge=0)
    health: HealthSummary
    latest_error: dict[str, Any] | None = None
    ai: dict[str, list[str]]
    actions: dict[str, ActionState]


class OverviewSummary(SecretSafeModel):
    total: int
    # Backward-compatible operational count. Unlike the original
    # implementation, data feeds are never included.
    connected: int
    api_connections: int
    legacy_integrations: int
    data_feeds: int
    disconnected: int
    planned: int
    unknown: int
    healthy: int
    missing_permissions: int
    attention_required: int


class OverviewResponse(SecretSafeModel):
    generated_at: datetime | str
    summary: OverviewSummary
    providers: list[ProviderCard]
    safety_policy: dict[str, Any]


class CapabilityResponse(SecretSafeModel):
    generated_at: datetime | str
    providers: list[dict[str, Any]]
    safety_policy: dict[str, Any]


class ActivityListResponse(SecretSafeModel):
    items: list[dict[str, Any]]
    total: int
    limit: int


class ConnectionTestResponse(SecretSafeModel):
    provider: str
    run_id: str
    status: str
    health: HealthSummary
    message: str


class SnapchatAnalyticsSyncResponse(SecretSafeModel):
    run_id: str
    provider: Literal["snapchat_ads"]
    status: Literal["complete", "partial", "failed"]
    date_from: str | None = None
    date_to: str | None = None
    accounts_attempted: int = Field(ge=0)
    accounts_complete: int = Field(ge=0)
    rows_saved: int = Field(ge=0)
    errors_count: int = Field(ge=0)
    source_only: Literal[True]
    accounting_write_reached: Literal[False]
    qoyod_write_reached: Literal[False]
