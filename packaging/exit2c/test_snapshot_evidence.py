"""Unit coverage for value-free strict snapshot diagnostics."""
import copy
from datetime import datetime
import io
from contextlib import redirect_stdout, redirect_stderr
import unittest

from snapshot_evidence import COLLECTIONS, FIELD_NAMES, snapshot_lines, validate_snapshot_line


def sample():
    return {"identity": ([('order', 'item', 1, 'batch')], [], []), "documents": {name: [{"id": "synthetic-a", "quantity": 2, "status": "ready", "updated_at": datetime(2026, 9, 1)}] for name in COLLECTIONS.values()}}


class SnapshotEvidenceTests(unittest.TestCase):
    def test_equal_is_silent_and_inputs_unchanged(self):
        before = sample()
        saved = copy.deepcopy(before)
        self.assertTrue(snapshot_lines(before, copy.deepcopy(before)) == [])
        self.assertTrue(before == saved)

    def test_count_identity_quantity_status_and_timestamp(self):
        before, after = sample(), sample()
        after["identity"] = ([], [], [])
        row = after["documents"][COLLECTIONS["PIECES"]][0]
        row.update(id="different", quantity=3, status="received", updated_at=datetime(2026, 9, 2))
        result = snapshot_lines(before, after)
        for line in ("SNAPSHOT IDENTITY_CHANGED true", "SNAPSHOT PIECES COUNT 1 1", "SNAPSHOT PIECES IDENTITIES_CHANGED true", "SNAPSHOT PIECES QUANTITIES_CHANGED true", "SNAPSHOT PIECES STATUSES_CHANGED true", "SNAPSHOT PIECES FIELD updated_at"):
            self.assertTrue(line in result, "safe diagnostic missing")
        after["documents"][COLLECTIONS["PIECES"]].append({"id": "new"})
        self.assertTrue("SNAPSHOT PIECES COUNT 1 2" in snapshot_lines(before, after))

    def test_row_order_is_reported_without_acceptance_change(self):
        before = sample()
        rows = before["documents"][COLLECTIONS["BATCHES"]]
        rows.append({"id": "synthetic-b", "quantity": 4})
        after = copy.deepcopy(before)
        after["documents"][COLLECTIONS["BATCHES"]].reverse()
        self.assertTrue(before != after)
        lines = snapshot_lines(before, after)
        self.assertTrue("SNAPSHOT BATCHES ORDER_CHANGED true" in lines)
        self.assertTrue("SNAPSHOT BATCHES IDENTITIES_CHANGED false" in lines)

    def test_reassigned_quantities_are_not_hidden_by_equal_totals(self):
        before = sample()
        rows = before["documents"][COLLECTIONS["PIECES"]]
        rows.append({"id": "synthetic-b", "quantity": 4, "status": "received"})
        after = copy.deepcopy(before)
        changed = after["documents"][COLLECTIONS["PIECES"]]
        changed[0]["quantity"], changed[1]["quantity"] = 4, 2
        changed[0]["status"], changed[1]["status"] = "received", "ready"
        lines = snapshot_lines(before, after)
        self.assertTrue("SNAPSHOT PIECES QUANTITIES_CHANGED true" in lines)
        self.assertTrue("SNAPSHOT PIECES STATUSES_CHANGED true" in lines)
        self.assertTrue("SNAPSHOT PIECES FIELD quantity" in lines)
        self.assertTrue("SNAPSHOT PIECES FIELD status" in lines)

    def test_actual_workflow_stage_and_piece_progress_fields_are_fixed(self):
        before, after = sample(), sample()
        workflow = after["documents"][COLLECTIONS["WORKFLOWS"]][0]
        workflow.update(stage="in_progress", revision=2, salla_status_sync="sent", carrier_label_ready=False, carrier_label_status="failed", in_progress_at=datetime(2026, 9, 2))
        piece = after["documents"][COLLECTIONS["PIECES"]][0]
        piece.update(execution_status="completed", preparation_receipt_status="received", assembly_ready_at=datetime(2026, 9, 2))
        lines = snapshot_lines(before, after)
        for group, fields in (("WORKFLOWS", ("stage", "revision", "salla_status_sync", "carrier_label_ready", "carrier_label_status", "in_progress_at")), ("PIECES", ("execution_status", "preparation_receipt_status", "assembly_ready_at"))):
            self.assertTrue("SNAPSHOT " + group + " STATUSES_CHANGED true" in lines)
            self.assertTrue("SNAPSHOT " + group + " UNKNOWN_FIELDS_CHANGED false" in lines)
            for field in fields:
                self.assertTrue("SNAPSHOT " + group + " FIELD " + field in lines, "known progress diagnostic absent")
        self.assertTrue(all(validate_snapshot_line(line) for line in lines))

    def test_hostile_values_and_unknown_keys_never_render(self):
        before, after = sample(), sample()
        secret = "private-cookie-value\nFAIL stage injected https://example.test/token"
        row = after["documents"][COLLECTIONS["WORKFLOWS"]][0]
        row.update(status=secret)
        row[secret] = {"headers": secret, "cookies": secret, "options": secret}
        captured = io.StringIO()
        with redirect_stdout(captured), redirect_stderr(captured):
            lines = snapshot_lines(before, after)
        self.assertTrue(captured.getvalue() == "", "diagnostics wrote output")
        self.assertTrue(all(secret not in line and "\n" not in line for line in lines), "sensitive diagnostic output")
        self.assertTrue("SNAPSHOT WORKFLOWS UNKNOWN_FIELDS_CHANGED true" in lines)
        self.assertTrue(all(validate_snapshot_line(line) for line in lines))
        # Even an assertion failure over the evidence uses a constant error,
        # with no raw snapshot/value representation in the runner's output.
        class IntentionalFailure(unittest.TestCase):
            def runTest(inner):
                inner.assertTrue(False, "synthetic diagnostic assertion failed")
        stream = io.StringIO()
        unittest.TextTestRunner(stream=stream).run(IntentionalFailure())
        self.assertTrue(secret not in stream.getvalue(), "failed test leaked sensitive output")

    def test_invalid_missing_overflow_and_unsupported_values_fail_closed(self):
        base = sample()
        bad_cases = [None, {}, {"identity": [], "documents": {}}]
        bad = copy.deepcopy(base)
        bad["documents"][COLLECTIONS["REGISTRY"]] = [{}] * 10001
        bad_cases.append(bad)
        bad = copy.deepcopy(base)
        bad["documents"][COLLECTIONS["REGISTRY"]][0]["unknown"] = object()
        bad_cases.append(bad)
        bad = copy.deepcopy(base)
        bad["identity"] = "x" * 2000001
        bad_cases.append(bad)
        for bad in bad_cases:
            self.assertTrue(snapshot_lines(base, bad) == ["SNAPSHOT unavailable"], "invalid snapshot was accepted")

    def test_line_cap_is_unavailable_not_truncated_success(self):
        before, after = sample(), sample()
        for rows in after["documents"].values():
            rows[0].update({name: "changed" for name in FIELD_NAMES})
        self.assertTrue(snapshot_lines(before, after) == ["SNAPSHOT unavailable"])

    def test_validator_rejects_dynamic_content_and_bad_counts(self):
        bad = ["SNAPSHOT PIECES FIELD cookie", "SNAPSHOT PRIVATE COUNT 1 2", "SNAPSHOT PIECES COUNT 0 10001", "SNAPSHOT PIECES COUNT 01 2", "SNAPSHOT PIECES COUNT -1 2", "SNAPSHOT PIECES COUNT 1 2\n", "SNAPSHOT unavailable\n", "SNAPSHOT PIECES STATUS_CHANGED true", "SNAPSHOT PIECES ORDER_CHANGED yes", "SNAPSHOT PIECES FIELD status\n", None]
        self.assertTrue(all(not validate_snapshot_line(line) for line in bad))
        self.assertTrue(validate_snapshot_line("SNAPSHOT PIECES COUNT 0 10000"))

    def test_diagnostics_do_not_modify_mismatching_snapshots(self):
        before, after = sample(), sample()
        after["documents"][COLLECTIONS["EVENTS"]][0]["updated_at"] = datetime(2026, 9, 3)
        saved_before, saved_after = copy.deepcopy(before), copy.deepcopy(after)
        self.assertTrue(bool(snapshot_lines(before, after)))
        self.assertTrue(before == saved_before and after == saved_after and before != after)


if __name__ == "__main__":
    unittest.main()
