"""Conservative runtime bounds and transient failures for the main Mongo client."""
from __future__ import annotations

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


__all__ = [
    "MONGO_CONNECT_TIMEOUT_MS",
    "MONGO_MAX_IDLE_TIME_MS",
    "MONGO_MAX_POOL_SIZE",
    "MONGO_MIN_POOL_SIZE",
    "MONGO_SERVER_SELECTION_TIMEOUT_MS",
    "MONGO_WAIT_QUEUE_TIMEOUT_MS",
    "TRANSIENT_MONGO_ERRORS",
    "main_client_options",
]
