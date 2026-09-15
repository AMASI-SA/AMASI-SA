"""Conservative runtime bounds for the main Mongo client and readiness probe."""
from __future__ import annotations

import asyncio
from typing import Any

from pymongo.errors import (
    AutoReconnect,
    ConnectionFailure,
    NetworkTimeout,
    ServerSelectionTimeoutError,
    WaitQueueTimeoutError,
)


MONGO_MAX_POOL_SIZE = 20
MONGO_MIN_POOL_SIZE = 0
MONGO_SERVER_SELECTION_TIMEOUT_MS = 3_000
MONGO_CONNECT_TIMEOUT_MS = 3_000
MONGO_WAIT_QUEUE_TIMEOUT_MS = 2_000
MONGO_MAX_IDLE_TIME_MS = 60_000
MONGO_READINESS_TIMEOUT_SECONDS = 2.5

# These failures mean Mongo could not service a request in the bounded window.
# They are availability failures, not evidence that an auth token is invalid.
TRANSIENT_MONGO_ERRORS = (
    AutoReconnect,
    ConnectionFailure,
    NetworkTimeout,
    ServerSelectionTimeoutError,
    WaitQueueTimeoutError,
)


def main_client_options(*, event_listener: Any) -> dict[str, Any]:
    """Return the single conservative option set for the web-process client."""
    return {
        "maxPoolSize": MONGO_MAX_POOL_SIZE,
        "minPoolSize": MONGO_MIN_POOL_SIZE,
        "serverSelectionTimeoutMS": MONGO_SERVER_SELECTION_TIMEOUT_MS,
        "connectTimeoutMS": MONGO_CONNECT_TIMEOUT_MS,
        "waitQueueTimeoutMS": MONGO_WAIT_QUEUE_TIMEOUT_MS,
        "maxIdleTimeMS": MONGO_MAX_IDLE_TIME_MS,
        "event_listeners": [event_listener],
    }


async def bounded_readiness(client: Any, *, timeout_seconds: float = MONGO_READINESS_TIMEOUT_SECONDS) -> bool:
    """Probe Mongo only, returning within ``timeout_seconds`` in every case."""
    try:
        await asyncio.wait_for(
            client.admin.command("ping"),
            timeout=max(0.001, float(timeout_seconds)),
        )
        return True
    except asyncio.TimeoutError:
        return False
    except TRANSIENT_MONGO_ERRORS:
        return False
    except Exception:
        # Readiness is deliberately fail-closed without leaking driver details.
        return False


__all__ = [
    "MONGO_CONNECT_TIMEOUT_MS",
    "MONGO_MAX_IDLE_TIME_MS",
    "MONGO_MAX_POOL_SIZE",
    "MONGO_MIN_POOL_SIZE",
    "MONGO_READINESS_TIMEOUT_SECONDS",
    "MONGO_SERVER_SELECTION_TIMEOUT_MS",
    "MONGO_WAIT_QUEUE_TIMEOUT_MS",
    "TRANSIENT_MONGO_ERRORS",
    "bounded_readiness",
    "main_client_options",
]

