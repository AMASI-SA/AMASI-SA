"""Real Mongo engine constraints BEFORE implementing namespace bootstrap.

Only a disposable authenticated loopback replica is accepted. This file does not
import a production bootstrap module or make any production/provider request.
"""
import asyncio
from contextlib import asynccontextmanager
import os
from urllib.parse import urlsplit
import uuid

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReadPreference
from pymongo.errors import DuplicateKeyError, OperationFailure
from pymongo.monitoring import CommandListener
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern
import pytest


NAMES = (
    'mezan_google_ads_automation_policies_v1',
    'mezan_google_ads_automation_creation_policies_v1',
    'mezan_google_ads_automation_budget_guards_v1',
    'google_ads_budget_guard_engine_probes_v1',
)
INDEX_NAMES = ('google_automation_tenant_account', 'google_creation_tenant_account')
RECEIPTS = 'google_storage_commit_probe_receipts_v1'
SLOT = 'google_namespace_bootstrap_v1'
WC = {'w': 'majority', 'j': True, 'wtimeout': 3000}


class Capture(CommandListener):
    def __init__(self): self.commands = []
    def started(self, event):
        if event.command_name in ('create', 'createIndexes', 'commitTransaction', 'abortTransaction'):
            self.commands.append((event.command_name, dict(event.command)))
    def succeeded(self, event): pass
    def failed(self, event): pass


@asynccontextmanager
async def fixture(*, missing=None):
    uri = os.environ.get('GOOGLE_NAMESPACE_BOOTSTRAP_TEST_ADMIN_URI')
    assert uri, 'dedicated disposable actual-Mongo URI required; no skip/fallback'
    parsed = urlsplit(uri)
    assert parsed.scheme == 'mongodb' and parsed.hostname == '127.0.0.1' and parsed.port == 27483
    root = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=2000)
    raw = root['g_namespace_bootstrap_' + uuid.uuid4().hex]
    client = None
    try:
        hello = await root.admin.command('hello')
        assert hello.get('setName') == 'namespaceBootstrap' and hello.get('isWritablePrimary') is True
        assert not hello.get('msg')
        await raw.create_collection(RECEIPTS)
        foreign = {'_id': 'owner_existing_diagnostic', 'state': 'completed', 'untouched': True}
        await raw[RECEIPTS].insert_one(foreign)
        privileges = [{'resource': {'db': raw.name, 'collection': ''},
                       'actions': ['listCollections', 'listIndexes']}]
        for i, name in enumerate(NAMES):
            actions = ['find', 'insert'] + (['createIndex'] if i < 2 else [])
            if missing in actions and i == 0: actions.remove(missing)
            privileges.append({'resource': {'db': raw.name, 'collection': name}, 'actions': actions})
        privileges.append({'resource': {'db': raw.name, 'collection': RECEIPTS},
                           'actions': ['find', 'insert', 'update']})
        await raw.command({'createRole': 'bounded_bootstrap', 'privileges': privileges, 'roles': []})
        await raw.command({'createUser': 'bootstrap_fixture', 'pwd': 'ephemeral_fixture_only',
                           'roles': [{'role': 'bounded_bootstrap', 'db': raw.name}]})
        capture = Capture()
        client = AsyncIOMotorClient('mongodb://127.0.0.1:27483/', username='bootstrap_fixture',
            password='ephemeral_fixture_only', authSource=raw.name, serverSelectionTimeoutMS=2000,
            retryWrites=False, event_listeners=[capture])
        yield root, raw, client, client[raw.name], capture, foreign
    finally:
        if client is not None: client.close()
        await root.drop_database(raw.name)
        root.close()


def index_command(i):
    return {'createIndexes': NAMES[i], 'indexes': [{'key': {'user_id': 1, 'account_id': 1},
        'unique': True, 'name': INDEX_NAMES[i]}]}


def begin(session, level='local'):
    session.start_transaction(read_concern=ReadConcern(level),
        write_concern=WriteConcern('majority', j=True), read_preference=ReadPreference.PRIMARY,
        max_commit_time_ms=3000)


async def raw_commit(client, session):
    reply = await client.admin.command({'commitTransaction': 1, 'writeConcern': WC,
                                        'maxTimeMS': 3000}, session=session)
    assert reply.get('ok') == 1 and 'writeConcernError' not in reply


async def target_metadata(raw):
    cursor = await raw.list_collections(filter={'name': {'$in': list(NAMES)}})
    rows = await cursor.to_list(length=5)
    return {row['name']: row for row in rows}


@pytest.mark.asyncio
async def test_real_local_transaction_creates_all_four_with_required_restricted_privileges():
    async with fixture() as (_, raw, client, db, capture, foreign):
        grants = (await db.command({'connectionStatus': 1, 'showPrivileges': True}))['authInfo']['authenticatedUserPrivileges']
        assert not any('createCollection' in g['actions'] or 'anyAction' in g['actions'] for g in grants)
        reservation = {'_id': SLOT, 'operation': 'namespace_bootstrap', 'attempt': 'one', 'state': 'reserved'}
        await db[RECEIPTS].with_options(write_concern=WriteConcern('majority', j=True)).insert_one(reservation)
        session = await client.start_session()
        try:
            begin(session)
            assert await db[RECEIPTS].find_one(reservation, session=session) == reservation
            for name in NAMES:
                assert (await db.command({'create': name}, session=session))['ok'] == 1
            for i in range(2):
                assert (await db.command(index_command(i), session=session))['ok'] == 1
            changed = await db[RECEIPTS].update_one(reservation, {'$set': {'state': 'applied'}}, session=session)
            assert changed.modified_count == 1
            await raw_commit(client, session)
        finally: await session.end_session()
        majority = db[RECEIPTS].with_options(read_concern=ReadConcern('majority'))
        assert await majority.find_one({'_id': SLOT}) == {**reservation, 'state': 'applied'}
        assert await raw[RECEIPTS].find_one({'_id': foreign['_id']}) == foreign
        metadata = await target_metadata(raw)
        assert set(metadata) == set(NAMES)
        for i, name in enumerate(NAMES):
            assert metadata[name]['type'] == 'collection' and metadata[name]['options'] == {}
            assert metadata[name]['info']['uuid']
            assert await raw[name].find_one({}) is None
            indexes = await raw[name].list_indexes().to_list(length=4)
            assert {r['name'] for r in indexes} == ({'_id_', INDEX_NAMES[i]} if i < 2 else {'_id_'})
            if i < 2:
                unique = next(r for r in indexes if r['name'] == INDEX_NAMES[i])
                assert list(unique['key'].items()) == [('user_id', 1), ('account_id', 1)] and unique['unique'] is True
        ddls = [c for name, c in capture.commands if name in ('create', 'createIndexes')]
        assert len(ddls) == 6 and all(c.get('autocommit') is False and 'txnNumber' in c for c in ddls)
        commits = [c for name, c in capture.commands if name == 'commitTransaction']
        assert len(commits) == 1 and commits[0]['writeConcern']['w'] == 'majority' and commits[0]['writeConcern']['j'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('completed_commands', range(1, 7))
async def test_real_abort_rolls_back_each_partial_ddl_stage(completed_commands):
    async with fixture() as (_, raw, client, db, _, foreign):
        session = await client.start_session()
        try:
            begin(session)
            commands = [{'create': name} for name in NAMES] + [index_command(i) for i in range(2)]
            for command in commands[:completed_commands]: await db.command(command, session=session)
            await session.abort_transaction()
        finally: await session.end_session()
        assert await target_metadata(raw) == {}
        assert await raw[RECEIPTS].find({}).to_list(length=2) == [foreign]


@pytest.mark.asyncio
async def test_real_snapshot_explicit_ddl_rejected():
    async with fixture() as (_, raw, client, db, _, _):
        session = await client.start_session()
        try:
            begin(session, 'snapshot')
            with pytest.raises(OperationFailure): await db.command({'create': NAMES[0]}, session=session)
        finally: await session.end_session()
        assert await target_metadata(raw) == {}


@pytest.mark.asyncio
async def test_real_existing_empty_collection_cannot_receive_new_index_in_transaction():
    async with fixture() as (_, raw, client, db, _, _):
        await raw.create_collection(NAMES[0])
        before = await target_metadata(raw)
        session = await client.start_session()
        try:
            begin(session)
            with pytest.raises(OperationFailure): await db.command(index_command(0), session=session)
        finally: await session.end_session()
        assert await target_metadata(raw) == before
        assert [x['name'] for x in await raw[NAMES[0]].list_indexes().to_list(length=3)] == ['_id_']


@pytest.mark.asyncio
async def test_real_raw_create_on_existing_same_options_is_noop():
    async with fixture() as (_, raw, _, db, _, _):
        await raw.create_collection(NAMES[0])
        foreign = {'_id': 'foreign', 'user_id': 'another_owner'}
        await raw[NAMES[0]].insert_one(foreign)
        before = await target_metadata(raw)
        assert (await db.command({'create': NAMES[0], 'writeConcern': WC}))['ok'] == 1
        assert await target_metadata(raw) == before
        assert await raw[NAMES[0]].find({}).to_list(length=2) == [foreign]


@pytest.mark.asyncio
async def test_real_transactional_create_rejects_foreign_existing_guard_without_new_index():
    async with fixture() as (_, raw, client, db, _, _):
        assert await target_metadata(raw) == {}
        await raw.create_collection(NAMES[2])
        before = await target_metadata(raw)
        session = await client.start_session()
        try:
            begin(session)
            # Unlike the policy race, there is no subsequent new-index command
            # which could detect an old collection. This is a distinct fence.
            with pytest.raises(OperationFailure) as error:
                await db.command({'create': NAMES[2]}, session=session)
            assert error.value.code == 48  # NamespaceExists; never privilege/network failure.
        finally: await session.end_session()
        assert await target_metadata(raw) == before


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['insert', 'createIndex'])
async def test_real_denied_target_privilege_is_not_replaced_by_broad_role(missing):
    async with fixture(missing=missing) as (_, raw, client, db, _, _):
        session = await client.start_session()
        try:
            begin(session)
            if missing == 'insert':
                with pytest.raises(OperationFailure) as error: await db.command({'create': NAMES[0]}, session=session)
            else:
                await db.command({'create': NAMES[0]}, session=session)
                with pytest.raises(OperationFailure) as error: await db.command(index_command(0), session=session)
            assert error.value.code == 13
        finally: await session.end_session()
        assert await target_metadata(raw) == {}


@pytest.mark.asyncio
@pytest.mark.parametrize('command', ['listCollections', 'listIndexes'])
async def test_real_catalog_commands_are_not_transaction_snapshot_reads(command):
    async with fixture() as (_, raw, client, db, _, _):
        await raw.create_collection(NAMES[0])
        session = await client.start_session()
        try:
            begin(session)
            await db[RECEIPTS].find_one({}, session=session)
            query = {'listCollections': 1, 'nameOnly': False} if command == 'listCollections' else {'listIndexes': NAMES[0]}
            with pytest.raises(OperationFailure): await db.command(query, session=session)
        finally: await session.end_session()


@pytest.mark.asyncio
@pytest.mark.parametrize('foreign_kind', ['same_options', 'custom_options', 'nonempty'])
async def test_real_foreign_create_race_before_transaction_is_not_silently_indexed(foreign_kind):
    async with fixture() as (_, raw, client, db, _, _):
        assert await target_metadata(raw) == {}  # Initial complete observation.
        options = {'validator': {'foreign_owner': {'$exists': True}}} if foreign_kind == 'custom_options' else {}
        await raw.create_collection(NAMES[0], **options)
        if foreign_kind == 'nonempty': await raw[NAMES[0]].insert_one({'_id': 'foreign'})
        before = await target_metadata(raw)
        documents = await raw[NAMES[0]].find({}).to_list(length=2)
        session = await client.start_session()
        try:
            begin(session)
            with pytest.raises(OperationFailure):
                await db.command({'create': NAMES[0]}, session=session)
                await db.command(index_command(0), session=session)
        finally: await session.end_session()
        assert await target_metadata(raw) == before
        assert await raw[NAMES[0]].find({}).to_list(length=2) == documents
        assert [x['name'] for x in await raw[NAMES[0]].list_indexes().to_list(length=3)] == ['_id_']


@pytest.mark.asyncio
async def test_real_drop_recreate_changes_uuid_even_when_contract_same():
    async with fixture() as (_, raw, _, _, _, _):
        await raw.create_collection(NAMES[2])
        before = (await target_metadata(raw))[NAMES[2]]
        await raw.drop_collection(NAMES[2])
        await raw.create_collection(NAMES[2])
        after = (await target_metadata(raw))[NAMES[2]]
        assert before['options'] == after['options'] == {}
        assert before['info']['uuid'] != after['info']['uuid']


@pytest.mark.asyncio
async def test_real_global_reservation_serializes_independent_clients():
    async with fixture() as (_, raw, _, db, _, foreign):
        second = AsyncIOMotorClient('mongodb://127.0.0.1:27483/', username='bootstrap_fixture',
            password='ephemeral_fixture_only', authSource=raw.name, retryWrites=False, serverSelectionTimeoutMS=2000)
        try:
            async def reserve(collection, owner):
                try:
                    await collection.with_options(write_concern=WriteConcern('majority', j=True)).insert_one(
                        {'_id': SLOT, 'owner_id': owner, 'state': 'reserved'})
                    return 'reserved'
                except DuplicateKeyError: return 'blocked'
            results = await asyncio.gather(reserve(db[RECEIPTS], 'one'), reserve(second[raw.name][RECEIPTS], 'two'))
            assert sorted(results) == ['blocked', 'reserved']
            assert await raw[RECEIPTS].count_documents({'_id': SLOT}) == 1
            assert await raw[RECEIPTS].find_one({'_id': foreign['_id']}) == foreign
            assert await target_metadata(raw) == {}
        finally: second.close()
