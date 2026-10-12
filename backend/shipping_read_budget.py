"""A request budget and method boundary for shipping reconciliation only."""
from contextlib import contextmanager
from contextvars import ContextVar
import httpx

from salla_integration.service import SallaError

_budget = ContextVar("shipping_read_budget", default=None)


@contextmanager
def read_budget(limit=12, *, claim=None):
    if _budget.get() is not None:
        yield
        return
    token = _budget.set({"remaining": limit, "claim": claim})
    try:
        yield
    finally:
        _budget.reset(token)


def current_delivery_claim():
    return (_budget.get() or {}).get("claim")


async def call_salla(db, owner, method, path, **kwargs):
    if method.upper() != "GET":
        raise SallaError("Shipping reconciliation permits GET only", status_code=409)
    budget = _budget.get()
    if budget is not None:
        if budget["remaining"] <= 0:
            raise SallaError("Shipping read budget exhausted", status_code=429)
        budget["remaining"] -= 1
    # Do not use the general transport: it may POST an OAuth refresh or replay
    # authentication. Token maintenance is a separate existing service. A stale
    # token here defers reconciliation without any external mutation.
    from salla_integration.service import get_integration, _decrypt_access, SALLA_API_BASE
    integration = await get_integration(db, owner)
    token = _decrypt_access(integration) if integration else ""
    if not token or not path.startswith("/") or kwargs.get("json") is not None:
        raise SallaError("Shipping read credentials or request unavailable", status_code=409)
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8, connect=3),
                                     follow_redirects=False, trust_env=False) as client:
            response = await client.get(SALLA_API_BASE + path,
                headers={"Authorization": "Bearer " + token}, params=kwargs.get("params"))
        if not 200 <= response.status_code < 300:
            raise SallaError("Shipping GET requires reconciliation", status_code=response.status_code)
        result = response.json()
        if not isinstance(result, dict):
            raise ValueError("invalid response")
        return result
    except (httpx.HTTPError, ValueError) as exc:
        raise SallaError("Shipping GET unavailable", status_code=502) from exc
