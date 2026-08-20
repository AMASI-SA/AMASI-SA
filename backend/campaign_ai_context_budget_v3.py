"""Bound Decision Intelligence V3 prompt context before calling OpenAI.

The V3 evidence pack is intentionally rich, but provider/entity/product history can
expand faster than the model context window. This module keeps the source contract
intact while projecting that evidence into a bounded model view:

* preserve provider coverage and direct budget owners first;
* keep high-spend entities when detailed evidence must be reduced;
* compact verbose strings/lists and product chronology without mutating source data;
* estimate the complete request footprint (instructions + schema + payload);
* retry a real context-length rejection with a stronger compaction tier.

No marketing decision is made here. The helper only controls representation size.
"""
from __future__ import annotations

from copy import deepcopy
import json
import logging
import os
from typing import Any, Awaitable, Callable


logger = logging.getLogger("campaign_ai_context_budget_v3")

DEFAULT_INPUT_TOKEN_BUDGET = 70_000
MIN_INPUT_TOKEN_BUDGET = 20_000
MAX_INPUT_TOKEN_BUDGET = 180_000
CHARS_PER_TOKEN_ESTIMATE = 3.0

# (candidate_limit, list_limit, text_limit)
_TIERS: tuple[tuple[int, int, int], ...] = (
    (96, 12, 900),
    (60, 8, 600),
    (40, 6, 420),
    (24, 4, 300),
    (12, 3, 220),
)


def _input_token_budget() -> int:
    raw = str(os.environ.get("MEZAN_CAMPAIGN_AI_INPUT_TOKEN_BUDGET") or "").strip()
    try:
        value = int(float(raw)) if raw else DEFAULT_INPUT_TOKEN_BUDGET
    except (TypeError, ValueError):
        value = DEFAULT_INPUT_TOKEN_BUDGET
    return min(MAX_INPUT_TOKEN_BUDGET, max(MIN_INPUT_TOKEN_BUDGET, value))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))


def estimate_input_tokens(*, instructions: str, payload: dict[str, Any], schema: dict[str, Any]) -> int:
    """Conservative dependency-free request estimate used only for bounding."""
    chars = len(str(instructions or "")) + len(_json(payload)) + len(_json(schema))
    return max(1, int(chars / CHARS_PER_TOKEN_ESTIMATE) + 1)


def _entity_key(row: dict[str, Any]) -> str:
    return "|".join((
        str(row.get("provider") or ""),
        str(row.get("entity_level") or ""),
        str(row.get("account_id") or ""),
        str(row.get("entity_id") or ""),
    ))


def _number(value: Any) -> float:
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return parsed if parsed == parsed and abs(parsed) != float("inf") else 0.0


def _candidate_priority(row: dict[str, Any]) -> tuple[int, float, float, str]:
    # Budget owners remain represented before child entities. Within each group,
    # spend and revenue keep the most commercially material evidence detailed.
    level = str(row.get("entity_level") or "")
    budget_owner = 1 if level in {"campaign", "ad_group"} else 0
    spend = max(
        _number(row.get("spend_sar")),
        _number(row.get("entity_period_spend_sar")),
        _number(row.get("campaign_period_spend_sar")),
    )
    revenue = max(_number(row.get("revenue_sar")), _number(row.get("sales_sar")))
    return (budget_owner, spend, revenue, _entity_key(row))


def _select_candidates(rows: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(rows) <= limit:
        return list(rows)
    ordered = sorted(rows, key=_candidate_priority, reverse=True)
    return ordered[:limit]


def _compact(value: Any, *, list_limit: int, text_limit: int, depth: int = 0) -> Any:
    """Recursively bound verbose evidence while retaining fields and numeric facts."""
    if depth >= 10:
        return "[bounded]"
    if isinstance(value, str):
        normalized = value.strip()
        return normalized if len(normalized) <= text_limit else normalized[:text_limit] + "…"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (list, tuple, set)):
        return [
            _compact(item, list_limit=list_limit, text_limit=text_limit, depth=depth + 1)
            for item in list(value)[:list_limit]
        ]
    if isinstance(value, dict):
        return {
            str(key): _compact(item, list_limit=list_limit, text_limit=text_limit, depth=depth + 1)
            for key, item in value.items()
        }
    rendered = str(value)
    return rendered if len(rendered) <= text_limit else rendered[:text_limit] + "…"


def _filter_entity_maps(value: Any, selected_keys: set[str]) -> Any:
    """Filter only maps explicitly named `entities`; leave aggregate evidence intact."""
    if isinstance(value, list):
        return [_filter_entity_maps(item, selected_keys) for item in value]
    if not isinstance(value, dict):
        return value
    output: dict[str, Any] = {}
    for key, item in value.items():
        if key == "entities" and isinstance(item, dict):
            output[key] = {
                entity_key: _filter_entity_maps(entity_value, selected_keys)
                for entity_key, entity_value in item.items()
                if entity_key in selected_keys
            }
        else:
            output[key] = _filter_entity_maps(item, selected_keys)
    return output


def _tighten_known_heavy_sections(evidence: dict[str, Any], *, tier: int) -> dict[str, Any]:
    result = deepcopy(evidence)
    product_entities = ((result.get("product_intelligence") or {}).get("entities") or {})
    if isinstance(product_entities, dict):
        for block in product_entities.values():
            if not isinstance(block, dict):
                continue
            products = block.get("products") if isinstance(block.get("products"), list) else []
            product_limit = 3 if tier <= 1 else 2 if tier <= 2 else 1
            block["products"] = products[:product_limit]
            for product in block["products"]:
                if not isinstance(product, dict):
                    continue
                product["images"] = list(product.get("images") or [])[: (2 if tier == 0 else 1)]
                product["options"] = list(product.get("options") or [])[: (6 if tier <= 1 else 3)]
                product["variants"] = list(product.get("variants") or [])[: (6 if tier <= 1 else 3)]
                inventory = product.get("inventory")
                if isinstance(inventory, dict):
                    inventory["variants"] = list(inventory.get("variants") or [])[: (6 if tier <= 1 else 3)]
                if tier >= 3:
                    # URLs/status/inventory remain; long merchandising prose is already
                    # represented by the first pass evidence and is the safest field to trim.
                    for field in ("description", "short_description"):
                        if field in product and isinstance(product.get(field), str):
                            product[field] = product[field][:180]

    knowledge = result.get("marketing_knowledge")
    if isinstance(knowledge, dict) and isinstance(knowledge.get("retrieved"), list):
        knowledge["retrieved"] = knowledge["retrieved"][: (5 if tier <= 1 else 3 if tier == 2 else 1)]

    alerts = result.get("operational_product_watch_alerts")
    if isinstance(alerts, list):
        result["operational_product_watch_alerts"] = alerts[: (24 if tier <= 1 else 12 if tier == 2 else 6)]
    return result


def prepare_context_budget(
    *,
    instructions: str,
    payload: dict[str, Any],
    evidence_pack: dict[str, Any],
    schema: dict[str, Any],
    forced_tier: int | None = None,
    token_budget: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Return a bounded model payload/evidence pair plus safe operational diagnostics."""
    budget = int(token_budget or _input_token_budget())
    original_rows = payload.get("active_entities") if isinstance(payload.get("active_entities"), list) else []
    start_tier = max(0, min(len(_TIERS) - 1, int(forced_tier or 0)))
    last: tuple[dict[str, Any], dict[str, Any], dict[str, Any]] | None = None

    for tier in range(start_tier, len(_TIERS)):
        candidate_limit, list_limit, text_limit = _TIERS[tier]
        selected_rows = _select_candidates(original_rows, candidate_limit)
        selected_keys = {_entity_key(row) for row in selected_rows}

        bounded_evidence = _filter_entity_maps(deepcopy(evidence_pack), selected_keys)
        bounded_evidence = _tighten_known_heavy_sections(bounded_evidence, tier=tier)
        bounded_evidence = _compact(
            bounded_evidence,
            list_limit=list_limit,
            text_limit=text_limit,
        )

        bounded_payload = deepcopy(payload)
        bounded_payload["active_entities"] = _compact(
            selected_rows,
            list_limit=list_limit,
            text_limit=text_limit,
        )
        bounded_payload["decision_evidence_v3"] = bounded_evidence

        # Context/history are supporting evidence. Preserve them, but progressively
        # compact their representation rather than letting them duplicate the main pack.
        for field in (
            "overall_store_profit_context",
            "executed_experiments",
            "legacy_campaign_history_context",
            "current_market_context",
            "first_pass_recommendations",
        ):
            if field in bounded_payload:
                bounded_payload[field] = _compact(
                    bounded_payload[field],
                    list_limit=list_limit,
                    text_limit=text_limit,
                )

        bounded_payload["context_budget_v3"] = {
            "representation_only": True,
            "tier": tier,
            "candidates_before": len(original_rows),
            "candidates_detailed": len(selected_rows),
            "candidates_omitted_from_detail": max(0, len(original_rows) - len(selected_rows)),
            "full_provider_sources_preserved": True,
            "marketing_decision_unchanged": True,
        }
        estimated = estimate_input_tokens(
            instructions=instructions,
            payload=bounded_payload,
            schema=schema,
        )
        diagnostics = {
            "tier": tier,
            "estimated_input_tokens": estimated,
            "token_budget": budget,
            "candidates_before": len(original_rows),
            "candidates_after": len(selected_rows),
            "context_compacted": bool(tier > 0 or len(selected_rows) < len(original_rows)),
        }
        last = (bounded_payload, bounded_evidence, diagnostics)
        if estimated <= budget:
            return last

    assert last is not None
    return last


def is_context_length_error(exc: Exception) -> bool:
    code = str(getattr(exc, "code", "") or "").lower()
    body = str(getattr(exc, "body", "") or "").lower()
    message = str(exc or "").lower()
    combined = " ".join((code, body, message))
    return any(marker in combined for marker in (
        "context_length_exceeded",
        "maximum context length",
        "context window",
        "input is too long",
        "too many tokens",
        "max context",
    ))


def wrap_structured_response(base: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Install budget-aware projection and one stronger retry on context overflow."""
    async def bounded_response(
        client: Any,
        *,
        instructions: str,
        payload: dict[str, Any],
        evidence_pack: dict[str, Any],
        schema_name: str,
        schema: dict[str, Any],
        max_output_tokens: int,
    ) -> Any:
        bounded_payload, bounded_evidence, diagnostics = prepare_context_budget(
            instructions=instructions,
            payload=payload,
            evidence_pack=evidence_pack,
            schema=schema,
        )
        logger.info(
            "Campaign AI context budget schema=%s tier=%s estimated_input_tokens=%s "
            "budget=%s candidates_before=%s candidates_after=%s compacted=%s",
            schema_name,
            diagnostics["tier"],
            diagnostics["estimated_input_tokens"],
            diagnostics["token_budget"],
            diagnostics["candidates_before"],
            diagnostics["candidates_after"],
            diagnostics["context_compacted"],
        )
        try:
            return await base(
                client,
                instructions=instructions,
                payload=bounded_payload,
                evidence_pack=bounded_evidence,
                schema_name=schema_name,
                schema=schema,
                max_output_tokens=max_output_tokens,
            )
        except Exception as exc:
            if not is_context_length_error(exc):
                raise
            retry_tier = min(len(_TIERS) - 1, int(diagnostics["tier"]) + 2)
            retry_payload, retry_evidence, retry_diag = prepare_context_budget(
                instructions=instructions,
                payload=payload,
                evidence_pack=evidence_pack,
                schema=schema,
                forced_tier=retry_tier,
                token_budget=min(_input_token_budget(), 45_000),
            )
            logger.warning(
                "Campaign AI context overflow retry schema=%s tier=%s estimated_input_tokens=%s "
                "candidates_before=%s candidates_after=%s",
                schema_name,
                retry_diag["tier"],
                retry_diag["estimated_input_tokens"],
                retry_diag["candidates_before"],
                retry_diag["candidates_after"],
            )
            return await base(
                client,
                instructions=instructions,
                payload=retry_payload,
                evidence_pack=retry_evidence,
                schema_name=schema_name,
                schema=schema,
                max_output_tokens=max_output_tokens,
            )

    return bounded_response


__all__ = [
    "estimate_input_tokens",
    "is_context_length_error",
    "prepare_context_budget",
    "wrap_structured_response",
]
