from datetime import datetime, timezone

import pytest
from integrations_control_center.google_oauth_generation import commit_refreshed_credential, RefreshConflict, InvalidGeneration

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc)

class Collection:
    def __init__(self, row): self.row = row
    async def update_one(self, query, update, *, upsert):
        assert upsert is False
        if not all(self.row.get(k) == v for k, v in query.items()):
            return type('Result', (), {'matched_count': 0})()
        self.row.update(update['$set'])
        return type('Result', (), {'matched_count': 1})()

@pytest.mark.asyncio
async def test_refresh_commits_exact_generation_and_revision():
    c=Collection({'user_id':'u','grant_generation':'grant1','token_revision':3})
    assert await commit_refreshed_credential(c, user_id='u', grant_generation='grant1', token_revision=3, ciphertext='encrypted', expires_at=NOW, updated_at=NOW) == 4
    assert c.row['token_revision'] == 4 and c.row['access_token_ciphertext'] == 'encrypted'

@pytest.mark.asyncio
@pytest.mark.parametrize('row', [ {'user_id':'u','grant_generation':'grant2','token_revision':3}, {'user_id':'u','grant_generation':'grant1','token_revision':4}, {}])
async def test_stale_refresh_cannot_overwrite_new_grant_or_revision(row):
    c=Collection(row.copy())
    with pytest.raises(RefreshConflict):
        await commit_refreshed_credential(c, user_id='u', grant_generation='grant1', token_revision=3, ciphertext='encrypted', expires_at=NOW, updated_at=NOW)
    assert c.row == row

@pytest.mark.asyncio
@pytest.mark.parametrize('gen,revision', [(None,0),('',0),('g',True),('g',-1),('g',2**63-1)])
async def test_missing_legacy_or_invalid_fences_fail_closed(gen, revision):
    with pytest.raises(InvalidGeneration):
        await commit_refreshed_credential(Collection({}),user_id='u',grant_generation=gen,token_revision=revision,ciphertext='encrypted',expires_at=NOW,updated_at=NOW)

@pytest.mark.asyncio
async def test_ambiguous_commit_error_propagates_without_retry():
    class Ambiguous:
        calls=0
        async def update_one(self,*args,**kwargs):
            self.calls+=1
            raise TimeoutError('unknown')
    c=Ambiguous()
    with pytest.raises(TimeoutError):
        await commit_refreshed_credential(c,user_id='u',grant_generation='g',token_revision=0,ciphertext='encrypted',expires_at=NOW,updated_at=NOW)
    assert c.calls == 1
