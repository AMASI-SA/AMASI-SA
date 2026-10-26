from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from integrations_control_center.google_commercial_plan import CommercialPlanInput, PLANS, review_context, save_commercial_plan

NOW = datetime(2026, 10, 26, 22, tzinfo=timezone.utc)  # 27 October in Riyadh
ACTOR = {"user_id": "owner", "actor_id": "owner"}


def body(**changes):
    return CommercialPlanInput.model_validate({"expected_revision": 0, "instruction": "ركز على هدايا الشتاء بالسعودية", "season": "هدايا الشتاء",
        "starts_on": "2026-11-01", "ends_on": "2026-12-31", "market": "SA", "audience": "رجال يبحثون عن هدية",
        "message": "هدية الشتاء", "category_url": "https://amasi-sa.com/shawls", "pinned_product_ids": ["123"],
        "acknowledge_review": True, **changes})


class Collection:
    def __init__(self): self.rows = []; self.race = False; self.indexes = []
    async def create_index(self, keys, **kwargs): self.indexes.append((keys, kwargs))
    async def find_one(self, query, projection=None):
        return next((deepcopy(row) for row in self.rows if all(row.get(key) == value for key, value in query.items())), None)
    async def insert_one(self, row):
        if self.race or any((r['user_id'], r['plan_id']) == (row['user_id'], row['plan_id']) for r in self.rows): raise DuplicateKeyError('race')
        self.rows.append(deepcopy(row))
    async def update_one(self, query, update):
        if self.race: return SimpleNamespace(matched_count=0)
        for row in self.rows:
            if all(row.get(key) == value for key, value in query.items()):
                row.update(deepcopy(update['$set']))
                row['reviews'] = (row['reviews'] + deepcopy(update['$push']['reviews']['$each']))[-50:]
                return SimpleNamespace(matched_count=1)
        return SimpleNamespace(matched_count=0)


class DB:
    def __init__(self): self.plans = Collection()
    def __getitem__(self, key):
        assert key == PLANS, 'commercial plans must not write authorization, catalog or campaign collections'
        return self.plans


def test_windows_are_closed_riyadh_days_and_missing_data_is_not_zero():
    result = review_context(body(calendar="hijri_season"), NOW)
    assert result['sales_last_14_days']['from'] == '2026-10-13'
    assert result['sales_last_14_days']['to'] == '2026-10-26'
    assert result['sales_previous_14_days']['from'] == '2026-09-29'
    assert result['sales_previous_14_days']['to'] == '2026-10-12'
    assert result['trend_last_90_days']['from'] == '2026-07-29'
    assert result['prior_season']['status'] == 'alignment_required'
    assert result['profitability'] is None and result['cost_quality'] == 'unknown'
    assert result['category_membership'] == result['merchant_offer_mapping'] == 'not_verified'
    assert result['cross_platform_results'] == []


@pytest.mark.parametrize('changes', [
    {'instruction': ' '}, {'starts_on': '2026-02-30'}, {'ends_on': '2026-01-01'}, {'acknowledge_review': False},
    {'pinned_product_ids': ['123', '123']}, {'excluded_product_ids': ['123']},
    {'category_url': 'http://amasi-sa.com/shawls'}, {'category_url': 'https://user:secret@amasi-sa.com/a'},
    {'limits': {'currency_code': 'SAR', 'daily_budget_native': '0'}},
    {'limits': {'currency_code': 'SAR', 'minimum_margin_percent': '101'}},
    {'selection_mode': 'review_category_candidates', 'category_url': None},
    {'execute': True},
])
def test_invalid_or_unreviewed_fields_rejected(changes):
    with pytest.raises(ValidationError): body(**changes)


@pytest.mark.asyncio
async def test_save_keeps_instruction_and_limits_without_granting_authorization():
    db = DB()
    result = await save_commercial_plan(db, ACTOR, 'winter', body(limits={'currency_code': 'SAR', 'daily_budget_native': '50'}), current=NOW)
    assert result['fields']['instruction'] == 'ركز على هدايا الشتاء بالسعودية'
    assert result['fields']['limits']['daily_budget_native'] == '50'
    assert result['revision'] == 1 and result['provider_writes'] == 0
    assert result['execution_authorized'] is False and result['status'] == 'reviewed_context_only'
    assert result['natural_language_extraction_available'] is False
    assert 'user_id' not in result and 'reviews' not in result
    assert len(db.plans.rows[0]['reviews']) == 1


@pytest.mark.asyncio
async def test_revision_and_audit_are_atomic_and_tenant_scoped():
    db = DB()
    await save_commercial_plan(db, ACTOR, 'winter', body(), current=NOW)
    with pytest.raises(HTTPException) as exc:
        await save_commercial_plan(db, ACTOR, 'winter', body(), current=NOW)
    assert exc.value.status_code == 409
    updated = await save_commercial_plan(db, ACTOR, 'winter', body(expected_revision=1, message='دفء الشتاء'), current=NOW)
    assert updated['revision'] == 2 and db.plans.rows[0]['reviews'][0]['fields']['message'] == 'هدية الشتاء'
    other = await save_commercial_plan(db, {'user_id': 'other', 'actor_id': 'other'}, 'winter', body(), current=NOW)
    assert other['revision'] == 1 and len(db.plans.rows) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('existing', [True, False])
async def test_concurrent_write_never_overwrites_revision(existing):
    db = DB()
    if existing: await save_commercial_plan(db, ACTOR, 'winter', body(), current=NOW)
    before = deepcopy(db.plans.rows)
    db.plans.race = True
    with pytest.raises(HTTPException) as exc:
        await save_commercial_plan(db, ACTOR, 'winter', body(expected_revision=int(existing)), current=NOW)
    assert exc.value.status_code == 409 and db.plans.rows == before


@pytest.mark.asyncio
async def test_routes_require_owner_and_never_use_foreign_plan(monkeypatch):
    import httpx
    from fastapi import APIRouter, FastAPI
    from integrations_control_center import google_commercial_plan as module
    db = DB()
    identity = {'user_id': 'owner', 'actor_id': 'owner', 'owner': True}
    async def current_user(): return {'id': identity['actor_id']}
    async def principal(*args, **kwargs): return dict(identity)
    monkeypatch.setattr(module, 'principal', principal)
    app, router = FastAPI(), APIRouter()
    module.attach_google_commercial_plan_routes(router, db, current_user)
    app.include_router(router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
        created = await client.put('/google_ads/commercial-plans/winter', json=body().model_dump())
        assert created.status_code == 200
        identity.update(user_id='other', actor_id='other')
        assert (await client.get('/google_ads/commercial-plans/winter')).status_code == 404
        identity.update(user_id='owner', actor_id='staff', owner=False)
        assert (await client.get('/google_ads/commercial-plans/winter')).status_code == 403
        assert (await client.put('/google_ads/commercial-plans/winter', json=body().model_dump())).status_code == 403
        identity.update(user_id='owner', actor_id='owner', owner=True)
        fetched = await client.get('/google_ads/commercial-plans/winter')
        assert fetched.status_code == 200 and fetched.json()['revision'] == 1
        assert (await client.put('/google_ads/commercial-plans/invalid.id', json=body().model_dump())).status_code == 422
