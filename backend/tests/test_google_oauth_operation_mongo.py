"""Disposable real Mongo replica tests, not production credential access."""
import asyncio
from copy import copy
import os
from uuid import uuid4
from datetime import datetime,timezone
from urllib.parse import urlsplit
import pytest
import pytest_asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from integrations_control_center.google_oauth_operation import (register_refresh_operation,claim_refresh_operation,
    inspect_refresh_operation,OperationNotClaimed,OperationAlreadyExists)
NOW=datetime(2026,10,26,tzinfo=timezone.utc)

@pytest_asyncio.fixture
async def operations():
    uri=os.getenv('GOOGLE_ADS_TEST_MONGO_URI')
    if not uri:pytest.skip('disposable loopback Mongo replica required')
    u=urlsplit(uri)
    if (u.scheme!='mongodb' or u.hostname not in ('localhost','127.0.0.1') or u.port is None or u.username or u.password
        or ',' in u.netloc or u.path not in ('','/') or u.query or u.fragment):raise ValueError('loopback only')
    c=AsyncIOMotorClient(uri,serverSelectionTimeoutMS=2000,retryWrites=False);db=c['goauth_ops_'+uuid4().hex]
    try:
        h=await db.command('hello');assert h.get('setName') and h.get('isWritablePrimary') and type(h.get('logicalSessionTimeoutMinutes')) is int
        yield db.operations
    finally:
        await c.drop_database(db.name);c.close()

def scope():return dict(user_id='u',grant_generation='old',token_revision=0,operation_id=str(uuid4()))

@pytest.mark.asyncio
async def test_racing_claims_only_one_receives_ack(operations):
    s=scope();ticket=await register_refresh_operation(operations,**s,now=NOW)
    async def claim():
        try:await claim_refresh_operation(operations,**s,attempt_id=str(uuid4()),now=NOW,ticket=copy(ticket));return 'claimed'
        except OperationNotClaimed:return 'rejected'
    assert sorted(await asyncio.gather(claim(),claim()))==['claimed','rejected']

@pytest.mark.asyncio
async def test_operation_id_cannot_be_reregistered_after_relink(operations):
    s=scope();ticket=await register_refresh_operation(operations,**s,now=NOW)
    with pytest.raises(OperationAlreadyExists):await register_refresh_operation(operations,**{**s,'grant_generation':'new'},now=NOW)
    with pytest.raises(OperationNotClaimed):await claim_refresh_operation(operations,**{**s,'grant_generation':'new'},attempt_id=str(uuid4()),now=NOW,ticket=ticket)

@pytest.mark.asyncio
async def test_lost_registration_ack_never_retries_or_accepts_duplicate(operations):
    class Lost:
        database=operations.database
        calls=0
        def with_options(self,**kw):self.target=operations.with_options(**kw);return self
        async def insert_one(self,doc):
            self.calls+=1;await self.target.insert_one(doc);raise TimeoutError('lost ack')
    w=Lost();s=scope()
    with pytest.raises(TimeoutError):await register_refresh_operation(w,**s,now=NOW)
    assert w.calls==1 and await inspect_refresh_operation(operations,**s)=='registered'
    with pytest.raises(OperationAlreadyExists):await register_refresh_operation(operations,**s,now=NOW)

@pytest.mark.asyncio
async def test_lost_claim_ack_does_not_grant_permission_or_retry(operations):
    class Lost:
        database=operations.database
        calls=0
        def with_options(self,**kw):self.target=operations.with_options(**kw);return self
        async def update_one(self,*a,**kw):
            self.calls+=1;await self.target.update_one(*a,**kw);raise TimeoutError('lost ack')
    w=Lost();s=scope();ticket=await register_refresh_operation(operations,**s,now=NOW)
    with pytest.raises(TimeoutError):await claim_refresh_operation(w,**s,attempt_id=str(uuid4()),now=NOW,ticket=ticket)
    assert w.calls==1 and await inspect_refresh_operation(operations,**s)=='claimed'
    with pytest.raises(OperationNotClaimed):await claim_refresh_operation(operations,**s,attempt_id=str(uuid4()),now=NOW,ticket=ticket)
