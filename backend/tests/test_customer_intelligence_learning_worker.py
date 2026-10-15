"""Customer-channel learning uses the existing AI runtime without sending."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

import customer_identity
from customer_identity import decrypt_private_payload, encrypt_private_payload
from customer_intelligence.foundation import (
    CHANNELS_COLLECTION,
    CONVERSATION_MESSAGES_COLLECTION,
)
from customer_intelligence.learning_contract import (
    CUSTOMER_DECISIONS_COLLECTION,
    CUSTOMER_MESSAGE_ANALYSES_COLLECTION,
    CUSTOMER_PROBLEMS_COLLECTION,
    CUSTOMER_SIGNALS_COLLECTION,
)
from customer_intelligence.learning_worker import (
    _process_message,
    queue_existing_channel_evidence,
)


NOW = datetime(2026, 8, 13, 11, 0, tzinfo=timezone.utc)


class FakeCollection:
    def __init__(self, document=None):
        self.document = document
        self.inserted = []
        self.updates = []
        self.bulk_updates = []

    async def find_one(self, _query, _projection=None):
        return self.document

    async def insert_one(self, document):
        self.inserted.append(document)
        return SimpleNamespace(inserted_id=len(self.inserted))

    async def update_one(self, query, update):
        self.updates.append((query, update))
        return SimpleNamespace(modified_count=1)

    async def update_many(self, query, update):
        self.bulk_updates.append((query, update))
        return SimpleNamespace(modified_count=1)


class FakeDb:
    def __init__(self):
        setattr(self, CHANNELS_COLLECTION, FakeCollection({"provider": "instagram"}))
        setattr(self, CONVERSATION_MESSAGES_COLLECTION, FakeCollection())
        for name in (
            CUSTOMER_MESSAGE_ANALYSES_COLLECTION,
            CUSTOMER_SIGNALS_COLLECTION,
            CUSTOMER_PROBLEMS_COLLECTION,
            CUSTOMER_DECISIONS_COLLECTION,
        ):
            setattr(self, name, FakeCollection())


class FakeResponses:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(output_text=json.dumps(self.payload, ensure_ascii=False))


def _payload():
    return {
        "summary": "العميل غير راضٍ عن التأخير ويسأل عن موعد التسليم.",
        "language": "ar",
        "sentiment": "negative",
        "urgency": "high",
        "response_guidance": "اعتذر وانقل تفاصيل الطلب إلى الخاص.",
        "signals": [{
            "signal_type": "complaint",
            "label": "تأخر توصيل",
            "rationale": "ذكر العميل أن الطلب متأخر.",
            "commercial_impact": "retention_risk",
            "urgency": "high",
            "confidence": 0.95,
        }],
        "problems": [{
            "problem_code": "delivery_delay",
            "problem_type": "fulfillment",
            "severity": "high",
            "title": "تأخر التوصيل",
            "description": "بلاغ عميل عن تأخر الطلب.",
            "proposed_solution": "مراجعة زمن تجهيز وشحن الطلبات.",
            "measurement_plan": "قياس متوسط زمن التسليم أسبوعيًا.",
            "confidence": 0.9,
        }],
        "decisions": [{
            "decision_type": "problem_resolution",
            "recommendation": "تحليل الطلبات المتأخرة وتحديد السبب.",
            "expected_impact": "تقليل الشكاوى وتحسين الاحتفاظ.",
            "risk": "low",
            "required_approval": "owner",
            "confidence": 0.88,
        }],
    }


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch):
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("MEZAN_OPENAI_CUSTOMER_LEARNING_MODEL", "test-model")
    customer_identity._fernet = None
    yield
    customer_identity._fernet = None


@pytest.mark.asyncio
async def test_inbound_instagram_comment_feeds_every_learning_record_without_send():
    db = FakeDb()
    responses = FakeResponses(_payload())
    client = SimpleNamespace(responses=responses)
    customer_text = "طلبي متأخر، متى يوصل؟"
    message = {
        "user_id": "owner-1",
        "merchant_id": "merchant-1",
        "message_id": "message-1",
        "conversation_id": "conversation-1",
        "customer_id": "customer-1",
        "channel_id": "channel-1",
        "content_type": "text",
        "content_ciphertext": encrypt_private_payload({
            "payload": {"surface": "comment", "text": customer_text}
        }),
        "analysis_lease_id": "lease-1",
        "analysis_attempts": 1,
        "analysis_status": "pending",
    }

    completed = await _process_message(
        db,
        message=message,
        client_factory=lambda: client,
        now=lambda: NOW,
    )

    assert completed is True
    call = responses.calls[0]
    assert call["store"] is False
    assert "للتعليق العام" in call["instructions"]
    assert customer_text in call["input"]
    for name in (
        CUSTOMER_MESSAGE_ANALYSES_COLLECTION,
        CUSTOMER_SIGNALS_COLLECTION,
        CUSTOMER_PROBLEMS_COLLECTION,
        CUSTOMER_DECISIONS_COLLECTION,
    ):
        assert len(getattr(db, name).inserted) == 1
        assert customer_text not in repr(getattr(db, name).inserted[0])

    analysis = getattr(db, CUSTOMER_MESSAGE_ANALYSES_COLLECTION).inserted[0]
    assert decrypt_private_payload(analysis["result_ciphertext"])["summary"]
    update = getattr(db, CONVERSATION_MESSAGES_COLLECTION).updates[-1][1]
    assert update["$set"]["analysis_status"] == "ready"
    assert update["$set"]["analysis_id"] == analysis["analysis_id"]


@pytest.mark.asyncio
async def test_employee_reply_feeds_service_quality_without_becoming_customer_opinion():
    db = FakeDb()
    payload = _payload()
    payload["signals"] = [{
        "signal_type": "response_quality",
        "label": "رد يحتاج تفاصيل أوضح",
        "rationale": "الرد مختصر ولا يحدد الخطوة التالية.",
        "commercial_impact": "service_quality",
        "urgency": "medium",
        "confidence": 0.87,
    }]
    responses = FakeResponses(payload)
    client = SimpleNamespace(responses=responses)
    message = {
        "user_id": "owner-1",
        "merchant_id": "merchant-1",
        "message_id": "employee-message-1",
        "conversation_id": "conversation-1",
        "customer_id": "customer-1",
        "channel_id": "channel-1",
        "direction": "outbound",
        "sender_type": "employee",
        "content_type": "text",
        "content_ciphertext": encrypt_private_payload({
            "payload": {"surface": "direct_message", "text": "حياك، بنراجع الموضوع."}
        }),
        "analysis_lease_id": "lease-employee-1",
        "analysis_attempts": 1,
        "analysis_status": "pending",
    }

    completed = await _process_message(
        db,
        message=message,
        client_factory=lambda: client,
        now=lambda: NOW,
    )

    assert completed is True
    call = responses.calls[0]
    assert '"source_role": "employee"' in call["input"]
    assert "لا تنسب نص الموظف إلى العميل" in call["instructions"]
    analysis = getattr(db, CUSTOMER_MESSAGE_ANALYSES_COLLECTION).inserted[0]
    assert analysis["source_role"] == "employee"
    assert "service_quality" in analysis["analysis_targets"]
    assert "customer_signal" not in analysis["analysis_targets"]
    signal = getattr(db, CUSTOMER_SIGNALS_COLLECTION).inserted[0]
    assert signal["source_role"] == "employee"
    assert signal["signal_type"] == "response_quality"


@pytest.mark.asyncio
async def test_uncaptioned_media_is_explicitly_marked_metadata_only():
    db = FakeDb()
    responses = FakeResponses({
        **_payload(),
        "signals": [],
        "problems": [],
        "decisions": [],
    })
    client = SimpleNamespace(responses=responses)
    message = {
        "user_id": "owner-1",
        "merchant_id": "merchant-1",
        "message_id": "image-message-1",
        "conversation_id": "conversation-1",
        "customer_id": "customer-1",
        "channel_id": "channel-1",
        "direction": "inbound",
        "sender_type": "customer",
        "content_type": "image",
        "content_ciphertext": encrypt_private_payload({
            "payload": {"surface": "direct_message", "provider_media_id": "opaque-media"}
        }),
        "analysis_lease_id": "lease-image-1",
        "analysis_attempts": 1,
        "analysis_status": "pending",
    }

    completed = await _process_message(
        db,
        message=message,
        client_factory=lambda: client,
        now=lambda: NOW,
    )

    assert completed is True
    assert '"content_mode": "metadata_only"' in responses.calls[0]["input"]
    analysis = getattr(db, CUSTOMER_MESSAGE_ANALYSES_COLLECTION).inserted[0]
    assert analysis["content_mode"] == "metadata_only"


@pytest.mark.asyncio
async def test_terminal_failure_is_content_free_and_never_claims_send_capability():
    db = FakeDb()
    message = {
        "user_id": "owner-1",
        "merchant_id": "merchant-1",
        "message_id": "message-2",
        "conversation_id": "conversation-1",
        "customer_id": "customer-1",
        "channel_id": "channel-1",
        "content_type": "text",
        "content_ciphertext": encrypt_private_payload({
            "payload": {"surface": "direct_message", "text": "نص سري للعميل"}
        }),
        "analysis_lease_id": "lease-2",
        "analysis_attempts": 3,
        "analysis_status": "pending",
    }

    def broken_client():
        raise RuntimeError("provider unavailable")

    completed = await _process_message(
        db,
        message=message,
        client_factory=broken_client,
        now=lambda: NOW,
    )

    assert completed is False
    update = getattr(db, CONVERSATION_MESSAGES_COLLECTION).updates[-1][1]
    assert update["$set"]["analysis_status"] == "failed"
    assert update["$set"]["analysis_last_error_code"] == "analysis_provider_failed"
    assert "نص سري" not in repr(update)


@pytest.mark.asyncio
async def test_existing_customer_and_employee_evidence_is_queued_idempotently():
    db = FakeDb()

    result = await queue_existing_channel_evidence(db)

    assert result == {
        "customer_messages_queued": 1,
        "employee_responses_queued": 1,
    }
    updates = getattr(db, CONVERSATION_MESSAGES_COLLECTION).bulk_updates
    assert updates[0][0]["sender_type"] == "customer"
    assert "customer_signal" in updates[0][1]["$set"]["analysis_targets"]
    assert updates[1][0]["sender_type"] == "employee"
    assert "service_quality" in updates[1][1]["$set"]["analysis_targets"]
    assert "customer_signal" not in updates[1][1]["$set"]["analysis_targets"]
