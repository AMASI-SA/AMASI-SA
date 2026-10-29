"""Prove the unit collaborator does not leak uncommitted writes."""
import pytest

from tests.google_ads_transactional_fake import TransactionalDBMixin, BasicCollection


class DB(TransactionalDBMixin):
    def __init__(self):self._tx_setup({'policy':BasicCollection(),'guard':BasicCollection()})
    def __getitem__(self,name):return self._tx_get(name)


@pytest.mark.asyncio
async def test_session_snapshot_isolation_and_abort_discard_both_collections():
    db=DB()
    async with await db.start_session() as session:
        session.start_transaction()
        await db['policy'].insert_one({'revision':1},session=session)
        await db['guard'].insert_one({'epoch':1},session=session)
        assert await db['policy'].find_one({}) is None
        assert await db['guard'].find_one({}) is None
        assert (await db['policy'].find_one({},session=session))['revision']==1
        await session.abort_transaction()
    assert db['policy'].rows == db['guard'].rows == []


@pytest.mark.asyncio
async def test_explicit_commit_publishes_both_snapshots_once():
    db=DB()
    async with await db.start_session() as session:
        session.start_transaction()
        await db['policy'].insert_one({'revision':1},session=session)
        await db['guard'].insert_one({'epoch':1},session=session)
        await session.commit_transaction()
        assert session.commit_calls==1
    assert db['policy'].rows==[{'revision':1}]
    assert db['guard'].rows==[{'epoch':1}]


@pytest.mark.asyncio
async def test_commit_ack_loss_preserves_committed_both_collections():
    db=DB()
    async def lose_ack():raise ConnectionError('unit injected after commit')
    db.after_commit=lose_ack
    with pytest.raises(ConnectionError):
        async with await db.start_session() as session:
            session.start_transaction()
            await db['policy'].insert_one({'revision':1},session=session)
            await db['guard'].insert_one({'epoch':1},session=session)
            await session.commit_transaction()
    assert db['policy'].rows==[{'revision':1}]
    assert db['guard'].rows==[{'epoch':1}]
