from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from pymongo.errors import DuplicateKeyError
from integrations_control_center.google_oauth_operation import (register_refresh_operation,claim_refresh_operation,
    inspect_refresh_operation,OperationAlreadyExists,OperationNotClaimed,OperationInvalid,RetryWritesEnabled)

NOW=datetime(2026,10,26,tzinfo=timezone.utc)
class Store:
    database=SimpleNamespace(client=SimpleNamespace(options=SimpleNamespace(retry_writes=False)))
    def __init__(self): self.rows={};self.options=[]
    def with_options(self,**kw): self.options.append(kw);return self
    async def insert_one(self,doc):
        if doc['_id'] in self.rows: raise DuplicateKeyError('duplicate')
        self.rows[doc['_id']]=doc.copy()
    async def update_one(self,q,u,upsert=False):
        row=self.rows.get(q['_id'])
        ok=bool(row and all(row.get(k)==v for k,v in q.items()))
        if ok: row.update(u['$set'])
        return SimpleNamespace(matched_count=int(ok))
    async def find_one(self,q,projection):
        row=self.rows.get(q['_id']);return row if row and all(row.get(k)==v for k,v in q.items()) else None

def scope():return dict(user_id='u',grant_generation='g',token_revision=0,operation_id=str(uuid4()))

@pytest.mark.asyncio
async def test_duplicate_registration_and_claim_never_authorizes_reuse():
    c=Store();s=scope();ticket=await register_refresh_operation(c,**s,now=NOW)
    with pytest.raises(OperationAlreadyExists):await register_refresh_operation(c,**s,now=NOW)
    await claim_refresh_operation(c,**s,attempt_id=str(uuid4()),now=NOW,ticket=ticket)
    with pytest.raises(OperationNotClaimed):await claim_refresh_operation(c,**s,attempt_id=str(uuid4()),now=NOW,ticket=ticket)
    assert await inspect_refresh_operation(c,**s)=='claimed'

@pytest.mark.asyncio
async def test_relink_scope_cannot_reuse_operation():
    c=Store();s=scope();ticket=await register_refresh_operation(c,**s,now=NOW)
    with pytest.raises(OperationNotClaimed):await claim_refresh_operation(c,**{**s,'grant_generation':'new'},attempt_id=str(uuid4()),now=NOW,ticket=ticket)
    with pytest.raises(OperationAlreadyExists):await register_refresh_operation(c,**{**s,'grant_generation':'new'},now=NOW)

@pytest.mark.asyncio
async def test_unknown_claim_ack_propagates_and_no_retry():
    class Lost(Store):
        calls=0
        async def update_one(self,*a,**kw):
            self.calls+=1;await super().update_one(*a,**kw);raise TimeoutError('ack unknown')
    c=Lost();s=scope();ticket=await register_refresh_operation(c,**s,now=NOW)
    with pytest.raises(TimeoutError):await claim_refresh_operation(c,**s,attempt_id=str(uuid4()),now=NOW,ticket=ticket)
    assert c.calls==1 and await inspect_refresh_operation(c,**s)=='claimed'
    # Inspecting claimed never grants authority after unknown ACK.

@pytest.mark.asyncio
async def test_missing_or_bad_identity_and_enabled_retry_fail_closed():
    c=Store();s=scope()
    with pytest.raises(OperationInvalid):await register_refresh_operation(c,**{**s,'operation_id':'not-uuid'},now=NOW)
    c.database=SimpleNamespace(client=SimpleNamespace(options=SimpleNamespace(retry_writes=True)))
    with pytest.raises(RetryWritesEnabled):await register_refresh_operation(c,**s,now=NOW)
    assert not c.rows

@pytest.mark.asyncio
async def test_unknown_registration_ack_cannot_mint_ticket():
    class Lost(Store):
        calls=0
        async def insert_one(self,doc):
            self.calls+=1;await super().insert_one(doc);raise TimeoutError('lost ack')
    c=Lost();s=scope()
    with pytest.raises(TimeoutError):await register_refresh_operation(c,**s,now=NOW)
    assert c.calls == 1 and await inspect_refresh_operation(c,**s) == 'registered'
    with pytest.raises(OperationNotClaimed):
        await claim_refresh_operation(c,**s,attempt_id=str(uuid4()),now=NOW,ticket=None)
