"""Offline capture-to-import checks with native BSON and an in-memory target.

The capture producer is the separately reviewed sibling package. No source URI,
Mongo connection, auth service, provider or genuine account data is used.
"""
from copy import deepcopy
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
import unittest
from unittest.mock import patch

from bson import BSON, Binary, Decimal128, Int64, ObjectId, Timestamp
from bson.raw_bson import RawBSONDocument

import import_snapshot as imp
from private_common import BINDING_COLLECTION, REPLICA_SET, STATE_COLLECTION, RuntimeBlocked, digest, load_export, validate_config

ROOT = Path(__file__).resolve().parent
DATA_TOOLS = Path(os.environ.get('PR2095_DATA_TOOLS', ROOT.parent / 'preview-data-tools'))
sys.path.insert(0, str(DATA_TOOLS))
import export_native_snapshot as producer
from test_export_native_snapshot import ARCHIVE_SOURCE, OWNER, FakeReader, financial_order_fixture, fixtures, key


def identity(value):
    return bytes(BSON.encode({'_id': value}))


class MemoryCollection:
    def __init__(self, database, name):
        self.database, self.name, self.rows = database, name, {}

    def insert_one(self, value):
        self.database.events.append(('insert', self.name))
        raw = value.raw if isinstance(value, RawBSONDocument) else bytes(BSON.encode(value))
        decoded = BSON(raw).decode()
        ident = identity(decoded['_id'])
        if ident in self.rows: raise RuntimeError('synthetic_duplicate_key')
        self.rows[ident] = raw
        if self.name == BINDING_COLLECTION and self.database.fail_binding:
            raise OSError('synthetic_lost_acknowledgement')

    def insert_many(self, values, ordered):
        assert ordered is True
        assert all(isinstance(row, RawBSONDocument) for row in values)
        for row in values: self.insert_one(row)

    def count_documents(self, query, maxTimeMS):
        assert query == {} and maxTimeMS > 0
        self.database.events.append(('count', self.name))
        return len(self.rows)

    def find(self, query, max_time_ms):
        assert max_time_ms > 0
        wanted = {identity(value) for value in query['_id']['$in']}
        self.database.events.append(('readback', self.name))
        for ident, raw in self.rows.items():
            if ident in wanted:
                if self.database.corrupt_readback and self.name == STATE_COLLECTION:
                    decoded = BSON(raw).decode(); decoded['revision'] += 1
                    raw = bytes(BSON.encode(decoded))
                yield RawBSONDocument(raw)

    def delete_one(self, query):
        self.database.events.append(('delete', self.name))
        ident = identity(query['_id'])
        raw = self.rows.get(ident)
        if raw is not None and all(BSON(raw).decode().get(k) == v for k, v in query.items()):
            del self.rows[ident]


class MemoryDatabase:
    def __init__(self):
        self.collections, self.events = {}, []
        self.fail_binding = self.corrupt_readback = False

    def __getitem__(self, name):
        return self.collections.setdefault(name, MemoryCollection(self, name))

    def list_collection_names(self):
        return list(self.collections)


class MemoryClient:
    def __init__(self):
        self.database, self.admin, self.existing = MemoryDatabase(), self, []
        self.hello = {'setName': REPLICA_SET, 'hosts': ['127.0.0.1:27019'], 'isWritablePrimary': True}

    def command(self, name):
        assert name == 'hello'
        return self.hello

    def list_database_names(self):
        return self.existing

    def get_database(self, name, codec_options, write_concern):
        assert codec_options.document_class is RawBSONDocument
        assert write_concern.document == {'j': True, 'w': 'majority'}
        return self.database


class ImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture = self.root / 'capture'
        state, pages = fixtures()
        reader = FakeReader(state, pages)
        reader.rows[key('users', {'created_by': OWNER, 'role': {'$ne': 'owner'}})] = [BSON.encode({
            '_id':'synthetic-staff-id', 'id':'synthetic-staff', 'created_by':OWNER, 'role':'accountant'})]
        reader.rows[key('unified_orders', {'user_id': OWNER})] = [BSON.encode(financial_order_fixture())]
        self.proof = bytes(BSON.encode({'_id': ObjectId('000000000000000000000001'), 'user_id': OWNER,
            'decimal': Decimal128('1234.5600'), 'large': Int64(2**62), 'binary': Binary(b'\x00\xff', subtype=128),
            'timestamp': Timestamp(123, 4), 'ordered': {'z': 1, 'a': 2}}))
        reader.rows[key('mezan_product_cost_profiles_v2', {'user_id': OWNER})] = [self.proof]
        budget = producer.Budget()
        output = producer.PrivateOutput(str(self.capture))
        try:
            sha, _ = producer.export_snapshot(reader, output, producer.load_archive(str(ARCHIVE_SOURCE), budget),
                budget, OWNER, 'synthetic_source', include_navigation=True, min_revision=7, require_active=True)
        finally:
            output.close()
        self.config = validate_config({'source_root': str(ARCHIVE_SOURCE.parent.parent), 'export_directory': str(self.capture),
            'manifest_sha256': sha, 'expected_owner_id': OWNER, 'database_name': 'mezan_pr2095_20261125_synthetic',
            'preview_origin': 'https://synthetic.preview.emergentagent.com'}, self.root / 'runtime.json')
        self.manifest, self.counts, self.policy_sha = load_export(self.config)
        self.files = imp.ExportFiles(self.capture)
        self.addCleanup(self.files.close)

    def validate(self):
        budget = imp.Budget()
        records = imp.validate_files(self.config, self.manifest, self.counts, self.files, budget)
        return records, budget

    def copy(self, client):
        records, budget = self.validate()
        return imp.copy_to_new_database(client, self.config, self.manifest, self.counts, self.policy_sha,
            records, self.files, budget)

    def rewrite_record(self, record, change):
        path = self.capture / record['file']
        row = BSON(path.read_bytes()).decode()
        change(row)
        raw = bytes(BSON.encode(row)); path.write_bytes(raw)
        record.update(bytes=len(raw), sha256=digest(raw))
        if 'source_sha256' in record: record['source_sha256'] = digest(raw)

    def persist_manifest(self):
        raw = json.dumps(self.manifest).encode()
        (self.capture / 'manifest.json').write_bytes(raw)
        (self.capture / 'COMPLETE.json').write_text(json.dumps({'schema_version':2, 'manifest_sha256':digest(raw)}))
        self.config['manifest_sha256'] = digest(raw)

    def test_reviewed_producer_to_byte_exact_import_and_last_binding(self):
        client = MemoryClient()
        result = self.copy(client)
        self.assertTrue(result['ok'])
        stored = next(iter(client.database['mezan_product_cost_profiles_v2'].rows.values()))
        self.assertEqual(stored, self.proof)
        decoded = BSON(stored).decode()
        self.assertIsInstance(decoded['decimal'], Decimal128)
        self.assertIsInstance(decoded['large'], Int64)
        self.assertEqual(decoded['binary'].subtype, 128)
        self.assertEqual(list(decoded['ordered']), ['z', 'a'])
        self.assertEqual(len(client.database['users'].rows), 2)
        self.assertEqual(client.database.events[0], ('insert', imp.CLAIM_COLLECTION))
        self.assertEqual(client.database.events[-1], ('insert', BINDING_COLLECTION))
        self.assertEqual(stat.S_IMODE(Path(self.config['config_path']).stat().st_mode), 0o600)
        self.assertNotIn('mongo', json.dumps(json.loads(Path(self.config['config_path']).read_text())).lower())

    def test_all_hashes_and_duplicate_ids_are_checked_before_writes(self):
        client = MemoryClient()
        record = next(row for row in self.manifest['supplemental'] if row['scope'] == 'team')['files'][0]
        original = (self.capture / record['file']).read_bytes()
        (self.capture / record['file']).write_bytes(original[:-1] + b'!')
        with self.assertRaisesRegex(RuntimeBlocked, 'hash_mismatch'): self.copy(client)
        self.assertEqual(client.database.events, [])
        (self.capture / record['file']).write_bytes(original)
        self.rewrite_record(record, lambda row: row.update(_id='synthetic-owner-id'))
        with self.assertRaisesRegex(RuntimeBlocked, 'duplicate_or_oversized_identity'): self.copy(client)
        self.assertEqual(client.database.events, [])

    def test_owner_nested_private_fields_and_receipt_tamper_are_rejected(self):
        record = next(row for row in self.manifest['supplemental'] if row['scope'] == 'financial-orders')['files'][0]
        original_record, original = deepcopy(record), (self.capture / record['file']).read_bytes()
        changes = [lambda row: row.update(user_id='another-owner'),
            lambda row: row['raw_by_source']['salla_direct']['items'][0].update(customer={'phone':'private'}),
            lambda row: row['raw_by_source']['salla_direct']['payment'].update(customer={'phone':'private'})]
        # Add an allowed payment container once so the malicious child is the issue.
        base = BSON(original).decode(); base['raw_by_source']['salla_direct']['payment'] = {'status':'paid'}
        for change in changes:
            with self.subTest(change=changes.index(change)):
                (self.capture / record['file']).write_bytes(BSON.encode(base))
                self.rewrite_record(record, change)
                client = MemoryClient()
                with self.assertRaises(RuntimeBlocked): self.copy(client)
                self.assertEqual(client.database.events, [])
        record.clear(); record.update(original_record)
        (self.capture / record['file']).write_bytes(original)
        entry = next(row for row in self.manifest['supplemental'] if row['scope'] == 'financial-orders')
        entry['order_shape_receipt']['truthiness_checks'] += 1
        with self.assertRaisesRegex(RuntimeBlocked, 'shape_receipt_invalid'): self.validate()

    def test_coverage_implementation_and_incomplete_receipts_fail_closed(self):
        baseline = deepcopy(self.manifest)
        modifications = [lambda: self.manifest['tool_implementation']['files'].update({'navigation_sources.py':'0'*64}),
            lambda: self.manifest['supplemental'][0]['projection'].update({'email':1}),
            lambda: self.manifest['scope_limits'].update(full_navigation_source_closure_verified=False),
            lambda: self.manifest['source_recheck'].update(outside_capture_snapshot=False)]
        for change in modifications:
            self.manifest = deepcopy(baseline); change(); self.persist_manifest()
            with self.assertRaises(RuntimeBlocked): load_export(self.config)
        self.manifest = baseline; self.persist_manifest()
        (self.capture / 'INCOMPLETE').write_text('synthetic incomplete')
        with self.assertRaisesRegex(RuntimeBlocked, 'incomplete_export'): load_export(self.config)

    def test_wrong_replica_existing_database_and_nonprivate_name_write_nothing(self):
        for mutation in ('replica', 'member', 'not_primary', 'existing', 'name'):
            with self.subTest(mutation=mutation):
                client = MemoryClient(); old_name = self.config['database_name']
                if mutation == 'replica': client.hello['setName'] = 'another'
                elif mutation == 'member': client.hello['hosts'] = ['127.0.0.1:27018']
                elif mutation == 'not_primary': client.hello['isWritablePrimary'] = False
                elif mutation == 'existing': client.existing = [old_name]
                else: self.config['database_name'] = 'P01'
                try:
                    with self.assertRaises(RuntimeBlocked): self.copy(client)
                    self.assertEqual(client.database.events, [])
                    self.assertFalse(Path(self.config['config_path']).exists())
                finally: self.config['database_name'] = old_name

    def test_readback_and_lost_binding_ack_leave_no_ready_publication(self):
        for fail in ('corrupt_readback', 'fail_binding'):
            with self.subTest(fail=fail):
                client = MemoryClient(); setattr(client.database, fail, True)
                with self.assertRaises((RuntimeBlocked, OSError)): self.copy(client)
                self.assertFalse(Path(self.config['config_path']).exists())
                self.assertFalse(client.database[BINDING_COLLECTION].rows)
                self.assertTrue(client.database[imp.CLAIM_COLLECTION].rows)
                self.assertTrue(client.database[STATE_COLLECTION].rows)

    def test_config_write_failure_removes_only_created_file(self):
        client = MemoryClient()
        with patch('import_snapshot.os.fsync', side_effect=OSError('synthetic_disk_error')):
            with self.assertRaises(OSError): self.copy(client)
        self.assertFalse(Path(self.config['config_path']).exists())
        self.assertFalse(client.database[BINDING_COLLECTION].rows)
        path = Path(self.config['config_path']); path.write_text('preexisting')
        with self.assertRaisesRegex(RuntimeBlocked, 'config_must_be_new'): self.copy(MemoryClient())
        self.assertEqual(path.read_text(), 'preexisting')


if __name__ == '__main__':
    unittest.main()
