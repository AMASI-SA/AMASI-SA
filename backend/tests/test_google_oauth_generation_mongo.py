"""Disposable Mongo replica evidence; no credentials or provider access."""
import asyncio
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from integrations_control_center.google_oauth_generation import commit_refreshed_credential, RefreshConflict

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)

@pytest_asyncio.fixture
async def credentials():
    uri = os.getenv('GOOGLE_ADS_TEST_MONGO_URI')
    if not uri:
        pytest.skip('disposable loopback Mongo replica required')
    parsed = urlsplit(uri)
    if (parsed.scheme != 'mongodb' or parsed.hostname not in {'127.0.0.1', 'localhost'}
        or parsed.port is None or parsed.username or parsed.password or ',' in parsed.netloc
        or parsed.path not in ('', '/') or parsed.query or parsed.fragment):
        raise ValueError('only disposable loopback Mongo is accepted')
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
    db = client['goauth_refresh_' + uuid.uuid4().hex]
    try:
        hello = await db.command('hello')
        assert hello.get('setName') and hello.get('isWritablePrimary') and type(hello.get('logicalSessionTimeoutMinutes')) is int
        c = db.credentials
        await c.create_index('user_id', unique=True)
        await c.insert_one({'user_id':'owner','grant_generation':'old','token_revision':2,'access_token_ciphertext':'old-encrypted'})
        yield c
    finally:
        await client.drop_database(db.name)
        client.close()

async def refresh(collection):
    return await commit_refreshed_credential(collection,user_id='owner',grant_generation='old',token_revision=2,
        ciphertext='refreshed-encrypted', expires_at=NOW,updated_at=NOW, operation_id='op_0123456789abcdef')

@pytest.mark.asyncio
async def test_relink_wins_before_old_refresh_cas(credentials):
    entered, release = asyncio.Event(), asyncio.Event()
    class Gate:
        async def update_one(self, *a, **kw):
            entered.set()
            await release.wait()
            return await credentials.update_one(*a, **kw)
    task = asyncio.create_task(refresh(Gate()))
    await asyncio.wait_for(entered.wait(), 2)
    await credentials.update_one({'user_id':'owner','grant_generation':'old'}, {'$set':{'grant_generation':'new','token_revision':0,'access_token_ciphertext':'new-encrypted'}})
    release.set()
    with pytest.raises(RefreshConflict): await task
    row = await credentials.find_one({'user_id':'owner'})
    assert (row['grant_generation'],row['token_revision'],row['access_token_ciphertext']) == ('new',0,'new-encrypted')

@pytest.mark.asyncio
async def test_old_refresh_then_relink_preserves_new_grant(credentials):
    assert await refresh(credentials) == 3
    await credentials.update_one({'user_id':'owner'}, {'$set':{'grant_generation':'new','token_revision':0,'access_token_ciphertext':'new-encrypted'}})
    row = await credentials.find_one({'user_id':'owner'})
    assert (row['grant_generation'],row['token_revision'],row['access_token_ciphertext']) == ('new',0,'new-encrypted')

@pytest.mark.asyncio
async def test_lost_write_ack_propagates_and_does_not_replay(credentials):
    class LostAck:
        calls = 0
        async def update_one(self, *a, **kw):
            self.calls += 1
            await credentials.update_one(*a, **kw)
            raise TimeoutError('simulated lost acknowledgment after Mongo write')
    wrapper = LostAck()
    with pytest.raises(TimeoutError): await refresh(wrapper)
    assert wrapper.calls == 1
    row = await credentials.find_one({'user_id':'owner'})
    assert row['token_revision'] == 3 and row['access_token_ciphertext'] == 'refreshed-encrypted'
    # This read proves what the test injected, not a general recovery protocol.
    # A competing writer could also produce revision 3; callers need an operation
    # identity before treating unknown acknowledgement as their own success.

@pytest.mark.asyncio
async def test_concurrent_refresh_same_revision_only_winner_has_positive_proof(credentials):
    from integrations_control_center.google_oauth_generation import resolve_refreshed_credential
    async def attempt(op):
        try:
            await commit_refreshed_credential(credentials,user_id='owner',grant_generation='old',token_revision=2,
                ciphertext='encrypted',expires_at=NOW,updated_at=NOW,operation_id=op)
            return 'committed'
        except RefreshConflict:
            return 'conflict'
    ops=['op_aaaaaaaaaaaaaaaa','op_bbbbbbbbbbbbbbbb']
    results=await asyncio.gather(*(attempt(op) for op in ops))
    assert sorted(results) == ['committed','conflict']
    proofs = [await resolve_refreshed_credential(credentials,user_id='owner',grant_generation='old',token_revision=2,operation_id=op) for op in ops]
    assert proofs == ['committed' if result == 'committed' else 'unknown' for result in results]

@pytest.mark.asyncio
async def test_read_only_resolution_of_lost_ack_and_later_relink(credentials):
    from integrations_control_center.google_oauth_generation import resolve_refreshed_credential
    class LostAck:
        calls=0
        async def update_one(self,*a,**kw):
            self.calls+=1
            await credentials.update_one(*a,**kw)
            raise TimeoutError('lost acknowledgment')
    wrapper=LostAck()
    with pytest.raises(TimeoutError): await refresh(wrapper)
    async def resolve():
        return await resolve_refreshed_credential(credentials,user_id='owner',grant_generation='old',token_revision=2,operation_id='op_0123456789abcdef')
    assert await resolve() == 'committed' and wrapper.calls == 1
    await credentials.update_one({'user_id':'owner'},{'$set':{'grant_generation':'new','token_revision':0}})
    assert await resolve() == 'unknown' and wrapper.calls == 1
