"""Closed public error vocabulary for diagnostic and integration boundaries.

Callers pass a local code, never an exception or provider response. Unknown
codes deliberately collapse to operation_failed.
"""

_PUBLIC_ERRORS = {
    "operation_failed": "operation_failed",
    "diagnostic_failed": "diagnostic_failed",
    "provider_operation_failed": "provider_operation_failed",
    "invalid_import_file": "invalid_import_file",
    "import_unavailable": "import_unavailable",
    "import_row_failed": "import_row_failed",
    "order_ingest_failed": "order_ingest_failed",
    "invalid_order_payload": "invalid_order_payload",
}


def public_error(code: str) -> str:
    if type(code) is not str:
        return "operation_failed"
    return _PUBLIC_ERRORS.get(code, "operation_failed")
