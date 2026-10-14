"""Synthetic capability admission: no provider, stock or financial writes."""
from datetime import datetime, timedelta, timezone
import pytest
from mongomock_motor import AsyncMongoMockClient
from shipping_print_document import COLLECTION, DocumentError
import shipping_document_admission as admission


async def fixture():
    db = AsyncMongoMockClient().admission
    for number in range(5):
        await db[COLLECTION].insert_one({"_id": str(number), "status": "verified",
            "expires_at": datetime.now(timezone.utc) + timedelta(seconds=300)})
    return db


@pytest.mark.asyncio
async def test_only_two_capabilities_in_flight_and_same_token_single_flight():
    db = await fixture()
    async with admission.document_read(db, {"_id": "0"}):
        with pytest.raises(DocumentError) as same:
            async with admission.document_read(db, {"_id": "0"}):
                pytest.fail("duplicate token admitted")
        assert same.value.status_code == 429
        await db[admission.SLOTS].update_many({"claim": None}, {"$set": {"available_at": admission.now()}})
        async with admission.document_read(db, {"_id": "1"}):
            with pytest.raises(DocumentError) as third:
                async with admission.document_read(db, {"_id": "2"}):
                    pytest.fail("third read admitted")
            assert third.value.status_code == 429
    assert (await db[COLLECTION].find_one({"_id": "0"}))["read_attempts"] == 1
    assert (await db[COLLECTION].find_one({"_id": "1"}))["read_attempts"] == 1


@pytest.mark.asyncio
async def test_cooldown_and_four_lifetime_attempts_fail_closed(monkeypatch):
    db = await fixture()
    instant = admission.now()
    monkeypatch.setattr(admission, "now", lambda: instant)
    for attempt in range(4):
        async with admission.document_read(db, {"_id": "0"}):
            pass
        with pytest.raises(DocumentError):
            async with admission.document_read(db, {"_id": "0"}):
                pytest.fail("cooldown ignored")
        instant += timedelta(seconds=6)
    with pytest.raises(DocumentError):
        async with admission.document_read(db, {"_id": "0"}):
            pytest.fail("fifth request admitted")
    assert (await db[COLLECTION].find_one({"_id": "0"}))["read_attempts"] == 4


@pytest.mark.asyncio
async def test_old_claim_cannot_release_successor_after_lease_loss():
    db = await fixture()
    with pytest.raises(DocumentError) as error:
        async with admission.document_read(db, {"_id": "0"}):
            await db[COLLECTION].update_one({"_id": "0"}, {"$set": {"read_claim": "successor"}})
            await db[admission.SLOTS].update_many({"claim": {"$ne": None}}, {"$set": {"claim": "successor"}})
    assert error.value.code == "shipping_document_read_lease_lost"
    assert (await db[COLLECTION].find_one({"_id": "0"}))["read_claim"] == "successor"
    assert await db[admission.SLOTS].count_documents({"claim": "successor"}) == 1


@pytest.mark.asyncio
async def test_crashed_lease_recovers_but_expired_document_does_not(monkeypatch):
    db = await fixture()
    instant = admission.now()
    await db[COLLECTION].update_one({"_id": "0"}, {"$set": {
        "read_claim": "dead", "read_available_at": instant + timedelta(seconds=45)}})
    with pytest.raises(DocumentError):
        async with admission.document_read(db, {"_id": "0"}):
            pytest.fail("live lease stolen")
    instant += timedelta(seconds=46)
    monkeypatch.setattr(admission, "now", lambda: instant)
    async with admission.document_read(db, {"_id": "0"}):
        pass
    instant += timedelta(seconds=301)
    with pytest.raises(DocumentError):
        async with admission.document_read(db, {"_id": "1"}):
            pytest.fail("expired token admitted")
