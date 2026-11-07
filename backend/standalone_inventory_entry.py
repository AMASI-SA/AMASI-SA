"""Repeated owner stock counts, isolated from fulfillment and financial writers.

Each command records one physical receipt with pending valuation. The independent
admission switch never changes the existing physical/financial writer switches.
"""
from pydantic import BaseModel, ConfigDict, Field
from operational_balance_store import digest, fail, now
from operational_opening_inventory import (
    OpeningRequest, OpeningApproval, owner_actor, compile_preview, place_compiled,
    SCHEMA, validate_insert as opening_insert, validate_update as opening_update,
)
from operational_purchase_metadata import products, locations
from operational_opening_inventory import components
from inventory_write_gate import admitted, read_control, root_db, standalone_writes_paused
from operational_atomic import operational_owner

SOURCE = 'standalone_inventory_entry'
ENTRIES = 'mz2_standalone_inventory_entries_v1'


class EntryRequest(OpeningRequest):
    # One save is one actual stock item. Color/configuration stays in its identity.
    lines: list = Field(min_length=1, max_length=1)


class EntryControl(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=0, strict=True)
    paused: bool = Field(strict=True)


def request_body(payload, approval=False):
    request = (OpeningApproval if approval else OpeningRequest).model_validate(payload).model_dump()
    if len(request['lines']) != 1:
        fail('standalone_inventory_single_line', 'احفظ منتجًا واحدًا في كل مرة', 422)
    return request


async def context(db, user):
    actor = await owner_actor(db, user); owner = actor['id']
    warehouses = await db['warehouse_locations_warehouses'].find(
        {'user_id': owner, 'inventory_scope': 'standalone', 'status': 'active'}, {'_id': 0}).to_list(20001)
    if len(warehouses) > 20000:
        fail('inventory_locations_incomplete', 'تعذر قراءة المخازن كاملة', 409)
    ids = {w['id'] for w in warehouses}
    control = await read_control(root_db(db), owner) or {}
    entries = await db[ENTRIES].find({'user_id': owner}, {'_id': 0}).sort('recorded_at', -1).limit(100).to_list(100)
    return {'products': await products(db, owner, opening_cached_options=True),
            'components': await components(db, owner),
            'locations': [l for l in await locations(db, owner) if l['warehouse_id'] in ids],
            'writer_disabled': standalone_writes_paused(control),
            'control_revision': control.get('standalone_inventory_control_revision', 0),
            'entries': [{'id': e['id'], 'recorded_at': e['recorded_at'], **e['result']} for e in entries],
            'financial_effect': 'none', 'fulfillment_eligible': False}


async def set_control(db, user, payload):
    from accounting_atomic import _owner_transaction
    command = EntryControl.model_validate(payload)
    actor = await owner_actor(db, user); owner = actor['id']
    async def commit(scoped):
        await owner_actor(scoped, user, owner)
        state = await scoped.mz2_atomic_owners.find_one({'_id': owner}) or {}
        if state.get('standalone_inventory_control_revision', 0) != command.revision:
            fail('standalone_inventory_control_changed', 'تغير إعداد المخزون؛ أعد تحميل الصفحة', 409)
        await scoped.mz2_atomic_owners.update_one({'_id': owner}, {'$set': {
            'standalone_inventory_writes_paused': command.paused,
            'standalone_inventory_control_revision': command.revision + 1}})
        await scoped.mz2_inventory_control_audit.insert_one({
            'id': digest([SOURCE, owner, 'control', command.revision + 1]), 'user_id': owner,
            'actor_id': owner, 'scope': SOURCE, 'paused': command.paused,
            'revision': command.revision + 1, 'at': now()})
        return {'paused': command.paused, 'revision': command.revision + 1}
    return await _owner_transaction(root_db(db), owner, commit, control=True)


async def preview(db, user, payload):
    request = request_body(payload)
    actor = await owner_actor(db, user)
    result = await compile_preview(db, actor['id'], request, standalone=True)
    result['writer_disabled'] = standalone_writes_paused(await read_control(root_db(db), actor['id']))
    return result


async def save(db, user, payload):
    request = request_body(payload, approval=True)
    expected = request.pop('preview_hash')
    actor = await owner_actor(db, user); owner = actor['id']
    entry_id = digest([SOURCE, owner, request['request_id']]); fingerprint = digest(request)
    async def commit(scoped):
        await owner_actor(scoped, user, owner)
        previous = await scoped[ENTRIES].find_one({'_id': entry_id, 'user_id': owner})
        if previous:
            if previous['request_hash'] != fingerprint or previous['preview_hash'] != expected:
                fail('standalone_inventory_request_conflict', 'مرجع الحفظ مستخدم لبيانات مختلفة', 409)
            return previous['result']
        compiled = await compile_preview(scoped, owner, request, standalone=True)
        if compiled['preview_hash'] != expected:
            fail('opening_inventory_preview_changed', 'تغير المنتج أو الموقع؛ أعد تحميل بيانات الحفظ', 409)
        timestamp = now()
        result = {**compiled, 'status': 'approved', 'batch_id': entry_id}
        await scoped[ENTRIES].insert_one({'_id': entry_id, 'id': entry_id, 'user_id': owner,
            'source_type': SOURCE, 'schema_version': SCHEMA, 'request_hash': fingerprint,
            'preview_hash': expected, 'result': result, 'financial_effect': 'none',
            'recorded_by': owner, 'recorded_at': timestamp})
        await place_compiled(scoped, owner, compiled, timestamp, entry_id, source=SOURCE)
        return result
    return await admitted(db, owner, SOURCE,
        lambda: operational_owner(db, owner, commit, profile='standalone_inventory'), standalone=True)


def validate_insert(name, doc):
    return opening_insert(name, doc, source=SOURCE, batches=ENTRIES)


async def validate_update(db, owner, name, query, update):
    if name == 'warehouse_locations':
        loc = await db[name].find_one({'user_id': owner, 'id': query.get('id')})
        warehouse = await db['warehouse_locations_warehouses'].find_one(
            {'user_id': owner, 'id': (loc or {}).get('warehouse_id'), 'inventory_scope': 'standalone', 'status': 'active'})
        if not warehouse:
            return False
    return await opening_update(db, owner, name, query, update, source=SOURCE)
