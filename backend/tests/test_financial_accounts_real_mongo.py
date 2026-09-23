from __future__ import annotations

import asyncio
import hashlib
import os
from types import SimpleNamespace
import uuid

import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI, HTTPException, Request
from httpx import ASGITransport, AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient

import accounting_financial_accounts as financial_accounts
from accounting_atomic import atomic_owner
from accounting_financial_accounts import (
    PERMISSIONS,
    ensure_financial_account_indexes,
    install_financial_account_routes,
)
from accounting_ledger_v2 import (
    AccountingLedgerV2Error,
    ensure_accounting_ledger_v2_indexes,
)
from accounting_source_files import preserve_original
from accounting_writer_transition import assert_writer_allowed
from ledger_core import post_txn_group


OWNER = "owner-1131"
BASE = "/api/accounting-module/financial-accounts"
OPENING = BASE + "/opening-balances"
LEGACY_PAGE = "accounting.settlements.view"
ALL_NEW = sorted(set(PERMISSIONS.values()))


def _user(user_id: str, permissions: list[str], *, owner: str = OWNER, role: str = "employee"):
    row = {
        "_id": user_id,
        "id": user_id,
        "role": role,
        "accounting_permissions": sorted(set([LEGACY_PAGE, *permissions])),
        "is_active": True,
        "disabled": False,
    }
    if role != "owner":
        row["created_by"] = owner
    return row


USERS = [
    _user("full", ALL_NEW),
    _user("viewer", [PERMISSIONS["accounts_view"], PERMISSIONS["opening_view"]]),
    _user(
        "manager",
        [
            PERMISSIONS["accounts_view"],
            PERMISSIONS["accounts_manage"],
            PERMISSIONS["opening_view"],
            PERMISSIONS["drafts_manage"],
        ],
    ),
    _user("reviewer", [PERMISSIONS["opening_view"], PERMISSIONS["review"]]),
    _user("poster", [PERMISSIONS["opening_view"], PERMISSIONS["post"]]),
    _user("reverser", [PERMISSIONS["opening_view"], PERMISSIONS["reverse"]]),
    _user("legacy-approve", ["accounting.opening_balances.approve"]),
    _user("no-new-permissions", []),
    _user("owner-plain", [], owner="owner-plain", role="owner"),
]


def _headers(user: str) -> dict[str, str]:
    return {"X-Test-User": user}


def _detail_code(response) -> str | None:
    body = response.json()
    detail = body.get("detail", body)
    return detail.get("code") if isinstance(detail, dict) else None


@pytest_asyncio.fixture
async def mongo_db():
    uri = os.environ.get("MZ2_TEST_MONGO_URI")
    if not uri:
        pytest.skip("MZ2_TEST_MONGO_URI is required for real Mongo tests")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5_000)
    name = f"mz2_1131_{uuid.uuid4().hex}"
    db = client[name]
    await client.admin.command("ping")
    await ensure_accounting_ledger_v2_indexes(db)
    await ensure_financial_account_indexes(db)
    await db.users.insert_many(USERS)
    try:
        yield db
    finally:
        await client.drop_database(name)
        client.close()


@pytest_asyncio.fixture
async def api(mongo_db):
    app = FastAPI()
    router = APIRouter()

    async def current_user(request: Request):
        return {"id": request.headers.get("X-Test-User", "")}

    install_financial_account_routes(router, mongo_db, current_user)
    app.include_router(router, prefix="/api")
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(db=mongo_db, client=client)


async def _upload(api, *, user: str = "manager", content: bytes = b"opening-evidence-v1"):
    response = await api.client.post(
        OPENING + "/evidence",
        headers=_headers(user),
        data={"purpose": "opening_balance", "section_id": "banks_cash"},
        files={"file": ("opening.xlsx", content, "application/octet-stream")},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _draft_payload(file_id: str, *, key: str, replaces: str | None = None):
    payload = {
        "idempotency_key": key,
        "cutover_at": "2026-09-15T00:00:00+03:00",
        "lines": [
            {
                "category": "banks_cash",
                "entity_type": "bank",
                "entity_id": "bank-main",
                "sub_account": "main",
                "side": "debit",
                "amount": "115.00",
                "currency": "SAR",
                "sar_amount": "115.00",
                "fx_rate": "1.00",
                "evidence_file_id": file_id,
            },
            {
                "category": "equity",
                "entity_type": "equity",
                "entity_id": "opening-equity",
                "side": "credit",
                "amount": "115.00",
                "currency": "SAR",
                "sar_amount": "115.00",
                "fx_rate": "1.00",
                "evidence_file_id": file_id,
            },
        ],
    }
    if replaces:
        payload["replaces_draft_id"] = replaces
    return payload


async def _draft(api, file_id: str, *, key: str = "draft-key-0001", replaces: str | None = None):
    response = await api.client.post(
        OPENING + "/drafts",
        headers=_headers("manager"),
        json=_draft_payload(file_id, key=key, replaces=replaces),
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _review(api, draft: dict, *, key: str = "review-key-0001"):
    response = await api.client.post(
        f"{OPENING}/drafts/{draft['id']}/review",
        headers=_headers("reviewer"),
        json={"version": draft["version"], "idempotency_key": key, "note": "مراجعة موثقة"},
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _activate_v2(api):
    blocked = await api.client.post(
        BASE + "/transition",
        headers=_headers("manager"),
        json={"target": "transition_blocked", "expected_revision": 0, "activation_ref": "freeze-ref"},
    )
    assert blocked.status_code == 200, blocked.text
    active = await api.client.post(
        BASE + "/transition",
        headers=_headers("manager"),
        json={"target": "v2_active", "expected_revision": 1, "activation_ref": "activation-1131"},
    )
    assert active.status_code == 200, active.text
    return active.json()


def _post_payload(version: int, *, key: str = "post-key-0001", note: str = "ترحيل الافتتاحية"):
    return {"version": version, "idempotency_key": key, "note": note}


def _reverse_payload(version: int, *, key: str = "reverse-key-0001"):
    return {
        "version": version,
        "idempotency_key": key,
        "note": "عكس إلحاقي موثق",
        "effective_at": "2026-09-16T10:00:00+03:00",
    }


@pytest.mark.asyncio
async def test_real_mongo_supports_sessions_and_read_only_transaction(mongo_db):
    hello = await mongo_db.client.admin.command("hello")
    assert hello.get("setName")
    assert hello.get("logicalSessionTimeoutMinutes") is not None
    async with await mongo_db.client.start_session() as session:
        session.start_transaction()
        await mongo_db.users.count_documents({}, session=session)
        await session.abort_transaction()


@pytest.mark.asyncio
async def test_http_account_crud_idempotency_and_tenant_isolation(api):
    definitions = await api.client.get(BASE + "/definitions", headers=_headers("viewer"))
    assert definitions.status_code == 200
    assert "cash" in definitions.json()["account_types"]

    payload = {
        "name": "صندوق المتجر",
        "account_type": "cash",
        "currency": "sar",
        "external_ref": "cash-main",
        "idempotency_key": "account-key-0001",
    }
    created = await api.client.post(BASE, headers=_headers("manager"), json=payload)
    assert created.status_code == 200, created.text
    account = created.json()
    assert account["currency"] == "SAR"
    assert account["existing"] is False

    replay = await api.client.post(BASE, headers=_headers("manager"), json=payload)
    assert replay.status_code == 200
    assert replay.json()["id"] == account["id"]
    assert replay.json()["existing"] is True

    conflict = await api.client.post(
        BASE,
        headers=_headers("manager"),
        json={**payload, "name": "صندوق آخر"},
    )
    assert conflict.status_code == 409
    assert _detail_code(conflict) == "financial_account_idempotency_conflict"

    updated = await api.client.patch(
        f"{BASE}/accounts/{account['id']}",
        headers=_headers("manager"),
        json={"version": 1, "name": "الصندوق الرئيسي"},
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == 2

    await api.db.mz2_financial_accounts.insert_one({
        "_id": "other:account",
        "id": "other-account",
        "user_id": "other-owner",
        "name": "حساب مستأجر آخر",
        "status": "active",
        "version": 1,
    })
    foreign = await api.client.get(f"{BASE}/accounts/other-account", headers=_headers("full"))
    assert foreign.status_code == 404

    archived = await api.client.request(
        "DELETE",
        f"{BASE}/accounts/{account['id']}",
        headers=_headers("manager"),
        json={"version": 2, "reason": "إغلاق الصندوق"},
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"


@pytest.mark.asyncio
async def test_permissions_are_separate_and_legacy_key_or_owner_do_not_grant(api):
    assert (await api.client.get(BASE, headers=_headers("viewer"))).status_code == 200
    account_payload = {
        "name": "حساب مرفوض",
        "account_type": "cash",
        "currency": "SAR",
        "idempotency_key": "denied-account-1",
    }
    assert (await api.client.post(BASE, headers=_headers("viewer"), json=account_payload)).status_code == 403
    assert (await api.client.get(BASE + "/definitions", headers=_headers("no-new-permissions"))).status_code == 403
    assert (await api.client.get(BASE + "/definitions", headers=_headers("owner-plain"))).status_code == 403

    action = {"version": 1, "idempotency_key": "permission-action", "note": "اختبار فصل الصلاحيات"}
    assert (
        await api.client.post(f"{OPENING}/drafts/missing/review", headers=_headers("manager"), json=action)
    ).status_code == 403
    assert (
        await api.client.post(f"{OPENING}/drafts/missing/post", headers=_headers("manager"), json=action)
    ).status_code == 403
    assert (
        await api.client.post(f"{OPENING}/drafts/missing/post", headers=_headers("reviewer"), json=action)
    ).status_code == 403
    assert (
        await api.client.post(f"{OPENING}/drafts/missing/post", headers=_headers("reverser"), json=action)
    ).status_code == 403
    assert (
        await api.client.post(f"{OPENING}/drafts/missing/review", headers=_headers("legacy-approve"), json=action)
    ).status_code == 403


@pytest.mark.asyncio
async def test_opening_replacement_and_evidence_is_immutable(api):
    evidence = await _upload(api)
    source = await api.db.accounting_source_files.find_one({"file_id": evidence["source_file_id"]})
    assert source["user_id"] == OWNER
    assert source["sha256"] == hashlib.sha256(bytes(source["content"])).hexdigest()
    assert source["size"] == len(bytes(source["content"]))
    with pytest.raises(ValueError):
        await preserve_original(api.db, OWNER, evidence["source_file_id"], b"different-bytes")

    first = await _draft(api, evidence["source_file_id"], key="draft-replace-0001")
    rejected = await api.client.post(
        OPENING + "/drafts",
        headers=_headers("manager"),
        json=_draft_payload(evidence["source_file_id"], key="draft-replace-0002"),
    )
    assert rejected.status_code == 409
    assert _detail_code(rejected) == "opening_draft_replacement_required"

    second = await _draft(
        api,
        evidence["source_file_id"],
        key="draft-replace-0002",
        replaces=first["id"],
    )
    assert second["status"] == "draft"
    assert await api.db.mz2_opening_balance_drafts.count_documents(
        {"user_id": OWNER, "active_slot": "opening"}
    ) == 1
    replaced = await api.db.mz2_opening_balance_drafts.find_one({"id": first["id"]})
    assert replaced["status"] == "replaced"
    assert "active_slot" not in replaced

    replay = await _draft(
        api,
        evidence["source_file_id"],
        key="draft-replace-0002",
        replaces=first["id"],
    )
    assert replay["id"] == second["id"]
    assert replay["existing"] is True


@pytest.mark.asyncio
async def test_http_review_post_concurrency_retry_and_append_only_reverse(api):
    evidence = await _upload(api)
    draft = await _draft(api, evidence["source_file_id"])
    reviewed = await _review(api, draft)
    assert reviewed["status"] == "reviewed"
    snapshot = reviewed["evidence_snapshot"][0]
    assert snapshot["owner_id"] == OWNER
    assert snapshot["source_file_id"] == evidence["source_file_id"]
    assert snapshot["purpose"] == "opening_balance"
    assert snapshot["section_id"] == "banks_cash"
    assert snapshot["sha256"] == evidence["sha256"]
    assert snapshot["size"] == evidence["size"]
    assert snapshot["approval_version"] == reviewed["version"]
    assert snapshot["approved_by"] == "reviewer"
    assert snapshot["approved_at"] == reviewed["reviewed_at"]

    before_activation = await api.client.post(
        f"{OPENING}/drafts/{draft['id']}/post",
        headers=_headers("poster"),
        json=_post_payload(reviewed["version"]),
    )
    assert before_activation.status_code == 423
    assert _detail_code(before_activation) == "accounting_v2_not_active"
    assert await api.db.accounting_journal_groups_v2.count_documents({}) == 0

    blocked = await api.client.post(
        BASE + "/transition",
        headers=_headers("manager"),
        json={"target": "transition_blocked", "expected_revision": 0, "activation_ref": "freeze-ref"},
    )
    assert blocked.status_code == 200
    blocked_post = await api.client.post(
        f"{OPENING}/drafts/{draft['id']}/post",
        headers=_headers("poster"),
        json=_post_payload(reviewed["version"]),
    )
    assert blocked_post.status_code == 423
    assert _detail_code(blocked_post) == "accounting_transition_blocked"
    assert await api.db.accounting_journal_groups_v2.count_documents({}) == 0

    active = await api.client.post(
        BASE + "/transition",
        headers=_headers("manager"),
        json={"target": "v2_active", "expected_revision": 1, "activation_ref": "activation-1131"},
    )
    assert active.status_code == 200

    post_url = f"{OPENING}/drafts/{draft['id']}/post"
    post_json = _post_payload(reviewed["version"])
    first, second = await asyncio.gather(
        api.client.post(post_url, headers=_headers("poster"), json=post_json),
        api.client.post(post_url, headers=_headers("poster"), json=post_json),
    )
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    assert first.json()["txn_group_id"] == second.json()["txn_group_id"]
    assert await api.db.accounting_journal_groups_v2.count_documents({"user_id": OWNER}) == 1
    assert await api.db.accounting_general_ledger_v2.count_documents({"user_id": OWNER}) == 2

    replay = await api.client.post(post_url, headers=_headers("poster"), json=post_json)
    assert replay.status_code == 200
    assert replay.json()["existing"] is True
    conflict = await api.client.post(
        post_url,
        headers=_headers("poster"),
        json=_post_payload(reviewed["version"], note="محتوى مختلف"),
    )
    assert conflict.status_code == 409
    assert _detail_code(conflict) == "opening_action_idempotency_conflict"

    posted = first.json()
    original_before = await api.db.accounting_journal_groups_v2.find_one({
        "txn_group_id": posted["txn_group_id"]
    })
    reverse_url = f"{OPENING}/drafts/{draft['id']}/reverse"
    reversed_response = await api.client.post(
        reverse_url,
        headers=_headers("reverser"),
        json=_reverse_payload(posted["version"]),
    )
    assert reversed_response.status_code == 200, reversed_response.text
    reversed_draft = reversed_response.json()
    assert reversed_draft["status"] == "reversed"
    assert reversed_draft["reversal_txn_group_id"] != posted["txn_group_id"]
    original_after = await api.db.accounting_journal_groups_v2.find_one({
        "txn_group_id": posted["txn_group_id"]
    })
    assert original_after == original_before
    assert await api.db.accounting_journal_groups_v2.count_documents({"user_id": OWNER}) == 2

    reverse_replay = await api.client.post(
        reverse_url,
        headers=_headers("reverser"),
        json=_reverse_payload(posted["version"]),
    )
    assert reverse_replay.status_code == 200
    assert reverse_replay.json()["reversal_txn_group_id"] == reversed_draft["reversal_txn_group_id"]
    assert reverse_replay.json()["existing"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["owner", "hash", "size", "version"])
async def test_evidence_mismatch_fails_closed(api, mismatch):
    evidence = await _upload(api, content=b"immutable-evidence")
    draft = await _draft(api, evidence["source_file_id"], key=f"draft-mismatch-{mismatch}")

    if mismatch == "owner":
        await api.db.mz2_opening_evidence.update_one(
            {"source_file_id": evidence["source_file_id"]}, {"$set": {"user_id": "other-owner"}}
        )
    elif mismatch == "hash":
        await api.db.accounting_source_files.update_one(
            {"file_id": evidence["source_file_id"]}, {"$set": {"sha256": "0" * 64}}
        )
    elif mismatch == "size":
        await api.db.mz2_opening_evidence.update_one(
            {"source_file_id": evidence["source_file_id"]}, {"$inc": {"size": 1}}
        )

    if mismatch != "version":
        response = await api.client.post(
            f"{OPENING}/drafts/{draft['id']}/review",
            headers=_headers("reviewer"),
            json={"version": 1, "idempotency_key": f"review-{mismatch}-0001", "note": "مراجعة مرفوضة"},
        )
        assert response.status_code == 409
        assert _detail_code(response) in {
            "opening_evidence_missing_or_foreign",
            "opening_evidence_contract_mismatch",
        }
        assert await api.db.accounting_journal_groups_v2.count_documents({}) == 0
        return

    reviewed = await _review(api, draft, key="review-version-0001")
    await api.db.mz2_opening_balance_drafts.update_one(
        {"id": draft["id"]}, {"$set": {"evidence_snapshot.0.approval_version": 999}}
    )
    await _activate_v2(api)
    response = await api.client.post(
        f"{OPENING}/drafts/{draft['id']}/post",
        headers=_headers("poster"),
        json=_post_payload(reviewed["version"], key="post-version-0001"),
    )
    assert response.status_code == 409
    assert _detail_code(response) == "opening_evidence_approval_mismatch"
    assert await api.db.accounting_journal_groups_v2.count_documents({}) == 0


@pytest.mark.asyncio
async def test_closed_period_fails_before_any_journal(api):
    evidence = await _upload(api)
    draft = await _draft(api, evidence["source_file_id"], key="draft-closed-0001")
    reviewed = await _review(api, draft, key="review-closed-0001")
    await _activate_v2(api)
    await api.db.mz2_accounting_periods.insert_one({
        "_id": f"{OWNER}:2026-09",
        "user_id": OWNER,
        "month": "2026-09",
        "closed": True,
    })
    response = await api.client.post(
        f"{OPENING}/drafts/{draft['id']}/post",
        headers=_headers("poster"),
        json=_post_payload(reviewed["version"], key="post-closed-0001"),
    )
    assert response.status_code == 409
    assert _detail_code(response) == "accounting_period_closed"
    assert await api.db.accounting_journal_groups_v2.count_documents({}) == 0
    stored = await api.db.mz2_opening_balance_drafts.find_one({"id": draft["id"]})
    assert stored["status"] == "reviewed"


@pytest.mark.asyncio
async def test_post_failure_rolls_back_and_never_falls_back_to_legacy(api, monkeypatch):
    evidence = await _upload(api)
    draft = await _draft(api, evidence["source_file_id"], key="draft-rollback-0001")
    reviewed = await _review(api, draft, key="review-rollback-0001")
    await _activate_v2(api)
    real_post = financial_accounts.post_opening_journal_v2

    async def fail_after_v2_write(*args, **kwargs):
        await real_post(*args, **kwargs)
        raise RuntimeError("forced-after-v2-write")

    monkeypatch.setattr(financial_accounts, "post_opening_journal_v2", fail_after_v2_write)
    response = await api.client.post(
        f"{OPENING}/drafts/{draft['id']}/post",
        headers=_headers("poster"),
        json=_post_payload(reviewed["version"], key="post-rollback-0001"),
    )
    assert response.status_code == 500
    assert await api.db.accounting_journal_groups_v2.count_documents({}) == 0
    assert await api.db.accounting_general_ledger_v2.count_documents({}) == 0
    assert await api.db.general_ledger.count_documents({}) == 0
    stored = await api.db.mz2_opening_balance_drafts.find_one({"id": draft["id"]})
    assert stored["status"] == "reviewed"

    monkeypatch.setattr(financial_accounts, "post_opening_journal_v2", real_post)
    retry = await api.client.post(
        f"{OPENING}/drafts/{draft['id']}/post",
        headers=_headers("poster"),
        json=_post_payload(reviewed["version"], key="post-rollback-0001"),
    )
    assert retry.status_code == 200, retry.text
    assert await api.db.accounting_journal_groups_v2.count_documents({}) == 1
    assert await api.db.general_ledger.count_documents({}) == 0


@pytest.mark.asyncio
async def test_transition_gate_blocks_all_channels_and_invalid_revision(api):
    blocked = await api.client.post(
        BASE + "/transition",
        headers=_headers("manager"),
        json={"target": "transition_blocked", "expected_revision": 0, "activation_ref": "freeze-ref"},
    )
    assert blocked.status_code == 200
    for writer in ("legacy", "v2"):
        with pytest.raises(HTTPException) as exc:
            await assert_writer_allowed(api.db, OWNER, writer)
        assert exc.value.detail["code"] == "accounting_transition_blocked"

    legacy_entries = [
        {"entity_type": "bank", "entity_id": "bank-main", "entry_type": "test", "amount": 1, "side": "debit"},
        {"entity_type": "equity", "entity_id": "equity", "entry_type": "test", "amount": 1, "side": "credit"},
    ]
    for channel in ("worker", "webhook", "retry", "manual"):
        with pytest.raises(HTTPException) as exc:
            await post_txn_group(
                api.db,
                user_id=OWNER,
                actor_id="system",
                actor_name="System",
                entries=legacy_entries,
                txn_type=f"test_{channel}",
                metadata={"channel": channel},
            )
        assert exc.value.detail["code"] == "accounting_transition_blocked"

        async def direct(scoped, channel=channel):
            await scoped.general_ledger.insert_one({
                "user_id": OWNER,
                "channel": channel,
                "amount": 1,
            })

        with pytest.raises(HTTPException) as direct_exc:
            await atomic_owner(api.db, OWNER, direct)
        assert direct_exc.value.detail["code"] == "accounting_transition_blocked"

    assert await api.db.general_ledger.count_documents({}) == 0
    wrong_revision = await api.client.post(
        BASE + "/transition",
        headers=_headers("manager"),
        json={"target": "v2_active", "expected_revision": 0, "activation_ref": "activation"},
    )
    assert wrong_revision.status_code == 409
    assert _detail_code(wrong_revision) == "accounting_transition_revision_mismatch"

    await api.db.mz2_writer_transition.update_one(
        {"_id": OWNER}, {"$set": {"contract_revision": 999, "state": "v2_active", "activation_ref": "bad"}}
    )
    invalid = await api.client.get(BASE + "/transition", headers=_headers("viewer"))
    assert invalid.status_code == 423
    assert _detail_code(invalid) == "accounting_transition_contract_invalid"


@pytest.mark.asyncio
async def test_unknown_tenant_and_opening_draft_are_isolated(api):
    evidence = await _upload(api)
    draft = await _draft(api, evidence["source_file_id"], key="draft-isolation-0001")
    await api.db.mz2_opening_balance_drafts.update_one(
        {"id": draft["id"]}, {"$set": {"user_id": "other-owner"}}
    )
    response = await api.client.get(
        f"{OPENING}/drafts/{draft['id']}", headers=_headers("viewer")
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_ledger_error_does_not_trigger_legacy_fallback(api, monkeypatch):
    evidence = await _upload(api)
    draft = await _draft(api, evidence["source_file_id"], key="draft-v2-failure-0001")
    reviewed = await _review(api, draft, key="review-v2-failure-0001")
    await _activate_v2(api)

    async def fail_v2(*_args, **_kwargs):
        raise AccountingLedgerV2Error("forced_v2_failure", "forced failure")

    monkeypatch.setattr(financial_accounts, "post_opening_journal_v2", fail_v2)
    response = await api.client.post(
        f"{OPENING}/drafts/{draft['id']}/post",
        headers=_headers("poster"),
        json=_post_payload(reviewed["version"], key="post-v2-failure-0001"),
    )
    assert response.status_code == 409
    assert _detail_code(response) == "forced_v2_failure"
    assert await api.db.general_ledger.count_documents({}) == 0
    assert await api.db.accounting_journal_groups_v2.count_documents({}) == 0
