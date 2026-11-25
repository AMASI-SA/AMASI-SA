"""Synthetic, offline acceptance tests. No production credentials or network."""
from contextlib import redirect_stderr, redirect_stdout
import ast
from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import MagicMock, Mock, patch
import warnings
import zlib

from bson import BSON, Binary, Decimal128, Int64

import export_native_snapshot as export
import navigation_sources as navigation


ARCHIVE_SOURCE = Path(os.environ.get("PR2095_TEST_ARCHIVE_SOURCE",
    Path(__file__).resolve().parents[1] / "mezan-navigation/backend/operational_balance_archive.py"))
OWNER = "synthetic-test-owner"
DATABASE = "synthetic_source"


def packet(payload):
    data = BSON.encode(payload)
    return {"size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "data": zlib.compress(data)}


def fixtures():
    """Construct archive hashes independently of the validator under test."""
    pages, refs, start = {}, [], 0
    for number in range(2):
        events = [{"kind": "synthetic", "amount": Decimal128("12.3400"),
                   "sequence": Int64(number), "proof": Binary(b"\x00\xff", subtype=128)}]
        digest = hashlib.sha256(BSON.encode({"owner_id": OWNER, "start": start, "events": events})).hexdigest()
        pages[digest] = BSON.encode({"_id": digest, "owner_id": OWNER, "start": start, "events": events})
        refs.append({"id": digest, "count": len(events)})
        start += len(events)
    manifest = {"version": 1, "count": start, "pages": refs}
    manifest["sha256"] = hashlib.sha256(BSON.encode(manifest)).hexdigest()
    fields = {"facts": [{"amount": Decimal128("19.2300"), "currency": "SAR",
                         "created_at": datetime(2026, 1, 1, tzinfo=timezone.utc)}], "obligations": {}}
    financial = {"version": 1, "fields": sorted(fields), **packet({"owner_id": OWNER, "fields": fields})}
    state = {"_id": OWNER, "owner_id": OWNER, "revision": Int64(7), "status": "active",
        "engine": {"facts": {"stored_in_financial_snapshot_v1": True},
            "obligations": {"stored_in_financial_snapshot_v1": True},
            "audit": {"tail": [{"kind": "synthetic-inline"}]},
            "issues": [{"code": "operational_source_issues_packed"}]},
        "engine_audit_archive": manifest,
        "packed_financial_snapshot_v1": financial,
        "packed_source_issues_v1": packet({"owner_id": OWNER, "issues": [{"code": "synthetic"}]})}
    return state, pages


def key(collection, query):
    return collection, json.dumps(query, sort_keys=True)


def financial_order_fixture():
    current = {"company_name": "Synthetic carrier", "company_code": "synthetic", "status": "shipped",
        "shipment_id": Int64(123), "source_kind": "order", "superseded_shipment_ids": []}
    raw = {"id": Int64(99), "reference_id": "SYNTHETIC-99", "currency": "SAR", "collection_status": "refunded",
        "total": {"amount": "100.00", "currency": "SAR"}, "paid_amount": {"amount": "0.00", "currency": "SAR"},
        "remaining_amount": {"amount": "100.00", "currency": "SAR"}, "payment_method": "cod",
        "salla_shipping_current": deepcopy(current),
        "items": [{"id": "line-1", "quantity": Int64(2), "product_id": "p1", "options": [
            {"id": 8, "value": {"id": 19, "value": "Exact customization"}}],
            "custom_fields": [{"id": 31, "value": {"files": [Binary(b"synthetic", subtype=128)]}}],
            "selected_context": [["id:8", "id:19"]], "name": "Marketing label is not a financial input"}],
        "shipping": {"company": {"id": "synthetic", "name": "Synthetic carrier", "address": "private irrelevant"},
            "tracking": {"number": "SYNTHETIC-AWB", "url": "https://example.invalid/financial-proof"},
            "ship_to": {"name": "Private irrelevant"}},
        "refunds": [{"id": Int64(25), "amount": Decimal128("10.00"), "card": {"last4": "0000"}}]}
    return {"_id": "synthetic-order", "user_id": OWNER, "order_number": "SYNTHETIC-99",
        "payment_method": "cod", "salla_shipping_current": current,
        "raw_by_source": {"salla_direct": raw}, navigation.WITNESS: {
            "parent_type": "object", "direct_type": "object", "direct_nonempty": True}}


class FakeReader:
    def __init__(self, state, pages):
        self.state = bytes(BSON.encode(state))
        self.final_state = self.state
        self.pages = dict(pages)
        self.calls, self.context_calls, self.state_reads, self.current_reads = [], [], 0, 0
        self.rows = {key("users", {"id": OWNER}): [BSON.encode({"_id": "synthetic-owner-id", "id": OWNER,
            "role": "owner", "name": "Synthetic owner", "operational_balance_access": {"version": 2}})]}

    def one(self, collection, query):
        self.calls.append((collection, query))
        if collection == export.STATES:
            assert query == {"_id": OWNER, "owner_id": OWNER}
            self.state_reads += 1
            raw = self.state if self.state_reads == 1 else self.final_state
            return None if raw is None else bytes(raw)
        assert collection == export.PAGES and query["owner_id"] == OWNER
        raw = self.pages.get(query["_id"])
        return None if raw is None else bytes(raw)

    def projected(self, collection, query, projection, maximum):
        self.context_calls.append((collection, query, projection, maximum))
        yield from (bytes(row) for row in self.rows.get(key(collection, query), []))

    def current_one(self, collection, query):
        self.current_reads += 1
        return self.one(collection, query)


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "capture"
        self.budget = export.Budget()
        self.archive = export.load_archive(str(ARCHIVE_SOURCE), self.budget)
        self.state, self.pages = fixtures()
        self.reader = FakeReader(self.state, self.pages)

    def capture(self, **options):
        output = export.PrivateOutput(str(self.path))
        try:
            return export.export_snapshot(self.reader, output, self.archive, self.budget,
                OWNER, DATABASE, **options)
        finally:
            output.close()

    def failure(self, code=None, **options):
        with self.assertRaises(Exception) as raised:
            self.capture(**options)
        if code is not None:
            self.assertIsInstance(raised.exception, export.ExportFailure)
            self.assertEqual(raised.exception.code, code)
        self.assertFalse((self.path / "COMPLETE.json").exists())
        return raised.exception

    def test_exact_bson_manifest_order_and_private_files(self):
        # An unreferenced page must never be read or copied.
        self.reader.pages["f" * 64] = BSON.encode({"owner_id": "unrelated", "events": []})
        progress = []
        digest, manifest = self.capture(progress=progress.append, min_revision=7, require_active=True)
        self.assertEqual((self.path / "state.bson").read_bytes(), self.reader.state)
        self.assertEqual(manifest["state"]["revision"], 7)
        refs = self.state["engine_audit_archive"]["pages"]
        for index, ref in enumerate(refs):
            self.assertEqual((self.path / f"audit/{index:06d}.bson").read_bytes(), self.pages[ref["id"]])
            self.assertEqual(manifest["audit"]["pages"][index]["id"], ref["id"])
        self.assertEqual(self.reader.calls, [(export.STATES, {"_id": OWNER, "owner_id": OWNER}),
            *[(export.PAGES, {"_id": ref["id"], "owner_id": OWNER}) for ref in refs],
            (export.STATES, {"_id": OWNER, "owner_id": OWNER})])
        self.assertEqual(manifest["audit"]["total_events"], 3)
        self.assertEqual(self.reader.current_reads, 1)
        self.assertEqual(manifest["consistency"]["source_reads"], "one_read_only_snapshot_session")
        self.assertTrue(manifest["source_recheck"]["outside_capture_snapshot"])
        self.assertFalse(manifest["scope_limits"]["expected_forecast_source_graph_included"])
        manifest_bytes = (self.path / "manifest.json").read_bytes()
        self.assertEqual(digest, hashlib.sha256(manifest_bytes).hexdigest())
        self.assertEqual(json.loads((self.path / "COMPLETE.json").read_bytes())["manifest_sha256"], digest)
        self.assertFalse((self.path / "INCOMPLETE").exists())
        for path in [self.path, *self.path.rglob("*")]:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o700 if path.is_dir() else 0o600)
        self.assertNotIn(OWNER, json.dumps(progress))

    def test_corrupt_audit_bytes_fail_without_complete_marker(self):
        first = next(iter(self.pages))
        doc = BSON(self.pages[first]).decode()
        doc["events"][0]["amount"] = Decimal128("999.00")
        self.reader.pages[first] = BSON.encode(doc)
        self.assertEqual(type(self.failure()).__name__, "ArchiveError")

    def test_missing_audit_page_fails(self):
        self.reader.pages.clear()
        self.failure("AUDIT_PAGE_MISSING")

    def test_wrong_owner_in_page_fails(self):
        first = next(iter(self.pages))
        doc = BSON(self.pages[first]).decode()
        doc["owner_id"] = "other-owner"
        self.reader.pages[first] = BSON.encode(doc)
        self.assertEqual(type(self.failure()).__name__, "ArchiveError")

    def test_raw_state_change_at_same_revision_is_rejected(self):
        changed = dict(self.state, status="changed-without-revision")
        self.reader.final_state = BSON.encode(changed)
        self.failure("STATE_CHANGED_DURING_CAPTURE")

    def test_revision_change_is_rejected(self):
        self.reader.final_state = BSON.encode(dict(self.state, revision=8))
        self.failure("STATE_CHANGED_DURING_CAPTURE")

    def test_packet_integrity_is_checked_before_writing_state(self):
        self.state["packed_financial_snapshot_v1"]["sha256"] = "0" * 64
        self.reader = FakeReader(self.state, self.pages)
        self.assertEqual(type(self.failure()).__name__, "ArchiveError")
        self.assertFalse((self.path / "state.bson").exists())

    def test_credentials_inside_compressed_packet_are_rejected(self):
        fields = {"facts": [{"accessToken": "synthetic-sensitive-token"}], "obligations": {}}
        self.state["packed_financial_snapshot_v1"] = {"version": 1, "fields": sorted(fields),
            **packet({"owner_id": OWNER, "fields": fields})}
        self.reader = FakeReader(self.state, self.pages)
        self.failure("SECRET_MATERIAL_PRESENT")
        self.assertFalse((self.path / "state.bson").exists())

    def test_owner_context_positive_projection_and_exact_binding(self):
        team_query = {"created_by": OWNER, "role": {"$ne": "owner"}}
        self.reader.rows[key("users", team_query)] = [BSON.encode({"_id": "synthetic-team-id", "id": "synthetic-team",
            "created_by": OWNER, "role": "accountant", "web_page_access": {"version": 5}})]
        self.reader.rows[key("mz2_atomic_owners", {"_id": OWNER})] = [BSON.encode({
            "_id": OWNER, "ledger_backend_state": "ready"})]
        _, manifest = self.capture(include_context=True)
        self.assertEqual([scope["count"] for scope in manifest["supplemental"]], [1, 1, 1, 0])
        self.assertTrue(manifest["scope_limits"]["owner_context_included"])
        for _, _, fields, _ in self.reader.context_calls:
            self.assertNotIn("password", fields)
            self.assertNotIn("email", fields)
        self.assertEqual(self.reader.context_calls[1][1], team_query)

    def test_explicit_movement_denial_survives_owner_and_team_projection(self):
        permission = "accounting.movements.view"
        users = [spec for spec in export.context_specs(OWNER) if spec[1] == "users"]
        for _, _, _, fields, _, _ in users:
            self.assertEqual(fields.get("denied_permissions"), 1)
        owner = {"_id": "synthetic-owner-id", "id": OWNER, "role": "owner",
            "denied_permissions": [permission, "accounting.other.view"]}
        team = {"_id": "synthetic-team-id", "id": "synthetic-team", "role": "accountant",
            "created_by": OWNER, "denied_permissions": [permission]}
        self.reader.rows[key("users", {"id": OWNER})] = [BSON.encode(owner)]
        self.reader.rows[key("users", {"created_by": OWNER, "role": {"$ne": "owner"}})] = [BSON.encode(team)]
        _, manifest = self.capture(include_context=True)
        copied = []
        for scope in manifest["supplemental"]:
            if scope["collection"] == "users":
                copied.extend(BSON((self.path / item["file"]).read_bytes()).decode() for item in scope["files"])
        self.assertEqual([row["denied_permissions"] for row in copied],
            [owner["denied_permissions"], team["denied_permissions"]])
        # Execute the exact browser gate. Its explicit denial must preempt the
        # downstream accounting-grant check, even for the stored owner role.
        tree = ast.parse((ARCHIVE_SOURCE.parent / "operational_financial_activity.py").read_bytes())
        node = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
            and node.name == "require_browser_activity_permission")
        def denied(code, message, status):
            self.assertEqual(status, 403)
            raise PermissionError(code)
        namespace = {"fail": denied}
        exec(compile(ast.Module(body=[node], type_ignores=[]), "exact_browser_permission_gate", "exec"), namespace)
        grant = Mock()
        with patch.dict(export.sys.modules, {"accounting_module_contract": SimpleNamespace(require_accounting_permission=grant)}):
            for actor in (owner, *copied):
                with self.assertRaisesRegex(PermissionError, "accounting_permission_required"):
                    namespace["require_browser_activity_permission"](actor, permission)
            grant.assert_not_called()
            without_denials = {key: value for key, value in owner.items() if key != "denied_permissions"}
            namespace["require_browser_activity_permission"](without_denials, permission)
            grant.assert_called_once_with(without_denials, permission)

    def test_unprojected_secret_user_field_fails(self):
        self.reader.rows[key("users", {"id": OWNER})] = [BSON.encode({
            "_id": "synthetic-owner-id", "id": OWNER, "role": "owner", "password_hash": "synthetic"})]
        self.failure("CONTEXT_PROJECTION_MISMATCH", include_context=True)

    def test_wrong_owner_context_fails(self):
        self.reader.rows[key("users", {"id": OWNER})] = [BSON.encode({"_id": "synthetic-owner-id", "id": "other-owner", "role": "owner"})]
        self.failure("CONTEXT_OWNER_MISMATCH", include_context=True)

    def test_duplicate_owner_rows_fail_in_exporter_boundary(self):
        self.reader.rows[key("users", {"id": OWNER})] *= 2
        self.failure("CONTEXT_ROW_LIMIT", include_context=True)

    def test_duplicate_source_id_across_user_scopes_is_rejected(self):
        query = {"created_by": OWNER, "role": {"$ne": "owner"}}
        self.reader.rows[key("users", query)] = [BSON.encode({"_id": "synthetic-owner-id",
            "id": "synthetic-team", "created_by": OWNER, "role": "accountant"})]
        self.failure("CONTEXT_DUPLICATE_SOURCE_ID", include_context=True)

    def test_resource_directory_uses_narrow_nested_projections(self):
        self.reader.rows[key("settings", {"user_id": OWNER})] = [BSON.encode({
            "_id": "synthetic-settings", "user_id": OWNER,
            "shipping_companies": [{"name": "Synthetic carrier", "active": True}],
            "payment_methods": [{"name": "Cash"}]})]
        _, manifest = self.capture(include_context=True, include_directory=True)
        self.assertTrue(manifest["scope_limits"]["resource_directory_included"])
        self.assertIn("ad-accounts", [entry["scope"] for entry in manifest["supplemental"]])
        fields = next(call[2] for call in self.reader.context_calls if call[0] == "mezan_integration_accounts_v2")
        self.assertNotIn("access_token", fields)
        self.assertFalse(any(call[0] == "unified_orders" for call in self.reader.context_calls))

    def test_nested_projection_extra_field_is_rejected(self):
        self.reader.rows[key("settings", {"user_id": OWNER})] = [BSON.encode({
            "_id": "synthetic-settings", "user_id": OWNER,
            "shipping_companies": [{"name": "Synthetic", "address": "not-authorized"}]})]
        self.failure("CONTEXT_PROJECTION_MISMATCH", include_context=True, include_directory=True)

    def test_full_navigation_receipts_raw_proofs_and_duplicate_logical_ids(self):
        proof_collection = "accounting_general_ledger_v2"
        proofs = [BSON.encode({"_id": f"proof-{index}", "user_id": OWNER, "id": "same-logical-id",
            "amount": Decimal128("12.3400"), "source": Binary(b"\x00\xff", subtype=128)}) for index in range(2)]
        self.reader.rows[key(proof_collection, {"user_id": OWNER})] = proofs
        self.reader.rows[key("unified_orders", {"user_id": OWNER})] = [BSON.encode(financial_order_fixture())]
        self.reader.rows[key("mezan_ads_auto_sync_scheduler_v2", {"_id": "ads-v2-server-scheduler"})] = [BSON.encode({
            "_id": "ads-v2-server-scheduler", "enabled": False, "interval_seconds": 3600})]
        _, manifest = self.capture(include_navigation=True)
        self.assertEqual(manifest["schema_version"], 2)
        for field in ("expected_forecast_source_graph_included", "provider_order_label_sources_included",
            "courier_unready_order_discovery_included", "full_navigation_source_closure_verified"):
            self.assertTrue(manifest["scope_limits"][field])
        scopes = {row["collection"]: row for row in manifest["supplemental"]}
        self.assertTrue(navigation.EXPECTED_COLLECTIONS <= set(scopes))
        self.assertEqual(len(self.reader.context_calls), len(manifest["supplemental"]))
        for row in manifest["supplemental"]:
            self.assertTrue(row["cursor_exhausted"])
            self.assertEqual(row["source_count"], row["count"])
            self.assertEqual(row["count"], len(row["files"]))
        self.assertEqual(scopes[proof_collection]["count"], 2)
        self.assertEqual(scopes[proof_collection]["capture_mode"], "raw_bson")
        for proof, saved in zip(proofs, scopes[proof_collection]["files"]):
            self.assertEqual((self.path / saved["file"]).read_bytes(), proof)
            self.assertEqual(saved["source_sha256"], saved["sha256"])
        self.assertEqual(scopes["mezan_inventory_receipts_v2"]["source_count"], 0)
        self.assertEqual(scopes["mezan_ads_auto_sync_scheduler_v2"]["binding"], "global_metadata")
        order_scope = scopes["unified_orders"]
        self.assertEqual(order_scope["order_shape_receipt"]["documents"], 1)
        cleaned = BSON((self.path / order_scope["files"][0]["file"]).read_bytes()).decode()
        self.assertNotIn(navigation.WITNESS, cleaned)
        self.assertEqual(cleaned["raw_by_source"]["salla_direct"]["collection_status"], "refunded")
        contract_bytes = (self.path / manifest["source_contract"]["file"]).read_bytes()
        self.assertEqual(hashlib.sha256(contract_bytes).hexdigest(), manifest["source_contract"]["sha256"])
        self.assertNotIn(OWNER, contract_bytes.decode())

    def test_full_navigation_source_limit_aborts_without_truncation(self):
        self.reader.rows[key("mezan_products_v2", {"user_id": OWNER})] = [
            BSON.encode({"_id": str(index), "user_id": OWNER}) for index in range(2)]
        self.failure("CONTEXT_ROW_LIMIT", include_navigation=True, max_source_rows=1)

    def test_global_metadata_scope_cannot_capture_a_different_document(self):
        self.reader.rows[key("mezan_ads_auto_sync_scheduler_v2", {"_id": "ads-v2-server-scheduler"})] = [
            BSON.encode({"_id": "other-metadata", "enabled": False})]
        self.failure("CONTEXT_OWNER_MISMATCH", include_navigation=True)

    def test_page_count_cap(self):
        self.budget.max_pages = 1
        self.failure("AUDIT_LIMIT")

    def test_byte_cap(self):
        self.budget.max_bytes = len(self.reader.state) + len(next(iter(self.pages.values()))) - 1
        self.failure("BYTE_LIMIT")

    def test_total_document_cap(self):
        self.budget.max_documents = 2
        self.failure("DOCUMENT_COUNT_LIMIT")

    def test_deadline_cap(self):
        self.budget.deadline = 0
        self.failure("TIME_LIMIT")

    def test_stale_revision_gate(self):
        self.failure("STATE_BELOW_MIN_REVISION", min_revision=8)

    def test_active_gate(self):
        self.reader = FakeReader(dict(self.state, status="draft"), self.pages)
        self.failure("STATE_NOT_ACTIVE", require_active=True)

    def test_boolean_revision_is_not_an_integer_revision(self):
        self.reader = FakeReader(dict(self.state, revision=True), self.pages)
        self.failure("STATE_REVISION_INVALID")

    def test_missing_state_never_creates_a_fixture(self):
        self.reader.state = None
        self.failure("STATE_NOT_FOUND")

    def test_existing_output_is_never_overwritten(self):
        self.path.mkdir()
        sentinel = self.path / "sentinel"
        sentinel.write_bytes(b"unchanged")
        with self.assertRaises(FileExistsError):
            export.PrivateOutput(str(self.path))
        self.assertEqual(sentinel.read_bytes(), b"unchanged")

    def test_symlink_ancestor_is_rejected(self):
        link = Path(self.tmp.name) / "linked"
        link.symlink_to(Path(self.tmp.name), target_is_directory=True)
        with self.assertRaises(OSError):
            export.PrivateOutput(str(link / "private"))
        self.assertFalse((Path(self.tmp.name) / "private").exists())

    def test_untrusted_archive_source_is_not_executed(self):
        path = Path(self.tmp.name) / "wrong.py"
        path.write_text("raise RuntimeError('must never execute')\n")
        with self.assertRaises(export.ExportFailure) as raised:
            export.load_archive(str(path), self.budget)
        self.assertEqual(raised.exception.code, "VALIDATOR_SOURCE_MISMATCH")


class NativeReaderTests(unittest.TestCase):
    def test_all_capture_queries_share_one_snapshot_and_head_read_is_current(self):
        from bson.raw_bson import RawBSONDocument
        from pymongo.read_preferences import ReadPreference
        client = MagicMock()
        session = Mock()
        client.start_session.return_value = session
        collection = client.__getitem__.return_value.__getitem__.return_value
        doc = RawBSONDocument(bytes(BSON.encode({"_id": "synthetic", "user_id": OWNER})))
        collection.find_one.return_value = doc
        cursor = MagicMock()
        cursor.limit.return_value = cursor
        cursor.batch_size.return_value = cursor
        cursor.__next__.side_effect = [doc, StopIteration]
        collection.find.return_value = cursor
        with patch("pymongo.MongoClient", return_value=client) as constructor:
            reader = export.NativeReader("mongodb://source.invalid/db?tls=true", DATABASE, export.Budget())
            self.assertEqual(reader.one(export.STATES, {"_id": OWNER}), doc.raw)
            self.assertEqual(list(reader.projected("users", {"id": OWNER}, {"_id": 1}, 1)), [doc.raw])
            self.assertEqual(reader.current_one(export.STATES, {"_id": OWNER}), doc.raw)
            reader.close()
        client.start_session.assert_called_once_with(snapshot=True)
        self.assertIs(collection.find_one.call_args_list[0].kwargs["session"], session)
        self.assertIs(collection.find.call_args.kwargs["session"], session)
        self.assertIsNone(collection.find_one.call_args_list[1].kwargs["session"])
        self.assertEqual(constructor.call_args.kwargs["read_preference"], ReadPreference.PRIMARY)
        cursor.close.assert_called_once()
        session.end_session.assert_called_once()
        client.close.assert_called_once()

    def test_unsupported_snapshot_never_falls_back_to_ordinary_reads(self):
        from pymongo.errors import OperationFailure
        client = MagicMock()
        client.start_session.side_effect = OperationFailure("synthetic unsupported snapshot")
        with patch("pymongo.MongoClient", return_value=client):
            with self.assertRaises(OperationFailure):
                export.NativeReader("mongodb://source.invalid/db?tls=true", DATABASE, export.Budget())
        client.start_session.assert_called_once_with(snapshot=True)
        client.__getitem__.return_value.__getitem__.assert_not_called()
        client.close.assert_called_once()

    def test_order_aggregation_is_fixed_read_only_and_snapshot_bound(self):
        from bson.raw_bson import RawBSONDocument
        client = MagicMock()
        session = client.start_session.return_value
        collection = client.__getitem__.return_value.__getitem__.return_value
        cursor = MagicMock()
        row = RawBSONDocument(bytes(BSON.encode(financial_order_fixture())))
        cursor.__next__.side_effect = [row, StopIteration]
        collection.aggregate.return_value = cursor
        with patch("pymongo.MongoClient", return_value=client):
            reader = export.NativeReader("mongodb://source.invalid/db?tls=true", DATABASE, export.Budget())
            result = list(reader.projected("unified_orders", {"user_id": OWNER}, navigation.order_source_projection(), 10))
            reader.close()
        self.assertEqual(result, [row.raw])
        pipeline = collection.aggregate.call_args.args[0]
        self.assertEqual([set(stage) for stage in pipeline], [{"$match"}, {"$project"}, {"$limit"}])
        self.assertEqual(pipeline[0], {"$match": {"user_id": OWNER}})
        self.assertEqual(pipeline[-1], {"$limit": 11})
        self.assertIs(collection.aggregate.call_args.kwargs["session"], session)
        self.assertFalse(collection.aggregate.call_args.kwargs["allowDiskUse"])
        projected_fields = set(pipeline[1]["$project"])
        self.assertIn("payment_method", projected_fields)
        self.assertNotIn("raw_by_source", projected_fields)
        self.assertNotIn("raw_by_source.salla_direct.customer", projected_fields)
        cursor.close.assert_called_once()


class FinancialOrderTests(unittest.TestCase):
    def transform(self, doc):
        return navigation.financial_order(doc, export.require, lambda: None)

    def test_financial_filter_preserves_selection_types_and_current_equality(self):
        original = financial_order_fixture()
        result, receipt = self.transform(original)
        original_raw = original["raw_by_source"]["salla_direct"]
        result_raw = result["raw_by_source"]["salla_direct"]
        for field in ("options", "custom_fields", "selected_context", "quantity"):
            self.assertEqual(BSON.encode({"v": original_raw["items"][0][field]}),
                BSON.encode({"v": result_raw["items"][0][field]}))
        self.assertEqual(BSON.encode({"v": original["salla_shipping_current"]}),
            BSON.encode({"v": result["salla_shipping_current"]}))
        self.assertEqual(result_raw["salla_shipping_current"], result["salla_shipping_current"])
        self.assertNotIn("name", result_raw["items"][0])
        self.assertNotIn("address", result_raw["shipping"]["company"])
        self.assertNotIn("ship_to", result_raw["shipping"])
        self.assertNotIn("card", result_raw["refunds"][0])
        self.assertGreater(receipt["exact_selection_values"], 0)
        self.assertEqual(receipt["exact_current_observations"], 2)

    def test_truthy_unknown_evidence_cannot_be_replaced_by_empty_container(self):
        for path in ("shipment", "tracking", "refunds", "payment_actions"):
            doc = financial_order_fixture()
            doc["raw_by_source"]["salla_direct"][path] = {"unknown-evidence": "present"}
            with self.subTest(path=path), self.assertRaises(export.ExportFailure) as raised:
                self.transform(doc)
            self.assertEqual(raised.exception.code, "ORDER_PROJECTION_ERASES_EVIDENCE")

    def test_array_cardinality_order_scalars_and_nulls_are_preserved(self):
        doc = financial_order_fixture()
        values = [None, "unexpected-but-observed", {"id": Int64(3)}, {}, []]
        doc["raw_by_source"]["salla_direct"]["items"] = values
        result, _ = self.transform(doc)
        self.assertEqual(BSON.encode({"v": values}), BSON.encode({"v": result["raw_by_source"]["salla_direct"]["items"]}))

    def test_raw_source_missing_and_null_are_not_conflated(self):
        variants = [("missing", "missing", None), ("null", "missing", None),
            ("object", "missing", {}), ("object", "null", {"salla_direct": None}),
            ("object", "object", {"salla_direct": {}})]
        for parent, direct, observed in variants:
            doc = {"_id": "x", "user_id": OWNER, navigation.WITNESS: {
                "parent_type": parent, "direct_type": direct, "direct_nonempty": False}}
            if parent != "missing":
                doc["raw_by_source"] = observed
            result, _ = self.transform(doc)
            with self.subTest(parent=parent, direct=direct):
                if parent == "missing":
                    self.assertNotIn("raw_by_source", result)
                else:
                    self.assertEqual(result["raw_by_source"], observed)

    def test_malformed_raw_source_parent_requires_failure(self):
        for parent, direct in (("array", "missing"), ("string", "missing"), ("object", "array"), ("object", "double")):
            doc = {"_id": "x", "user_id": OWNER, navigation.WITNESS: {
                "parent_type": parent, "direct_type": direct, "direct_nonempty": False}}
            with self.subTest(parent=parent, direct=direct), self.assertRaises(export.ExportFailure) as raised:
                self.transform(doc)
            self.assertEqual(raised.exception.code, "ORDER_SOURCE_PARENT_SHAPE")

    def test_original_truthy_direct_source_cannot_disappear_in_projection(self):
        doc = {"_id": "x", "user_id": OWNER, "raw_by_source": {"salla_direct": {}}, navigation.WITNESS: {
            "parent_type": "object", "direct_type": "object", "direct_nonempty": True}}
        with self.assertRaises(export.ExportFailure) as raised:
            self.transform(doc)
        self.assertEqual(raised.exception.code, "ORDER_PROJECTION_ERASES_EVIDENCE")

    def test_unexpected_current_observation_fields_fail_instead_of_being_stripped(self):
        for field in ("ship_to", "unknown-proof"):
            doc = financial_order_fixture()
            doc["salla_shipping_current"][field] = {"name": "sensitive"}
            with self.subTest(field=field), self.assertRaises(export.ExportFailure):
                self.transform(doc)

    def test_native_refunded_collection_status_exclusion_survives(self):
        # Execute the exact existing pure COD calculation, not a rewritten test approximation.
        namespace = {"Decimal": Decimal, "InvalidOperation": InvalidOperation, "SUPPORTED_ORDER_CURRENCIES": {"SAR"}}
        root = ARCHIVE_SOURCE.parent
        selections = [
            ("operational_payment_evidence.py", {"PaymentEvidenceError", "_amount"}, {"CENT", "LIMIT"}),
            ("operational_prestart_cod_forecast.py", {"_remaining", "_explicit_amount"}, set()),
        ]
        for filename, functions, constants in selections:
            tree = ast.parse((root / filename).read_bytes())
            nodes = [node for node in tree.body if
                isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in functions or
                isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and node.targets[0].id in constants]
            exec(compile(ast.Module(body=nodes, type_ignores=[]), "synthetic_cod_reference", "exec"), namespace)
        doc = financial_order_fixture()
        original = doc["raw_by_source"]["salla_direct"]
        original.pop("refunds")
        result, _ = self.transform(doc)
        filtered = result["raw_by_source"]["salla_direct"]
        for value in (original, filtered):
            with self.assertRaisesRegex(ValueError, "conflicting_payment_status"):
                namespace["_remaining"](value)
        deliberately_missing_field = {key: value for key, value in filtered.items() if key != "collection_status"}
        self.assertEqual(namespace["_remaining"](deliberately_missing_field), ("SAR", Decimal("100.00")))

    def test_contract_has_one_union_projection_and_no_credential_collections(self):
        contract, digest, raw = export.source_contract(True, True, True)
        scopes = contract["scopes"]
        by_collection = {}
        for scope in scopes:
            by_collection.setdefault(scope["collection"], []).append(scope)
        self.assertEqual({name for name, records in by_collection.items() if len(records) != 1}, {"users"})
        self.assertEqual(len(by_collection["users"]), 2)
        shipping = by_collection["mz2_shipping_setup_v2"][0]["projection"]
        self.assertTrue({"couriers", "contracts", "contract_evidence"} <= set(shipping))
        accounts = by_collection["mezan_integration_accounts_v2"][0]["projection"]
        self.assertTrue({"mezan_selected", "financial_coverage.expected_requests", "connection_provenance"} <= set(accounts))
        for collection in ("customers", "sessions", "mezan_order_cost_corrections_v1", "accounting_ledger_sequences_v2"):
            self.assertNotIn(collection, by_collection)
        self.assertEqual(hashlib.sha256(raw).hexdigest(), digest)


class InputBoundaryTests(unittest.TestCase):
    def test_low_output_capacity_fails_before_hidden_input(self):
        errors = io.StringIO()
        with tempfile.TemporaryDirectory() as temp, redirect_stderr(errors), \
             patch.object(export.PrivateOutput, "require_capacity", side_effect=export.ExportFailure("OUTPUT_FREE_SPACE_LOW")), \
             patch.object(export, "hidden_uri") as prompt:
            result = export.main(["--source-db", DATABASE, "--owner", OWNER,
                "--output-dir", str(Path(temp) / "capture"), "--archive-source", str(ARCHIVE_SOURCE)])
        self.assertEqual(result, 2)
        self.assertEqual(json.loads(errors.getvalue())["code"], "OUTPUT_FREE_SPACE_LOW")
        prompt.assert_not_called()

    def test_no_tty_refuses_before_getpass(self):
        with patch.object(export.sys, "stdin", io.StringIO()), patch.object(export.getpass, "getpass") as prompt:
            with self.assertRaises(export.ExportFailure) as raised:
                export.hidden_uri()
        self.assertEqual(raised.exception.code, "CONTROLLING_TTY_REQUIRED")
        prompt.assert_not_called()

    def test_getpass_echo_fallback_is_an_error(self):
        tty = Mock()
        tty.isatty.return_value = True
        def fallback(*args):
            warnings.warn("synthetic fallback", export.getpass.GetPassWarning)
            self.fail("getpass must stop before reading with echo")
        with patch.object(export.sys, "stdin", tty), patch.object(export.sys, "stderr", tty), \
             patch.object(export.os, "open", return_value=88), patch.object(export.os, "close"), \
             patch("termios.tcgetattr"), patch.object(export.getpass, "getpass", side_effect=fallback):
            with self.assertRaises(export.getpass.GetPassWarning):
                export.hidden_uri()

    def test_driver_errors_never_echo_the_uri(self):
        synthetic_uri = "mongodb://synthetic:must-never-print@source.invalid/db?tls=true"
        output, errors = io.StringIO(), io.StringIO()
        with tempfile.TemporaryDirectory() as temp, redirect_stdout(output), redirect_stderr(errors), \
             patch.object(export, "hidden_uri", return_value=synthetic_uri), \
             patch.object(export, "NativeReader", side_effect=RuntimeError(synthetic_uri)):
            result = export.main(["--source-db", DATABASE, "--owner", OWNER,
                "--output-dir", str(Path(temp) / "capture"), "--archive-source", str(ARCHIVE_SOURCE)])
        self.assertEqual(result, 2)
        self.assertEqual(output.getvalue(), "")
        self.assertEqual(json.loads(errors.getvalue()), {"ok": False, "code": "EXPORT_FAILED", "error_type": "RuntimeError"})
        self.assertNotIn("must-never-print", errors.getvalue())

    def test_accidental_uri_cli_argument_is_not_echoed(self):
        errors = io.StringIO()
        with redirect_stderr(errors), patch.object(export, "hidden_uri") as prompt:
            result = export.main(["--uri", "mongodb://synthetic:must-never-print@source.invalid/db"])
        self.assertEqual(result, 2)
        self.assertNotIn("must-never-print", errors.getvalue())
        self.assertEqual(json.loads(errors.getvalue())["code"], "ARGUMENTS_INVALID")
        prompt.assert_not_called()

    def test_secure_transport_and_loopback_are_accepted(self):
        for uri in ("mongodb://127.0.0.1/db", "mongodb://[::1]/db", "mongodb://localhost/db",
            "mongodb://source.invalid/db?tls=true", "mongodb://source.invalid/db?ssl=true",
            "mongodb+srv://source.invalid/db"):
            with self.subTest(uri=uri):
                export.validate_transport(uri)

    def test_transport_downgrade_is_rejected(self):
        for uri in ("mongodb://source.invalid/db", "mongodb://source.invalid/db?tls=false",
            "mongodb+srv://source.invalid/db?ssl=false",
            "mongodb://source.invalid/db?tls=true&tlsAllowInvalidCertificates=true",
            "mongodb://source.invalid/db?tls=true&tlsAllowInvalidHostnames=true",
            "mongodb+srv://source.invalid/db?tlsInsecure=true",
            "mongodb://127.0.0.1,source.invalid/db",
            "mongodb://source.invalid/db?tls=true&ssl=false"):
            with self.subTest(uri=uri), self.assertRaises(export.ExportFailure):
                export.validate_transport(uri)


if __name__ == "__main__":
    unittest.main()
