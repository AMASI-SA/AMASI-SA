"""Closed configuration and write boundaries for the V3 shadow observer.

P0 deliberately has one environment switch and no cutover switch.  All other
budgets are reviewed constants so a deployment environment cannot silently
widen provider traffic, retention, concurrency, or retry behaviour.
"""

from __future__ import annotations

import re
from typing import Any


SHADOW_ENABLED_ENV = "SALLA_ORDERS_V3_SHADOW_ENABLED"
CONFIG_ENV_ALLOWLIST = frozenset({SHADOW_ENABLED_ENV})

SHADOW_COLLECTION = "salla_orders_v3_shadow"
EVENTS_COLLECTION = "salla_orders_v3_events"
JOBS_COLLECTION = "salla_orders_v3_jobs"
STATE_COLLECTION = "salla_orders_v3_sync_state"
LEASES_COLLECTION = "salla_orders_v3_leases"
PARITY_AUDITS_COLLECTION = "salla_orders_v3_parity_audits"
PARITY_RUNS_COLLECTION = "salla_orders_v3_parity_runs"
PARITY_EVIDENCE_COLLECTION = "salla_orders_v3_parity_evidence"

COLLECTION_ALLOWLIST = frozenset({
    SHADOW_COLLECTION,
    EVENTS_COLLECTION,
    JOBS_COLLECTION,
    STATE_COLLECTION,
    LEASES_COLLECTION,
    PARITY_AUDITS_COLLECTION,
    PARITY_RUNS_COLLECTION,
    PARITY_EVIDENCE_COLLECTION,
})

ORDERS_PER_PAGE = 30
MAX_PROVIDER_ATTEMPTS = 3
MAX_DISCOVERY_PAGES_PER_RUN = 20
MAX_DISCOVERED_ORDERS_PER_RUN = ORDERS_PER_PAGE * MAX_DISCOVERY_PAGES_PER_RUN
MAX_JOBS_PER_CYCLE = 20
MAX_JOB_ATTEMPTS = 5
MAX_CONCURRENCY = 4
MAX_INTEGRATIONS_PER_CYCLE = 100
MAX_EVENT_OUTBOX_REPAIRS_PER_CYCLE = 100
MAX_EVENT_OUTBOX_ATTEMPTS = 5
EVENT_OUTBOX_BACKOFF_BASE_SECONDS = 600
EVENT_OUTBOX_BACKOFF_MAX_SECONDS = 60 * 60
EVENT_OUTBOX_ROW_LEASE_SECONDS = 60
PARITY_EVIDENCE_MAX_AGE_SECONDS = 60 * 60
PARITY_EVIDENCE_FUTURE_SKEW_SECONDS = 60
PARITY_RUN_TTL_SECONDS = 60 * 60

DISCOVERY_INITIAL_LOOKBACK_DAYS = 1
DISCOVERY_WINDOW_DAYS = 1
RECOVERY_INTERVAL_SECONDS = 300
LEASE_SECONDS = 180
LEASE_HEARTBEAT_SECONDS = 45
TTL_SECONDS = 30 * 24 * 60 * 60

# No operational writer or cutover adapter exists in P0.
CUTOVER_IMPLEMENTED = False

_ORDER_DETAILS_PATH = re.compile(r"^/orders/[A-Za-z0-9_-]+$")
_PROVIDER_READ_PATHS = frozenset({"/orders", "/orders/items"})


def shadow_collection(db: Any, name: str) -> Any:
    """Return only a reviewed isolated collection; reject every other name."""
    if name not in COLLECTION_ALLOWLIST:
        raise RuntimeError(f"Salla Orders V3 collection is not allowlisted: {name}")
    return getattr(db, name)


def validate_provider_read_request(method: str, path: str) -> None:
    """Fail closed before any non-GET or non-orders provider request."""
    normalized_method = str(method or "").strip().upper()
    normalized_path = str(path or "").strip()
    path_allowed = (
        normalized_path in _PROVIDER_READ_PATHS
        or bool(_ORDER_DETAILS_PATH.fullmatch(normalized_path))
    )
    if normalized_method != "GET" or not path_allowed:
        raise RuntimeError(
            f"Salla Orders V3 provider request is not allowlisted: "
            f"{normalized_method} {normalized_path}"
        )
