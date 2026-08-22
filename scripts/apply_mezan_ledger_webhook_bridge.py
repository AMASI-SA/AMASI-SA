from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "backend/salla_integration/webhook_order_sync.py"
TEST = ROOT / "backend/tests/test_salla_webhook_attribution_ledger_bridge.py"

text = TARGET.read_text(encoding="utf-8")

needle = '''        result = await upsert_order(
            db,
            user_id,
            order_number,
            doc,
            source="salla_direct",
            raw=payload,
        )
        first_party_attribution = {
'''
replacement = '''        result = await upsert_order(
            db,
            user_id,
            order_number,
            doc,
            source="salla_direct",
            raw=payload,
        )
        attribution_ledger = {
            "synced": False,
            "reason": "not_attempted",
        }
        try:
            from mezan_attribution_ledger_sync import (
                safe_sync_order_to_attribution_ledger,
            )

            attribution_ledger = await safe_sync_order_to_attribution_ledger(
                db,
                user_id=user_id,
                order=result.get("doc") or doc,
            )
        except Exception as exc:
            # Ledger attribution is analytics enrichment only. It must never
            # block authoritative Salla ingestion or fulfilment.
            attribution_ledger = {
                "synced": False,
                "reason": "ledger_bridge_unavailable",
                "error_type": type(exc).__name__,
            }
        first_party_attribution = {
'''
if needle not in text:
    raise SystemExit("target upsert insertion point not found")
text = text.replace(needle, replacement, 1)

needle2 = '''            "auto_fulfillment": auto_fulfillment,
            "first_party_attribution": first_party_attribution,
            "no_salla_api_calls": True,
'''
replacement2 = '''            "auto_fulfillment": auto_fulfillment,
            "first_party_attribution": first_party_attribution,
            "attribution_ledger": attribution_ledger,
            "no_salla_api_calls": True,
'''
if needle2 not in text:
    raise SystemExit("target return insertion point not found")
text = text.replace(needle2, replacement2, 1)
TARGET.write_text(text, encoding="utf-8")

TEST.write_text(r'''import sys
import types

import pytest

import salla_integration.webhook_order_sync as webhook_sync


@pytest.mark.asyncio
async def test_verified_order_webhook_refreshes_attribution_ledger(monkeypatch):
    async def fake_resolve_user_id(_db, _merchant_id):
        return "user-1"

    async def fake_upsert_order(_db, _user_id, _order_number, doc, **_kwargs):
        return {
            "created": True,
            "doc": {
                **doc,
                "raw_by_source": {
                    "salla_direct": {
                        "id": "123",
                        "source_details": {
                            "source": "snapchat",
                            "campaign_id": "cmp-1",
                        },
                    }
                },
            },
        }

    ledger_calls = []

    async def fake_ledger_sync(_db, *, user_id, order):
        ledger_calls.append((user_id, order))
        return {
            "synced": True,
            "order_key": order.get("order_number"),
            "attribution_quality": "confirmed",
            "decision_safe": True,
            "profit_known": False,
        }

    async def fake_link_order_attribution(*_args, **_kwargs):
        return {"linked": False, "reason": "test"}

    async def fake_get_order(*_args, **_kwargs):
        return {"order_number": "123"}

    async def fake_auto_route(*_args, **_kwargs):
        return {"promoted": False, "reason": "test"}

    class FakeRepo:
        def __init__(self, _db):
            pass

    monkeypatch.setattr(webhook_sync, "_resolve_user_id", fake_resolve_user_id)
    monkeypatch.setattr(webhook_sync, "upsert_order", fake_upsert_order)
    monkeypatch.setattr(
        webhook_sync,
        "_salla_order_to_doc",
        lambda payload: {"order_number": str(payload.get("id") or "123")},
    )
    monkeypatch.setattr(webhook_sync, "is_attribution_pilot_store", lambda _merchant: False)

    monkeypatch.setitem(
        sys.modules,
        "mezan_attribution_ledger_sync",
        types.SimpleNamespace(
            safe_sync_order_to_attribution_ledger=fake_ledger_sync,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "first_party_attribution",
        types.SimpleNamespace(link_order_attribution=fake_link_order_attribution),
    )
    monkeypatch.setitem(
        sys.modules,
        "fulfillment_v2_routes",
        types.SimpleNamespace(auto_route_instant_order=fake_auto_route),
    )
    monkeypatch.setitem(
        sys.modules,
        "order_engine.repository",
        types.SimpleNamespace(MongoOrderRepository=FakeRepo),
    )
    monkeypatch.setitem(
        sys.modules,
        "order_engine.service",
        types.SimpleNamespace(get_order=fake_get_order),
    )

    result = await webhook_sync.sync_order_from_verified_webhook(
        object(),
        {
            "event": "order.created",
            "merchant": "store-1",
            "data": {"id": "123", "reference_id": "123"},
        },
    )

    assert result["synced"] is True
    assert result["attribution_ledger"]["synced"] is True
    assert result["attribution_ledger"]["attribution_quality"] == "confirmed"
    assert len(ledger_calls) == 1
    assert ledger_calls[0][0] == "user-1"
    assert ledger_calls[0][1]["raw_by_source"]["salla_direct"]["id"] == "123"


@pytest.mark.asyncio
async def test_ledger_failure_never_blocks_salla_order_ingestion(monkeypatch):
    async def fake_resolve_user_id(_db, _merchant_id):
        return "user-1"

    async def fake_upsert_order(_db, _user_id, _order_number, doc, **_kwargs):
        return {"created": False, "doc": dict(doc)}

    async def exploding_ledger_sync(*_args, **_kwargs):
        raise RuntimeError("ledger unavailable")

    monkeypatch.setattr(webhook_sync, "_resolve_user_id", fake_resolve_user_id)
    monkeypatch.setattr(webhook_sync, "upsert_order", fake_upsert_order)
    monkeypatch.setattr(
        webhook_sync,
        "_salla_order_to_doc",
        lambda payload: {"order_number": str(payload.get("id") or "456")},
    )
    monkeypatch.setattr(webhook_sync, "is_attribution_pilot_store", lambda _merchant: False)
    monkeypatch.setitem(
        sys.modules,
        "mezan_attribution_ledger_sync",
        types.SimpleNamespace(
            safe_sync_order_to_attribution_ledger=exploding_ledger_sync,
        ),
    )

    result = await webhook_sync.sync_order_from_verified_webhook(
        object(),
        {
            "event": "order.updated",
            "merchant": "store-1",
            "data": {"id": "456", "reference_id": "456"},
        },
    )

    assert result["synced"] is True
    assert result["attribution_ledger"]["synced"] is False
    assert result["attribution_ledger"]["reason"] == "ledger_bridge_unavailable"
''', encoding="utf-8")

print(f"updated {TARGET.relative_to(ROOT)}")
print(f"wrote {TEST.relative_to(ROOT)}")
