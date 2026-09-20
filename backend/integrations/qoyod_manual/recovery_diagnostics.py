"""Bounded read diagnostics. Never retain exception messages or provider bodies."""
from contextlib import contextmanager
from functools import wraps
from time import monotonic

STAGES = frozenset({"facts", "salla_refresh", "salla_accounting", "preflight",
                    "observe", "provider_page", "provider_invoice", "invoice_parse"})
ERRORS = frozenset({"ManualQoyodError", "HTTPException", "EvidenceError",
    "ReadTimeout", "ConnectTimeout", "WriteTimeout", "PoolTimeout", "TimeoutError",
    "ConnectError", "ReadError", "RemoteProtocolError", "ValueError", "TypeError",
    "KeyError", "AttributeError", "NameError", "RuntimeError", "InvalidOperation"})


def error_type(exc):
    name = type(exc).__name__
    return name if name in ERRORS else "UnexpectedError"


@contextmanager
def reading(stage, *, page=None):
    assert stage in STAGES
    started = monotonic()
    try:
        yield
    except Exception as exc:
        if not hasattr(exc, "_recovery_read_diagnostic"):
            detail = {"stage": stage, "error_type": error_type(exc),
                      "elapsed_ms": max(0, round((monotonic() - started) * 1000))}
            status = getattr(exc, "status_code", None)
            if type(status) is int and 0 <= status <= 599:
                detail["http_status"] = status
            if type(page) is int and 1 <= page <= 200:
                detail["page"] = page
            if exc.__cause__ is not None:
                detail["cause_type"] = error_type(exc.__cause__)
            # Code locations only, never locals, messages, URLs or response text.
            tb = exc.__traceback__
            while tb:
                name = tb.tb_frame.f_code.co_filename.replace("\\", "/").split("/")[-1]
                if name in {"recovery_adapter.py", "send.py", "sync.py", "client.py"}:
                    detail["location"] = f"{name}:{tb.tb_lineno}"
                tb = tb.tb_next
            exc._recovery_read_diagnostic = detail
        raise


def diagnosed(stage):
    def decorate(fn):
        @wraps(fn)
        async def wrapped(*args, **kwargs):
            with reading(stage):
                return await fn(*args, **kwargs)
        return wrapped
    return decorate
