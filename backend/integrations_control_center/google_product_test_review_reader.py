"""Trusted source orchestration with bounded process admission and retained cleanup."""
import asyncio
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from bson import BSON
from .google_product_season_review import capture_scope, verify_scope, read_reviews, current_owner_reviews, canonical
from .google_native_inventory_reader import selected_inventory_preview
from .google_live_category_reader import PUBLIC_INTEGRATION
from .google_live_category_proof import canonical_id as native_id
from .google_plan_store_observations import read_store_observations
from .google_prior_season_reader import read_prior_season_observations
from .google_linked_campaign_reader import linked_campaign_evidence
from .google_product_test_review import build_product_test_review

class ReviewAdmissionUnavailable(ValueError):pass
class ReviewAdmission:
    """Process-local caps; timed out tasks retain their slot until truly done.

    We bound response waiting, not time spent by cancellation-resistant cleanup.
    No cross-worker singleflight or hard transport/peak-memory guarantee.
    """
    def __init__(self,maximum=4):self.maximum=maximum;self.active={}
    def _done(self,key,task):
        if self.active.get(key) is task:self.active.pop(key)
        if not task.cancelled():task.exception()
    async def run(self,key,factory,*,timeout=45):
        for previous,task in list(self.active.items()):
            if task.done():self._done(previous,task)
        if key in self.active:raise ReviewAdmissionUnavailable('owner_operation_in_progress')
        if len(self.active)>=self.maximum:raise ReviewAdmissionUnavailable('process_resource_limit')
        task=asyncio.create_task(factory());self.active[key]=task
        task.add_done_callback(lambda original:self._done(key,original))
        try:
            done,_=await asyncio.wait({task},timeout=timeout)
            if not done:
                task.cancel()
                raise ReviewAdmissionUnavailable('response_deadline_cleanup_may_continue')
            return task.result()
        except asyncio.CancelledError:
            task.cancel()
            raise
        finally:
            if task.done():self._done(key,task)

def _aware(value):
    if not isinstance(value,datetime) or value.tzinfo is None or value.utcoffset() is None:raise ValueError('aware operation clock required')
    return value

def _instant(value):return _aware(datetime.fromisoformat(value.replace('Z','+00:00')))

def _rows_snapshot(rows):return BSON.encode({'rows':[rows[p] for p in sorted(rows)]})

async def review_selected_product_tests(db,*,authenticated_user,plan_id,expected_revision,category_id,selected_ids,caller,clock=lambda:datetime.now(timezone.utc)):
    category=canonical(category_id)
    if not isinstance(selected_ids,list) or not 1<=len(selected_ids)<=5:raise ValueError('selected product bound')
    selected_ids=[canonical(p) for p in selected_ids]
    if len(set(selected_ids))!=len(selected_ids):raise ValueError('duplicate selected product')
    as_of=_aware(clock());started=as_of
    scope=await capture_scope(db,authenticated_user,plan_id,expected_revision)
    fields=scope['fields']
    if set(selected_ids)&set(fields.excluded_product_ids):raise ValueError('excluded selected product')
    reviews=await read_reviews(db,scope,category,selected_ids)
    connection=await db.salla_integrations.find_one({'user_id':scope['owner_id']},PUBLIC_INTEGRATION)
    if not connection:raise ValueError('owned connection unproved')
    native=await selected_inventory_preview(db,owner_id=scope['owner_id'],fields=fields.model_dump(),category_id=category,selected_ids=selected_ids,caller=caller)
    if native.get('category_id')!=category or native.get('store_id')!=native_id(connection.get('store_id')):raise ValueError('native scope changed')
    current=await read_store_observations(db,owner_id=scope['owner_id'],product_ids=selected_ids,as_of=as_of)
    prior=None
    if fields.prior_season_context is not None:
        prior_context=fields.prior_season_context
        stamp=_instant(scope['plan'].get('updated_at'))
        if (scope['plan'].get('updated_by')!=scope['owner_id'] or stamp>as_of
            or date.fromisoformat(prior_context.date_to)>=as_of.astimezone(ZoneInfo('Asia/Riyadh')).date()):raise ValueError('prior owner provenance or closed day unproved')
        prior=await read_prior_season_observations(db,owner_id=scope['owner_id'],product_ids=selected_ids,as_of=as_of,date_from=prior_context.date_from,date_to=prior_context.date_to)
    linked=await linked_campaign_evidence(db,owner_id=scope['owner_id'],product_ids=selected_ids,as_of=as_of)
    await verify_scope(db,authenticated_user,scope)
    latest_reviews=await read_reviews(db,scope,category,selected_ids)
    if _rows_snapshot(latest_reviews)!=_rows_snapshot(reviews):raise ValueError('owner product review changed')
    latest_connection=await db.salla_integrations.find_one({'user_id':scope['owner_id']},PUBLIC_INTEGRATION)
    if BSON.encode(latest_connection or {})!=BSON.encode(connection):raise ValueError('owned connection changed')
    finished=_aware(clock())
    if not started<=_instant(native['observed_started_at'])<=_instant(native['observed_finished_at'])<=finished:raise ValueError('native operation timing unproved')
    result=build_product_test_review(plan_id=plan_id,plan_revision=expected_revision,category_id=category,selected_ids=selected_ids,
        frozen_as_of=as_of,owner_reviews=current_owner_reviews(reviews,scope,as_of),inventory=native,current=current,prior=prior,linked=linked)
    result.update(operation_started_at=started.isoformat(),operation_finished_at=finished.isoformat(),native_observed_started_at=native['observed_started_at'],
        native_observed_finished_at=native['observed_finished_at'],category_response_digest=native['category_response_digest'],store_id=native['store_id'],
        current_source_limitations=current.get('limitations',[]),prior_source_limitations=prior.get('limitations',[]) if prior else [],
        linked_source_limitations=linked.get('limitations',[]),admission_scope='process_local_not_cross_worker_singleflight',
        cleanup_deadline_guaranteed=False,remote_snapshot_atomic=False)
    return result
