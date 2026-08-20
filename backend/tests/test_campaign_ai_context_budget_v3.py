import asyncio
from copy import deepcopy
from types import SimpleNamespace

import campaign_ai_context_budget_v3 as context_budget


def _candidate(index: int, *, level: str | None = None) -> dict:
    return {
        "provider": "snapchat" if index % 2 else "meta",
        "entity_level": level or ("campaign" if index < 30 else "ad"),
        "account_id": f"account-{index % 4}",
        "entity_id": f"entity-{index}",
        "entity_name": f"Entity {index}",
        "spend_sar": float(10_000 - index * 20),
        "revenue_sar": float(12_000 - index * 10),
        "purchases": index % 11,
        "description": "تفاصيل حملة طويلة " * 250,
    }


def _evidence(rows: list[dict]) -> dict:
    entities = {}
    product_entities = {}
    for row in rows:
        key = "|".join((
            row["provider"],
            row["entity_level"],
            row["account_id"],
            row["entity_id"],
        ))
        entities[key] = {
            "today": {"metrics": {"spend_sar": row["spend_sar"], "purchases": row["purchases"]}},
            "history": [
                {"day": day, "notes": "سجل زمني مفصل " * 120}
                for day in range(30)
            ],
        }
        product_entities[key] = {
            "products": [{
                "product_id": f"p-{row['entity_id']}",
                "description": "وصف منتج طويل " * 500,
                "short_description": "وصف مختصر " * 100,
                "images": [f"https://example.test/{n}.jpg" for n in range(8)],
                "options": [{"name": f"o-{n}", "values": list(range(12))} for n in range(8)],
                "variants": [{"id": n, "stock": n} for n in range(20)],
                "inventory": {"variants": [{"id": n, "stock": n} for n in range(20)]},
            } for _ in range(4)]
        }
    return {
        "temporal": {"entities": entities},
        "funnel_and_creative": {"entities": entities},
        "product_intelligence": {"entities": product_entities},
        "operational_product_watch_alerts": [
            {"id": n, "detail": "تنبيه تشغيلي " * 80} for n in range(100)
        ],
        "marketing_knowledge": {
            "retrieved": [
                {"source_id": str(n), "insight_summary": "معرفة تسويقية " * 120}
                for n in range(10)
            ]
        },
    }


def test_large_81_candidate_payload_is_bounded_without_mutating_source():
    rows = [_candidate(index) for index in range(81)]
    evidence = _evidence(rows)
    payload = {
        "active_entities": rows,
        "decision_evidence_v3": evidence,
        "legacy_campaign_history_context": {"rows": rows * 3},
        "executed_experiments": {"rows": rows},
        "overall_store_profit_context": {"detail": "ربحية " * 3000},
    }
    original_payload = deepcopy(payload)
    original_evidence = deepcopy(evidence)

    bounded_payload, bounded_evidence, diagnostics = context_budget.prepare_context_budget(
        instructions="حلل السبب الجذري " * 100,
        payload=payload,
        evidence_pack=evidence,
        schema={"type": "object", "properties": {}},
        token_budget=20_000,
    )

    assert diagnostics["candidates_before"] == 81
    assert diagnostics["candidates_after"] <= 81
    assert diagnostics["context_compacted"] is True
    assert diagnostics["estimated_input_tokens"] <= 20_000
    assert len(bounded_payload["active_entities"]) == diagnostics["candidates_after"]
    assert bounded_payload["context_budget_v3"]["full_provider_sources_preserved"] is True
    assert payload == original_payload
    assert evidence == original_evidence

    selected_levels = {row["entity_level"] for row in bounded_payload["active_entities"]}
    if diagnostics["candidates_after"] <= 30:
        assert selected_levels == {"campaign"}

    temporal_keys = set((bounded_evidence.get("temporal") or {}).get("entities") or {})
    selected_keys = {
        "|".join((row["provider"], row["entity_level"], row["account_id"], row["entity_id"]))
        for row in bounded_payload["active_entities"]
    }
    assert temporal_keys <= selected_keys


def test_context_length_rejection_retries_with_stronger_compaction():
    rows = [_candidate(index) for index in range(81)]
    evidence = _evidence(rows)
    payload = {
        "active_entities": rows,
        "decision_evidence_v3": evidence,
    }
    calls = []

    class ContextLengthError(RuntimeError):
        code = "context_length_exceeded"

    async def base(client, **kwargs):
        calls.append({
            "count": len(kwargs["payload"]["active_entities"]),
            "tier": kwargs["payload"]["context_budget_v3"]["tier"],
        })
        if len(calls) == 1:
            raise ContextLengthError("maximum context length exceeded")
        return SimpleNamespace(status="completed", output_text="{}"), 0, False

    wrapped = context_budget.wrap_structured_response(base)
    result = asyncio.run(wrapped(
        object(),
        instructions="diagnose",
        payload=payload,
        evidence_pack=evidence,
        schema_name="campaign_decision_intelligence_v3",
        schema={"type": "object", "properties": {}},
        max_output_tokens=24_000,
    ))

    assert result[0].status == "completed"
    assert len(calls) == 2
    assert calls[1]["tier"] > calls[0]["tier"]
    assert calls[1]["count"] <= calls[0]["count"]


def test_context_length_classifier_is_specific():
    class ContextLengthError(RuntimeError):
        code = "context_length_exceeded"

    assert context_budget.is_context_length_error(ContextLengthError("bad request")) is True
    assert context_budget.is_context_length_error(RuntimeError("maximum context length is 128k")) is True
    assert context_budget.is_context_length_error(RuntimeError("rate limit exceeded")) is False
