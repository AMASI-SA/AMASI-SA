"""Explicit provider capabilities for the shared read-only marketing gateway.

Adding a platform means supplying readers for this contract, not copying AI logic.
Registration does not grant advertising write capabilities.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any, Awaitable, Callable, Mapping

Reader = Callable[..., Awaitable[Any]]

@dataclass(frozen=True)
class MarketingProvider:
    provider: str
    ai_provider: str
    evidence_source: str
    readers: Mapping[str, Reader]

async def _snapchat_reader(name: str, *args: Any, **kwargs: Any) -> Any:
    # Resolve lazily to preserve existing gateway monkeypatch/instrumentation hooks.
    from . import gateway
    return await getattr(gateway, name)(*args, **kwargs)

PROVIDERS: dict[str, MarketingProvider] = {
    "snapchat_ads": MarketingProvider(
        provider="snapchat_ads", ai_provider="snapchat",
        evidence_source="unified-marketing-data-v1:snapchat-v2",
        readers={name: partial(_snapchat_reader, "load_snapchat_v2_" + name) for name in (
            "account_identity", "account_report", "entity_metadata", "dashboard_spend",
            "entity_report", "entity_readiness_evidence", "entity_daily_series",
        )},
    ),
}

def get_provider(provider: str) -> MarketingProvider:
    key = str(provider or "").strip().lower()
    if key not in PROVIDERS:
        raise ValueError(f"unsupported_unified_marketing_provider:{key}")
    return PROVIDERS[key]

def get_reader(provider: str, capability: str) -> Reader:
    spec = get_provider(provider)
    reader = spec.readers.get(capability)
    if not callable(reader):
        raise ValueError(f"unsupported_unified_marketing_capability:{spec.provider}:{capability}")
    return reader
