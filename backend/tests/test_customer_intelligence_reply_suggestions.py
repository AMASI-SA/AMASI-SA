"""Hermetic human-approval contract for Customer Intelligence replies."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from pymongo.errors import DuplicateKeyError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import customer_identity
from ai_store_access_contract import ROLE_ASSIGNMENTS, merged_session_permissions
from customer_intelligence.foundation import (
    CONVERSATIONS_COLLECTION,
    CONVERSATION_MESSAGES_COLLECTION,
)
from customer_intelligence.reply_suggestions import (
    REPLY_SUGGESTIONS_COLLECTION,
    ConversationNotFound,
    CustomerIntelligenceActor,
    ReplySuggestionConflict,
    ReplySuggestionReviewIn,
    ReplySuggestionService,
    ensure_reply_suggestion_indexes,
    mark_pending_suggestions_stale,
)
from customer_intelligence.routes import _actor_context, _require_actor_permission
from customer_intelligence.routes import make_customer_intelligence_router

NOW = datetime(2026, 8, 12, 20, 0, tzinfo=timezone.utc)
OWNER_ID = "owner-ci"
MERCHANT_ID = "merchant-ci"
CONVERSATION_ID = "conversation-ci"
EMPLOYEE_ID = "employee-ci"


def _get(document, path):
    value = document
    for part in str(path).split("."):
        if not isinstance(value, dict) or part not in value:
            return None
        value = value[part]
    return value


def _matches(document, selector):
    for key, expected in selector.items():
        actual = _get(document, key)
        if isinstance(expected, dict) and "$in" in expected:
            if actual not in expected["$in"]:
                return False
        elif actual != expected:
            return False
    return True


class FakeCursor:
    def __init__(self, documents):
        self.documents = deepcopy(documents)

    def sort(self, specification):
        for key, direction in reversed(specification):
            self.documents.sort(
                key=lambda row: (row.get(key) is not None, row.get(key)),
                reverse=direction < 0,
            )
        return self

    def limit(self, value):
        self.documents = self.documents[:value]
        return self

    async def to_list(self, *, length):
        return deepcopy(self.documents[:length])


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = deepcopy(documents or [])
        self.indexes = []

    def find(self, selector, projection=None):
        del projection
        return FakeCursor([row for row in self.documents if _matches(row, selector)])

    async def find_one(self, selector, projection=None):
        del projection
        row = next((row for row in self.documents if _matches(row, selector)), None)
        return deepcopy(row) if row is not None else None

    async def update_one(self, selector, update, upsert=False):
        row = next((row for row in self.documents if _matches(row, selector)), None)
        inserted = row is None
        if inserted:
            if not upsert:
                return SimpleNamespace(upserted_id=None, modified_count=0)
            row = deepcopy(selector)
            self.documents.append(row)
            row.update(deepcopy(update.get("$setOnInsert", {})))
        row.update(deepcopy(update.get("$set", {})))
        for key, value in update.get("$inc", {}).items():
            row[key] = int(row.get(key) or 0) + value
        return SimpleNamespace(
            upserted_id="created" if inserted else None,
            modified_count=1,
        )

    async def update_many(self, selector, update):
        modified = 0
        for row in self.documents:
            if not _matches(row, selector):
                continue
            row.update(deepcopy(update.get("$set", {})))
            for key, value in update.get("$inc", {}).items():
                row[key] = int(row.get(key) or 0) + value
            modified += 1
        return SimpleNamespace(modified_count=modified)

    async def delete_one(self, selector):
        for index, row in enumerate(self.documents):
            if _matches(row, selector):
                self.documents.pop(index)
                return SimpleNamespace(deleted_count=1)
        return SimpleNamespace(deleted_count=0)

    async def create_index(self, keys, **options):
        self.indexes.append((keys, options))
        return options.get("name")


class FakeDB:
    def __init__(self):
        conversation = {
            "user_id": OWNER_ID,
            "merchant_id": MERCHANT_ID,
            "conversation_id": CONVERSATION_ID,
            "channel_id": "channel-ci",
            "customer_id": "customer-ci",
            "status": "open",
            "assigned_employee_id": EMPLOYEE_ID,
        }
        inbound = {
            "user_id": OWNER_ID,
            "merchant_id": MERCHANT_ID,
            "conversation_id": CONVERSATION_ID,
            "channel_id": "channel-ci",
            "message_id": "message-customer-1",
            "direction": "inbound",
            "sender_type": "customer",
            "content_type": "text",
            "content_ciphertext": customer_identity.encrypt_private_payload(
                {
                    "content_type": "text",
                    "payload": {"text": "هل السلسال متوفر؟"},
                }
            ),
            "source_event": "whatsapp.messages.text",
            "occurred_at": NOW,
        }
        assignment = {
            "owner_user_id": OWNER_ID,
            "user_id": EMPLOYEE_ID,
            "created_by": OWNER_ID,
            "role_key": "customer_service",
            "enabled": True,
        }
        self.collections = {
            CONVERSATIONS_COLLECTION: FakeCollection([conversation]),
            CONVERSATION_MESSAGES_COLLECTION: FakeCollection([inbound]),
            REPLY_SUGGESTIONS_COLLECTION: FakeCollection(),
            ROLE_ASSIGNMENTS: FakeCollection([assignment]),
        }

    def __getattr__(self, name):
        return self.collections.setdefault(name, FakeCollection())

    def __getitem__(self, name):
        return self.__getattr__(name)


class FakeResponses:
    def __init__(self, *, text="أهلًا بك، سأتحقق لك من التوفر.", callback=None):
        self.text = text
        self.callback = callback
        self.calls = 0
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = deepcopy(kwargs)
        if self.callback:
            await self.callback(kwargs)
        return SimpleNamespace(
            output_text=json.dumps({"text": self.text}, ensure_ascii=False)
        )


@pytest.fixture(autouse=True)
def _encryption(monkeypatch):
    monkeypatch.setenv("MEZAN_CUSTOMER_PII_ENC_KEY", Fernet.generate_key().decode())
    customer_identity._fernet = None
    yield
    customer_identity._fernet = None


def _actor(*, owner=False, actor_id=EMPLOYEE_ID):
    return CustomerIntelligenceActor(
        actor_id=actor_id,
        owner_user_id=OWNER_ID,
        permissions=frozenset(
            {
                "customer_intelligence.inbox.read",
                "customer_intelligence.suggestions.review",
                "customer_intelligence.escalate",
            }
        ),
        is_owner=owner,
    )


@pytest.mark.asyncio
async def test_create_is_encrypted_idempotent_pending_only_and_never_sends():
    db = FakeDB()
    responses = FakeResponses()
    client = SimpleNamespace(responses=responses)
    service = ReplySuggestionService(
        db,
        client_factory=lambda: client,
        now=lambda: NOW + timedelta(seconds=1),
    )

    first = await service.create(actor=_actor(), conversation_id=CONVERSATION_ID)
    second = await service.create(actor=_actor(), conversation_id=CONVERSATION_ID)

    assert first == second
    assert first.status == "pending_approval"
    assert first.requires_human_approval is True
    assert first.send_allowed is False
    assert responses.calls == 1
    assert responses.last_kwargs["store"] is False
    assert not hasattr(client, "whatsapp")

    stored = db.collections[REPLY_SUGGESTIONS_COLLECTION].documents[0]
    assert "text" not in stored
    assert "أهلًا بك" not in repr(stored)
    assert (
        customer_identity.decrypt_private_payload(stored["text_ciphertext"])["text"]
        == first.text
    )
    assert stored["auto_send_allowed"] is False


@pytest.mark.asyncio
async def test_instagram_comment_suggestion_uses_public_privacy_policy():
    db = FakeDB()
    inbound = db.collections[CONVERSATION_MESSAGES_COLLECTION].documents[0]
    inbound["source_event"] = "instagram.comments.comment"
    inbound["content_ciphertext"] = customer_identity.encrypt_private_payload(
        {
            "content_type": "text",
            "payload": {
                "surface": "comment",
                "text": "طلبي متأخر، هذا رقم الطلب 123",
            },
        }
    )
    responses = FakeResponses(text="نعتذر لك، فضلاً راسلنا على الخاص بالتفاصيل.")
    service = ReplySuggestionService(
        db,
        client_factory=lambda: SimpleNamespace(responses=responses),
        now=lambda: NOW + timedelta(seconds=1),
    )

    result = await service.create(actor=_actor(), conversation_id=CONVERSATION_ID)

    assert result.surface == "comment"
    assert "تعليق إنستغرام عام" in responses.last_kwargs["instructions"]
    assert "لا تذكر ولا تطلب رقم جوال" in responses.last_kwargs["instructions"]
    stored = db.collections[REPLY_SUGGESTIONS_COLLECTION].documents[0]
    assert stored["surface"] == "comment"
    assert stored["send_allowed"] is False


@pytest.mark.asyncio
async def test_concurrent_generation_lease_collision_is_safe_conflict_not_500():
    db = FakeDB()
    responses = FakeResponses()
    service = ReplySuggestionService(
        db,
        client_factory=lambda: SimpleNamespace(responses=responses),
        now=lambda: NOW + timedelta(seconds=1),
    )
    collection = db.collections[REPLY_SUGGESTIONS_COLLECTION]
    original_update_one = collection.update_one
    collision_injected = False

    async def racing_update(selector, update, upsert=False):
        nonlocal collision_injected
        if upsert and not collision_injected:
            collision_injected = True
            # Simulate another worker winning the unique basis insert after
            # this worker's initial find_one returned no row.
            await original_update_one(selector, update, upsert=True)
            raise DuplicateKeyError("simulated suggestion basis race")
        return await original_update_one(selector, update, upsert=upsert)

    collection.update_one = racing_update

    with pytest.raises(ReplySuggestionConflict, match="generation_in_progress"):
        await service.create(actor=_actor(), conversation_id=CONVERSATION_ID)

    assert len(collection.documents) == 1
    assert collection.documents[0]["generation_status"] == "in_progress"
    assert responses.calls == 0


@pytest.mark.asyncio
async def test_review_edits_encrypted_text_and_remains_send_locked():
    db = FakeDB()
    service = ReplySuggestionService(
        db,
        client_factory=lambda: SimpleNamespace(responses=FakeResponses()),
        now=lambda: NOW + timedelta(seconds=1),
    )
    suggestion = await service.create(actor=_actor(), conversation_id=CONVERSATION_ID)

    reviewed = await service.review(
        actor=_actor(),
        conversation_id=CONVERSATION_ID,
        suggestion_id=suggestion.suggestion_id,
        review=ReplySuggestionReviewIn(
            decision="approve",
            version=suggestion.version,
            text="أهلًا، أتحقق لك من التوفر الآن.",
        ),
    )

    assert reviewed.status == "reviewed"
    assert reviewed.version == 2
    assert reviewed.send_allowed is False
    assert reviewed.text == "أهلًا، أتحقق لك من التوفر الآن."
    with pytest.raises(ReplySuggestionConflict, match="not_pending"):
        await service.review(
            actor=_actor(),
            conversation_id=CONVERSATION_ID,
            suggestion_id=suggestion.suggestion_id,
            review=ReplySuggestionReviewIn(decision="reject", version=2),
        )


@pytest.mark.asyncio
async def test_review_rejects_whitespace_only_edited_text():
    db = FakeDB()
    service = ReplySuggestionService(
        db,
        client_factory=lambda: SimpleNamespace(responses=FakeResponses()),
        now=lambda: NOW + timedelta(seconds=1),
    )
    suggestion = await service.create(actor=_actor(), conversation_id=CONVERSATION_ID)

    with pytest.raises(ValueError, match="at least 1 character"):
        ReplySuggestionReviewIn(
            decision="approve",
            version=suggestion.version,
            text="   ",
        )

    stored = db.collections[REPLY_SUGGESTIONS_COLLECTION].documents[0]
    assert stored["status"] == "pending_approval"
    assert stored["version"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("decision", "status"),
    [("reject", "rejected"), ("escalate", "escalated")],
)
async def test_reject_and_escalate_are_terminal_internal_decisions(decision, status):
    db = FakeDB()
    service = ReplySuggestionService(
        db,
        client_factory=lambda: SimpleNamespace(responses=FakeResponses()),
        now=lambda: NOW + timedelta(seconds=1),
    )
    suggestion = await service.create(actor=_actor(), conversation_id=CONVERSATION_ID)
    result = await service.review(
        actor=_actor(),
        conversation_id=CONVERSATION_ID,
        suggestion_id=suggestion.suggestion_id,
        review=ReplySuggestionReviewIn(decision=decision, version=1, note="مراجعة"),
    )

    assert result.status == status
    assert result.send_allowed is False
    stored = db.collections[REPLY_SUGGESTIONS_COLLECTION].documents[0]
    assert "review_note" not in stored
    assert "مراجعة" not in repr(stored)
    if decision == "escalate":
        assert (
            db.collections[CONVERSATIONS_COLLECTION].documents[0]["status"]
            == "needs_human"
        )


@pytest.mark.asyncio
async def test_new_customer_or_echo_stales_pending_without_ai_call():
    db = FakeDB()
    responses = FakeResponses()
    service = ReplySuggestionService(
        db,
        client_factory=lambda: SimpleNamespace(responses=responses),
        now=lambda: NOW + timedelta(seconds=1),
    )
    await service.create(actor=_actor(), conversation_id=CONVERSATION_ID)
    assert responses.calls == 1

    changed = await mark_pending_suggestions_stale(
        db,
        user_id=OWNER_ID,
        merchant_id=MERCHANT_ID,
        conversation_id=CONVERSATION_ID,
        reason="smb_message_echo",
        stale_at=NOW + timedelta(minutes=1),
    )

    assert changed == 1
    stored = db.collections[REPLY_SUGGESTIONS_COLLECTION].documents[0]
    assert stored["status"] == "stale"
    assert stored["stale_reason"] == "smb_message_echo"
    assert stored["version"] == 2
    assert responses.calls == 1


@pytest.mark.asyncio
async def test_generation_result_is_stale_if_customer_writes_during_provider_call():
    db = FakeDB()

    async def add_new_inbound(_kwargs):
        prior = deepcopy(db.collections[CONVERSATION_MESSAGES_COLLECTION].documents[0])
        prior.update(
            {
                "message_id": "message-customer-2",
                "occurred_at": NOW + timedelta(seconds=2),
                "content_ciphertext": customer_identity.encrypt_private_payload(
                    {"content_type": "text", "payload": {"text": "ومتى الشحن؟"}}
                ),
            }
        )
        db.collections[CONVERSATION_MESSAGES_COLLECTION].documents.append(prior)

    service = ReplySuggestionService(
        db,
        client_factory=lambda: SimpleNamespace(
            responses=FakeResponses(callback=add_new_inbound)
        ),
        now=lambda: NOW + timedelta(seconds=1),
    )
    with pytest.raises(ReplySuggestionConflict, match="stale"):
        await service.create(actor=_actor(), conversation_id=CONVERSATION_ID)
    assert (
        db.collections[REPLY_SUGGESTIONS_COLLECTION].documents[0]["status"] == "stale"
    )


@pytest.mark.asyncio
async def test_employee_role_permissions_and_cross_tenant_or_assignment_are_hidden():
    db = FakeDB()
    actor = await _actor_context(
        db,
        {"id": EMPLOYEE_ID, "role": "employee", "created_by": OWNER_ID},
    )
    _require_actor_permission(actor, "customer_intelligence.inbox.read")
    _require_actor_permission(actor, "customer_intelligence.suggestions.review")
    _require_actor_permission(actor, "customer_intelligence.escalate")

    service = ReplySuggestionService(db, client_factory=lambda: None)
    with pytest.raises(ConversationNotFound):
        await service.latest(
            actor=CustomerIntelligenceActor(
                actor_id="other-employee",
                owner_user_id=OWNER_ID,
                permissions=actor.permissions,
            ),
            conversation_id=CONVERSATION_ID,
        )
    with pytest.raises(ConversationNotFound):
        await service.latest(
            actor=CustomerIntelligenceActor(
                actor_id=EMPLOYEE_ID,
                owner_user_id="other-owner",
                permissions=actor.permissions,
            ),
            conversation_id=CONVERSATION_ID,
        )


@pytest.mark.asyncio
async def test_auth_session_permission_merge_uses_v2_assignment_without_admin_leakage():
    db = FakeDB()
    employee_permissions = await merged_session_permissions(
        db,
        {"id": EMPLOYEE_ID, "role": "employee", "created_by": OWNER_ID},
        {"dashboard.view"},
    )
    assert "dashboard.view" in employee_permissions
    assert "customer_intelligence.inbox.read" in employee_permissions
    assert "customer_intelligence.suggestions.review" in employee_permissions

    admin_permissions = await merged_session_permissions(
        db,
        {"id": "legacy-admin", "role": "admin"},
        {"dashboard.view"},
    )
    assert admin_permissions == ["dashboard.view"]

    owner_permissions = await merged_session_permissions(
        db,
        {"id": OWNER_ID, "role": "owner"},
        {"dashboard.view"},
    )
    assert "customer_intelligence.inbox.read" in owner_permissions
    assert "customer_intelligence.suggestions.review" in owner_permissions


@pytest.mark.asyncio
async def test_reply_indexes_are_tenant_scoped_and_basis_unique():
    db = FakeDB()
    await ensure_reply_suggestion_indexes(db)
    indexes = db.collections[REPLY_SUGGESTIONS_COLLECTION].indexes
    unique = next(
        (keys, options)
        for keys, options in indexes
        if options.get("name") == "mezan_customer_reply_suggestion_basis_unique"
    )
    assert unique[1]["unique"] is True
    assert unique[0][:3] == [
        ("user_id", 1),
        ("merchant_id", 1),
        ("conversation_id", 1),
    ]


@pytest.mark.asyncio
async def test_employee_create_route_is_scoped_and_no_send_route_exists():
    db = FakeDB()
    responses = FakeResponses()
    service = ReplySuggestionService(
        db,
        client_factory=lambda: SimpleNamespace(responses=responses),
        now=lambda: NOW + timedelta(seconds=1),
    )

    async def current_user():
        return {
            "id": EMPLOYEE_ID,
            "role": "employee",
            "created_by": OWNER_ID,
        }

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(
            current_user,
            db=db,
            reply_suggestion_service=service,
        ),
        prefix="/api",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        created = await client.post(
            f"/api/customer-intelligence/v1/conversations/{CONVERSATION_ID}/reply-suggestion"
        )
        cross_tenant = await client.post(
            "/api/customer-intelligence/v1/conversations/another-tenant/reply-suggestion"
        )
        send = await client.post(
            f"/api/customer-intelligence/v1/conversations/{CONVERSATION_ID}/reply-suggestion/send",
            json={"text": "must not leave Mezan"},
        )

    assert created.status_code == 201, created.text
    assert created.json()["status"] == "pending_approval"
    assert created.json()["send_allowed"] is False
    assert cross_tenant.status_code == 404
    assert cross_tenant.json()["detail"]["code"] == "conversation_not_found"
    assert send.status_code in {404, 405}
    assert responses.calls == 1


@pytest.mark.asyncio
async def test_employee_without_customer_service_permission_is_denied_before_ai():
    db = FakeDB()
    db.collections[ROLE_ASSIGNMENTS].documents[0]["role_key"] = "product_operator"
    db.collections[ROLE_ASSIGNMENTS].documents[0]["extra_permissions"] = [
        "customer_intelligence.suggestions.review"
    ]
    responses = FakeResponses()
    service = ReplySuggestionService(
        db,
        client_factory=lambda: SimpleNamespace(responses=responses),
    )

    async def current_user():
        return {"id": EMPLOYEE_ID, "role": "employee", "created_by": OWNER_ID}

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(
            current_user,
            db=db,
            reply_suggestion_service=service,
        ),
        prefix="/api",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/customer-intelligence/v1/conversations/{CONVERSATION_ID}/reply-suggestion"
        )

    assert response.status_code == 403
    assert response.json()["detail"] == {
        "code": "customer_intelligence_permission_required",
        "permission": "customer_intelligence.inbox.read",
    }
    assert responses.calls == 0


@pytest.mark.asyncio
async def test_employee_with_inbox_but_denied_review_cannot_generate():
    db = FakeDB()
    db.collections[ROLE_ASSIGNMENTS].documents[0]["denied_permissions"] = [
        "customer_intelligence.suggestions.review"
    ]
    responses = FakeResponses()
    service = ReplySuggestionService(
        db,
        client_factory=lambda: SimpleNamespace(responses=responses),
    )

    async def current_user():
        return {"id": EMPLOYEE_ID, "role": "employee", "created_by": OWNER_ID}

    app = FastAPI()
    app.include_router(
        make_customer_intelligence_router(
            current_user,
            db=db,
            reply_suggestion_service=service,
        ),
        prefix="/api",
    )
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        response = await client.post(
            f"/api/customer-intelligence/v1/conversations/{CONVERSATION_ID}/reply-suggestion"
        )

    assert response.status_code == 403
    assert response.json()["detail"]["permission"] == (
        "customer_intelligence.suggestions.review"
    )
    assert responses.calls == 0
